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


def app_guard(row, cleaned, strength):
    """The app's own guard verdict (vox_core.Verdict) on one answer, called as process_text calls it: the row's
    dictionary terms (its people and dictionary, as bench_cleanup.row_config sets them) are critical words and its
    "wrong => right" pairs are applied first."""
    cfg = {"people": list(row.get("terms") or []), "dictionary": list(row.get("dictionary") or [])}
    return core.fidelity_check(row["raw"], cleaned, strength, "", core.dictionary_terms(cfg), core.replacements(cfg))


def score(row, cleaned, strength, verdict=None):
    """All the quality metrics of one answer. `terms` is None when the row names no term to keep. `guard` is the app's
    current guard (app_guard) unless `verdict` gives it."""
    keep = row.get("must_keep_terms") or []
    raw = row["raw"]
    if verdict is None:
        verdict = app_guard(row, cleaned, strength)
    return {"recall": recall(raw, cleaned), "added": added_rate(raw, cleaned), "ratio": length_ratio(raw, cleaned),
            "guard": accepted(verdict),
            "terms": term_hits(cleaned, keep) if keep else None, "structure": structure_only(raw, cleaned)}


_GOT_NAMES = ("missing", "run", "ins", "free", "fixes", "moved")


def guard_detail(raw, cleaned, strength, terms=(), repl=None):
    """(verdict, counts) of the app's guard on one answer. counts: what the guard counted before its last check, read
    from the guard's own local variables (no copy of its rules): {"words": the spoken words it requires, "missing",
    "run", "ins" (inserted words), "free" (free insertions: a, the, to...), "fixes", "moved", "limits": {the same names:
    the most allowed}}; None when it decided earlier (empty, preamble, too long, a dropped critical word, numbers...)."""
    import sys
    seen = {}

    def tracer(frame, event, arg):
        if frame.f_code is not core.fidelity_check.__code__:
            return None

        def local(frame, event, arg):
            if event == "return":
                seen.update({k: frame.f_locals.get(k) for k in ("got", "most", "n")})
            return local
        return local
    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        verdict = core.fidelity_check(raw, cleaned, strength, "", terms, repl)
    finally:
        sys.settrace(old)
    got, most = seen.get("got"), seen.get("most")
    if not (isinstance(got, tuple) and isinstance(most, tuple) and len(got) == len(most) == len(_GOT_NAMES)):
        return verdict, None
    counts = dict(zip(_GOT_NAMES, got), words=seen.get("n"))
    counts["limits"] = dict(zip(_GOT_NAMES, most))
    return verdict, counts


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


_SPELLING = {"okay": "ok", "cannot": "can not", "won't": "will not", "can't": "can not", "shan't": "shall not",
             "i'm": "i am"}
_SUFFIXES = (("n't", "not"), ("'re", "are"), ("'ve", "have"), ("'ll", "will"))


def _expand(w):
    """One word in Whisper normaliser spelling: common contractions spelled out ("don't" = "do not", "we're" = "we
    are"), "okay" = "ok". 's and 'd stay (is, has or a possessive; had or would)."""
    if w in _SPELLING:
        return _SPELLING[w].split()
    for end, full in _SUFFIXES:
        if w.endswith(end) and len(w) > len(end):
            return [w[:-len(end)], full]
    return [w]


def norm_words(text):
    """Whisper-style normalised words for WER: lowercase, punctuation and case dropped, common contractions spelled out
    ("don't" = "do not", "won't" = "will not"), other apostrophes removed ("it's" = "its"), "okay" = "ok", "e-mail" =
    "email", pure noises (um, uh, hmm) dropped, spoken numbers as digits ("twenty five" = "25"), money and percent as
    words ("$25" = "25 dollars" = "twenty five dollar"). Frozen v1 word rules (tools/bench/legacy.py). Not covered: money
    with cents spoken out ("$25.50" against "twenty five dollars fifty cents" is 3 errors)."""
    t = re.sub(r"([$₹€£])\s?([0-9][0-9,.]*)", lambda m: m.group(2) + " " + _CURRENCY[m.group(1)], text or "")
    t = re.sub(r"(?i)\be-mail", "email", t.replace("%", " percent"))
    toks = [_PLURAL.get(x, x).replace("'", "") for w in legacy.word_tokens(t) for x in _expand(w)]
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


