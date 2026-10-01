"""The fidelity guard: a cleanup that lost the speaker's words is rejected, and the raw words are used instead.

The same rules run on the phone (Fidelity.java); spec/golden.txt (kinds fidelity, tokens, recall) ties the two together.
These tests add the property-style and long-text checks that do not fit one line of that file."""
import random

import pytest
import requests

import vox_core as core


SENTENCES = [
    "so yesterday I went to the market and bought some apples and bananas",
    "then I came home and cooked dinner for the whole family",
    "we all sat down together and talked about the trip we are planning for the summer",
    "my sister said she would book the tickets if we agree on the dates",
    "I told her that the second week of june works best for me and for the kids",
    "after that we looked at a few hotels near the beach and compared the prices",
    "the cheapest one had no breakfast so we kept looking for something better",
    "in the end we picked the small place with the garden and the old stone wall",
]


def long_text(words):
    out = []
    i = 0
    while len(out) < words:
        out.extend(SENTENCES[i % len(SENTENCES)].split())
        i += 1
    return " ".join(out[:words])


def punctuate(text):
    """What a good Light cleanup does to a long text: capitals, commas, full stops and paragraph breaks only."""
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if i % 14 == 0:
            w = w.capitalize()
        out.append(w)
        if i % 14 == 13:
            out[-1] += "."
            if i % 56 == 55:
                out[-1] += "\n\n"
        elif i % 6 == 5:
            out[-1] += ","
    return " ".join(out).replace("\n\n ", "\n\n")


# ------------------------------------------------------------------ tokens

def test_word_tokens_basics():
    assert core.word_tokens("Hello, World! It's 5pm.") == ["hello", "world", "it's", "5pm"]
    assert core.word_tokens("") == [] and core.word_tokens(None) == []
    assert core.word_tokens("- one\n- two\n\n  three") == ["one", "two", "three"]
    assert core.word_tokens("don’t") == ["don't"]            # a curly apostrophe is the same word
    assert core.word_tokens("'quoted'") == ["quoted"]              # quotes around a word are not part of it


def test_fillers_constant():
    assert {"um", "uh", "er", "erm", "ah", "hmm", "like", "you know", "i mean", "sort of", "kind of"} <= core.FILLERS
    assert isinstance(core.FILLERS, frozenset)


# ------------------------------------------------------------------ recall

def test_recall_is_one_for_empty_raw_and_zero_for_nothing_kept():
    assert core.word_recall("", "whatever") == 1.0
    assert core.word_recall("...", "whatever") == 1.0
    assert core.word_recall("one two", "") == 0.0


def test_recall_counts_repeated_words_as_a_multiset():
    assert core.word_recall("the the the cat", "the cat") == 0.5
    assert core.word_recall("the cat", "the the the cat") == 1.0


def test_recall_ignores_order():
    assert core.word_recall("red green blue", "blue red green") == 1.0


def test_number_words_equal_digits():
    assert core.word_recall("I have twenty five apples", "I have 25 apples.") == 1.0
    assert core.word_recall("I have twenty-five apples", "I have 25 apples.") == 1.0
    assert core.word_recall("one hundred people", "100 people") == 1.0
    assert core.word_recall("it costs 25 dollars", "it costs twenty five dollars") == 1.0
    assert core.word_recall("nineteen ninety nine", "1999") == 1.0


def test_big_numbers_with_and_and_point_equal_their_digits():
    assert core.word_recall("it costs two hundred dollars", "It costs $200.") == 1.0
    assert core.word_recall("two thousand twenty six", "2026") == 1.0
    assert core.word_recall("one hundred and five degrees", "105°") == 1.0
    assert core.word_recall("one thousand two hundred and fifty", "1,250") == 1.0
    assert core.word_recall("a hundred and a thousand", "100 and 1000") == 1.0
    assert core.word_recall("three point five liters", "3.5 liters") == 1.0
    assert core.word_recall("three thirty p m", "3:30 PM") == 1.0
    assert core.word_recall("seven a m", "7 a.m.") == 1.0


