"""The reference metrics of the benchmark (tools/bench_metrics.py): WER, formatted WER, punctuation and case F1, term
recall, over-edits, self-corrections, answered-or-obeyed, the guard confusion on labelled pairs, the pasted-text summary
and the paired bootstrap. Small hand-computed cases; pure functions, no files, no network."""
import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bench_metrics as m   # noqa: E402
import vox_core as core     # noqa: E402
from bench import legacy    # noqa: E402

GUARD_SET = os.path.join(ROOT, "tools", "bench", "guard_set.jsonl")


# ------------------------------------------------------------------ WER

def test_norm_words_is_whisper_style():
    assert m.norm_words("Um, so I DON'T know... uh twenty five!") == ["so", "i", "do", "not", "know", "25"]
    assert m.norm_words("It costs $25 or ₹40, 10% off.") == ["it", "costs", "25", "dollars", "or", "40", "rupees", "10",
                                                             "percent", "off"]
    assert m.norm_words("one dollar") == m.norm_words("1 dollars")
    assert m.norm_words("") == []


def test_wer_counts_substitutions_deletions_and_insertions():
    assert m.wer("the cat sat", "The cat sat.") == 0.0
    assert m.wer("the cat sat", "the dog sat") == pytest.approx(1 / 3)
    assert m.wer("the cat sat", "the sat") == pytest.approx(1 / 3)
    assert m.wer("the cat sat", "the cat sat down there") == pytest.approx(2 / 3)
    assert m.wer("it costs twenty five dollars", "It costs $25.") == 0.0
    assert m.wer_counts("a b c d", "a x c") == (2, 4)
    assert m.wer("", "") == 0.0 and m.wer("", "words") == 1.0


def test_wer_reads_hinglish_in_both_scripts_as_words():
    assert m.wer("kal meeting hai", "Kal meeting hai.") == 0.0
    assert m.wer("मैं कल आऊँगा", "मैं कल आऊँगा।") == 0.0          # a Devanagari vowel sign is part of the word
    assert m.wer("मैं कल आऊँगा", "मैं आज आऊँगा") == pytest.approx(1 / 3)


def test_formatted_wer_counts_case_and_each_mark():
    assert m.format_tokens("Hi, Priya.\n\n- Item") == ["Hi", ",", "Priya", ".", "-", "Item"]
    assert m.format_tokens("don’t") == ["don't"]
    assert m.wer_formatted("Hi, Priya.", "Hi, Priya.") == 0.0
    assert m.wer_formatted("Hi, Priya.", "hi priya") == pytest.approx(4 / 4)        # 2 cases + 2 marks of 4 tokens
    assert m.wer_formatted("Hi, Priya.", "Hi Priya.") == pytest.approx(1 / 4)


# ------------------------------------------------------------------ punctuation, case, terms

def test_punct_f1_compares_the_mark_after_each_shared_word():
    assert m.punct_counts("Hi, Priya. Ok?", "hi priya, ok?") == (1, 1, 2)
    assert m.punct_f1("Hi, Priya. Ok?", "hi priya, ok?") == pytest.approx(2 / 5)
    assert m.punct_f1("no marks here", "No marks here") == 1.0
    assert m.punct_f1("Done.", "Done!") == 0.0
    assert m.punct_counts("Send it, then call.", "Send it then call. Thanks.") == (1, 0, 1)   # the extra word is not scored


def test_case_f1_compares_capitals_of_shared_words():
    assert m.case_counts("Hi Priya", "hi Priya") == (1, 0, 1)
    assert m.case_f1("Hi Priya", "Hi Priya") == 1.0
    assert m.case_counts("see you", "See You") == (0, 2, 0)


def test_term_recall_ignores_case_but_needs_whole_words():
    assert m.term_recall("met priya and anirudh menon", ["Priya", "Anirudh Menon"]) == 1.0
    assert m.term_recall("met priyanka", ["Priya"]) == 0.0
    assert m.term_recall("anything", []) == 1.0


