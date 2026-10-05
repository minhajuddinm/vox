"""Speech-to-text benchmark over your recorded clips (tools/bench_record.py): transcribes each clip once per speech
setting and caches the text next to the clip, so a rerun costs nothing.

    python tools/bench_stt.py --provider groq
    python tools/bench_stt.py --provider groq --compare whisper-large-v3-turbo,whisper-large-v3 --prompt on,off

Each clip goes through the app's own speech path (vox_core.process_detailed with the AI cleanup off: the same upload,
silence and hallucination handling, and Whisper prompt as a dictation), with the benchmark dictionary (every clip's
names and terms, never your own dictionary or snippets) as the prompt when --prompt is on. The cache is
%APPDATA%\\Vox\\bench\\clips\\clip-NNN.stt.json, one entry per setting (server, model, prompt on/off, language); an
entry is redone only when the audio or the prompt changed. Prints WER against your verbatim text (per kind of clip too),
term recall, and time; saves the numbers to %APPDATA%\\Vox\\bench\\stt-DATE.json. Then: python tools/bench_cleanup.py.

The key is never printed or saved, and only sent to the server it is set for. A rate limit (429) is waited out. The audio
of every clip is sent to the speech server you choose (that is the point); nothing else leaves the PC.
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "windows"))
sys.path.insert(0, HERE)

import bench_cleanup as bench     # noqa: E402
import bench_clips as clips       # noqa: E402
import bench_metrics as metrics   # noqa: E402
import providers                  # noqa: E402
import vox_core as core           # noqa: E402


def stt_config(cfg, model, terms, language):
    """The settings for transcribing a clip: the benchmark terms as the dictionary (none with the prompt off), the
    cleanup off, and nothing personal (no snippets, replacements or styles of your own)."""
    return dict(cfg, stt_model=model, people=list(terms), dictionary=[], snippets={}, app_styles={}, cleanup=False,
                language=language, default_style="neutral")


def prompt_text(cfg):
    """The speech prompt these settings send (to know when a cached transcript is out of date)."""
    try:
        return core.whisper_prompt_with_context(core.dictionary_terms(cfg))
    except Exception:   # the prompt code changed shape: the terms still identify it
        return "terms:" + "|".join(core.dictionary_terms(cfg))


def transcribe_clip(cfg, pcm):
    """The transcript of a clip through the app's speech path (the cleanup is off in cfg)."""
    return core.process_detailed(cfg, pcm, "", "").raw


def setting_label(host, model, prompt, language):
    return f"{host} {model} prompt-{prompt}" + (f" lang-{language}" if language else "")


def run(folder, settings, transcribe=transcribe_clip, pause=0, sleep=time.sleep, clock=time.perf_counter, say=print):
    """Transcribes every typed clip with every setting ([(label, cfg, prompt_text)]) unless the cache already has it for
    the same audio and prompt. Settings take turns clip by clip in alternating order. Returns {label: [row result]}."""
    rows = [r for r in clips.load_manifest(folder) if r.get("ref_verbatim")]
    out = {label: [] for label, _, _ in settings}
    calls = 0
    for i, row in enumerate(rows):
        cid = row["id"]
        try:
            pcm = clips.read_pcm(clips.wav_path(folder, cid))
        except (OSError, ValueError, EOFError) as e:
            say(f"  {cid}: cannot read the clip ({e}), skipped")
            continue
        audio = hashlib.sha256(pcm).hexdigest()
        cache = clips.load_stt(folder, cid)
        for label, cfg, prompt in (settings if i % 2 == 0 else settings[::-1]):
            phash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
            hit = cache.get(label)
            if not (hit and hit.get("audio_sha256") == audio and hit.get("prompt_sha256") == phash and "text" in hit):
                if calls and pause:
                    sleep(pause)
                calls += 1
                t0 = [clock()]

                def once(cfg=cfg):
                    t0[0] = clock()
                    return transcribe(cfg, pcm)
                try:
                    text = bench.call_waiting(once, sleep, say)
                except Exception as e:
                    key = providers.role_settings(cfg, "stt")[1]
                    say(f"  {label} {cid}: {bench.redact(e, key)}")
                    out[label].append({"id": cid, "error": str(bench.redact(e, key))})
                    continue
                hit = {"text": text, "ms": round((clock() - t0[0]) * 1000), "when": datetime.now().isoformat(timespec="seconds"),
                       "audio_sha256": audio, "prompt_sha256": phash, "seconds": round(clips.seconds(pcm), 2)}
                cache[label] = hit
                clips.save_stt(folder, cid, cache)
                say(f"  {label} [{i + 1}/{len(rows)}] {cid}: {hit['ms']} ms")
            out[label].append(dict(score_clip(row, hit["text"]), id=cid, ms=hit["ms"], seconds=hit["seconds"], error=""))
    return out


def score_clip(row, text):
    errors, words = metrics.wer_counts(row["ref_verbatim"], text)
    return {"kind": row.get("kind", ""), "wer": [errors, words], "terms": metrics.term_recall(text, row.get("terms") or [])
            if row.get("terms") else None, "empty": not text.strip()}


