"""Fidelity guard v2 (vox_core.fidelity_check; Fidelity.check on the phone): the labelled pairs in spec/golden.txt
(kind guard) scored against their human labels, speed, and how the pipeline uses the verdict (EMPTY, dictionary)."""
import os
import time

import vox_core as core

GOLDEN = os.path.join(os.path.dirname(__file__), "..", "spec", "golden.txt")


def labelled():
    """(strength, split, label g/b, accepted) for every guard row of the golden file that carries a label."""
    out = []
    with open(GOLDEN, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\r\n").split("\t")
            if p[0] == "guard" and p[9] != "-":
                _, split, label = p[9].split()
                out.append((p[1], split, label, p[7] != "reject"))
    return out


def test_the_labelled_set_is_all_there():
    rows = labelled()
    assert len(rows) == 2 * 189
    assert sum(1 for r in rows if r[1] == "heldout") == 2 * 30


def test_accuracy_per_strength_meets_the_target():
    """D1's target: at least 95% right on the held-out pairs and no bad cleanup accepted there; Light never accepts a
    bad cleanup at all. (The verdicts themselves are pinned by the golden rows; this scores them.)"""
    rows = labelled()
    for strength in ("light", "standard"):
        for split in ("main", "heldout"):
            rs = [r for r in rows if r[0] == strength and r[1] == split]
            right = sum(1 for r in rs if r[3] == (r[2] == "g"))
            assert right * 100 >= 95 * len(rs), (strength, split, right, len(rs))
        bad_accepted = [r for r in rows if r[0] == strength and r[2] == "b" and r[3]]
        held_bad = [r for r in bad_accepted if r[1] == "heldout"]
        assert not held_bad
        if strength == "light":
            assert not bad_accepted
        else:
            assert len(bad_accepted) <= 2   # FL08, FL10: a filler with a meaning (known limit)


def _long(words):
    base = ("so yesterday I went to the market and bought some apples and bananas then I came home and cooked dinner "
            "for the whole family we all sat down together and talked about the trip we are planning").split()
    return " ".join((base * (words // len(base) + 1))[:words])


def test_a_300_word_dictation_is_checked_in_a_few_milliseconds():
    raw = _long(300)
    cleaned = raw.capitalize().replace(" then ", ". Then ").replace(" we all ", ", we all ") + "."
    for strength in ("light", "standard"):
        assert core.fidelity_check(raw, cleaned, strength).ok
        t0 = time.perf_counter()
        for _ in range(5):
            core.fidelity_check(raw, cleaned, strength, "", ["Vox", "Groq"], {"vox": "Vox"})
        assert (time.perf_counter() - t0) / 5 < 0.1   # about 5 ms on a laptop; generous for a slow CI runner


def test_a_long_rewrite_stays_bounded():
    """The worst case of the banded alignment: a long text rewritten throughout (nothing in common at the ends)."""
    raw = _long(1500)
    rewrite = " ".join(w[::-1] for w in raw.split()[:900])
    t0 = time.perf_counter()
    assert not core.fidelity_check(raw, rewrite, "standard").ok
    assert time.perf_counter() - t0 < 5


def test_the_reason_never_holds_a_dictated_word():
    raw = "lets meet on thursday at the office near the river bank"
    v = core.fidelity_check(raw, "Let's meet at the office near the river bank.", "standard", "", ["Rivendell"])
    assert not v.ok and v.reason == "critical word dropped"
    for cleaned in ("Let's meet on Friday at the office near the river bank.", "Here is the text: " + raw, raw + " Sure!"):
        v = core.fidelity_check(raw, cleaned, "light")
        assert not v.ok and not set(v.reason.split()) & set(raw.split())


def test_lcs_pairs_binds_a_repeated_word_to_its_later_copy():
    assert core.lcs_pairs("the plan no wait the plan is".split(), "the plan is".split()) == [(4, 0), (5, 1), (6, 2)]
    assert core.lcs_pairs([], ["a"]) == [] and core.lcs_pairs(["a"], []) == []


# ------------------------------------------------------------ process_text


def _run(monkeypatch, raw, answer, **cfg):
    monkeypatch.setattr(core, "cleanup", lambda c, r, style, label: answer)
    return core.process_text(dict(core.DEFAULT_CONFIG, api_key="k", cleanup_min_words=1, **cfg), raw, "notepad.exe",
                             "Notepad")


def test_filler_only_speech_answered_with_empty_types_nothing(monkeypatch):
    r = _run(monkeypatch, "um uh hmm", "EMPTY")
    assert r.cleaned and r.text == "" and not r.fidelity_fallback and r.raw == "um uh hmm"
    r = _run(monkeypatch, "um like you know", "EMPTY", cleanup_strength="standard")
    assert r.cleaned and r.text == ""


def test_empty_for_real_words_falls_back_to_the_words(monkeypatch):
    r = _run(monkeypatch, "send the report to priya today", "EMPTY")
    assert not r.cleaned and r.fidelity_fallback and r.text == "Send the report to priya today"
    r = _run(monkeypatch, "um like you know", "EMPTY")   # Light: like and you know are words
    assert r.fidelity_fallback and r.text.lower().split() == ["um", "like", "you", "know"]


def test_the_dictionary_reaches_the_guard(monkeypatch):
    raw = "please turn on tailscale before the call starts today"
    cleaned = "Please turn on before the call starts today."
    assert _run(monkeypatch, raw, cleaned, cleanup_strength="standard").cleaned   # an ordinary word may go in Standard
    r = _run(monkeypatch, raw, cleaned, cleanup_strength="standard", people=["Tailscale"])
    assert r.fidelity_fallback   # a dictionary term may not
    r = _run(monkeypatch, "ask u v raj about the build", "Ask Yuvraj about the build.", dictionary=["u v raj => Yuvraj"])
    assert r.cleaned and r.text == "Ask Yuvraj about the build."


def test_a_self_correction_is_kept_in_standard_only(monkeypatch):
    raw = "lets meet on thursday no wait friday at noon"
    assert _run(monkeypatch, raw, "Let's meet on Friday at noon.", cleanup_strength="standard").cleaned
    assert _run(monkeypatch, raw, "Let's meet on Friday at noon.").fidelity_fallback
