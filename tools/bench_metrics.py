"""Pure metrics of the cleanup and speech benchmarks (tools/bench_cleanup.py, tools/bench_stt.py). No network, no files.

The first group reads the words the way the fidelity guard does (vox_core.word_tokens: case, punctuation and bullet
markers do not count; spoken numbers, symbols and commands are matched like the guard matches them), so a metric and the
guard never disagree about what a word is. The reference metrics further down (WER, punctuation and case F1, over-edit,
self-corrections) compare with the texts typed for a recorded clip; they normalise with the frozen v1 word rules
(tools/bench/legacy.py), so their numbers do not move when the app's guard changes."""
import math
import random
import re
import statistics
import unicodedata
from collections import Counter

import vox_core as core
from bench import legacy


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
            "guard": accepted(core.looks_valid(raw, cleaned, strength)),
            "terms": term_hits(cleaned, keep) if keep else None, "structure": structure_only(raw, cleaned)}


def accepted(verdict):
    """A guard's verdict as a bool: a plain bool, or an object with `.ok` (a guard that also gives its reason)."""
    return bool(getattr(verdict, "ok", verdict))


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


# ------------------------------------------------------------------ reference metrics (recorded clips)

_CURRENCY = {"$": "dollars", "₹": "rupees", "€": "euros", "£": "pounds"}
_PLURAL = {"dollar": "dollars", "rupee": "rupees", "euro": "euros", "pound": "pounds"}


def norm_words(text):
    """Whisper-style normalised words for WER: lowercase, punctuation and case dropped, apostrophes removed ("don't" =
    "dont"), pure noises (um, uh, hmm) dropped, spoken numbers as digits ("twenty five" = "25"), money and percent as
    words ("$25" = "25 dollars" = "twenty five dollar"). Frozen v1 word rules (tools/bench/legacy.py)."""
    t = re.sub(r"([$₹€£])\s?([0-9][0-9,.]*)", lambda m: m.group(2) + " " + _CURRENCY[m.group(1)], text or "")
    toks = [_PLURAL.get(w, w).replace("'", "") for w in legacy.word_tokens(t.replace("%", " percent"))]
    return legacy._merge_numbers([w for w in toks if w and w not in legacy.NOISES])


def _align(ref, hyp):
    """Levenshtein alignment of two token lists: (errors, [(i, j) of the tokens that match]). Substitution, deletion and
    insertion each cost 1; on a tie a match is preferred."""
    n, m = len(ref), len(hyp)
    d = [list(range(m + 1))] + [[i] + [0] * m for i in range(1, n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]), d[i - 1][j] + 1, d[i][j - 1] + 1)
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        if ref[i - 1] == hyp[j - 1] and d[i][j] == d[i - 1][j - 1]:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif d[i][j] == d[i - 1][j - 1] + 1:
            i, j = i - 1, j - 1
        elif d[i][j] == d[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    return d[n][m], pairs[::-1]


def _rate(errors, n):
    return errors / n if n else (0.0 if not errors else 1.0)


def wer_counts(ref, hyp):
    """(word errors, reference words) of hyp against ref after norm_words; summed over rows they give a micro WER."""
    r = norm_words(ref)
    return _align(r, norm_words(hyp))[0], len(r)


def wer(ref, hyp):
    """Word error rate of hyp against ref (0 = the same words; above 1 when hyp adds many). An empty reference gives 0 for
    an empty hyp, else 1."""
    return _rate(*wer_counts(ref, hyp))


def _is_word_char(ch):
    return unicodedata.category(ch)[0] in "LNM"   # letters, numbers and marks (Devanagari vowel signs), as the guard


def format_tokens(text):
    """Words with their case kept, and every punctuation mark or symbol as a token of its own; whitespace and line
    breaks do not count. A curly apostrophe inside a word is a straight one."""
    s = (text or "").replace("’", "'")
    out, cur = [], []
    for i, ch in enumerate(s):
        if _is_word_char(ch) or (ch == "'" and cur and i + 1 < len(s) and _is_word_char(s[i + 1])):
            cur.append(ch)
            continue
        if cur:
            out.append("".join(cur))
            cur = []
        if not ch.isspace():
            out.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def wer_formatted_counts(ref, hyp):
    r = format_tokens(ref)
    return _align(r, format_tokens(hyp))[0], len(r)


def wer_formatted(ref, hyp):
    """Formatted WER: case and punctuation count (each mark is a token), so "hi priya" against "Hi, Priya." is 3 errors
    in 4 tokens."""
    return _rate(*wer_formatted_counts(ref, hyp))


MARKS = ".,?!:;"


def _marked_words(text):
    """[(word, mark after it)]: the mark is the first of . , ? ! : ; between this word and the next ('' for none)."""
    out = []
    for t in format_tokens(text):
        if _is_word_char(t[0]):
            out.append([t, ""])
        elif t in MARKS and out and not out[-1][1]:
            out[-1][1] = t
    return out


def _shared_words(ref, hyp):
    r, h = _marked_words(ref), _marked_words(hyp)
    key = lambda w: w[0].lower().replace("'", "")   # noqa: E731
    return [(r[i], h[j]) for i, j in _align([key(w) for w in r], [key(w) for w in h])[1]]


def _f1(tp, fp, fn):
    return 1.0 if tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)


