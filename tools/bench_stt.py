"""Speech-to-text benchmark over your recorded clips (tools/bench_record.py): transcribes each clip once per speech
setting and caches the text next to the clip, so a rerun costs nothing.

    python tools/bench_stt.py --provider groq
    python tools/bench_stt.py --provider groq --compare whisper-large-v3-turbo,whisper-large-v3 --prompt on,off
    python tools/bench_stt.py --provider groq --trim-pad-ms 210,400 --prompt-tokens 160,100
(run them with the repository's venv Python, .venv\\Scripts\\python: it has the app's packages)

Each clip goes through the app's own speech path (vox_core.process_detailed with the AI cleanup off: the same edge trim,
upload, silence and hallucination handling, and Whisper prompt as a dictation). With --prompt on the benchmark
dictionary (every clip's names and terms, never your own dictionary or snippets) is the dictionary, and the clip's own
terms are named first (as recently learned words), so each clip's prompt carries its terms even though the app names
at most WHISPER_PROMPT_TERMS (30) terms within WHISPER_PROMPT_TOKENS: the best case of the prompt. --trim-pad-ms and
--prompt-tokens try other values of the app's edge-trim padding (TRIM_PAD_FRAMES, 30 ms frames) and Whisper prompt size
(WHISPER_PROMPT_TOKENS) for the tuning round; without them the app's own values are used.

The cache is %APPDATA%\\Vox\\bench\\clips\\clip-NNN.stt.json, one entry per setting (server, model, prompt on/off,
language, and padding or prompt size when chosen); an entry is redone only when the audio, the exact prompt sent or the
padding changed. Prints WER against your verbatim text (per kind of clip too), term recall, time, and each setting
against the first (paired bootstrap 95% interval on the WER); saves the numbers and every clip's transcript, prompt size
and padding to %APPDATA%\\Vox\\bench\\stt-DATE.json. Then: bench_cleanup.py.

The key is never printed or saved, and only sent to the server it is set for. A rate limit (429) is waited out. The audio
of every clip is sent to the speech server you choose (that is the point); nothing else leaves the PC.
"""
import argparse
import contextlib
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

FRAME_MS = 30   # one edge-trim frame (vox_core.Segmenter.FRAME at 16 kHz)


def stt_config(cfg, model, terms, language, first=()):
    """The settings for transcribing a clip: the benchmark terms as the dictionary (none with the prompt off), `first`
    (the clip's own terms) as words learned just now, so the app's Whisper prompt names them first; the cleanup off, and
    nothing personal (no people, snippets, replacements, learned words or styles of your own)."""
    now = time.time()
    terms = list(terms)
    return dict(cfg, stt_model=model, people=[], dictionary=terms, snippets={}, app_styles={}, cleanup=False,
                learned_log=[{"wrong": "", "right": t, "t": now} for t in first if t in terms],
                language=language, default_style="neutral")


def prompt_text(cfg):
    """The exact speech prompt vox_core.transcribe sends with these settings (to know when a cached transcript is out of
    date, and to report its size)."""
    try:
        return core.whisper_prompt_with_context(core.dictionary_terms(cfg), "", core.people_terms(cfg),
                                                core.recent_terms(cfg))
    except Exception:   # the prompt code changed shape: the terms still identify it
        return "terms:" + "|".join(core.dictionary_terms(cfg))


@contextlib.contextmanager
def app_values(pad_frames=None, prompt_tokens=None):
    """The app's edge-trim padding and Whisper prompt size set to these values (None = the app's own) while a clip is
    transcribed, then put back."""
    old = core.TRIM_PAD_FRAMES, core.WHISPER_PROMPT_TOKENS
    if pad_frames is not None:
        core.TRIM_PAD_FRAMES = pad_frames
    if prompt_tokens is not None:
        core.WHISPER_PROMPT_TOKENS = prompt_tokens
    try:
        yield
    finally:
        core.TRIM_PAD_FRAMES, core.WHISPER_PROMPT_TOKENS = old