def test_ordinals_scale_words_and_half_past_equal_their_digits():
    assert core.word_recall("the twenty first of march", "the 21st of March") == 1.0
    assert core.word_recall("the twenty-second and the thirtieth", "the 22nd and the 30th") == 1.0
    assert core.word_recall("the third eleventh twelfth thirteenth", "the 3rd 11th 12th 13th") == 1.0
    assert core.word_recall("five million two hundred thousand", "5,200,000") == 1.0
    assert core.word_recall("two crore fifty lakh and five", "2,50,00,005") == 1.0
    assert core.word_recall("a billion", "1,000,000,000") == 1.0
    assert core.word_recall("half past three", "3:30") == 1.0
    assert core.word_recall("five lakh rupees", "Rs. 5,00,000") == 1.0
    assert core.word_recall("five lakh rupees", "5,00,000") == 0.5


def test_scale_words_do_not_merge_out_of_order():
    assert core._merge_numbers(["five", "thousand", "five", "thousand"]) == ["5005", "thousand"]
    assert core.word_recall("two thousand million", "2000") < 1.0
    assert core.word_recall("five lakh twenty thousand", "5,00,000") == 0.0


def test_a_wrong_number_is_still_a_lost_word():
    assert core.word_recall("two hundred", "300") == 0.0
    assert core.word_recall("two thousand twenty six", "2025") == 0.0
    assert core.word_recall("three point five", "3.6") == 0.0
    assert core.word_recall("three thirty p m", "3:30 AM") < 1.0


def test_and_is_a_word_unless_it_sits_inside_a_number():
    assert core.word_recall("salt and pepper", "salt pepper") < 1.0
    assert core.word_recall("one hundred and then some", "100 then some") < 1.0   # that "and" is a real word


def test_spoken_at_and_dot_are_kept_only_for_an_at_sign_or_a_dot_inside_a_word():
    assert core.word_recall("mail john at gmail dot com", "Mail john@gmail.com.") == 1.0
    assert core.word_recall("see www dot example dot org", "See www.example.org.") == 1.0
    assert core.word_recall("meet me at noon", "Meet me noon.") < 1.0
    assert core.word_recall("a dot on the page", "A on the page.") < 1.0      # a full stop is not a spoken dot
    assert core.word_recall("a dot on the page", "A dot. On the page.") == 1.0


def test_common_dictation_with_numbers_and_emails_passes_the_guard_in_both_strengths():
    pairs = [
        ("it costs two hundred dollars for the big one", "It costs $200 for the big one."),
        ("two thousand twenty six", "2026"),
        ("one hundred and five degrees", "105°"),
        ("john at gmail dot com", "john@gmail.com"),
        ("three point five liters", "3.5 liters"),
        ("three thirty p m", "3:30 PM"),
    ]
    for raw, cleaned in pairs:
        for strength in ("light", "standard"):
            assert core.fidelity_ok(raw, cleaned, strength), (raw, cleaned, strength)


def test_symbols_cover_currency_and_percent_words_only_when_present():
    assert core.word_recall("five dollars", "$5") == 1.0
    assert core.word_recall("ten percent off", "10% off") == 1.0
    assert core.word_recall("five dollars", "5") == 0.5


@pytest.mark.parametrize("seed", range(20))
def test_adding_only_whitespace_and_bullet_markers_never_lowers_recall(seed):
    rnd = random.Random(seed)
    words = [rnd.choice(["alpha", "beta", "um", "gamma", "twenty", "five", "don't", "the", "the", "x1"])
             for _ in range(rnd.randint(1, 60))]
    raw = " ".join(words)
    base = core.word_recall(raw, raw)
    marked = ""
    for w in words:
        marked += rnd.choice([" ", "\n", "\n\n", "\n- ", "  ", "\t", "\n* ", "\n• "]) + w
    assert core.word_recall(raw, marked) >= base
    assert core.word_recall(raw, marked + "\n- " + "\n".join(words)) >= base