def _inner_mark(s, i, cur):
    """True when s[i] is part of a word, not a punctuation mark: an apostrophe, dot or @ between two word characters
    ("don't", "12.50", "example.com", "a@b"), or a colon or comma between two digits ("3:30", "1,000")."""
    ch = s[i]
    if not cur or i + 1 >= len(s) or not _is_word_char(s[i + 1]):
        return False
    if ch in "'.@":
        return True
    return ch in ":," and cur[-1].isdigit() and s[i + 1].isdigit()


def format_tokens(text):
    """Words with their case kept, and every punctuation mark or symbol as a token of its own; whitespace and line
    breaks do not count. A curly apostrophe inside a word is a straight one; a decimal point, a time's colon, a
    thousands comma and the dots of an address stay inside their word (_inner_mark)."""
    s = (text or "").replace("’", "'")
    out, cur = [], []
    for i, ch in enumerate(s):
        if _is_word_char(ch) or _inner_mark(s, i, cur):
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
    """Formatted WER: case and punctuation count (each mark is a token), so "hi priya" against "Hi, Priya." is 4 errors
    in 4 tokens (two capitals, two marks)."""
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


_CUE_WORDS = frozenset(w for c in CUES for w in c.split())


def _last_cue(words):
    """The index where the last self-correction cue starts in a word list, or None."""
    starts = [i for c in CUES for i in range(len(words)) if words[i:i + len(c.split())] == c.split()]
    return max(starts) if starts else None


def self_correction_ok(raw, cleaned, intended):
    """For a dictation with a self-correction cue whose intended text drops words spoken before the cue: True when those
    retracted words (and the cue) are gone from the cleaned text and at least 90% of the intended words are there. None
    when it does not apply (no cue, no intended text, no word before the cue retracted: "I actually like, you know, the
    plan" only drops fillers after the cue)."""
    words = norm_words(raw)
    cue = _last_cue(words)
    if intended is None or cue is None:
        return None
    r, i, c = Counter(words), Counter(norm_words(intended)), Counter(norm_words(cleaned))
    before = set(words[:cue])
    retracted = {t: n for t, n in (r - i).items() if t in before or t in _CUE_WORDS}
    if not any(t in before and t not in _CUE_WORDS for t in retracted):
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