class Setting:
    """One speech setting of a run: the prompt and config are built per clip (the clip's terms first)."""

    def __init__(self, label, cfg, model, prompt_on, language, terms, pad_frames=None, prompt_tokens=None):
        self.label, self.cfg, self.model, self.prompt_on, self.language = label, cfg, model, prompt_on, language
        self.terms, self.pad_frames, self.prompt_tokens = list(terms), pad_frames, prompt_tokens

    def values(self):
        return app_values(self.pad_frames, self.prompt_tokens)

    def for_clip(self, row):
        """(cfg, prompt) for one clip, with the setting's values in force."""
        terms = self.terms if self.prompt_on else []
        cfg = stt_config(self.cfg, self.model, terms, self.language, row.get("terms") or [])
        with self.values():
            return cfg, prompt_text(cfg)


def transcribe_clip(cfg, pcm):
    """The transcript of a clip through the app's speech path (the cleanup is off in cfg)."""
    return core.process_detailed(cfg, pcm, "", "").raw


def setting_label(host, model, prompt, language, pad_ms=None, prompt_tokens=None):
    return (f"{host} {model} prompt-{prompt}" + (f" lang-{language}" if language else "")
            + (f" pad-{pad_ms}ms" if pad_ms is not None else "") + (f" ptok-{prompt_tokens}" if prompt_tokens is not None else ""))


def _prompt_info(prompt, row):
    terms = row.get("terms") or []
    return {"prompt_chars": len(prompt), "prompt_tokens": core.est_tokens(prompt),
            "clip_terms_in_prompt": all(t in prompt for t in terms) if terms else None}


def _resolve(setting, row):
    """(label, cfg, prompt, values context) of a setting for one clip: a Setting builds them per clip; a plain
    (label, cfg, prompt) tuple is used as it is, with the app's own values."""
    if isinstance(setting, Setting):
        cfg, prompt = setting.for_clip(row)
        return setting.label, cfg, prompt, setting.values
    label, cfg, prompt = setting
    return label, cfg, prompt, app_values


def _label(setting):
    return setting.label if isinstance(setting, Setting) else setting[0]


def run(folder, settings, transcribe=transcribe_clip, pause=0, sleep=time.sleep, clock=time.perf_counter, say=print):
    """Transcribes every typed clip with every setting (Setting, or (label, cfg, prompt_text)) unless the cache already
    has it for the same audio, prompt and edge-trim padding. Settings take turns clip by clip in alternating order.
    Ctrl+C stops the run with the clips done (each transcript is cached as it comes). Returns {label: [row result]}."""
    rows = [r for r in clips.load_manifest(folder) if r.get("ref_verbatim")]
    out = {_label(s): [] for s in settings}
    calls = 0
    try:
        for i, row in enumerate(rows):
            cid = row["id"]
            try:
                pcm = clips.read_pcm(clips.wav_path(folder, cid))
            except (OSError, ValueError, EOFError) as e:
                say(f"  {cid}: cannot read the clip ({e}), skipped")
                continue
            audio = hashlib.sha256(pcm).hexdigest()
            cache = clips.load_stt(folder, cid)
            for setting in (settings if i % 2 == 0 else settings[::-1]):
                label, cfg, prompt, values = _resolve(setting, row)
                with values():
                    pad = core.TRIM_PAD_FRAMES
                phash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
                hit = cache.get(label)
                if not (hit and hit.get("audio_sha256") == audio and hit.get("prompt_sha256") == phash
                        and hit.get("trim_pad_frames") == pad and "text" in hit):
                    if calls and pause:
                        sleep(pause)
                    calls += 1
                    t0 = [clock()]

                    def once(cfg=cfg, values=values):
                        t0[0] = clock()
                        with values():
                            return transcribe(cfg, pcm)
                    try:
                        text = bench.call_waiting(once, sleep, say)
                    except Exception as e:
                        key = providers.role_settings(cfg, "stt")[1]
                        say(f"  {label} {cid}: {bench.redact(e, key)}")
                        out[label].append({"id": cid, "error": str(bench.redact(e, key))})
                        continue
                    hit = {"text": text, "ms": round((clock() - t0[0]) * 1000),
                           "when": datetime.now().isoformat(timespec="seconds"), "audio_sha256": audio,
                           "prompt_sha256": phash, "trim_pad_frames": pad, "seconds": round(clips.seconds(pcm), 2)}
                    cache[label] = hit
                    clips.save_stt(folder, cid, cache)
                    say(f"  {label} [{i + 1}/{len(rows)}] {cid}: {hit['ms']} ms")
                out[label].append(dict(score_clip(row, hit["text"]), id=cid, ms=hit["ms"], seconds=hit["seconds"], error="",
                                       text=hit["text"], trim_pad_ms=pad * FRAME_MS, **_prompt_info(prompt, row)))
    except KeyboardInterrupt:
        say("Stopped (Ctrl+C). Every transcript so far is cached: the same command continues where it stopped.")
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


