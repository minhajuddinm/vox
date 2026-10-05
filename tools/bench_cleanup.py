"""Benchmark of the cleanup step: runs a model over transcripts and measures what it keeps, what gets pasted and how
long it takes.

    python tools/bench_cleanup.py --provider groq --model openai/gpt-oss-20b --strength light
    python tools/bench_cleanup.py --compare openai/gpt-oss-20b,openai/gpt-oss-120b --strength light
    python tools/bench_cleanup.py --compare-prompt v1,v3 --compare-guard v1,v2 --strength light
    python tools/bench_cleanup.py --guard-only --compare-guard v1,v2      (offline: the guards on labelled pairs only)
(run them with the repository's venv Python, .venv\\Scripts\\python: it has the app's packages)

What it cleans: your recorded clips (bench_record.py, transcribed by bench_stt.py) when they have transcripts, else the
synthetic text corpus tools/bench/corpus.jsonl; --corpus FILE or --clips chooses. With clips, what gets pasted is scored
against the text you typed for each clip (formatted WER, punctuation and case F1, over-edits, self-corrections).

Prompts and guards: "v1" is the frozen copy in tools/bench/legacy.py (Vox before guard v2 and prompt v3); any other name
(v2, v3, current) is the app's own code as it is now (vox_core.system_prompt through vox_core.cleanup, and the guard
called exactly as the app calls it: vox_core.fidelity_check with the row's dictionary terms and replacements). Prompt
variants are separate requests, interleaved row by row so the time of day favours none; guard variants are only
scoring, so they cost nothing. "What gets pasted" follows the app: a phrase under your "skip AI cleanup below" words is
not cleaned, and a rejected or skipped answer is replaced by the rules layer for the row's style and this strength. Each
comparison with the first column prints the difference with a paired bootstrap 95% interval on the same number the
table shows, and a short decision summary ends the run.

Cost: every answer is cached in %APPDATA%\\Vox\\bench\\cleanup-cache.jsonl by the exact request (server, model, system
prompt, transcript, max_tokens), so a repeated or interrupted run, or a re-scoring, sends nothing again. Requests are
paced for free tiers (--pause seconds, and --tpm tokens a minute from the answers' token counts); a rate limit (429) is
waited out, and after 3 rows in a row that still get one (a daily limit) the run stops and saves what it has. Ctrl+C
also saves. The results so far are saved after every row.

It uses the real cleanup call of the Windows app (About you, dictionary and style taken from each row, never from your
own settings) and your normal Vox settings for the server and key. The key is never printed or saved, and a key is only
sent to the server it is set for. Not run in CI: it costs requests, so you run it yourself. Results (the numbers and each
answer, with its token usage) are saved to %APPDATA%\\Vox\\bench\\ and never uploaded. Metrics: tools/bench_metrics.py.
Design: documentation/specs/p9a-cleanup-keeps-my-words.md.
"""
import argparse
import contextlib
import hashlib
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "windows"))
sys.path.insert(0, HERE)

import bench_clips as clips       # noqa: E402
import bench_metrics as metrics   # noqa: E402
import providers                  # noqa: E402
import vox_core as core           # noqa: E402
from bench import legacy          # noqa: E402

CORPUS = os.path.join(HERE, "bench", "corpus.jsonl")
GUARD_SET = os.path.join(HERE, "bench", "guard_set.jsonl")
FROZEN = "v1"   # the one prompt/guard name that means the frozen copy; every other name is the app's current code
STOP_AFTER_429 = 3   # rows in a row that still get a rate limit after the waits: a daily limit, stop the run
DEFAULT_TPM = 6000   # tokens a minute: under Groq's free tier for gpt-oss-20b (8,000; check your own limits)