def punct_counts(ref, hyp):
    """(tp, fp, fn) of the punctuation after the words both texts share (word alignment): a mark is right when the same
    mark follows the same word; a different mark is one fp and one fn."""
    tp = fp = fn = 0
    for (_, a), (_, b) in _shared_words(ref, hyp):
        if a and a == b:
            tp += 1
        else:
            fp += bool(b)
            fn += bool(a)
    return tp, fp, fn


def punct_f1(ref, hyp):
    """Punctuation F1 over the shared words (1 when neither text has a mark)."""
    return _f1(*punct_counts(ref, hyp))


def case_counts(ref, hyp):
    """(tp, fp, fn) of capitals among the words both texts share: positive = the word starts with a capital letter."""
    tp = fp = fn = 0
    for (a, _), (b, _) in _shared_words(ref, hyp):
        a, b = a[0].isupper(), b[0].isupper()
        tp += a and b
        fp += b and not a
        fn += a and not b
    return tp, fp, fn


def case_f1(ref, hyp):
    return _f1(*case_counts(ref, hyp))


def term_recall(text, terms):
    """The share (0..1) of the terms found in text as whole words, case ignored (for speech-to-text); 1.0 without terms."""
    if not terms:
        return 1.0
    low = (text or "").lower()
    return sum(bool(re.search(r"(?<!\w)" + re.escape(t.lower()) + r"(?!\w)", low)) for t in terms) / len(terms)


def _added(raw_words, intended_words, cleaned_words):
    """Words of the cleaned text that were neither spoken nor intended (counted with repeats)."""
    cr, ci = Counter(raw_words), Counter(intended_words)
    return sum(max(0, n - max(cr[t], ci[t])) for t, n in Counter(cleaned_words).items())


def over_edit_counts(raw, cleaned, intended=None, strength="light"):
    """(edits not allowed, spoken words). Allowed is what the intended text also does (without one: dropping the fillers
    the strength lets go). Not allowed: a cleaned word that is neither spoken nor intended (added or swapped), and a
    spoken word the intended text keeps but the cleaned text lost. Order is not looked at; pure noises never count."""
    r = norm_words(raw)
    target = norm_words(intended) if intended is not None else legacy._drop_fillers(r, legacy.clean_strength(strength) == "standard")
    c = norm_words(cleaned)
    cr, ct, cc = Counter(r), Counter(target), Counter(c)
    lost = sum(max(0, min(n, ct[t]) - cc[t]) for t, n in cr.items())
    return _added(r, target, c) + lost, len(r)