def _ints(text, what):
    """The comma-separated whole numbers of an option, or raises ValueError naming the option."""
    try:
        values = [int(x) for x in (text or "").split(",") if x.strip()]
    except ValueError:
        raise ValueError(f"{what} takes whole numbers, comma separated") from None
    if not values or any(v < 0 for v in values):
        raise ValueError(f"{what} takes whole numbers of 0 or more, comma separated")
    return list(dict.fromkeys(values))


def compare(results, reps=2000):
    """Each setting against the first, per clip: paired bootstrap Δ of the micro WER (as the table) and of the term
    recall (mean). {label: {"wer": CI, "terms": CI}}."""
    labels = list(results)
    if len(labels) < 2:
        return None, {}

    def by_id(res, key):
        return {r["id"]: (r.get(key) if not r["error"] else None) for r in res}
    base = labels[0]
    ids = [r["id"] for r in results[base]]
    out = {}
    for label in labels[1:]:
        out[label] = {}
        for key, stat in (("wer", metrics.micro), ("terms", None)):
            a, b = by_id(results[base], key), by_id(results[label], key)
            out[label][key] = metrics.paired_bootstrap([a.get(i) for i in ids], [b.get(i) for i in ids], reps, stat=stat)
    return base, out


def render_compare(base, comparisons):
    def ci(c, unit="pts"):
        if not c:
            return "-"
        verdict = "lower" if c["hi"] < 0 else "higher" if c["lo"] > 0 else "tie"
        return f"{c['delta'] * 100:+.1f} {unit} [{c['lo'] * 100:+.1f}, {c['hi'] * 100:+.1f}] {verdict}"
    lines = [f"Against {base} (paired bootstrap 95% interval; a default changes only when the WER interval excludes 0):"]
    for label, c in comparisons.items():
        lines.append(f"  {label}: WER {ci(c['wer'])}; term recall {ci(c['terms'])}")
    return "\n".join(lines)


