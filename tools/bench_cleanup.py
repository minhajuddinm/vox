"""Benchmark of the cleanup step: runs a model over a corpus of transcripts and measures what it keeps.

    python tools/bench_cleanup.py --provider groq --model openai/gpt-oss-20b --strength light
    python tools/bench_cleanup.py --compare openai/gpt-oss-20b,openai/gpt-oss-120b --strength light

It uses the real cleanup call and prompt of the Windows app (vox_core.cleanup, with About you, dictionary and style
taken from each corpus row, never from your own settings) and your normal Vox settings for the server and key. The key is
never printed or saved, and a key is only sent to the server it is set for. Not run in CI: it costs requests, so you run
it yourself. Results (the numbers and each answer) are saved to %APPDATA%\\Vox\\bench\\ and never uploaded.
Metrics: tools/bench_metrics.py. Design: documentation/specs/p9a-cleanup-keeps-my-words.md.
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

import bench_metrics as metrics   # noqa: E402
import providers                  # noqa: E402
import vox_core as core           # noqa: E402

CORPUS = os.path.join(HERE, "bench", "corpus.jsonl")


def load_corpus(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def row_config(cfg, row, strength):
    """The settings for one corpus row: the row's About you, dictionary and style, never the user's own
    (not their learned cleanup rules either: those hold personal names and would skew the comparison)."""
    return dict(cfg, user_context=row["about"], people=list(row["terms"]), dictionary=[], app_styles={},
                my_cleanup_rules="", default_style=row["style"], cleanup_strength=strength)


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


def _pct(x, digits=0):
    return "-" if x is None else f"{x * 100:.{digits}f}%"


COLUMNS = (
    ("latency median", lambda s: f"{s['median_ms'] / 1000:.2f} s"),
    ("latency p95", lambda s: f"{s['p95_ms'] / 1000:.2f} s"),
    ("word recall", lambda s: _pct(s["recall"], 1)),
    ("added words", lambda s: _pct(s["added_rate"], 1)),
    ("length ratio", lambda s: f"{s['length_ratio']:.2f}"),
    ("guard pass", lambda s: _pct(s["guard_pass"])),
    ("term accuracy", lambda s: _pct(s["term_accuracy"])),
    ("format only", lambda s: _pct(s["structure_ok"])),
    ("errors", lambda s: f"{s['errors']}/{s['rows']}"),
)


def render_table(summaries):
    """A text table, one column per model ({label: metrics.summarize(...)}), one line per metric."""
    width = max([len(label) for label in summaries] + [8]) + 2
    lines = ["".ljust(16) + "".join(label.rjust(width) for label in summaries)]
    for name, fmt in COLUMNS:
        lines.append(name.ljust(16) + "".join(fmt(s).rjust(width) for s in summaries.values()))
    return "\n".join(lines)


def provider_settings(cfg, provider):
    """The settings with the chosen provider as the cleanup server. The main key goes only to its own server: a key set
    for another address is dropped, so the provider then has no key (the caller says so)."""
    preset = next(p for p in providers.PRESETS if p["id"] == provider)
    base = preset["base_url"].rstrip("/")
    cfg = dict(cfg, relay_proxy=False)   # the relay would ignore the provider
    if providers.role_settings(cfg, "llm")[0] != base:
        cfg["llm_api_key"] = ""
    cfg["llm_base_url"] = base
    return cfg


def main(argv=None, call=core.cleanup):
    """Returns 0 when the run finished, 1 when every request failed, 2 when it could not start. `call` is the cleanup
    call, (cfg, raw, style, app_label) -> text; tests pass a fake one."""
    ap = argparse.ArgumentParser(description="Benchmark the cleanup step of Vox over a corpus of transcripts.")
    ap.add_argument("--provider", choices=[p["id"] for p in providers.PRESETS if p["base_url"]],
                    help="server preset (default: the one set in Vox, relay included)")
    ap.add_argument("--model", help="cleanup model (default: the one set in Vox)")
    ap.add_argument("--compare", help="comma-separated models to run one after the other on the same rows")
    ap.add_argument("--strength", choices=("light", "standard"), default="light")
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--out", help="where to save the JSON (default: %%APPDATA%%\\Vox\\bench\\bench-DATE.json)")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between requests (default 2)")
    args = ap.parse_args(argv)

    cfg = core.load_config()
    if args.provider:
        cfg = provider_settings(cfg, args.provider)
    base, key, default_model = providers.role_settings(cfg, "llm")
    problem = core.endpoint_error(cfg if providers.uses_relay(cfg) else {"base_url": base})
    if not problem and not key and not core.is_private_host(urlparse(base).hostname):
        problem = (f"No API key for {args.provider or base}: set it in Vox (Settings, AI server) first. "
                   "The key of another server is never sent here.")
    if problem:
        print(problem, file=sys.stderr)
        return 2

    rows = load_corpus(args.corpus)
    models = [m.strip() for m in args.compare.split(",") if m.strip()] if args.compare else [args.model or default_model]
    print(f"{urlparse(base).hostname}, strength {args.strength}, {len(rows)} rows, models: {', '.join(models)}", file=sys.stderr)
    report = {"when": datetime.now().isoformat(timespec="seconds"), "provider": urlparse(base).hostname,
              "strength": args.strength, "corpus": os.path.basename(args.corpus), "models": {}}
    for model in models:
        mcfg = dict(cfg, llm_model=model)

        def cleanup(row):
            try:
                return call(row_config(mcfg, row, args.strength), row["raw"], row["style"], "")
            except Exception as e:
                raise type(e)(str(e).replace(key, "***") if key else str(e)) from None   # an error text never carries the key

        results = run(rows, cleanup, args.strength, args.pause,
                      progress=lambda i, n, r: print(f"  {model} [{i}/{n}] {r['id']}: {r['error'] or str(r['ms']) + ' ms'}", file=sys.stderr))
        report["models"][model] = {"summary": metrics.summarize(results), "rows": results}
    print(render_table({m: v["summary"] for m, v in report["models"].items()}))

    out = args.out or os.path.join(core.data_dir(), "bench", datetime.now().strftime("bench-%Y%m%d-%H%M%S.json"))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Saved: {out}", file=sys.stderr)
    return 1 if all(v["summary"]["errors"] == v["summary"]["rows"] for v in report["models"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