def over_edit_rate(raw, cleaned, intended=None, strength="light"):
    return _rate(*over_edit_counts(raw, cleaned, intended, strength))


CUES = ("no wait", "wait no", "actually", "i mean", "scratch that", "sorry", "no no", "nahi", "matlab")


def has_self_correction(raw):
    """True when the spoken words hold a self-correction cue (no wait, actually, I mean, scratch that, sorry, nahi...)."""
    text = " " + " ".join(legacy.word_tokens(raw)) + " "
    return any(" " + c + " " in text for c in CUES)


def self_correction_ok(raw, cleaned, intended):
    """For a dictation with a self-correction cue whose intended text drops words: True when those retracted words are
    gone from the cleaned text and at least 90% of the intended words are there. None when it does not apply (no cue,
    no intended text, nothing retracted)."""
    if intended is None or not has_self_correction(raw):
        return None
    r, i, c = Counter(norm_words(raw)), Counter(norm_words(intended)), Counter(norm_words(cleaned))
    retracted = r - i
    if not retracted:
        return None
    kept = sum(min(n, c[t]) for t, n in i.items())
    return all(c[t] <= i[t] for t in retracted) and kept * 10 >= 9 * sum(i.values())


_ANSWER_START = re.compile(r"^\W*(sure|certainly|of course|absolutely|here's|here is|here are|i can|i'd be happy|"
                           r"i would be happy|as an ai|i'm sorry|i am sorry|i cannot|i can't)\b", re.I)


def answered_or_obeyed(raw, cleaned, intended=None):
    """True when the cleanup looks like it answered or followed the dictation instead of typing it: it opens like an
    assistant (Sure, Here is, I can...) where the speaker did not, or it added at least max(3, 30% of the spoken words)
    words that were neither spoken nor intended. A heuristic, read as a rate over many rows."""
    if _ANSWER_START.search(cleaned or "") and not _ANSWER_START.search(raw or "") \
            and not _ANSWER_START.search(intended or ""):
        return True
    r = norm_words(raw)
    return _added(r, norm_words(intended or ""), norm_words(cleaned)) >= max(3, 0.3 * len(r))


def score_pasted(row, cleaned, accepted, strength, fallback):
    """What gets pasted under one guard (the cleaned text when the guard accepts it, else fallback(raw)) scored against
    the row's typed references when it has them (ref_intended: the text wanted). Counts are kept as [errors, total] or
    [tp, fp, fn] so the summary can add them up. A bare EMPTY answer (filler-only input) pastes nothing."""
    raw, intended, keep = row["raw"], row.get("ref_intended"), row.get("must_keep_terms") or []
    pasted = cleaned if accepted else fallback(raw)
    if pasted.strip() == "EMPTY":
        pasted = ""
    out = {"accepted": bool(accepted), "over_edit": list(over_edit_counts(raw, pasted, intended, strength)),
           "answered": answered_or_obeyed(raw, pasted, intended), "terms": term_hits(pasted, keep) if keep else None,
           "self_correction": self_correction_ok(raw, pasted, intended)}
    if intended is not None:
        out.update(fwer=list(wer_formatted_counts(intended, pasted)), wer=list(wer_counts(intended, pasted)),
                   punct=list(punct_counts(intended, pasted)), case=list(case_counts(intended, pasted)))
    return out


def _micro(scores, key):
    have = [x[key] for x in scores if x.get(key) is not None]
    return _rate(sum(e for e, _ in have), sum(n for _, n in have)) if have else None


def _f1_sum(scores, key):
    have = [x[key] for x in scores if x.get(key) is not None]
    return _f1(*(sum(c) for c in zip(*have))) if have else None


