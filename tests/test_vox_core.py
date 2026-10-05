import vox_core as core


# ---------------------------------------------------------------- sanitize

def test_sanitize_strips_think_block():
    assert core.sanitize("<think>hmm\nplan</think>Hello there") == "Hello there"


def test_sanitize_strips_transcript_tags():
    assert core.sanitize("<transcript>Hi</transcript>") == "Hi"


def test_sanitize_strips_wrapping_quotes_only_when_single_pair():
    assert core.sanitize('"Hello"') == "Hello"
    assert core.sanitize('"a" and "b"') == '"a" and "b"'


def test_sanitize_handles_none_and_empty():
    assert core.sanitize(None) == ""
    assert core.sanitize("  ") == ""


# ------------------------------------------------------- apply_replacements

def test_replacement_is_case_insensitive_and_whole_word():
    assert core.apply_replacements("Say Wispr flow now", {"wispr flow": "Wispr Flow"}) == "Say Wispr Flow now"
    assert core.apply_replacements("catalog", {"cat": "dog"}) == "catalog"


def test_replacement_treats_regex_chars_literally():
    assert core.apply_replacements("a.b axb", {"a.b": "X"}) == "X axb"


def test_replacement_right_side_backslashes_are_literal():
    assert core.apply_replacements("path", {"path": r"C:\new\1"}) == r"C:\new\1"


def test_replacement_empty_map_is_noop():
    assert core.apply_replacements("same", {}) == "same"


# -------------------------------------------------------------- looks_valid

def test_looks_valid_accepts_normal_cleanup():
    assert core.looks_valid("um hello there", "Hello there.")


def test_looks_valid_rejects_empty():
    assert not core.looks_valid("hello", "")
    assert not core.looks_valid("hello", "   ")
    assert not core.looks_valid("hello", None)


def test_looks_valid_rejects_answer_sized_output():
    raw = "what is the capital of france"
    limit = int(len(raw) * 1.6) + 40   # the longest accepted answer; padding it with "!" keeps the words the same
    assert not core.looks_valid(raw, raw + "!" * (limit - len(raw) + 1))
    assert core.looks_valid(raw, raw + "!" * (limit - len(raw)))
    assert not core.looks_valid(raw, raw + " " + "x" * (limit - len(raw) - 1))   # an added word: padding (guard v2)


# ----------------------------------------------------------- whisper_prompt

def test_whisper_prompt_empty():
    assert core.whisper_prompt([]) == ""


def test_whisper_prompt_is_one_sentence():
    assert core.whisper_prompt(["Alice", "Vox"]) == "We talked about Alice and Vox."
    assert core.whisper_prompt(["Alice", "Vox"], ["Alice"]) == "Talked with Alice about Vox."


def test_whisper_prompt_keeps_whole_terms_within_its_token_budget():
    terms = ["w" * 100] * 10 + ["Term%d" % k for k in range(100)]
    out = core.whisper_prompt(terms)
    assert core.est_tokens(out) <= core.WHISPER_PROMPT_TOKENS and out.endswith(".")
    named = out[len("We talked about "):-1].replace(" and ", ", ").split(", ")
    assert all(t in terms for t in named) and len(named) <= core.WHISPER_PROMPT_TERMS


# -------------------------------------------------------- dictionary_terms

def test_dictionary_terms_people_and_plain_lines():
    cfg = {"people": [" Alice ", ""], "dictionary": ["Kubernetes", "  ", "# comment"]}
    assert core.dictionary_terms(cfg) == ["Alice", "Kubernetes"]


def test_dictionary_terms_uses_right_side_of_arrow_and_dedupes():
    cfg = {"people": ["Bob"], "dictionary": ["bobb => Bob", "x =>", "Bob"]}
    assert core.dictionary_terms(cfg) == ["Bob"]


def test_dictionary_terms_empty_config():
    assert core.dictionary_terms({}) == []


# ------------------------------------------------------------- replacements