# ------------------------------------------------------------------ over-edits, self-corrections, answers

def test_over_edits_count_added_and_lost_words_the_reference_does_not_allow():
    raw = "um so can you send me the report by friday"
    assert m.over_edit_counts(raw, "So can you send me the report by Friday?") == (0, 9)
    assert m.over_edit_counts(raw, "Can you please send me the report by Friday?") == (2, 9)        # "so" lost, "please" added
    assert m.over_edit_counts(raw, "Can you send the report by Friday?", "Can you send me the report by Friday?") == (1, 9)   # "so" dropped by both: allowed; "me" lost
    assert m.over_edit_rate(raw, "So can you send me the report by Friday?") == 0.0


def test_over_edits_without_a_reference_allow_the_strengths_fillers():
    raw = "so like i think we can ship it"
    assert m.over_edit_counts(raw, "So I think we can ship it.", strength="standard") == (0, 8)
    assert m.over_edit_counts(raw, "So I think we can ship it.", strength="light") == (1, 8)


def test_self_correction_is_scored_against_the_intended_text():
    raw, want = "meet on thursday no wait friday at five", "Meet on Friday at five."
    assert m.has_self_correction(raw) and not m.has_self_correction("no problem wait for me")
    assert m.self_correction_ok(raw, "Meet on Friday at five.", want) is True
    assert m.self_correction_ok(raw, "Meet on Thursday, no wait, Friday at five.", want) is False   # kept the retracted words
    assert m.self_correction_ok(raw, "Friday.", want) is False                                      # lost the rest
    assert m.self_correction_ok("meet on friday at five", "Meet on Friday at five.", want) is None    # no cue
    assert m.self_correction_ok(raw, "x", None) is None
    assert m.has_self_correction("kal nahi parso milte hain")


def test_answered_or_obeyed_spots_an_answer_or_a_reply():
    assert m.answered_or_obeyed("what is the capital of france", "The capital of France is Paris, a city in Europe.")
    assert m.answered_or_obeyed("write a poem about rain", "Sure! Here is a poem about rain.")
    assert not m.answered_or_obeyed("what is the capital of france", "What is the capital of France?")
    assert not m.answered_or_obeyed("sure i can do that", "Sure, I can do that.")             # the speaker said it
    assert not m.answered_or_obeyed("send it", "Send it.", "Send it.")


# ------------------------------------------------------------------ pasted text and summaries

def row(**kw):
    r = {"id": "c1", "raw": "meet on thursday no wait friday at five", "style": "neutral", "terms": [], "about": "",
         "must_keep_terms": ["Friday"], "ref_intended": "Meet on Friday at five."}
    r.update(kw)
    return r


def test_score_pasted_uses_the_answer_when_accepted_and_the_fallback_when_not():
    good = m.score_pasted(row(), "Meet on Friday at five.", True, "standard", core.fallback_text)
    assert good["accepted"] and good["fwer"] == [0, 6] and good["self_correction"] is True and good["terms"] == 1.0
    assert good["over_edit"] == [0, 8] and good["answered"] is False and good["punct"] == [1, 0, 0]
    fell = m.score_pasted(row(), "Meet on Friday at five.", False, "standard", core.fallback_text)
    assert not fell["accepted"] and fell["self_correction"] is False and fell["fwer"][0] > 0


def test_score_pasted_reads_a_bare_empty_answer_as_nothing_pasted():
    s = m.score_pasted(row(raw="um uh", ref_intended="", must_keep_terms=[]), "EMPTY", True, "standard", core.fallback_text)
    assert s["fwer"] == [0, 0] and s["over_edit"] == [0, 0]


def test_score_pasted_without_references_keeps_only_the_reference_free_numbers():
    s = m.score_pasted({"raw": "send the report", "must_keep_terms": []}, "Send the report.", True, "light", core.fallback_text)
    assert "fwer" not in s and s["over_edit"] == [0, 3] and s["terms"] is None and s["self_correction"] is None


