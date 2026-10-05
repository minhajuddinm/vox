"""Benchmark of the cleanup step: runs a model over transcripts and measures what it keeps, what gets pasted and how
long it takes.

    python tools/bench_cleanup.py --provider groq --model openai/gpt-oss-20b --strength light
    python tools/bench_cleanup.py --compare openai/gpt-oss-20b,openai/gpt-oss-120b --strength light
    python tools/bench_cleanup.py --compare-prompt v1,v3 --compare-guard v1,v2 --strength light
    python tools/bench_cleanup.py --guard-only --compare-guard v1,v2      (offline: the guards on labelled pairs only)

What it cleans: your recorded clips (bench_record.py, transcribed by bench_stt.py) when they have transcripts, else the
synthetic text corpus tools/bench/corpus.jsonl; --corpus FILE or --clips chooses. With clips, what gets pasted is scored
against the text you typed for each clip (formatted WER, punctuation and case F1, over-edits, self-corrections).

Prompts and guards: "v1" is the frozen copy in tools/bench/legacy.py (Vox before guard v2 and prompt v3); any other name
(v2, v3, current) is the app's own code as it is now (vox_core.system_prompt through vox_core.cleanup, and
vox_core.looks_valid). Prompt variants are separate requests, interleaved row by row so the time of day favours none;
guard variants are only scoring, so they cost nothing. Each comparison with the first column prints the difference with
a paired bootstrap 95% interval.

It uses the real cleanup call of the Windows app (About you, dictionary and style taken from each row, never from your
own settings) and your normal Vox settings for the server and key. The key is never printed or saved, and a key is only
sent to the server it is set for. A rate limit (429) is waited out. Not run in CI: it costs requests, so you run it
yourself. Results (the numbers and each answer, with its token usage) are saved to %APPDATA%\\Vox\\bench\\ and never
uploaded. Metrics: tools/bench_metrics.py. Design: documentation/specs/p9a-cleanup-keeps-my-words.md.
"""
import argparse
import json
import os
import sys
import time
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
    `stt_label` (default: the newest transcript of any setting). Each row's dictionary is every clip's terms
    (clips.all_terms); the clip's own terms are the ones it must keep. A clip transcribed as nothing is left out (the
    app pastes nothing then)."""
    typed = [r for r in clips.load_manifest(folder) if r.get("ref_verbatim")]
    cache = {r["id"]: clips.load_stt(folder, r["id"]) for r in typed}
    if stt_label is None:
        entries = [(v.get("when", ""), k) for c in cache.values() for k, v in c.items()]
        stt_label = max(entries)[1] if entries else None
    terms = clips.all_terms(typed)
    rows = []
    for r in typed:
        text = (cache[r["id"]].get(stt_label) or {}).get("text") if stt_label else None
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
                say(f"  rate limited, waiting {wait:.0f} s")
            sleep(wait)


def run_variants(rows, variants, score, pause=0, sleep=time.sleep, clock=time.perf_counter, progress=None,
                 usage=core.last_usage):
    """Cleans every row once per variant ({label: cleanup(row) -> text}) and scores each answer with score(row, text).
    The variants take turns row by row, in alternating order (A B, B A, ...), so a slow minute of the server is shared.
    A rate limit is waited out (not timed); any other failure is recorded, not raised. Returns {label: [result per row]}
    with result = {"id", "ms", "error", "cleaned", "usage", "score"}."""
    out = {label: [] for label in variants}
    labels, calls = list(variants), 0
    for i, row in enumerate(rows):
        for label in (labels if i % 2 == 0 else labels[::-1]):
            if calls and pause:
                sleep(pause)
            calls += 1
            t0, error, cleaned, used = [clock()], "", "", None

            def once(label=label):
                core._llm_local.usage = None
                t0[0] = clock()   # the time of the last try only: a rate-limit wait is not the model's latency
                return variants[label](row)
            try:
                cleaned = call_waiting(once, sleep)
                used = usage()
            except Exception as e:   # a provider error, a malformed answer: all the same to the benchmark
                error = str(e)
            ms = round((clock() - t0[0]) * 1000)
            out[label].append({"id": row["id"], "ms": ms, "error": error, "cleaned": cleaned, "usage": used,
                               "score": None if error else score(row, cleaned)})
            if progress:
                progress(i + 1, len(rows), label, out[label][-1])
    return out


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
    ("guard pass", lambda s: _pct(s["guard_pass"])),
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
    lines = ["".ljust(17) + "".join(label.rjust(width) for label in summaries)]
    for name, fmt in columns:
        lines.append(name.ljust(17) + "".join(fmt(s).rjust(width) for s in summaries.values()))
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
    return legacy.looks_valid if name == FROZEN else core.looks_valid


def guard_report(guards, rows):
    """{guard: {strength: confusion on the labelled pairs (all rows), and on the held-out ones}}."""
    held = [r for r in rows if r.get("split") == "heldout"]
    return {g: {s: dict(metrics.guard_confusion(rows, guard_fn(g), s),
                        heldout=metrics.guard_confusion(held, guard_fn(g), s)) for s in ("light", "standard")}
            for g in guards}


def render_guard_report(report):
    lines = ["Guards on the labelled pairs (tools/bench/guard_set.jsonl; positive = a bad cleanup rejected):"]
    for g, by in report.items():
        for s, c in by.items():
            lines.append(f"  guard {g:8} {s:9} bad caught {c['tp']}/{c['tp'] + c['fn']} (recall {_pct(c['recall'])}), "
                         f"precision {_pct(c['precision'])}, good kept {c['tn']}/{c['tn'] + c['fp']}, "
                         f"bad among accepted {_pct(c['false_accept'], 1)}, accuracy {_pct(c['accuracy'])} "
                         f"(held-out {_pct(c['heldout']['accuracy'])})")
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


def compare(columns, rows_by_column, reps=2000):
    """Each column against the first: paired bootstrap Δ of formatted WER, over-edits and time. {label: {metric: CI}}."""
    labels = list(columns)
    base = labels[0]
    out = {}
    for label in labels[1:]:
        out[label] = {m: metrics.paired_bootstrap(rows_by_column[base][m], rows_by_column[label][m], reps)
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
                     f"time {ci(c['ms'], 1, ' ms', 0)}")
    return "\n".join(lines)


def main(argv=None, call=core.cleanup, legacy_call=legacy.cleanup):
    """Returns 0 when the run finished, 1 when every request failed, 2 when it could not start. `call` is the app's
    cleanup call and `legacy_call` the frozen v1 one, (cfg, raw, style, app_label) -> text; tests pass fake ones."""
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
    ap.add_argument("--stt", help="which cached transcript of the clips (the label bench_stt.py prints; default: newest)")
    ap.add_argument("--out", help="where to save the JSON (default: %%APPDATA%%\\Vox\\bench\\bench-DATE.json)")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between requests (default 2)")
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
        rows, stt_label = clip_rows(clips.clips_dir(), args.stt)
        source = f"your clips, transcript: {stt_label}"
        if not rows and (args.clips or args.stt):
            print("No transcribed clips: record with tools/bench_record.py, then run tools/bench_stt.py.", file=sys.stderr)
            return 2
        if not rows:
            rows, source = load_corpus(CORPUS), os.path.basename(CORPUS)
        report["stt"] = stt_label if rows and "ref_intended" in rows[0] else None
    models = [m.strip() for m in args.compare.split(",") if m.strip()] if args.compare else [args.model or default_model]
    print(f"{urlparse(base).hostname}, strength {args.strength}, {len(rows)} rows ({source}), models: {', '.join(models)}, "
          f"prompts: {', '.join(prompts)}, guards: {', '.join(guards)}", file=sys.stderr)
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
        s = metrics.score(row, cleaned, args.strength)
        s["answered"] = metrics.answered_or_obeyed(row["raw"], cleaned, row.get("ref_intended"))
        s["pasted"] = {g: metrics.score_pasted(row, cleaned, metrics.accepted(guard_fn(g)(row["raw"], cleaned, args.strength)),
                                               args.strength, core.fallback_text) for g in guards}
        return s

    results = run_variants(rows, variants, score, args.pause,
                           progress=lambda i, n, lbl, r: print(f"  {lbl} [{i}/{n}] {r['id']}: {r['error'] or str(r['ms']) + ' ms'}",
                                                              file=sys.stderr))
    columns, per_row = {}, {}
    for lbl, res in results.items():
        summary = dict(metrics.summarize(res), **metrics.summarize_usage(res))
        report["models"][lbl] = dict(meta[lbl], summary=summary,
                                     pasted={g: metrics.summarize_pasted(res, g) for g in guards}, rows=res)
        for g in guards:
            col = lbl if len(guards) == 1 else f"{lbl} | guard {g}"
            columns[col] = report["models"][lbl]["pasted"][g]
            per_row[col] = {m: metrics.row_values(res, g, m) for m in ("fwer", "over_edit", "ms")}
    print(render_table({m: v["summary"] for m, v in report["models"].items()}))
    print("\nWhat gets pasted (the cleaned text when the guard accepts it, else the spoken words):")
    print(render_table(columns, PASTED_COLUMNS))
    if len(columns) > 1:
        base_col, comparisons = compare(columns, per_row)
        report["compare"] = {"against": base_col, **comparisons}
        print("\n" + render_compare(base_col, comparisons))
    _save(out, report)
    return 1 if all(v["summary"]["errors"] == v["summary"]["rows"] for v in report["models"].values()) else 0


def _save(out, report):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Saved: {out}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