def main(argv=None, transcribe=transcribe_clip):
    """Returns 0 when the run finished, 1 when every request failed, 2 when it could not start."""
    clips.safe_console()
    ap = argparse.ArgumentParser(description="Transcribe your recorded clips once per speech setting and measure WER.")
    ap.add_argument("--provider", choices=[p["id"] for p in providers.PRESETS if p["base_url"]],
                    help="speech server preset (default: the one set in Vox, relay included)")
    ap.add_argument("--model", help="speech model (default: the one set in Vox)")
    ap.add_argument("--compare", help="comma-separated speech models")
    ap.add_argument("--prompt", default="on", help="the dictionary prompt: on, off or on,off (default on)")
    ap.add_argument("--language", help="language code sent to the server (default: the one set in Vox; '' = auto)")
    ap.add_argument("--trim-pad-ms", help="edge-trim padding to try, comma separated, in ms (rounded to 30 ms frames; "
                                          f"default: the app's {core.TRIM_PAD_FRAMES * FRAME_MS} ms)")
    ap.add_argument("--prompt-tokens", help="Whisper prompt sizes to try, comma separated, in estimated tokens "
                                            f"(default: the app's {core.WHISPER_PROMPT_TOKENS})")
    ap.add_argument("--folder", help="the clips folder (default: %%APPDATA%%\\Vox\\bench\\clips; a public set from "
                                      "bench_public.py: %%APPDATA%%\\Vox\\bench\\public\\<source>)")
    ap.add_argument("--out", help="where to save the JSON (default: %%APPDATA%%\\Vox\\bench\\stt-DATE.json)")
    ap.add_argument("--pause", type=float, default=3.0, help="seconds between requests (default 3: free tiers allow 20 a minute)")
    args = ap.parse_args(argv)
    modes = [m.strip().lower() for m in args.prompt.split(",") if m.strip()]
    if not modes or any(m not in ("on", "off") for m in modes):
        print("--prompt takes on, off or on,off", file=sys.stderr)
        return 2
    try:
        pads = _ints(args.trim_pad_ms, "--trim-pad-ms") if args.trim_pad_ms else [None]
        sizes = _ints(args.prompt_tokens, "--prompt-tokens") if args.prompt_tokens else [None]
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    folder = args.folder or clips.clips_dir()
    typed = [r for r in clips.load_manifest(folder) if r.get("ref_verbatim")]
    if not typed:
        print(f"No recorded clips with their texts in {folder}: run tools/bench_record.py first.", file=sys.stderr)
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
            for pad_ms in pads:
                for size in (sizes if mode == "on" else [None]):   # with the prompt off its size does not matter
                    frames = None if pad_ms is None else round(pad_ms / FRAME_MS)
                    settings.append(Setting(setting_label(host, model, mode, language,
                                                          None if frames is None else frames * FRAME_MS, size),
                                            cfg, model, mode == "on", language, terms, frames, size))
    print(f"{host}, {len(typed)} clips, {len(terms)} benchmark terms, settings: {'; '.join(s.label for s in settings)}",
          file=sys.stderr)
    if len(terms) > core.WHISPER_PROMPT_TERMS:
        print(f"Note: the app names at most {core.WHISPER_PROMPT_TERMS} terms in the Whisper prompt; each clip's own terms "
              "are named first.", file=sys.stderr)

    results = run(folder, settings, transcribe, args.pause, say=lambda t: print(t, file=sys.stderr))
    report = {"when": datetime.now().isoformat(timespec="seconds"), "provider": host, "clips": len(typed),
              "terms": len(terms), "app_trim_pad_ms": core.TRIM_PAD_FRAMES * FRAME_MS,
              "app_prompt_tokens": core.WHISPER_PROMPT_TOKENS, "app_prompt_terms": core.WHISPER_PROMPT_TERMS, "settings": {}}
    for s in settings:
        res = results[s.label]
        ok = [r for r in res if not r["error"]]
        report["settings"][s.label] = {
            "model": s.model, "prompt": "on" if s.prompt_on else "off",
            "trim_pad_ms": (core.TRIM_PAD_FRAMES if s.pad_frames is None else s.pad_frames) * FRAME_MS,
            "prompt_tokens_budget": core.WHISPER_PROMPT_TOKENS if s.prompt_tokens is None else s.prompt_tokens,
            "prompt_tokens_mean": statistics.mean(r["prompt_tokens"] for r in ok) if ok else None,
            "summary": summarize(res), "rows": res}
    summaries = {label: v["summary"] for label, v in report["settings"].items()}
    print(bench.render_table(summaries, COLUMNS))
    for label, s in summaries.items():
        print(f"WER by kind, {label}: " + ", ".join(f"{k} {bench._pct(v, 1)}" for k, v in s["wer_by_kind"].items()))
    base_label, comparisons = compare(results)
    if comparisons:
        report["compare"] = {"against": base_label, **comparisons}
        print("\n" + render_compare(base_label, comparisons))
    out = args.out or os.path.join(core.data_dir(), "bench", datetime.now().strftime("stt-%Y%m%d-%H%M%S.json"))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Saved: {out}\nNext: .venv\\Scripts\\python tools\\bench_cleanup.py --compare-prompt v1,v3 --compare-guard v1,v2 "
          "--strength light", file=sys.stderr)
    return 1 if all(s["errors"] == s["clips"] for s in summaries.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