def load_corpus(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def row_config(cfg, row, strength):
    """The settings for one corpus row: the row's About you, dictionary and style, never the user's own
    (not their learned cleanup rules either: those hold personal names and would skew the comparison)."""
    return dict(cfg, user_context=row["about"], people=list(row["terms"]), dictionary=[], app_styles={},
                my_cleanup_rules="", default_style=row["style"], cleanup_strength=strength)


def clip_rows(folder, stt_label=None):
    """(rows, the speech setting used) from the recorded clips that have their texts typed and a cached transcript for
    `stt_label` (default: clips.best_stt_label, the setting covering the most clips). Each row's dictionary is every
    clip's terms (clips.all_terms); the clip's own terms are the ones it must keep. A clip transcribed as nothing is
    left out (the app pastes nothing then)."""
    typed = [r for r in clips.load_manifest(folder) if r.get("ref_verbatim")]
    if stt_label is None:
        stt_label = clips.best_stt_label(folder, typed)
    terms = clips.all_terms(typed)
    rows = []
    for r in typed:
        text = (clips.load_stt(folder, r["id"]).get(stt_label) or {}).get("text") if stt_label else None
        if text:
            rows.append({"id": r["id"], "raw": text, "style": "neutral", "terms": terms, "about": "",
                         "must_keep_terms": list(r.get("terms", [])), "kind": r.get("kind", ""),
                         "ref_verbatim": r["ref_verbatim"], "ref_intended": r.get("ref_intended") or r["ref_verbatim"]})
    return rows, stt_label


def run(rows, cleanup, strength, pause=0, sleep=time.sleep, clock=time.perf_counter, progress=None):
    """Cleans every row with cleanup(row) -> text and scores it. A failed call is recorded, not raised, so one bad answer
    does not end a long run. `pause` seconds are waited between calls (free tiers limit requests per minute)."""
    out = []
    for i, row in enumerate(rows):
        t0, error, cleaned = clock(), "", ""
        try:
            cleaned = cleanup(row)
        except Exception as e:   # a provider error, a malformed answer: all the same to the benchmark
            error = str(e)
        ms = round((clock() - t0) * 1000)
        out.append({"id": row["id"], "ms": ms, "error": error, "cleaned": cleaned,
                    "score": None if error else metrics.score(row, cleaned, strength)})
        if progress:
            progress(i + 1, len(rows), out[-1])
        if pause and i + 1 < len(rows):
            sleep(pause)
    return out


def call_waiting(fn, sleep=time.sleep, say=None):
    """fn(), but a rate limit (ApiError 429) is waited out and fn called again, up to vox_core.RATE_LIMIT_TRIES times
    (Retry-After seconds, else RATE_LIMIT_WAIT, at most RATE_LIMIT_MAX_WAIT), as the app does for long recordings."""
    for attempt in range(core.RATE_LIMIT_TRIES + 1):
        try:
            return fn()
        except core.ApiError as e:
            if e.code != 429 or attempt == core.RATE_LIMIT_TRIES:
                raise
            wait = min(core.RATE_LIMIT_MAX_WAIT, core.RATE_LIMIT_WAIT if e.retry_after is None else e.retry_after)
            if say:
                say(f"  rate limited (429), waiting {wait:.0f} s, then trying again")
            sleep(wait)


def _daily_limit(text):
    t = text.lower()
    return "per day" in t or "tpd" in t or "rpd" in t


def run_variants(rows, variants, score, pause=0, sleep=time.sleep, clock=time.perf_counter, progress=None,
                 usage=core.last_usage, served=None, on_row=None, state=None, say=None):
    """Cleans every row once per variant ({label: cleanup(row) -> text}) and scores each answer with score(row, text).
    The variants take turns row by row, in alternating order (A B, B A, ...), so a slow minute of the server is shared.
    A rate limit is waited out (not timed); any other failure is recorded, not raised. Returns {label: [result per row]}
    with result = {"id", "raw", "ref_intended", "ms", "cached", "error", "cleaned", "usage", "score"}.

    served() -> {"ms", "cached"} or None: how the last answer was served (Sender: the request's own time, or the cache
    and the time it took when it was sent). on_row(out) runs after every row (a partial save). The run stops early, with
    the rows done by every variant, after STOP_AFTER_429 rows in a row that still got a rate limit (or one that names a
    daily limit) and on Ctrl+C; state["stopped"] then says why."""
    out = {label: [] for label in variants}
    labels, calls, limited = list(variants), 0, 0
    state = {} if state is None else state
    try:
        for i, row in enumerate(rows):
            row_limited = False
            for label in (labels if i % 2 == 0 else labels[::-1]):
                if calls and pause:
                    sleep(pause)
                calls += 1
                t0, error, cleaned, used, how = [clock()], "", "", None, None

                def once(label=label):
                    core._llm_local.usage = None
                    t0[0] = clock()   # the time of the last try only: a rate-limit wait is not the model's latency
                    return variants[label](row)
                try:
                    cleaned = call_waiting(once, sleep, say)
                    used = usage()
                    how = served() if served else None
                except Exception as e:   # a provider error, a malformed answer: all the same to the benchmark
                    error = str(e)
                    if isinstance(e, core.ApiError) and e.code == 429:
                        row_limited = True
                        if _daily_limit(error):
                            limited = STOP_AFTER_429 - 1
                ms = how["ms"] if how else round((clock() - t0[0]) * 1000)
                res = {"id": row["id"], "raw": row["raw"], "ms": ms, "cached": bool(how and how["cached"]),
                       "error": error, "cleaned": cleaned, "usage": used,
                       "score": None if error else score(row, cleaned)}
                if "ref_intended" in row:
                    res["ref_intended"] = row["ref_intended"]
                out[label].append(res)
                if progress:
                    progress(i + 1, len(rows), label, res)
            limited = limited + 1 if row_limited else 0
            if on_row:
                on_row(out)
            if limited >= STOP_AFTER_429:
                state["stopped"] = "rate limit"
                break
    except KeyboardInterrupt:
        state["stopped"] = "interrupted"
    n = min(len(v) for v in out.values()) if out else 0   # a row only some variants answered is left out
    return {label: v[:n] for label, v in out.items()}


class Sender:
    """Stands in for vox_core.chat_reply during a run (install it with patched()): an answer already in the cache is
    returned without a request; a real request first waits so requests keep `pause` seconds apart and the tokens stay
    under `tpm` a minute (estimated from the last answer's usage), then is timed and its answer cached. The cache key
    is the exact request: server address, model, temperature, max_tokens and messages (system prompt and transcript).
    Errors are never cached. The key never enters the cache (it is in the headers, not the request body)."""

    def __init__(self, path, pause=0.0, tpm=0, sleep=time.sleep, clock=time.perf_counter, real=None):
        self.path, self.pause, self.tpm, self.sleep, self.clock = path, pause, tpm, sleep, clock
        self.real = real or core.chat_reply
        self.cache, self.sent, self.hits, self.last, self._ready_at = {}, 0, 0, None, None
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        self.cache[e["key"]] = e
                    except (ValueError, KeyError, TypeError):   # a line cut off by a crash: skipped
                        continue

    @staticmethod
    def key(cfg, body):
        base = providers.role_settings(cfg, "llm")[0]
        return hashlib.sha256(json.dumps([base, body], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def __call__(self, cfg, body, timeout=60, retry_timeouts=True):
        k = self.key(cfg, body)
        hit = self.cache.get(k) if self.path else None
        if hit:
            self.hits += 1
            core._llm_local.usage = hit.get("usage")
            self.last = {"ms": hit.get("ms", 0), "cached": True}
            return hit["text"], hit.get("finish", "")
        if self._ready_at is not None:
            wait = self._ready_at - self.clock()
            if wait > 0:
                self.sleep(wait)
        t0 = self.clock()
        try:
            text, finish = self.real(cfg, body, timeout, retry_timeouts)
        finally:
            self.sent += 1
            u = core.last_usage() or {}
            tokens = (u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)
            self._ready_at = self.clock() + max(self.pause, 60.0 * tokens / self.tpm if self.tpm else 0)
        ms = round((self.clock() - t0) * 1000)
        self.last = {"ms": ms, "cached": False}
        if self.path:
            entry = {"key": k, "text": text, "finish": finish, "usage": core.last_usage(), "ms": ms,
                     "when": datetime.now().isoformat(timespec="seconds")}
            self.cache[k] = entry
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return text, finish

    def served(self):
        last, self.last = self.last, None
        return last

    @contextlib.contextmanager
    def patched(self):
        old = core.chat_reply
        core.chat_reply = self
        try:
            yield self
        finally:
            core.chat_reply = old


def redact(e, key):
    """The same error with the key masked in its text. An ApiError keeps its status and Retry-After (a 429 is waited
    out on them)."""
    text = str(e).replace(key, "***") if key else str(e)
    if isinstance(e, core.ApiError):
        return core.ApiError(e.code, text, e.retry_after)
    try:
        return type(e)(text)
    except Exception:   # an error type that needs other arguments
        return RuntimeError(text)


def _pct(x, digits=0):
    return "-" if x is None else f"{x * 100:.{digits}f}%"


def _num(x, digits=0):
    return "-" if x is None else f"{x:.{digits}f}"


COLUMNS = (
    ("latency median", lambda s: f"{s['median_ms'] / 1000:.2f} s"),
    ("latency p95", lambda s: f"{s['p95_ms'] / 1000:.2f} s"),
    ("word recall", lambda s: _pct(s["recall"], 1)),
    ("added words", lambda s: _pct(s["added_rate"], 1)),
    ("length ratio", lambda s: f"{s['length_ratio']:.2f}"),
    ("guard pass (current)", lambda s: _pct(s["guard_pass"])),
    ("term accuracy", lambda s: _pct(s["term_accuracy"])),
    ("format only", lambda s: _pct(s["structure_ok"])),
    ("prompt tokens", lambda s: _num(s.get("prompt_tokens"))),
    ("cached share", lambda s: _pct(s.get("cached_share"))),
    ("reasoning tok", lambda s: _num(s.get("reasoning_tokens"))),
    ("errors", lambda s: f"{s['errors']}/{s['rows']}"),
)

PASTED_COLUMNS = (
    ("guard pass", lambda s: _pct(s["guard_pass"])),
    ("formatted WER", lambda s: _pct(s["fwer"], 1)),
    ("WER", lambda s: _pct(s["wer"], 1)),
    ("punctuation F1", lambda s: _pct(s["punct_f1"])),
    ("case F1", lambda s: _pct(s["case_f1"])),
    ("term accuracy", lambda s: _pct(s["term_accuracy"])),
    ("over-edits", lambda s: _pct(s["over_edit"], 1)),
    ("self-corrections", lambda s: "-" if s["self_correction"] is None
     else f"{s['self_correction'] * 100:.0f}% of {s['self_correction_rows']}"),
    ("answered/obeyed", lambda s: _pct(s["answered"])),
)


def render_table(summaries, columns=COLUMNS):
    """A text table, one column per label ({label: summary}), one line per metric."""
    width = max([len(label) for label in summaries] + [8]) + 2
    name_w = max(17, max(len(n) for n, _ in columns) + 1)
    lines = ["".ljust(name_w) + "".join(label.rjust(width) for label in summaries)]
    for name, fmt in columns:
        lines.append(name.ljust(name_w) + "".join(fmt(s).rjust(width) for s in summaries.values()))
    return "\n".join(lines)


def provider_settings(cfg, provider, role="llm"):
    """The settings with the chosen provider as the server of `role` (llm: cleanup, stt: speech). The main key goes only
    to its own server: a key set for another address is dropped, so the provider then has no key (the caller says so)."""
    preset = next(p for p in providers.PRESETS if p["id"] == provider)
    base = preset["base_url"].rstrip("/")
    cfg = dict(cfg, relay_proxy=False)   # the relay would ignore the provider
    if providers.role_settings(cfg, role)[0] != base:
        cfg[f"{role}_api_key"] = ""
    cfg[f"{role}_base_url"] = base
    return cfg


def server_problem(cfg, role, name):
    """Why requests for `role` cannot be sent with these settings ('' when they can): a refused address, or no key for a
    public server (the key of another server is never used)."""
    base, key, _ = providers.role_settings(cfg, role)
    problem = core.endpoint_error(cfg if providers.uses_relay(cfg) else {"base_url": base})
    if not problem and not key and not core.is_private_host(urlparse(base).hostname):
        problem = (f"No API key for {name or base}: set it in Vox (Settings, AI server) first. "
                   "The key of another server is never sent here.")
    return problem


def _names(text, default):
    names = [x.strip() for x in (text or "").split(",") if x.strip()]
    return list(dict.fromkeys(names)) or [default]


def _check_variants(names, what):
    """Two names that both mean the app's current code would be the same thing twice."""
    current = [n for n in names if n != FROZEN]
    if len(current) > 1:
        return (f"--compare-{what}: only {FROZEN} is a frozen copy; {', '.join(current)} would all be the app's current "
                f"{what}. Use for example {FROZEN},{current[0]}.")
    return ""


def guard_fn(name):
    """The guard of the labelled pairs (they have no dictionary): the frozen v1 or the app's looks_valid."""
    return legacy.looks_valid if name == FROZEN else core.looks_valid


def guard_verdict(name, row, cleaned, strength):
    """({"ok", "reason"}, counts) of guard `name` on one answer. The current guard is called exactly as the app's
    process_text calls it: vox_core.fidelity_check with the row's dictionary terms (critical words) and replacements
    (row_config); counts are its insertion and missing-word counts against its limits (metrics.guard_detail; None when
    it decided before counting). v1 is the frozen looks_valid, which gives no reason."""
    if name == FROZEN:
        ok = bool(legacy.looks_valid(row["raw"], cleaned, strength))
        return {"ok": ok, "reason": "ok" if ok else "rejected"}, None
    rcfg = row_config({}, row, strength)
    v, counts = metrics.guard_detail(row["raw"], cleaned, strength, core.dictionary_terms(rcfg), core.replacements(rcfg))
    return {"ok": metrics.accepted(v), "reason": str(getattr(v, "reason", ""))}, counts


def guard_report(guards, rows):
    """{guard: {strength: confusion on the labelled pairs (all rows), and on the held-out ones}}."""
    held = [r for r in rows if r.get("split") == "heldout"]
    return {g: {s: dict(metrics.guard_confusion(rows, guard_fn(g), s),
                        heldout=metrics.guard_confusion(held, guard_fn(g), s)) for s in ("light", "standard")}
            for g in guards}


def _confusion_line(c):
    return (f"bad caught {c['tp']}/{c['tp'] + c['fn']} (recall {_pct(c['recall'])}), precision {_pct(c['precision'])}, "
            f"good kept {c['tn']}/{c['tn'] + c['fp']}, bad among accepted {_pct(c['false_accept'], 1)}, "
            f"accuracy {_pct(c['accuracy'])}")


def render_guard_report(report):
    lines = ["Guards on the labelled pairs (tools/bench/guard_set.jsonl; positive = a bad cleanup rejected):"]
    for g, by in report.items():
        for s, c in by.items():
            lines.append(f"  guard {g:8} {s:9} {_confusion_line(c)} (held-out {_pct(c['heldout']['accuracy'])})")
    return "\n".join(lines)


def _same_prompt():
    """True when the app's prompt is still byte-for-byte the frozen v1 prompt (before prompt v3 is merged)."""
    try:
        args = ("neutral", ["Priya", "Ledgerly"], "Slack", "I am a developer.", "light", "")
        return core.system_prompt(*args) == legacy.system_prompt(*args)
    except Exception:   # a changed signature: certainly not the same prompt
        return False


def _same_guard(rows):
    """True when the app's guard gives the frozen v1 verdict on every labelled pair (before guard v2 is merged)."""
    return all(metrics.accepted(core.looks_valid(r["raw"], core.sanitize(r["cleaned"]), s))
               == legacy.looks_valid(r["raw"], core.sanitize(r["cleaned"]), s) for r in rows for s in ("light", "standard"))


STATS = {"fwer": metrics.micro, "over_edit": metrics.micro, "ms": statistics.median}


def compare(columns, rows_by_column, reps=2000):
    """Each column against the first: paired bootstrap Δ of formatted WER and over-edits (micro: all errors / all words,
    as the table) and of the median time. {label: {metric: CI}}."""
    labels = list(columns)
    base = labels[0]
    out = {}
    for label in labels[1:]:
        out[label] = {m: metrics.paired_bootstrap(rows_by_column[base][m], rows_by_column[label][m], reps, stat=STATS[m])
                      for m in ("fwer", "over_edit", "ms")}
    return base, out


def render_compare(base, comparisons):
    def ci(c, scale, unit, digits):
        if not c:
            return "-"
        verdict = "lower" if c["hi"] < 0 else "higher" if c["lo"] > 0 else "tie"
        return (f"{c['delta'] * scale:+.{digits}f}{unit} [{c['lo'] * scale:+.{digits}f}, {c['hi'] * scale:+.{digits}f}] "
                f"{verdict}")
    lines = [f"Against {base} (difference, paired bootstrap 95% interval; a default changes only when the interval "
             "excludes 0 and over-edits do not rise):"]
    for label, c in comparisons.items():
        lines.append(f"  {label}: formatted WER {ci(c['fwer'], 100, ' pts', 1)}; over-edits {ci(c['over_edit'], 100, ' pts', 1)}; "
                     f"median time {ci(c['ms'], 1, ' ms', 0)}")
    return "\n".join(lines)


def meets_rule(c):
    """W3 7.5: a change wins only when the formatted-WER interval lies below 0 and over-edits do not rise. None when
    there is no formatted WER (no typed texts)."""
    if not c or not c.get("fwer"):
        return None
    oe = c.get("over_edit")
    return c["fwer"]["hi"] < 0 and (oe is None or oe["delta"] <= 0)


def _reason_kind(reason):
    head = reason.split(" ")[0]
    return head if head in metrics._GOT_NAMES else reason   # "missing 3 > 1" is a "missing"


def reason_counts(results, guard):
    """How often each rejection reason of a guard came up, most frequent first."""
    verdicts = [r["score"]["verdicts"][guard] for r in results if r["score"]]
    return dict(Counter(_reason_kind(v["reason"]) for v in verdicts if not v["ok"]).most_common())


def insertion_summary(results):
    """The current guard's insertion counts on the run's answers (the rows it counted): how many insert words, and per
    such row [id, inserted words, budget, spoken words the budget came from], split into within and over the budget."""
    counted = [(r["id"], r["score"]["guard_counts"]) for r in results if r["score"] and r["score"].get("guard_counts")]
    if not counted:
        return None
    rows = [[i, c["ins"], c["limits"]["ins"], c["words"]] for i, c in counted if c["ins"]]
    return {"counted": len(counted), "with_insertions": len(rows),
            "within": [x for x in rows if x[1] <= x[2]], "over": [x for x in rows if x[1] > x[2]]}


def _ins_rows(rows, most=5):
    text = ", ".join(f"{i} {n}/{lim} ({w} words)" for i, n, lim, w in rows[:most])
    return (text + (f" and {len(rows) - most} more" if len(rows) > most else "")) or "none"


def decision_summary(report, args):
    """A few lines for the tuning round: the prompt and guard rule verdicts, the guards on these answers, the
    rejection reasons, the insertion budget and the minimum words."""
    lines = ["\nDecision summary (W3 7.5: change a default only if the interval excludes 0 and over-edits do not rise):"]
    comp = report.get("compare") or {}
    for label, c in comp.items():
        if label == "against":
            continue
        verdict = meets_rule(c)
        lines.append(f"  {label} vs {comp['against']}: " + ("no typed texts, no verdict" if verdict is None else
                     "meets the rule" if verdict else "does not meet the rule"))
    for lbl, pairs in (report.get("real_pairs") or {}).items():
        for g, c in pairs.items():
            lines.append(f"  guard {g} on {lbl}'s answers (bad = over-edit or answered): {_confusion_line(c)}")
    for lbl, by in (report.get("reasons") or {}).items():
        for g, reasons in by.items():
            if reasons:
                lines.append(f"  rejections by guard {g} on {lbl}: " + ", ".join(f"{k} {n}" for k, n in reasons.items()))
    for lbl, ins in (report.get("insertions") or {}).items():
        if ins:
            lines.append(f"  insertions ({lbl}, current guard, inserted/budget): {ins['with_insertions']} of {ins['counted']} "
                         f"counted answers insert words; within the budget: {_ins_rows(ins['within'])}; over it: "
                         f"{_ins_rows(ins['over'])}")
    for lbl, by in (report.get("by_words") or {}).items():
        parts = []
        for b, v in by.items():
            ai = [f"{g} {_pct(v[g]['fwer'], 1)}" for g in report["guards"]]
            parts.append(f"{b} words ({v['rows']}): rules {_pct(v['rules']['fwer'], 1)}, AI " + ", ".join(ai))
        lines.append(f"  formatted WER by length ({lbl}; skip AI below {report['min_words']} words now): " + "; ".join(parts))
    if report.get("stopped"):
        lines.append(f"  The run stopped early ({report['stopped']}): numbers are on the rows done so far.")
    return "\n".join(lines)


def main(argv=None, call=core.cleanup, legacy_call=legacy.cleanup):
    """Returns 0 when the run finished, 1 when every request failed, 2 when it could not start. `call` is the app's
    cleanup call and `legacy_call` the frozen v1 one, (cfg, raw, style, app_label) -> text; tests pass fake ones."""
    clips.safe_console()
    ap = argparse.ArgumentParser(description="Benchmark the cleanup step of Vox over recorded clips or a text corpus.")
    ap.add_argument("--provider", choices=[p["id"] for p in providers.PRESETS if p["base_url"]],
                    help="server preset (default: the one set in Vox, relay included)")
    ap.add_argument("--model", help="cleanup model (default: the one set in Vox)")
    ap.add_argument("--compare", help="comma-separated models to run on the same rows")
    ap.add_argument("--compare-prompt", help=f"comma-separated prompts: {FROZEN} (frozen) and the app's current one "
                                             "(v3 or current), e.g. v1,v3")
    ap.add_argument("--compare-guard", help=f"comma-separated guards: {FROZEN} (frozen) and the app's current one "
                                            "(v2 or current), e.g. v1,v2")
    ap.add_argument("--guard-only", action="store_true", help="only score the guards on the labelled pairs (no requests)")
    ap.add_argument("--strength", choices=("light", "standard"), default="light")
    ap.add_argument("--corpus", help="a JSONL corpus of transcripts (default: your clips if transcribed, else "
                                     "tools/bench/corpus.jsonl)")
    ap.add_argument("--clips", action="store_true", help="use your recorded clips (fails when none is transcribed)")
    ap.add_argument("--folder", help="the clips folder (default: %%APPDATA%%\\Vox\\bench\\clips; a public set from "
                                      "bench_public.py: %%APPDATA%%\\Vox\\bench\\public\\<source>)")
    ap.add_argument("--stt", help="which cached transcript of the clips (the label bench_stt.py prints; default: the one "
                                  "covering the most clips)")
    ap.add_argument("--out", help="where to save the JSON (default: %%APPDATA%%\\Vox\\bench\\bench-DATE.json)")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between requests (default 2)")
    ap.add_argument("--tpm", type=int, default=DEFAULT_TPM,
                    help=f"tokens a minute to stay under (default {DEFAULT_TPM}, for free tiers; 0 = no limit)")
    ap.add_argument("--no-cache", action="store_true", help="send every request again (the cache is not read or written)")
    args = ap.parse_args(argv)

    prompts, guards = _names(args.compare_prompt, "current"), _names(args.compare_guard, "current")
    problem = _check_variants(prompts, "prompt") or _check_variants(guards, "guard")
    if problem:
        print(problem, file=sys.stderr)
        return 2
    report = {"when": datetime.now().isoformat(timespec="seconds"), "strength": args.strength,
              "prompts": prompts, "guards": guards, "models": {}}
    if args.compare_guard or args.guard_only:
        labelled = load_corpus(GUARD_SET)
        report["guard_set"] = guard_report(guards, labelled)
        print(render_guard_report(report["guard_set"]))
        if len(guards) > 1 and _same_guard(labelled):
            print("Note: the app's guard still gives the v1 verdicts, so the guard columns measure the same guard.",
                  file=sys.stderr)
    out = args.out or os.path.join(core.data_dir(), "bench", datetime.now().strftime("bench-%Y%m%d-%H%M%S.json"))
    if args.guard_only:
        _save(out, report)
        return 0

    cfg = core.load_config()
    if args.provider:
        cfg = provider_settings(cfg, args.provider)
    base, key, default_model = providers.role_settings(cfg, "llm")
    problem = server_problem(cfg, "llm", args.provider)
    if problem:
        print(problem, file=sys.stderr)
        return 2

    if args.corpus:
        rows, source = load_corpus(args.corpus), os.path.basename(args.corpus)
    else:
        folder = args.folder or clips.clips_dir()
        rows, stt_label = clip_rows(folder, args.stt)
        typed = sum(1 for r in clips.load_manifest(folder) if r.get("ref_verbatim"))
        source = f"your clips, transcript: {stt_label}"
        if not rows and (args.clips or args.stt):
            print("No transcribed clips: record with tools/bench_record.py, then run tools/bench_stt.py.", file=sys.stderr)
            return 2
        if rows and len(rows) < typed:
            print(f"Note: {typed - len(rows)} of {typed} typed clips have no transcript for '{stt_label}' (or an empty "
                  "one) and are left out; --stt picks another setting.", file=sys.stderr)
        if not rows:
            rows, source = load_corpus(CORPUS), os.path.basename(CORPUS)
        report["stt"] = stt_label if rows and "ref_intended" in rows[0] else None
    models = [m.strip() for m in args.compare.split(",") if m.strip()] if args.compare else [args.model or default_model]
    min_words = core.clean_min_words(cfg.get("cleanup_min_words", 4))
    report["min_words"] = min_words
    print(f"{urlparse(base).hostname}, strength {args.strength}, {len(rows)} rows ({source}), models: {', '.join(models)}, "
          f"prompts: {', '.join(prompts)}, guards: {', '.join(guards)}", file=sys.stderr)
    print(f"Up to {len(rows) * len(models) * len(prompts)} requests, paced to {args.pause:g} s apart"
          + (f" and {args.tpm} tokens a minute" if args.tpm else "") + "; answers already cached are not sent again. "
          "A free tier also has a daily token limit: when it is reached the run stops, and the same command continues "
          "from the cache later.", file=sys.stderr)
    if len(prompts) > 1 and _same_prompt():
        print("Note: the app's prompt is still the v1 prompt, so the prompt columns measure the same prompt.", file=sys.stderr)
    report.update(provider=urlparse(base).hostname, corpus=source)

    def make(model, prompt):
        fn, mcfg = (legacy_call if prompt == FROZEN else call), dict(cfg, llm_model=model)

        def cleanup(row):
            try:
                return fn(row_config(mcfg, row, args.strength), row["raw"], row["style"], "")
            except Exception as e:
                raise redact(e, key) from None   # an error text never carries the key
        return cleanup

    def label(model, prompt):
        if len(prompts) == 1:
            return model
        return f"prompt {prompt}" if len(models) == 1 else f"{model} prompt {prompt}"
    variants = {label(m, p): make(m, p) for m in models for p in prompts}
    meta = {label(m, p): {"model": m, "prompt": p} for m in models for p in prompts}

    def score(row, cleaned):
        raw, intended = row["raw"], row.get("ref_intended")
        words = len(raw.split())
        short = not core.needs_cleanup(raw, row["style"], True, min_words)   # the app pastes the rules layer then

        def fallback(text):
            return core.fallback_text(text, row["style"], args.strength)   # as process_text: the row's style, strength
        s = metrics.score(row, cleaned, args.strength)
        answer = "" if cleaned.strip() == "EMPTY" else cleaned
        s.update(words=words, short=short, answered=metrics.answered_or_obeyed(raw, answer, intended),
                 answer={"over_edit": list(metrics.over_edit_counts(raw, answer, intended, args.strength)),
                         "answered": metrics.answered_or_obeyed(raw, answer, intended)},
                 verdicts={}, guard_counts=None, pasted={}, guarded={},
                 rules=metrics.score_pasted(row, cleaned, False, args.strength, fallback))
        for g in guards:
            v, counts = guard_verdict(g, row, cleaned, args.strength)
            s["verdicts"][g] = v
            if counts is not None:
                s["guard_counts"] = counts
            s["pasted"][g] = metrics.score_pasted(row, cleaned, v["ok"], args.strength, fallback, skipped=short)
            full = metrics.score_pasted(row, cleaned, v["ok"], args.strength, fallback) if short else s["pasted"][g]
            s["guarded"][g] = {m: full.get(m) for m in ("fwer", "over_edit")}
        return s

    def summarise(results):
        report["models"] = {}
        columns, per_row = {}, {}
        report.update(by_words={}, real_pairs={}, reasons={}, insertions={})
        for lbl, res in results.items():
            summary = dict(metrics.summarize(res), **metrics.summarize_usage(res))
            summary["cached_answers"] = sum(1 for r in res if r.get("cached"))
            pasted = {g: metrics.summarize_pasted(res, g) for g in guards}
            if args.strength == "light":   # Light keeps a false start by design: the one reference cannot score it
                for p in pasted.values():
                    p["self_correction"], p["self_correction_rows"] = None, 0
            report["models"][lbl] = dict(meta[lbl], summary=summary, pasted=pasted, rows=res)
            report["by_words"][lbl] = metrics.by_word_count(res, guards)
            report["real_pairs"][lbl] = {g: metrics.real_pair_confusion(res, g) for g in guards}
            report["reasons"][lbl] = {g: reason_counts(res, g) for g in guards}
            report["insertions"][lbl] = insertion_summary(res)
            for g in guards:
                col = lbl if len(guards) == 1 else f"{lbl} | guard {g}"
                columns[col] = pasted[g]
                per_row[col] = {m: metrics.row_values(res, g, m) for m in ("fwer", "over_edit", "ms")}
        first = next(iter(results.values()), [])
        rules = metrics.summarize_pasted(first, None, "rules") if first else None
        if rules:
            rules["guard_pass"] = None   # no answer is judged: the rules layer is pasted on every row
            if args.strength == "light":
                rules["self_correction"], rules["self_correction_rows"] = None, 0
        report["rules_only"] = rules
        return columns, per_row

    def partial(results):
        _save(out, dict(report, partial=True, models={lbl: dict(meta[lbl], rows=res) for lbl, res in results.items()}),
              quiet=True)

    sender = Sender(None if args.no_cache else os.path.join(core.data_dir(), "bench", "cleanup-cache.jsonl"),
                    args.pause, args.tpm)
    state = {}
    say = lambda t: print(t, file=sys.stderr)   # noqa: E731
    with sender.patched():
        results = run_variants(rows, variants, score, 0, served=sender.served, on_row=partial, state=state, say=say,
                               progress=lambda i, n, lbl, r: say(
                                   f"  {lbl} [{i}/{n}] {r['id']}: "
                                   + (r['error'] or str(r['ms']) + " ms" + (" (cached)" if r["cached"] else ""))))
    if state.get("stopped") == "rate limit":
        say(f"Stopped: the server still refused (429) after the waits on {STOP_AFTER_429} rows in a row, most likely the "
            "daily token limit of a free tier. The answers so far are cached: run the same command again later "
            "(tomorrow) and it continues where it stopped, for free.")
    elif state.get("stopped") == "interrupted":
        say("Stopped (Ctrl+C). The answers so far are cached: the same command continues where it stopped.")
    report["stopped"] = state.get("stopped")
    report["requests"] = {"sent": sender.sent, "from_cache": sender.hits}
    columns, per_row = summarise(results)
    print(render_table({m: v["summary"] for m, v in report["models"].items()}))
    print("\nWhat gets pasted (the cleaned text when the guard accepts it, else the rules layer; phrases under "
          f"{min_words} words always get the rules layer, as in the app):")
    if report["rules_only"]:
        columns["rules only"] = report["rules_only"]
        per_row["rules only"] = {m: metrics.row_values(next(iter(results.values())), None, m, "rules")
                                 for m in ("fwer", "over_edit", "ms")}
        per_row["rules only"]["ms"] = [None] * len(per_row["rules only"]["ms"])
    print(render_table(columns, PASTED_COLUMNS))
    if len(columns) > 1:
        base_col, comparisons = compare(columns, per_row)
        report["compare"] = {"against": base_col, **comparisons}
        print("\n" + render_compare(base_col, comparisons))
    print(decision_summary(report, args))
    say(f"Requests sent: {sender.sent}, answered from the cache: {sender.hits}.")
    _save(out, report)
    if not report["models"] or not any(v["summary"]["rows"] for v in report["models"].values()):
        return 1
    return 1 if all(v["summary"]["errors"] == v["summary"]["rows"] for v in report["models"].values()) else 0


def _save(out, report, quiet=False):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    os.replace(out + ".tmp", out)
    if not quiet:
        print(f"Saved: {out}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