def summarize_pasted(results, guard):
    """The pasted-text numbers of one run under one guard: micro averages (all errors / all words) for WER, formatted WER
    and over-edits, F1 from the summed counts, and rates for the rest. None where no row has the number."""
    s = [r["score"]["pasted"][guard] for r in results if r["score"]]
    corr = [x["self_correction"] for x in s if x["self_correction"] is not None]
    terms = [x["terms"] for x in s if x["terms"] is not None]
    return {"rows": len(results), "errors": len(results) - len(s),
            "guard_pass": _mean([x["accepted"] for x in s]) if s else None,
            "fwer": _micro(s, "fwer"), "wer": _micro(s, "wer"), "punct_f1": _f1_sum(s, "punct"),
            "case_f1": _f1_sum(s, "case"), "term_accuracy": _mean(terms) if terms else None,
            "over_edit": _micro(s, "over_edit"), "self_correction": _mean(corr) if corr else None,
            "self_correction_rows": len(corr), "answered": _mean([x["answered"] for x in s]) if s else None}


def summarize_usage(results):
    """Token use of a run from the answers' usage (vox_core.last_usage): mean prompt, completion and reasoning tokens,
    and the share of prompt tokens the provider served from its cache. None where the server sent no such count."""
    u = [r.get("usage") for r in results if r.get("usage")]

    def mean(key):
        v = [x[key] for x in u if x.get(key) is not None]
        return sum(v) / len(v) if v else None
    both = [(x["prompt_tokens"], x["cached_tokens"]) for x in u
            if x.get("prompt_tokens") is not None and x.get("cached_tokens") is not None]
    total = sum(p for p, _ in both)
    return {"answers": len(u), "prompt_tokens": mean("prompt_tokens"), "completion_tokens": mean("completion_tokens"),
            "reasoning_tokens": mean("reasoning_tokens"), "cached_share": sum(c for _, c in both) / total if total else None}


def row_values(results, guard, metric):
    """One number per row, in row order, for paired comparisons (None for a failed row): "fwer" and "over_edit" are the
    row's rates under the guard, "ms" its time."""
    out = []
    for r in results:
        if not r["score"]:
            out.append(None)
        elif metric == "ms":
            out.append(r["ms"])
        else:
            v = r["score"]["pasted"][guard].get(metric)
            out.append(None if v is None else _rate(*v))
    return out


# ------------------------------------------------------------------ the guard on labelled pairs

def guard_confusion(rows, accepts, strength):
    """A guard's confusion on labelled pairs (tools/bench/guard_set.jsonl: raw, cleaned, finish, and "g"ood or "b"ad per
    strength). accepts(raw, cleaned, strength) -> bool. Positive = a bad cleanup: precision is the share of rejections
    that were right, recall the share of bad cleanups caught, false_accept the share of accepted answers that were bad.
    An answer cut off at max_tokens (finish "length") never reaches the guard in the app, so it counts as rejected."""
    tp = fn = fp = tn = 0
    for r in rows:
        bad = r[strength] == "b"
        ok = r.get("finish", "stop") != "length" and accepted(accepts(r["raw"], core.sanitize(r["cleaned"]), strength))
        tp += bad and not ok
        fn += bad and ok
        fp += not bad and not ok
        tn += ok and not bad
    return {"rows": len(rows), "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "false_accept": fn / (fn + tn) if fn + tn else None, "accuracy": (tp + tn) / len(rows) if rows else None}


# ------------------------------------------------------------------ comparing two runs

def paired_bootstrap(a, b, reps=2000, seed=0):
    """Δ = mean(b - a) over the rows that have a number in both runs, with a paired bootstrap 95% interval (2.5th and
    97.5th percentile of `reps` resamples of those rows; a fixed seed, so a rerun prints the same). None without rows."""
    d = [y - x for x, y in zip(a, b) if x is not None and y is not None]
    if not d:
        return None
    n, rng = len(d), random.Random(seed)
    means = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return {"n": n, "delta": sum(d) / n, "lo": means[int(0.025 * reps)], "hi": means[min(reps - 1, int(0.975 * reps))]}