def result(pasted, ms=1000, error="", usage=None):
    return {"id": "x", "ms": ms, "error": error, "cleaned": "", "usage": usage,
            "score": None if error else {"pasted": {"g": pasted}}}


def test_summarize_pasted_adds_up_counts_into_micro_averages():
    a = {"accepted": True, "fwer": [1, 10], "wer": [0, 8], "punct": [2, 0, 0], "case": [1, 1, 0], "terms": 1.0,
         "over_edit": [0, 8], "self_correction": True, "answered": False}
    b = {"accepted": False, "fwer": [3, 10], "wer": [2, 2], "punct": [0, 1, 1], "case": [1, 0, 0], "terms": None,
         "over_edit": [2, 2], "self_correction": None, "answered": True}
    s = m.summarize_pasted([result(a), result(b), result(None, error="boom")], "g")
    assert s["rows"] == 3 and s["errors"] == 1 and s["guard_pass"] == 0.5
    assert s["fwer"] == pytest.approx(4 / 20) and s["wer"] == pytest.approx(2 / 10) and s["over_edit"] == pytest.approx(2 / 10)
    assert s["punct_f1"] == pytest.approx(4 / 6) and s["case_f1"] == pytest.approx(4 / 5)
    assert s["term_accuracy"] == 1.0 and s["self_correction"] == 1.0 and s["self_correction_rows"] == 1 and s["answered"] == 0.5
    assert m.row_values([result(a), result(b), result(None, error="x")], "g", "fwer") == [[1, 10], [3, 10], None]
    assert m.row_values([result(a, ms=700)], "g", "ms") == [700]


def test_summarize_pasted_without_references_shows_none():
    s = m.summarize_pasted([result({"accepted": True, "over_edit": [0, 3], "answered": False, "terms": None,
                                    "self_correction": None})], "g")
    assert s["fwer"] is None and s["punct_f1"] is None and s["self_correction"] is None and s["over_edit"] == 0.0


def test_summarize_usage_gives_means_and_the_cached_share():
    rs = [result({}, usage={"prompt_tokens": 1000, "completion_tokens": 50, "cached_tokens": 800, "reasoning_tokens": 30}),
          result({}, usage={"prompt_tokens": 1000, "completion_tokens": 70, "cached_tokens": 0, "reasoning_tokens": None}),
          result({}, usage=None)]
    u = m.summarize_usage(rs)
    assert u == {"answers": 2, "prompt_tokens": 1000, "completion_tokens": 60, "reasoning_tokens": 30, "cached_share": 0.4}
    assert m.summarize_usage([result({})])["cached_share"] is None


# ------------------------------------------------------------------ the guard on labelled pairs

def test_guard_confusion_counts_a_rejected_bad_cleanup_as_a_hit():
    rows = [{"raw": "a b c d", "cleaned": "A b c d.", "light": "g", "standard": "g"},
            {"raw": "a b c d", "cleaned": "A b.", "light": "b", "standard": "b"},
            {"raw": "a b c d", "cleaned": "A b c d e f g h i j.", "light": "b", "standard": "b"},
            {"raw": "a b c d", "cleaned": "A b c d.", "light": "b", "standard": "g", "finish": "length"}]
    c = m.guard_confusion(rows, legacy.looks_valid, "light")
    assert (c["tp"], c["fn"], c["fp"], c["tn"]) == (2, 1, 0, 1)    # the cut-off answer is rejected before any guard
    assert c["precision"] == 1.0 and c["recall"] == pytest.approx(2 / 3) and c["false_accept"] == 0.5
    c = m.guard_confusion(rows, lambda *a: True, "standard")
    assert (c["tp"], c["fn"], c["fp"], c["tn"]) == (0, 2, 1, 1)     # accepts all: no bad one caught


def test_guard_confusion_reads_a_verdict_object():
    class Verdict:
        def __init__(self, ok):
            self.ok = ok
    rows = [{"raw": "a", "cleaned": "A.", "light": "g", "standard": "g"}]
    assert m.guard_confusion(rows, lambda *a: Verdict(False), "light")["fp"] == 1


