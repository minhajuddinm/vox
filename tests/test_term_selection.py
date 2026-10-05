"""Which dictionary terms go into the prompts: select_terms (cleanup prompt, transcript known) and the Whisper prompt v2
(no transcript yet). The golden rows pickterms, termkey and whisperv2 pin the exact rules for both apps."""
import random
import time

import vox_core as core

SAMPLE = {"people": ["Minhajuddin", "Peyman"],
          "dictionary": ["# my words", "Groq", "Tailscale", "gpt-oss", "Kubernetes", "u v raj => Yuvraj", "vox => Vox"]}


def test_the_sample_dictation_names_four_terms_with_or_without_150_more():
    terms, repl = core.dictionary_terms(SAMPLE), core.replacements(SAMPLE)
    raw = ("um so i talked to minhaj about the grok rate limits and the tail scale setup new paragraph u v raj will check "
           "it okay")
    want = ["Minhajuddin", "Groq", "Tailscale", "Yuvraj"]
    assert core.select_terms(raw, terms, repl) == want
    assert core.select_terms(raw, terms + ["Term%03dalpha" % k for k in range(150)], repl) == want


def test_ordinary_sentences_name_no_terms():
    terms = core.dictionary_terms(SAMPLE)
    for raw in ("okay so the plan for this week is to finish the relay work", "the fox jumped over the box",
                "i like the scale of this tale", "we talked about the grass and the rocks", "i was at a party"):
        assert core.select_terms(raw, terms) == []


def test_terms_come_in_the_order_they_are_spoken_and_at_most_twenty():
    assert core.select_terms("ask peyman about vox", ["Vox", "Peyman"]) == ["Peyman", "Vox"]
    nato = ("Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo Lima Mike November Oscar Papa Quebec Romeo "
            "Sierra Tango Uniform Victor Whiskey Xray Yankee").split()
    assert core.select_terms(" ".join(n.lower() for n in nato), nato[::-1]) == nato[:20]


def test_a_replacement_names_its_right_side_only_for_a_whole_word():
    assert core.select_terms("the voxel engine", [], {"vox": "Vox"}) == []
    assert core.select_terms("VOX is ready", [], {"vox": "Vox"}) == ["Vox"]


def test_term_keys_merge_the_sounds_indian_english_mixes_up():
    assert core.term_key("vocs") == core.term_key("Vox") == "VKS"
    assert core.term_key("kubernetis") == core.term_key("Kubernetes")
    assert core.term_key("uvraj") == core.term_key("Yuvraj")
    assert core.term_key("wikram") == core.term_key("vikram")
    assert core.term_key("bhai") == core.term_key("bai") and core.term_key("") == "" and core.term_key("123") == ""


def test_term_selection_is_fast_for_a_big_dictionary_and_a_long_dictation():
    """500 terms and a 300-word transcript: the term side is cached per dictionary, the windows are bucketed by first
    letter, so this takes tens of milliseconds; the bound is generous for slow CI machines."""
    rnd = random.Random(1)
    terms = ["".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(4, 12))).capitalize()
             for _ in range(500)]
    vocab = ["the", "meeting", "about", "deploy", "server", "kubernetes", "tomorrow", "friday", "budget", "yuvraj", "rocks",
             "alpha", "plan"]
    raw = " ".join(rnd.choice(vocab) for _ in range(300))
    core.select_terms(raw, terms)   # fills the cache for this dictionary
    t0 = time.perf_counter()
    for _ in range(3):
        out = core.select_terms(raw, terms)
    per_call = (time.perf_counter() - t0) / 3
    assert per_call < 1.0 and len(out) <= core.PROMPT_TERMS_MAX
    assert core._term_index(terms) is core._term_index(list(terms))   # one cache entry per dictionary


# ------------------------------------------------------------------ Whisper prompt v2

def test_people_come_first_then_recently_learned_terms_then_the_dictionary():
    terms = core.dictionary_terms(SAMPLE)
    out = core.whisper_prompt(terms, SAMPLE["people"], ["Yuvraj"])
    assert out == "Talked with Minhajuddin and Peyman about Yuvraj, Groq, Tailscale, gpt-oss, Kubernetes and Vox."


def test_terms_that_sound_like_the_earlier_text_come_first():
    terms = core.dictionary_terms(SAMPLE)
    out = core.whisper_prompt_with_context(terms, "then we moved the grok key to tail scale", SAMPLE["people"])
    assert out.startswith("Talked with Minhajuddin and Peyman about Groq, Tailscale, gpt-oss")
    assert out.endswith(". then we moved the grok key to tail scale")


def test_a_long_dictionary_keeps_people_and_whole_terms_within_the_budget():
    terms = core.dictionary_terms(SAMPLE) + ["Term%03dalpha" % k for k in range(150)]
    ctx = "Okay so the plan for this week is to finish the relay work and then look at the Android bubble again."
    out = core.whisper_prompt_with_context(terms, ctx, SAMPLE["people"])
    assert out.startswith("Talked with Minhajuddin and Peyman about ") and out.endswith(ctx)
    assert core.est_tokens(out) <= core.WHISPER_PROMPT_TOKENS
    named = out[len("Talked with Minhajuddin and Peyman about "):out.index(". Okay")].replace(" and ", ", ").split(", ")
    assert all(t in terms for t in named) and len(named) + 2 <= core.WHISPER_PROMPT_TERMS


def test_the_earlier_text_is_cut_from_its_front_at_a_word():
    out = core.whisper_prompt_with_context([], "x " * 400 + "the end")
    assert out.endswith(" x x the end") and not out.startswith(" ") and core.est_tokens(out) <= core.WHISPER_CONTEXT_TOKENS


def test_recent_terms_are_the_words_learned_in_the_last_two_weeks():
    now = 1_000_000_000.0
    cfg = {"learned_log": [{"t": now - 20 * 86400, "wrong": "a", "right": "Old"},
                           {"t": now - 3 * 86400, "wrong": "b", "right": "Fresh"},
                           {"t": now, "wrong": "c", "right": " Fresh "},
                           {"t": True, "wrong": "d", "right": "Bad"}, "junk", {"t": now, "wrong": "e", "right": 5}]}
    assert core.recent_terms(cfg, now) == ["Fresh"]
    assert core.recent_terms({}, now) == [] and core.recent_terms({"learned_log": "x"}, now) == []
    assert core.people_terms({"people": [" Ada ", "# note", "", "Ada", "Grace"]}) == ["Ada", "Grace"]


def test_transcribe_sends_the_v2_prompt_with_people_and_recent_terms(monkeypatch):
    seen = {}

    class R:
        status_code = 200

        def json(self):
            return {"text": "hi"}

    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.update(kw) or R())
    cfg = dict(SAMPLE, api_key="k", learned_log=[{"t": time.time(), "wrong": "u v raj", "right": "Yuvraj"}])
    core.transcribe(cfg, b"RIFF")
    assert seen["data"]["prompt"] == ("Talked with Minhajuddin and Peyman about Yuvraj, Groq, Tailscale, gpt-oss, Kubernetes "
                                      "and Vox.")
