"""Pure metrics of the cleanup benchmark (tools/bench_cleanup.py). No network, no files.

They read the words the way the fidelity guard does (vox_core.word_tokens: case, punctuation and bullet markers do not
count; spoken numbers, symbols and commands are matched like the guard matches them), so a metric and the guard never
disagree about what a word is."""
import math
import re
import statistics

import vox_core as core


def recall(raw, cleaned):
    """The share (0..1) of the spoken words that are still in the cleaned text."""
    return core.word_recall(raw, cleaned)


def added_rate(raw, cleaned):
    """The share (0..1) of the cleaned text's words that were not spoken."""
    r, c = core._compare_tokens(raw, cleaned)
    return 1 - core._matched(c, r) / len(c) if c else 0.0


def length_ratio(raw, cleaned):
    """Words in the cleaned text divided by words spoken; 1.0 when nothing was spoken."""
    r, c = core._compare_tokens(raw, cleaned)
    return len(c) / len(r) if r else 1.0


def term_hits(cleaned, terms):
    """The share (0..1) of the terms that appear in the cleaned text with exactly this spelling and case, as whole
    words; 1.0 without terms."""
    if not terms:
        return 1.0
    return sum(bool(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", cleaned)) for t in terms) / len(terms)


def structure_only(raw, cleaned):
    """True when the cleaned text is the spoken words in the same order and nothing else: only punctuation, case,
    whitespace, bullet markers and pure noises (um, uh: dropped or kept) differ."""
    r, c = core._compare_tokens(raw, cleaned)
    return core._drop_fillers(r, False) == core._drop_fillers(c, False)


def percentile(values, p):
    """Nearest-rank percentile; 0 for no values."""
    if not values:
        return 0
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def score(row, cleaned, strength):
    """All the quality metrics of one answer. `terms` is None when the row names no term to keep."""
    keep = row.get("must_keep_terms") or []
    raw = row["raw"]
    return {"recall": recall(raw, cleaned), "added": added_rate(raw, cleaned), "ratio": length_ratio(raw, cleaned),
            "guard": core.looks_valid(raw, cleaned, strength), "terms": term_hits(cleaned, keep) if keep else None,
            "structure": structure_only(raw, cleaned)}


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def summarize(results):
    """One model's numbers from the run's results ({"ms", "error", "score"} each). Rows that failed count as errors and
    stay out of every other number."""
    ok = [r for r in results if r["score"]]
    s = [r["score"] for r in ok]
    ms = [r["ms"] for r in ok]
    terms = [x["terms"] for x in s if x["terms"] is not None]
    return {"rows": len(results), "errors": len(results) - len(ok),
            "median_ms": statistics.median(ms) if ms else 0, "p95_ms": percentile(ms, 95),
            "recall": _mean([x["recall"] for x in s]), "added_rate": _mean([x["added"] for x in s]),
            "length_ratio": _mean([x["ratio"] for x in s]), "guard_pass": _mean([x["guard"] for x in s]),
            "term_accuracy": _mean(terms) if terms else None, "structure_ok": _mean([x["structure"] for x in s])}