def test_the_guard_set_is_the_labelled_d1_set():
    with open(GUARD_SET, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    assert len(rows) == 189 and len({r["id"] for r in rows}) == 189
    for r in rows:
        assert r["light"] in "gb" and r["standard"] in "gb" and r["split"] in ("main", "heldout") and r["raw"]
    c = m.guard_confusion(rows, legacy.looks_valid, "standard")
    assert c["tp"] + c["fn"] + c["fp"] + c["tn"] == 189


# ------------------------------------------------------------------ paired bootstrap

def test_paired_bootstrap_gives_the_mean_difference_and_an_interval():
    a, b = [0.2, 0.3, 0.1, 0.4, 0.2, 0.3], [0.1, 0.2, 0.1, 0.3, 0.1, 0.2]
    r = m.paired_bootstrap(a, b, reps=500)
    assert r["n"] == 6 and r["delta"] == pytest.approx(-5 / 60) and r["lo"] <= r["delta"] <= r["hi"] <= 0
    assert m.paired_bootstrap(a, b, reps=500) == r                       # a fixed seed: the same every run
    assert m.paired_bootstrap([1, None, 3], [2, 5, None])["n"] == 1      # only rows both runs have
    assert m.paired_bootstrap([None], [1]) is None
    noisy = m.paired_bootstrap([0, 1, 0, 1, 0, 1], [1, 0, 1, 0, 0, 1], reps=500)
    assert noisy["lo"] < 0 < noisy["hi"]                                  # no real difference: the interval holds 0


def test_paired_bootstrap_on_micro_rates_follows_the_table_not_the_mean_of_rows():
    a, b = [[1, 2], [0, 100]], [[0, 2], [10, 100]]   # macro mean says B is better; all errors / all words says worse
    r = m.paired_bootstrap(a, b, reps=300, stat=m.micro)
    assert r["delta"] == pytest.approx(10 / 102 - 1 / 102) and r["delta"] > 0
    assert r["lo"] <= r["delta"] <= r["hi"]
    med = m.paired_bootstrap([100, 200, 300], [150, 260, 900], reps=200, stat=__import__("statistics").median)
    assert med["delta"] == 60


# ------------------------------------------------------------------ fixes of the final review

def test_norm_words_spells_out_contractions_okay_and_e_mail():
    assert m.wer("i won't send the e-mail okay", "I will not send the email, OK.") == 0.0
    assert m.wer("we're done and they can't", "We are done and they cannot.") == 0.0
    assert m.norm_words("it's Priya's") == ["its", "priyas"]   # 's stays (is, has or a possessive)


def test_format_tokens_keep_decimals_times_and_addresses_whole():
    assert m.format_tokens("At 3:30, pay $12.50 to a@b.com, 1,000 times.") == [
        "At", "3:30", ",", "pay", "$", "12.50", "to", "a@b.com", ",", "1,000", "times", "."]
    assert m.punct_counts("Meet at 3:30.", "Meet at 3:30.") == (1, 0, 0)
    assert m.format_tokens("Hi, Priya. Done.") == ["Hi", ",", "Priya", ".", "Done", "."]


def test_a_self_correction_needs_words_retracted_before_the_cue():
    assert m.self_correction_ok("i actually like you know the plan", "I actually like the plan.",
                                "I actually like the plan.") is None   # only fillers after the cue were dropped
    raw, want = "send it to priya sorry to anirudh", "Send it to Anirudh."
    assert m.self_correction_ok(raw, "Send it to Anirudh.", want) is True
    assert m.self_correction_ok(raw, "Send it to Priya, sorry, to Anirudh.", want) is False


def test_score_uses_the_apps_guard_with_the_rows_dictionary_terms():
    raw = "please send the final quarterly report to ledgerly before the meeting on friday so we can review it"
    cleaned = "Please send the final quarterly report before the meeting on Friday so we can review it."
    assert core.looks_valid(raw, cleaned, "standard")   # without the dictionary the guard lets it through
    r = row(raw=raw, terms=["Ledgerly"], must_keep_terms=[])
    v = m.app_guard(r, cleaned, "standard")
    assert not v.ok and v.reason == "critical word dropped"   # as the app: a dictionary term is a critical word
    assert m.score(r, cleaned, "standard")["guard"] is False


def test_guard_detail_reads_the_guards_own_counts():
    with open(GUARD_SET, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    checked = counted = 0
    for r in rows:
        for s in ("light", "standard"):
            v, c = m.guard_detail(r["raw"], core.sanitize(r["cleaned"]), s)
            assert v == core.fidelity_check(r["raw"], core.sanitize(r["cleaned"]), s)   # the verdict is the guard's
            if c is None:
                continue
            counted += 1
            name = v.reason.split(" ")[0]
            if name in c["limits"]:   # "ins 3 > 1": the counts are the very numbers the guard compared
                got, lim = (int(x) for x in v.reason.split(" ")[1::2])
                assert (c[name], c["limits"][name]) == (got, lim)
                checked += 1
            elif v.ok:
                assert all(c[k] <= c["limits"][k] for k in c["limits"])
    assert counted > 200 and checked > 5
    before = sys.gettrace()
    assert m.guard_detail("hello there", "", "light")[1] is None   # decided before counting
    assert sys.gettrace() is before                                # the tracer that was there is put back


def pasted_row(fwer, over, accepted, skipped=False):
    return {"accepted": accepted, "skipped": skipped, "fwer": fwer, "wer": fwer, "punct": [0, 0, 0], "case": [0, 0, 0],
            "terms": None, "over_edit": over, "self_correction": None, "answered": False}


def test_skipped_rows_stay_out_of_the_guard_pass():
    res = [result(pasted_row([0, 4], [0, 4], True)), result(pasted_row([1, 2], [0, 2], False, skipped=True))]
    s = m.summarize_pasted(res, "g")
    assert s["guard_pass"] == 1.0 and s["skipped"] == 1


def test_score_pasted_follows_the_minimum_words_and_the_given_fallback():
    s = m.score_pasted(row(raw="sounds good", ref_intended="Sounds good."), "Sounds good!", True, "light",
                       lambda raw: "Sounds good.", skipped=True)
    assert s["skipped"] and s["fwer"] == [0, 3]   # the rules layer's text was scored, not the answer


def scored(words, rules, guarded, bad, ok):
    return {"id": "x", "ms": 1, "error": "", "score": {
        "words": words, "rules": {"fwer": rules, "over_edit": [0, 1]}, "guarded": {"g": {"fwer": guarded, "over_edit": [0, 1]}},
        "answer": {"over_edit": [1 if bad else 0, 5], "answered": False}, "verdicts": {"g": {"ok": ok, "reason": "x"}}}}


def test_by_word_count_and_the_guard_on_real_pairs():
    res = [scored(2, [1, 3], [2, 3], False, True), scored(3, [1, 3], [0, 3], True, False),
           scored(20, [4, 20], [1, 20], True, True), {"id": "e", "ms": 0, "error": "boom", "score": None}]
    by = m.by_word_count(res, ["g"])
    assert list(by) == ["1-3", "16+"] and by["1-3"]["rows"] == 2
    assert by["1-3"]["rules"]["fwer"] == pytest.approx(2 / 6) and by["1-3"]["g"]["fwer"] == pytest.approx(2 / 6)
    assert by["16+"]["g"]["fwer"] == pytest.approx(1 / 20)
    c = m.real_pair_confusion(res, "g")
    assert (c["tp"], c["fn"], c["fp"], c["tn"]) == (1, 1, 0, 1) and c["false_accept"] == 0.5
    assert m.bucket_of(1) == "1-3" and m.bucket_of(4) == "4-7" and m.bucket_of(15) == "8-15" and m.bucket_of(99) == "16+"