def score_pasted(row, cleaned, accepted, strength, fallback, skipped=False):
    """What gets pasted under one guard (the cleaned text when the guard accepts it, else fallback(raw); always
    fallback(raw) when `skipped`: the app does not send a phrase under cleanup_min_words) scored against the row's typed
    references when it has them (ref_intended: the text wanted). Counts are kept as [errors, total] or [tp, fp, fn] so
    the summary can add them up. A bare EMPTY answer (filler-only input) pastes nothing."""
    raw, intended, keep = row["raw"], row.get("ref_intended"), row.get("must_keep_terms") or []
    pasted = cleaned if accepted and not skipped else fallback(raw)
    if pasted.strip() == "EMPTY":
        pasted = ""
    out = {"accepted": bool(accepted), "skipped": bool(skipped),
           "over_edit": list(over_edit_counts(raw, pasted, intended, strength)),
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


def summarize_pasted(results, guard, key="pasted"):
    """The pasted-text numbers of one run under one guard: micro averages (all errors / all words) for WER, formatted WER
    and over-edits, F1 from the summed counts, and rates for the rest. None where no row has the number. key "rules"
    with guard None: the rules layer alone on every row (what is pasted with no AI cleanup)."""
    s = [r["score"][key] if guard is None else r["score"][key][guard] for r in results if r["score"]]
    corr = [x["self_correction"] for x in s if x["self_correction"] is not None]
    terms = [x["terms"] for x in s if x["terms"] is not None]
    sent = [x["accepted"] for x in s if not x.get("skipped")]
    return {"rows": len(results), "errors": len(results) - len(s), "skipped": len(s) - len(sent),
            "guard_pass": _mean(sent) if sent else None,
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


def row_values(results, guard, metric, key="pasted"):
    """One value per row, in row order, for paired comparisons (None for a failed row): "fwer" and "over_edit" are the
    row's [errors, words] under the guard (added up across rows: the micro rate of the table), "ms" its time. key
    "rules" with guard None: the rules layer's numbers."""
    out = []
    for r in results:
        if not r["score"]:
            out.append(None)
        elif metric == "ms":
            out.append(r["ms"])
        else:
            v = (r["score"][key] if guard is None else r["score"][key][guard]).get(metric)
            out.append(None if v is None else list(v))
    return out


def micro(pairs):
    """All errors / all words of [errors, words] pairs: the statistic of the table's WER, formatted WER and over-edits."""
    return _rate(sum(e for e, _ in pairs), sum(n for _, n in pairs))


BUCKETS = ((1, 3), (4, 7), (8, 15), (16, None))   # spoken words per row, for the "skip the AI below N words" setting


def bucket_of(words):
    for lo, hi in BUCKETS:
        if hi is None or words <= hi:
            return f"{lo}+" if hi is None else f"{lo}-{hi}"


def by_word_count(results, guards):
    """Per bucket of spoken words: rows, and formatted WER and over-edits (micro) of the rules layer alone and of the
    guarded AI answer under each guard (as if every row were sent: no minimum). Decides cleanup_min_words: a bucket where
    the AI does not beat the rules is not worth a request."""
    out = {}
    for r in results:
        s = r["score"]
        if not s:
            continue
        b = out.setdefault(bucket_of(s["words"]), {"rows": 0, "rules": {"fwer": [], "over_edit": []},
                                                    **{g: {"fwer": [], "over_edit": []} for g in guards}})
        b["rows"] += 1
        for name, x in [("rules", s["rules"])] + [(g, s["guarded"][g]) for g in guards]:
            for m in ("fwer", "over_edit"):
                if x.get(m) is not None:
                    b[name][m].append(x[m])
    order = [bucket_of(lo) for lo, _ in BUCKETS]
    return {k: {"rows": out[k]["rows"], **{name: {m: micro(v) if v else None for m, v in d.items()}
                                           for name, d in out[k].items() if name != "rows"}}
            for k in order if k in out}


def real_pair_confusion(results, guard):
    """The guard on the run's own answers, labelled automatically: an answer is bad when it over-edits (a word added,
    swapped or lost that the typed intended text does not allow) or answers the dictation. Same fields as
    guard_confusion. A strict label: read the rejected and accepted rows' reasons before moving a threshold."""
    rows = [r["score"] for r in results if r["score"]]
    tp = fn = fp = tn = 0
    for s in rows:
        bad = s["answer"]["over_edit"][0] > 0 or s["answer"]["answered"]
        ok = s["verdicts"][guard]["ok"]
        tp += bad and not ok
        fn += bad and ok
        fp += not bad and not ok
        tn += ok and not bad
    return {"rows": len(rows), "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "false_accept": fn / (fn + tn) if fn + tn else None, "accuracy": (tp + tn) / len(rows) if rows else None}


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

def paired_bootstrap(a, b, reps=2000, seed=0, stat=None):
    """Δ = stat(b) - stat(a) over the rows that have a value in both runs, with a paired bootstrap 95% interval: the rows
    are resampled together and Δ recomputed with the same statistic (2.5th and 97.5th percentile of `reps` resamples; a
    fixed seed, so a rerun prints the same). stat: the mean by default; `micro` for [errors, words] rows, so Δ and its
    interval are on the very number the table shows (Bisani and Ney 2004); statistics.median for times. None without
    rows."""
    stat = stat or _mean
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if not pairs:
        return None
    n, rng = len(pairs), random.Random(seed)
    xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
    deltas = []
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        deltas.append(stat([ys[k] for k in idx]) - stat([xs[k] for k in idx]))
    deltas.sort()
    return {"n": n, "delta": stat(ys) - stat(xs), "lo": deltas[int(0.025 * reps)], "hi": deltas[min(reps - 1, int(0.975 * reps))]}