def test_replacements_parses_arrow_lines_only():
    cfg = {"dictionary": ["wrong => Right", "Plain", "# a => b", " => empty", "k=>v"]}
    assert core.replacements(cfg) == {"wrong": "Right", "k": "v"}


def test_replacements_empty():
    assert core.replacements({}) == {}


# --------------------------------------------- is_silence_hallucination

def test_silence_phrases_detected_ignoring_case_and_punctuation():
    for t in ["Thank you.", "THANKS FOR WATCHING!", "you", "Bye!", " Thank you for watching. "]:
        assert core.is_silence_hallucination(t), t


def test_real_speech_not_flagged():
    assert not core.is_silence_hallucination("Thank you for the update on the budget")
    assert not core.is_silence_hallucination("")


# ---------------------------------------------------------------- style_for

def test_style_for_app_match_is_case_insensitive():
    cfg = {"app_styles": {"Outlook.exe": "formal"}, "default_style": "neutral"}
    assert core.style_for(cfg, "OUTLOOK.EXE") == "formal"


def test_style_for_falls_back_to_default():
    cfg = {"app_styles": {}, "default_style": "casual"}
    assert core.style_for(cfg, "unknown.exe") == "casual"
    assert core.style_for(cfg, None) == "casual"


def test_style_for_defaults_to_neutral_when_unset():
    assert core.style_for({}, "x.exe") == "neutral"


# ------------------------------------------------------------ cleanup gate

def test_clean_min_words_clamps_and_falls_back_to_four():
    assert [core.clean_min_words(v) for v in (1, "7", " 20 ", 0, -4, 99, "99999999999999999999")] == [1, 7, 20, 1, 1, 20, 20]
    assert [core.clean_min_words(v) for v in (None, "", "banana", "2.5", 4.5)] == [4, 4, 4, 4, 4]
    assert core.DEFAULT_CONFIG["cleanup_min_words"] == 4


def test_process_text_uses_the_cleanup_min_words_setting(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "cleanup", lambda cfg, raw, style, label: calls.append(raw) or raw.capitalize())
    base = {"cleanup": True, "default_style": "neutral"}
    assert core.process_text(base, "one two three", "x.exe", "x").cleaned is False    # default: 4 words
    assert core.process_text(base, "one two three four", "x.exe", "x").cleaned is True
    assert core.process_text(dict(base, cleanup_min_words=1), "one", "x.exe", "x").cleaned is True
    assert core.process_text(dict(base, cleanup_min_words="oops"), "one two three", "x.exe", "x").cleaned is False
    assert calls == ["one two three four", "one"]


# ---- a long recording whose streaming failed is not sent as one huge upload (R2-M9) ---------------------------------------

def test_a_very_long_recording_is_sent_in_pieces_under_the_upload_limit(monkeypatch):
    sizes = []

    def fake_transcribe(cfg, wav, context=""):
        sizes.append(len(wav))
        return "word"

    monkeypatch.setattr(core, "transcribe", fake_transcribe)
    loud = b"\x10\x27" * 12_500_000          # 25 MB of 16-bit audio, about 13 minutes
    res = core.process_detailed({"cleanup": False}, loud, "", "")
    assert len(sizes) > 1 and max(sizes) < core.MAX_UPLOAD_BYTES
    assert res.text.split() == ["word"] * len(sizes)


def test_a_short_recording_is_still_sent_whole(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "transcribe", lambda cfg, wav, context="": calls.append(context) or "hello there friend")
    core.process_detailed({"cleanup": False}, b"\x10\x27" * 32000, "", "")
    assert calls == [""]


def test_peak_level_is_the_loudest_sample_with_or_without_numpy(monkeypatch):
    """ENG-11: numpy reads a long recording in milliseconds; the result is the same as the plain loop's."""
    import array
    import builtins
    samples = array.array("h", [0, 5, -32768, 1200, 32767, -3])
    pcm = samples.tobytes() + b"\x01"   # an odd last byte is ignored
    assert core.peak_level(pcm) == 32768
    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name))
                        if name == "numpy" else real_import(name, *a, **k))
    assert core.peak_level(pcm) == 32768 and core.peak_level(b"") == 0