@pytest.mark.parametrize("seed", range(20))
def test_adding_words_to_the_cleaned_text_never_lowers_recall(seed):
    rnd = random.Random(100 + seed)
    raw = long_text(rnd.randint(5, 80))
    cleaned = " ".join(w for w in raw.split() if rnd.random() < 0.9)
    assert core.word_recall(raw, cleaned + " and some more words") >= core.word_recall(raw, cleaned)


@pytest.mark.parametrize("seed", range(20))
def test_recall_stays_between_zero_and_one_and_identical_text_is_perfect(seed):
    rnd = random.Random(200 + seed)
    a = long_text(rnd.randint(1, 120))
    b = " ".join(rnd.sample(a.split(), k=max(1, len(a.split()) // 2)))
    assert 0.0 <= core.word_recall(a, b) <= 1.0
    assert core.word_recall(a, a) == 1.0
    assert core.fidelity_ok(a, a, "light") and core.fidelity_ok(a, a, "standard")


def test_spoken_commands_are_not_words_to_keep():
    assert core.word_recall("hello new line world", "Hello,\nworld") == 1.0
    assert core.word_recall("dear john comma thanks period", "Dear John, thanks.") == 1.0
    assert core.fidelity_ok("hello new line there my friend", "Hello,\nthere my friend.", "light")
    assert core.fidelity_ok("what time is it question mark", "What time is it?", "standard")
    assert not core.fidelity_ok("send it to sam colon the report is wrong", "Send it to Sam: wrong.", "light")


# ---------------------------------------------------------------- the guard

def test_light_keeps_every_word_but_pure_noises():
    raw = "um so I was uh thinking that we should go to the park tomorrow if the weather is nice"
    assert core.fidelity_ok(raw, "So I was thinking that we should go to the park tomorrow if the weather is nice.", "light")
    # "like" and "you know" are real words in Light
    raw = "I like you know the new design because it is simple and clean and fast"
    assert not core.fidelity_ok(raw, "I like the new design because it is simple and clean and fast.", "light")


def test_standard_may_drop_fillers_and_stutters():
    raw = "so um I like you know really want to go to the beach this weekend you know"
    cleaned = "I really want to go to the beach this weekend."
    assert core.fidelity_ok(raw, cleaned, "standard")
    assert not core.fidelity_ok(raw, cleaned, "light")
    assert core.fidelity_ok("I I I want to go to to the the store", "I want to go to the store.", "standard")


def test_a_summary_fails_in_both_strengths():
    raw = long_text(150)
    summary = "I went to the market, cooked dinner and we planned a trip for the summer."
    assert not core.fidelity_ok(raw, summary, "light")
    assert not core.fidelity_ok(raw, summary, "standard")


def test_empty_cleaned_text_fails():
    assert not core.fidelity_ok("hello there", "", "light")
    assert not core.fidelity_ok("hello there", "  \n ", "standard")
    assert not core.fidelity_ok("hello there", None, "light")


def test_an_unknown_strength_is_treated_as_the_strict_one():
    raw = "so um I like you know really want to go to the beach this weekend you know"
    cleaned = "I really want to go to the beach this weekend."
    assert not core.fidelity_ok(raw, cleaned, "banana")
    assert not core.fidelity_ok(raw, cleaned, None)
    assert not core.fidelity_ok(raw, cleaned, "")


def test_strength_is_not_case_sensitive():
    raw = "so um I like you know really want to go to the beach this weekend you know"
    assert core.fidelity_ok(raw, "I really want to go to the beach this weekend.", " Standard ")


def test_short_inputs_pass_the_length_part():
    assert core.fidelity_ok("um yeah", "Yeah.", "light")
    assert core.fidelity_ok("ok", "OK.", "standard")
    assert not core.fidelity_ok("hello there", "Goodbye now.", "light")   # but the words must still be there


# ------------------------------------------------------- very long dictation

def test_a_long_dictation_with_new_punctuation_and_paragraphs_passes():
    raw = long_text(1500)
    for strength in ("light", "standard"):
        assert core.fidelity_ok(raw, punctuate(raw), strength)


def test_a_long_dictation_shortened_to_a_tenth_fails():
    raw = long_text(1500)
    short = " ".join(punctuate(raw).split()[:150])
    assert not core.fidelity_ok(raw, short, "light")
    assert not core.fidelity_ok(raw, short, "standard")


def test_a_long_dictation_missing_one_chunk_fails():
    """Cleanup in chunks must keep all chunks: losing one of ten chunks (10% of the words) is caught."""
    raw = long_text(1500)
    words = punctuate(raw).split()
    without_one_chunk = " ".join(words[:600] + words[750:])
    assert not core.fidelity_ok(raw, without_one_chunk, "light")
    assert core.fidelity_ok(raw, " ".join(words), "light")


def _without_block(words, start, n):
    return " ".join(words[:start] + words[start + n:])


def test_light_loses_at_most_twelve_words_whatever_the_percentage():
    """Review fix: 97% of 1000 words is 30 words, enough to drop a whole paragraph silently. Light also has an
    absolute cap of 12 missing words (Fidelity.LIGHT_MAX_MISSING)."""
    raw = long_text(1000)
    words = punctuate(raw).split()
    assert core.fidelity_ok(raw, _without_block(words, 400, 12), "light")        # 12 missing: the limit
    assert not core.fidelity_ok(raw, _without_block(words, 400, 13), "light")    # 13 missing
    assert not core.fidelity_ok(raw, _without_block(words, 400, 25), "light")    # a dropped sentence or two
    assert not core.fidelity_ok(raw, _without_block(words, 400, 30), "light")
    assert core.word_recall(raw, _without_block(words, 400, 25)) >= 0.97         # the percentage alone would pass
    assert not core.looks_valid(raw, _without_block(words, 400, 25), "light")


def test_the_light_cap_does_not_touch_short_dictations_and_noises_do_not_count():
    raw = long_text(1000)
    noisy = raw.replace(" and ", " um and ", 40)   # 40 pure noises dropped: not lost words
    assert core.fidelity_ok(noisy, punctuate(raw), "light")
    short = long_text(100)
    assert not core.fidelity_ok(short, _without_block(punctuate(short).split(), 40, 4), "light")   # 4% still fails


def test_standard_keeps_the_percentage_rule_only():
    """Standard removes false starts and self-corrections, so no absolute cap: 4% of 1000 words may go."""
    raw = long_text(1000)
    words = punctuate(raw).split()
    assert core.fidelity_ok(raw, _without_block(words, 400, 25), "standard")
    assert not core.fidelity_ok(raw, _without_block(words, 400, 200), "standard")


def test_long_text_with_many_fillers_in_standard():
    noisy = []
    for i, w in enumerate(long_text(600).split()):
        noisy.append(w)
        if i % 7 == 3:
            noisy.append("um")
        if i % 11 == 5:
            noisy.append("you know")
    raw = " ".join(noisy)
    cleaned = punctuate(long_text(600))
    assert core.fidelity_ok(raw, cleaned, "standard")
    assert core.fidelity_ok(raw, cleaned, "light") is False   # "you know" is a real phrase in Light


# --------------------------------------------------------------- looks_valid

def test_looks_valid_now_includes_the_fidelity_guard():
    raw = long_text(60)
    assert core.looks_valid(raw, punctuate(raw))
    assert not core.looks_valid(raw, "I went to the market and cooked dinner.")
    assert not core.looks_valid(raw, "")


def test_looks_valid_takes_the_strength_and_defaults_to_light():
    raw = "so um I like you know really want to go to the beach this weekend you know"
    cleaned = "I really want to go to the beach this weekend."
    assert not core.looks_valid(raw, cleaned)
    assert not core.looks_valid(raw, cleaned, "light")
    assert core.looks_valid(raw, cleaned, "standard")


def test_looks_valid_still_rejects_an_answer_sized_output():
    raw = "what is the capital of france"
    answer = raw + " " + "x" * 200
    assert not core.looks_valid(raw, answer, "light")
    assert not core.looks_valid(raw, answer, "standard")


# ------------------------------------------------------------ process_text

class FakeResp:
    status_code = 200
    text = ""

    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def run_pipeline(monkeypatch, raw, answer, **cfg_extra):
    def fake_post(url, **kw):
        return FakeResp(answer)

    monkeypatch.setattr(core.requests, "post", fake_post)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    cfg = dict(core.DEFAULT_CONFIG, api_key="k", **cfg_extra)
    return core.process_text(cfg, raw, "notepad.exe", "Notepad")


def test_a_summarising_cleanup_is_rejected_and_the_raw_words_are_used(monkeypatch):
    raw = long_text(60)
    r = run_pipeline(monkeypatch, raw, "I went to the market and cooked dinner.")
    assert not r.cleaned and r.cleanup_error
    assert r.text == "S" + raw[1:] and r.raw == raw   # the spoken words, with a capital to start


def test_a_faithful_cleanup_is_used(monkeypatch):
    raw = long_text(60)
    good = punctuate(raw)
    r = run_pipeline(monkeypatch, raw, good)
    assert r.cleaned and r.text == good and r.cleanup_error == ""


def test_standard_strength_from_the_config_allows_filler_removal(monkeypatch):
    raw = "so um I like you know really want to go to the beach this weekend you know"
    cleaned = "I really want to go to the beach this weekend."
    r = run_pipeline(monkeypatch, raw, cleaned, cleanup_strength="standard")
    assert r.cleaned and r.text == cleaned
    r = run_pipeline(monkeypatch, raw, cleaned, cleanup_strength="light")
    assert not r.cleaned and r.cleanup_error


def test_network_error_still_falls_back(monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(core.requests, "post", boom)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    r = core.process_text(dict(core.DEFAULT_CONFIG, api_key="k"), long_text(30), "notepad.exe", "Notepad")
    assert not r.cleaned and "down" in r.cleanup_error


# ------------------------------------------------------------ strength setting and guard fallback (task A3)

def test_light_is_the_default_strength():
    assert core.DEFAULT_CONFIG["cleanup_strength"] == "light"
    for value, want in [(None, "light"), ("", "light"), ("light", "light"), ("standard", "standard"),
                        (" Standard ", "standard"), ("STANDARD", "standard"), ("banana", "light")]:
        assert core.clean_strength(value) == want


def test_an_unset_strength_means_light_for_the_guard_and_the_prompt(monkeypatch):
    raw = "so um I like you know really want to go to the beach this weekend you know"
    cleaned = "I really want to go to the beach this weekend."
    bodies = []

    def fake_post(url, **kw):
        bodies.append(kw["json"])
        return FakeResp(cleaned)

    monkeypatch.setattr(core.requests, "post", fake_post)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    cfg = {k: v for k, v in dict(core.DEFAULT_CONFIG, api_key="k").items() if k != "cleanup_strength"}
    r = core.process_text(cfg, raw, "notepad.exe", "Notepad")
    assert not r.cleaned and r.cleanup_error and r.fidelity_fallback   # Light: the fillers had to stay
    assert core.STRENGTH_TEXT["light"] in bodies[0]["messages"][0]["content"]


def test_a_rejected_cleanup_falls_back_to_the_spoken_words_with_capitals(monkeypatch):
    raw = "hello there new paragraph " + long_text(40)
    r = run_pipeline(monkeypatch, raw, "Short summary.")
    assert r.fidelity_fallback and not r.cleaned and r.raw == raw
    assert r.text.startswith("Hello there\n\nSo yesterday")   # new paragraph applied, the sentence starts are capitals
    assert r.text.replace("\n", " ").lower().split() == raw.replace(" new paragraph", "").lower().split()


def test_a_network_error_is_not_a_fidelity_fallback(monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(core.requests, "post", boom)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    r = core.process_text(dict(core.DEFAULT_CONFIG, api_key="k"), long_text(30), "notepad.exe", "Notepad")
    assert not r.fidelity_fallback and r.text == long_text(30)


def test_an_accepted_or_skipped_cleanup_is_not_a_fallback(monkeypatch):
    raw = long_text(60)
    assert not run_pipeline(monkeypatch, raw, punctuate(raw)).fidelity_fallback
    assert not run_pipeline(monkeypatch, raw, "x", cleanup=False).fidelity_fallback