def summarize(results):
    ok = [r for r in results if not r["error"]]
    ms = [r["ms"] for r in ok]
    terms = [r["terms"] for r in ok if r["terms"] is not None]
    kinds = {}
    for r in ok:
        kinds.setdefault(r["kind"] or "other", []).append(r["wer"])
    audio = sum(r["seconds"] for r in ok)
    return {"clips": len(results), "errors": len(results) - len(ok),
            "wer": metrics._rate(sum(e for e, _ in (r["wer"] for r in ok)), sum(n for _, n in (r["wer"] for r in ok))) if ok else None,
            "term_recall": sum(terms) / len(terms) if terms else None, "empty": sum(r["empty"] for r in ok),
            "median_ms": statistics.median(ms) if ms else 0, "p95_ms": metrics.percentile(ms, 95),
            "ms_per_audio_second": sum(ms) / audio if audio else None,
            "wer_by_kind": {k: metrics._rate(sum(e for e, _ in v), sum(n for _, n in v)) for k, v in sorted(kinds.items())}}


COLUMNS = (
    ("WER", lambda s: bench._pct(s["wer"], 1)),
    ("term recall", lambda s: bench._pct(s["term_recall"])),
    ("empty answers", lambda s: str(s["empty"])),
    ("latency median", lambda s: f"{s['median_ms'] / 1000:.2f} s"),
    ("latency p95", lambda s: f"{s['p95_ms'] / 1000:.2f} s"),
    ("ms per audio s", lambda s: bench._num(s["ms_per_audio_second"])),
    ("errors", lambda s: f"{s['errors']}/{s['clips']}"),
)


def main(argv=None, transcribe=transcribe_clip):
    """Returns 0 when the run finished, 1 when every request failed, 2 when it could not start."""
    ap = argparse.ArgumentParser(description="Transcribe your recorded clips once per speech setting and measure WER.")
    ap.add_argument("--provider", choices=[p["id"] for p in providers.PRESETS if p["base_url"]],
                    help="speech server preset (default: the one set in Vox, relay included)")
    ap.add_argument("--model", help="speech model (default: the one set in Vox)")
    ap.add_argument("--compare", help="comma-separated speech models")
    ap.add_argument("--prompt", default="on", help="the dictionary prompt: on, off or on,off (default on)")
    ap.add_argument("--language", help="language code sent to the server (default: the one set in Vox; '' = auto)")
    ap.add_argument("--folder", help="the clips folder (default: %%APPDATA%%\\Vox\\bench\\clips)")
    ap.add_argument("--out", help="where to save the JSON (default: %%APPDATA%%\\Vox\\bench\\stt-DATE.json)")
    ap.add_argument("--pause", type=float, default=3.0, help="seconds between requests (default 3: free tiers allow 20 a minute)")
    args = ap.parse_args(argv)
    modes = [m.strip().lower() for m in args.prompt.split(",") if m.strip()]
    if not modes or any(m not in ("on", "off") for m in modes):
        print("--prompt takes on, off or on,off", file=sys.stderr)
        return 2

    folder = args.folder or clips.clips_dir()
    typed = [r for r in clips.load_manifest(folder) if r.get("ref_verbatim")]
    if not typed:
        print(f"No recorded clips with their texts in {folder}: run python tools/bench_record.py first.", file=sys.stderr)
        return 2
    cfg = core.load_config()
    if args.provider:
        cfg = bench.provider_settings(cfg, args.provider, "stt")
    base, _, default_model = providers.role_settings(cfg, "stt")
    problem = bench.server_problem(cfg, "stt", args.provider)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    language = cfg.get("language", "") if args.language is None else args.language
    models = [m.strip() for m in args.compare.split(",") if m.strip()] if args.compare else [args.model or default_model]
    terms = clips.all_terms(typed)
    host = urlparse(base).hostname
    settings = []
    for model in models:
        for mode in dict.fromkeys(modes):
            scfg = stt_config(cfg, model, terms if mode == "on" else [], language)
            settings.append((setting_label(host, model, mode, language), scfg, prompt_text(scfg)))
    print(f"{host}, {len(typed)} clips, settings: {'; '.join(s[0] for s in settings)}", file=sys.stderr)

    results = run(folder, settings, transcribe, args.pause, say=lambda t: print(t, file=sys.stderr))
    report = {"when": datetime.now().isoformat(timespec="seconds"), "provider": host, "clips": len(typed),
              "terms": len(terms), "settings": {}}
    for label, res in results.items():
        report["settings"][label] = {"summary": summarize(res), "rows": res}
    summaries = {label: v["summary"] for label, v in report["settings"].items()}
    print(bench.render_table(summaries, COLUMNS))
    for label, s in summaries.items():
        print(f"WER by kind, {label}: " + ", ".join(f"{k} {bench._pct(v, 1)}" for k, v in s["wer_by_kind"].items()))
    out = args.out or os.path.join(core.data_dir(), "bench", datetime.now().strftime("stt-%Y%m%d-%H%M%S.json"))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Saved: {out}\nNext: python tools/bench_cleanup.py --compare-prompt v1,v3 --compare-guard v1,v2 --strength light",
          file=sys.stderr)
    return 1 if all(s["errors"] == s["clips"] for s in summaries.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
