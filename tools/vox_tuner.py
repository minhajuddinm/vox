"""Vox Tuner: speak, compare what Vox would type, approve one. Your approved transcripts and texts become benchmark
references (the same clips folder and manifest as tools/bench_record.py), and your short notes become feedback for
improving the cleanup prompts. English only.

    python tools/vox_tuner.py          (or double-click tools/vox_tuner.cmd)
    python tools/vox_tuner.py --stt-models whisper-large-v3-turbo,whisper-large-v3 --provider groq
(run it with the repository's venv Python, .venv\\Scripts\\python: it has sounddevice and the app's packages)

It starts a small web server on 127.0.0.1 only (a free port) and opens the page in your browser; the address carries a
random session token and every request without it is refused (403). Per clip: choose the scenario (the app Vox types
into), press Space to record and Space to stop, then the clip is transcribed by two speech models (both shown, differing
words marked) and cleaned three ways for that app:
    OLD     the frozen v1 cleanup prompt and guard (tools/bench/legacy.py) inside today's pipeline
    NEW     exactly what Vox types today for that app (vox_core.process_text with your saved settings: style per app,
            code mode, the "skip AI cleanup" rules, the guard, the rules layer, lists, snippets)
    FORCED  (the two AI-agent scenarios only) today's cleanup prompt with the neutral style, code mode off, run even
            where Vox would skip it: to judge whether prompts typed into an AI agent should be cleaned
You pick the right one or save your own version, with a one-line note. Requests per clip: one per speech model, and one
per candidate that uses the AI cleanup (at most 5); nothing runs in the background.

Saved only when you approve: the WAV and a manifest line in %APPDATA%\\Vox\\bench\\clips\\ (ref_verbatim = your approved
transcript, ref_intended = your approved text, kind = the scenario), and a line in
%APPDATA%\\Vox\\bench\\tuner-feedback.jsonl. "Export for Claude" writes tuner-feedback-DATE.md there (texts and notes, no
audio). A recording waiting for approval is kept in tuner-pending.wav until saved or skipped, so an error or a closed
window never loses it. The API key is never shown, logged or saved by this tool.
"""
import argparse
import contextlib
import difflib
import hmac
import json
import math
import os
import re
import secrets
import struct
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "windows"))
sys.path.insert(0, HERE)

import bench_clips as clips   # noqa: E402
import codemode               # noqa: E402
import providers              # noqa: E402
import vox_core as core       # noqa: E402

PAGE = os.path.join(HERE, "tuner", "index.html")
DEFAULT_STT_MODELS = ("whisper-large-v3-turbo", "whisper-large-v3")
SCENARIOS = (   # the order the page shows them; the first is the default
    {"id": "ai-terminal", "name": "AI agent in terminal", "exe": "windowsterminal.exe", "forced": True,
     "hint": "Claude Code or Gemini CLI in Windows Terminal"},
    {"id": "ai-editor", "name": "AI coding agent in editor", "exe": "code.exe", "forced": True,
     "hint": "Claude, Gemini or Copilot chat in VS Code or Cursor"},
    {"id": "email", "name": "Quick email", "exe": "outlook.exe", "forced": False, "hint": "Outlook"},
    {"id": "whatsapp", "name": "WhatsApp message", "exe": "whatsapp.exe", "forced": False, "hint": "WhatsApp desktop"},
    {"id": "note", "name": "Note / document", "exe": "notepad.exe", "forced": False, "hint": "Notepad or a document"},
)
SCENARIO_IDS = tuple(s["id"] for s in SCENARIOS)
CANDIDATES = ("old", "new", "forced")
CHOICES = CANDIDATES + ("edited",)
MIN_SECONDS = 0.5
MAX_BODY = 1_000_000
FEEDBACK = "tuner-feedback.jsonl"
PENDING_WAV = "tuner-pending.wav"
PENDING_JSON = "tuner-pending.json"
TOKEN_HEADER = "X-Vox-Token"


class TunerError(Exception):
    """A problem to show in the page in plain words, with the HTTP status to answer."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


# ------------------------------------------------------------------ plain-word errors

def plain_error(e, keys=()):
    """What the page shows for an error: plain words, never a key."""
    text = str(e)
    for k in keys:
        if k:
            text = text.replace(k, "***")
    text = text[:300]
    if isinstance(e, TunerError):
        return e.message
    if isinstance(e, core.ApiError):
        if e.code in (401, 403):
            return ("The server refused the API key (%d). Check the key in Vox (Settings, AI server), then try again."
                    % e.code)
        if e.code == 429:
            return ("The server's rate limit (429) is still on after waiting once. Wait a minute, then press Try "
                    "again. Your recording is kept.")
        if e.code == 0:
            return "The server's answer could not be used: " + text
        return f"The server answered with an error ({e.code}): {text}"
    try:
        import requests
        if isinstance(e, requests.Timeout):
            return "The server took too long to answer. Try again; your recording is kept."
        if isinstance(e, requests.ConnectionError):
            return "Could not reach the server (network). Check the internet connection, then try again."
    except ImportError:   # requests is part of the app's packages; without it there is nothing to compare with
        pass
    return f"Something went wrong ({type(e).__name__}): {text}"


def wait_429_once(fn, sleep=time.sleep):
    """fn wrapped so a rate limit (ApiError 429) is waited out once (Retry-After, else RATE_LIMIT_WAIT seconds, at most
    RATE_LIMIT_MAX_WAIT) and fn called again; a second 429, or any other error, is raised."""
    def run(*args, **kw):
        try:
            return fn(*args, **kw)
        except core.ApiError as e:
            if e.code != 429:
                raise
            sleep(min(core.RATE_LIMIT_MAX_WAIT, core.RATE_LIMIT_WAIT if e.retry_after is None else e.retry_after))
            return fn(*args, **kw)
    return run


# ------------------------------------------------------------------ the real speech and cleanup calls

_PIPE = threading.Lock()   # the pipelines swap module functions while they run: one at a time


@contextlib.contextmanager
def _swapped(module, **names):
    old = {n: getattr(module, n) for n in names}
    for n, v in names.items():
        setattr(module, n, v)
    try:
        yield
    finally:
        for n, v in old.items():
            setattr(module, n, v)


def real_stt(cfg, pcm, model, sleep=time.sleep):
    """The transcript of a clip through the app's speech path with this model (the cleanup off: no cleanup request)."""
    return wait_429_once(lambda: core.process_detailed(dict(cfg, stt_model=model, cleanup=False), pcm, "", "").raw,
                         sleep)()


def route(cfg, raw, exe):
    """How Vox handles a transcript for this app: its style, code mode, and whether the AI cleanup runs."""
    style = core.style_for(cfg, exe)
    code = codemode.is_code_app(cfg, exe, style)
    wanted = core.needs_cleanup(raw, style, cfg.get("cleanup", True), cfg.get("cleanup_min_words", 4))
    ai = wanted and not (code and codemode.code_cleanup(cfg.get("code_cleanup")) == "rules")
    if ai:
        why = "AI cleanup runs" + (" (code mode, AI cleanup chosen for code apps)" if code else "")
    elif not cfg.get("cleanup", True):
        why = "AI cleanup is off in your settings"
    elif style == "raw":
        why = f"the style for {exe} is raw: no AI cleanup"
    elif code and wanted:
        why = "code app: Vox uses code mode rules, no AI cleanup"
    else:
        why = f"under {core.clean_min_words(cfg.get('cleanup_min_words', 4))} words: no AI cleanup"
    return {"style": style, "code": bool(code), "ai": bool(ai), "why": why}


def forced_config(cfg, exe):
    """The settings of the FORCED candidate: today's cleanup with the neutral style for this app, code mode off and no
    minimum length, so the AI cleanup always runs."""
    styles = dict(cfg.get("app_styles") or {})
    for k in list(styles):
        if k.lower() == (exe or "").lower():
            del styles[k]
    styles[exe] = "neutral"
    return dict(cfg, app_styles=styles, code_mode="off", cleanup=True, cleanup_min_words=1)


def _v1_check(raw, cleaned, strength="light", finish_reason="", terms=(), repl=None):
    """The frozen v1 guard in the shape process_text calls the current one."""
    from bench import legacy
    ok = bool(legacy.looks_valid(raw, cleaned, strength))
    return core.Verdict(ok, False, "ok" if ok else "rejected by the v1 guard")


def _run_pipeline(cfg, raw, exe, strength, legacy_path, sleep):
    """process_text for this app, with the cleanup call waiting out one 429 and its error kept for the page."""
    from bench import legacy
    seen = {}
    base_cleanup = legacy.cleanup if legacy_path else core.cleanup
    retrying = wait_429_once(base_cleanup, sleep)

    def cleanup(*args, **kw):
        seen["calls"] = seen.get("calls", 0) + 1
        try:
            return retrying(*args, **kw)
        except Exception as e:
            seen["error"] = e
            raise
    names = {"cleanup": cleanup}
    if legacy_path:
        names["fidelity_check"] = _v1_check
    t0 = time.perf_counter()
    with _PIPE, _swapped(core, **names):
        r = core.process_text(dict(cfg, cleanup_strength=strength), raw, exe, exe)
    keys = [providers.role_settings(cfg, "llm")[1]]
    error = ""
    if "error" in seen:
        error = plain_error(seen["error"], keys)
    elif r.cleanup_error:
        error = "the cleanup answer was rejected by the guard" if r.fidelity_fallback else r.cleanup_error
    return {"text": r.text, "cleaned": bool(r.cleaned), "rejected": bool(r.fidelity_fallback), "error": error,
            "requests": seen.get("calls", 0), "ms": round((time.perf_counter() - t0) * 1000)}


def old_pipeline(cfg, raw, exe, strength, sleep=time.sleep):
    return _run_pipeline(cfg, raw, exe, strength, True, sleep)


def new_pipeline(cfg, raw, exe, strength, sleep=time.sleep):
    return _run_pipeline(cfg, raw, exe, strength, False, sleep)


def forced_pipeline(cfg, raw, exe, strength, sleep=time.sleep):
    return _run_pipeline(forced_config(cfg, exe), raw, exe, strength, False, sleep)


# ------------------------------------------------------------------ helpers

def _words(text):
    return (text or "").split()


def _norm_word(w):
    return re.sub(r"[^\w']", "", w.lower())


def diff_words(a, b):
    """([word, changed] for each word of a, the same for b): the words that differ between two transcripts (case and
    punctuation ignored)."""
    wa, wb = _words(a), _words(b)
    ma, mb = [[w, True] for w in wa], [[w, True] for w in wb]
    sm = difflib.SequenceMatcher(None, [_norm_word(w) for w in wa], [_norm_word(w) for w in wb], autojunk=False)
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            ma[block.a + k][1] = False
            mb[block.b + k][1] = False
    return ma, mb


def same_words(a, b):
    return [_norm_word(w) for w in _words(a)] == [_norm_word(w) for w in _words(b)]


def scenario(sid):
    for s in SCENARIOS:
        if s["id"] == sid:
            return s
    raise TunerError("Unknown scenario: choose one of the buttons.")


def _exe(value):
    exe = (value or "").strip()
    if not exe or len(exe) > 100 or not re.fullmatch(r"[\w .+()-]+\.exe", exe, re.I):
        raise TunerError("The app name must be a program file name such as outlook.exe.")
    return exe


def _strength(value):
    return "standard" if str(value or "").lower() == "standard" else "light"


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _append_jsonl(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cut = False
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            cut = f.read(1) != b"\n"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(("\n" if cut else "") + json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    return rows


# ------------------------------------------------------------------ the tuner

class Tuner:
    """The state of one tuner session: at most one clip waiting for approval. The speech and cleanup calls and the
    recorder are passed in (tests and --demo pass fakes): stt(cfg, pcm, model) -> text, and old/new/forced(cfg, raw, exe,
    strength) -> {"text", "cleaned", "rejected", "error", "requests", "ms"}."""

    def __init__(self, bench_dir, cfg, recorder, stt=real_stt, old=old_pipeline, new=new_pipeline,
                 forced=forced_pipeline, models=DEFAULT_STT_MODELS, stt_cfg=None, warnings=(), demo=False,
                 now=datetime.now, clock=time.perf_counter, mic=""):
        self.bench_dir, self.cfg, self.recorder = bench_dir, cfg, recorder
        self.stt_cfg = stt_cfg if stt_cfg is not None else cfg
        self.fns = {"old": old, "new": new, "forced": forced}
        self.stt, self.models = stt, list(models)
        self.warnings, self.demo, self.now, self.clock, self.mic = list(warnings), demo, now, clock, mic
        self.lock = threading.RLock()
        self.recording, self.clip, self.rec_started = False, None, None
        self.keys = [providers.role_settings(cfg, "llm")[1], providers.role_settings(self.stt_cfg, "stt")[1]]
        os.makedirs(self.clips_dir, exist_ok=True)
        self._load_pending()

    # -- paths
    @property
    def clips_dir(self):
        return os.path.join(self.bench_dir, "clips")

    @property
    def feedback_path(self):
        return os.path.join(self.bench_dir, FEEDBACK)

    def _pending(self, name):
        return os.path.join(self.bench_dir, name)

    # -- the waiting clip, kept on disk until saved or skipped
    def _load_pending(self):
        wav = self._pending(PENDING_WAV)
        if not os.path.exists(wav):
            return
        try:
            pcm = clips.read_pcm(wav)
        except (OSError, ValueError, EOFError):
            self.warnings.append("A kept recording (tuner-pending.wav) could not be read; it was left in place.")
            return
        clip = self._new_clip(pcm, "")
        try:
            with open(self._pending(PENDING_JSON), encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for k in ("recorded", "stt", "transcript", "cand", "made_for", "scenario", "exe", "strength", "note"):
                    if k in saved:
                        clip[k] = saved[k]
        except (OSError, ValueError):
            pass
        clip["resumed"] = True
        self.clip = clip

    def _persist(self):
        if self.clip is None:
            return
        data = {k: v for k, v in self.clip.items() if k != "pcm"}
        _atomic_write(self._pending(PENDING_JSON), json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _drop_pending(self):
        for name in (PENDING_WAV, PENDING_JSON):
            with contextlib.suppress(FileNotFoundError):
                os.remove(self._pending(name))

    def _new_clip(self, pcm, recorded):
        return {"pcm": pcm, "seconds": round(clips.seconds(pcm), 2), "recorded": recorded, "stt": {},
                "transcript": "", "cand": {}, "made_for": None, "silent": bool(core.is_silent(pcm)), "resumed": False}

    # -- recording
    def start(self):
        with self.lock:
            if self.recording:
                return self.state()
            if self.clip is not None:
                raise TunerError("Save or skip the current clip first: a recording is never thrown away silently.", 409)
            try:
                self.recorder.start()
            except Exception as e:
                raise TunerError("The microphone could not be opened: " + plain_error(e), 500) from None
            self.recording, self.rec_started = True, self.clock()
            return self.state()

    def stop(self):
        with self.lock:
            if not self.recording:
                raise TunerError("Not recording.", 409)
            self.recording = False
            pcm = self.recorder.stop() or b""
            secs = clips.seconds(pcm)
            if secs < MIN_SECONDS:
                raise TunerError(f"Only {secs:.1f} s were recorded: too short. Press Space and speak again.")
            clip = self._new_clip(pcm, self.now().isoformat(timespec="seconds"))
            _atomic_write(self._pending(PENDING_WAV), core.pcm_to_wav(pcm))
            self.clip = clip
            self._persist()
            return self.state()

    def audio(self):
        with self.lock:
            if self.clip is None:
                raise TunerError("No recording to play.", 404)
            return core.pcm_to_wav(self.clip["pcm"])

    def _need_clip(self):
        if self.clip is None:
            raise TunerError("Record a clip first (Space).", 409)
        return self.clip

    # -- speech to text
    def transcribe(self, force=False):
        with self.lock:
            clip = self._need_clip()
            for model in self.models:
                hit = clip["stt"].get(model)
                if hit and hit.get("text") is not None and not hit.get("error") and not force:
                    continue   # already have it: no request again
                t0 = self.clock()
                try:
                    text = self.stt(self.stt_cfg, clip["pcm"], model)
                    clip["stt"][model] = {"text": text or "", "ms": round((self.clock() - t0) * 1000), "error": ""}
                except Exception as e:
                    clip["stt"][model] = {"text": None, "ms": round((self.clock() - t0) * 1000),
                                          "error": plain_error(e, self.keys)}
            clip["transcript"] = self.prefill(clip)
            self._persist()
            return self.state()

    def prefill(self, clip):
        """The "what I said" text to start from: the last model's (large-v3) when the two differ, else the first's;
        the one that worked when one failed."""
        texts = [clip["stt"].get(m, {}).get("text") for m in self.models]
        ok = [t for t in texts if t is not None]
        if not ok:
            return clip.get("transcript", "")
        if len(ok) == len(texts) and all(same_words(t, texts[0]) for t in texts):
            return texts[0]
        return ok[-1]

    # -- cleanup
    def clean(self, transcript, sid, exe, strength):
        with self.lock:
            clip = self._need_clip()
            raw = (transcript or "").strip()
            if not raw:
                raise TunerError("The transcript is empty: type what you said in the box, or skip the clip.")
            sc, exe, strength = scenario(sid), _exe(exe), _strength(strength)
            names = [c for c in CANDIDATES if c != "forced" or sc["forced"]]
            cand = {}
            for name in names:
                try:
                    res = dict(self.fns[name](self.cfg, raw, exe, strength))
                except Exception as e:   # the pipelines catch the server errors; anything else is shown, the clip kept
                    res = {"text": "", "cleaned": False, "rejected": False, "error": plain_error(e, self.keys),
                           "requests": 0, "ms": 0, "failed": True}
                res["route"] = route(forced_config(self.cfg, exe) if name == "forced" else self.cfg, raw, exe)
                cand[name] = res
            clip.update(cand=cand, transcript=raw, scenario=sc["id"], exe=exe, strength=strength,
                        made_for={"transcript": raw, "scenario": sc["id"], "exe": exe, "strength": strength})
            self._persist()
            return self.state()

    # -- approve and save
    def save(self, choice, final, transcript, note, sid, exe, strength):
        with self.lock:
            clip = self._need_clip()
            if choice not in CHOICES:
                raise TunerError("Unknown choice.")
            raw = (transcript or "").strip()
            if not raw:
                raise TunerError("The transcript is empty: type what you said first.")
            sc, exe, strength = scenario(sid), _exe(exe), _strength(strength)
            made = clip.get("made_for")
            here = {"transcript": raw, "scenario": sc["id"], "exe": exe, "strength": strength}
            if choice in CANDIDATES:
                if made != here:
                    raise TunerError("The candidates were made for another transcript, app or strength: press "
                                     "\"Redo with my transcript\" first.", 409)
                c = clip["cand"].get(choice)
                if not c or c.get("failed"):
                    raise TunerError("That candidate is not there. Save your own version instead.", 409)
                final = c["text"]
            else:
                final = (final or "").strip()
                if made == here:   # the edited text is one of the candidates after all: count it as that one
                    for name in CANDIDATES:
                        c = clip["cand"].get(name)
                        if c and not c.get("failed") and c["text"] == final:
                            choice = name
                            break
            cid = clips.next_clip_id(self.clips_dir)
            clips.write_new_wav(clips.wav_path(self.clips_dir, cid), clip["pcm"])
            row = {"id": cid, "audio": cid + ".wav", "seconds": clip["seconds"], "kind": sc["id"],
                   "ref_verbatim": raw, "ref_intended": final, "intended_typed": choice == "edited", "terms": [],
                   "source": "tuner", "recorded": clip["recorded"] or self.now().isoformat(timespec="seconds"),
                   "saved": self.now().isoformat(timespec="seconds"), "mic": self.mic or "default",
                   "exe": exe, "strength": strength, "choice": choice, "note": (note or "").strip()[:500],
                   "stt": {m: (clip["stt"].get(m) or {}).get("text") for m in self.models},
                   "stt_ms": {m: (clip["stt"].get(m) or {}).get("ms") for m in self.models},
                   "outputs_for": made, "demo": self.demo}
            for name in CANDIDATES:
                c = clip["cand"].get(name) if made else None
                row[name] = None if c is None else {k: c.get(k) for k in ("text", "cleaned", "rejected", "error", "ms",
                                                                           "requests")}
            clips.append_row(self.clips_dir, row)
            _append_jsonl(self.feedback_path, row)
            self.clip = None
            self._drop_pending()
            st = self.state()
            st["saved"] = cid
            return st

    def skip(self):
        with self.lock:
            if self.recording:
                self.recording = False
                with contextlib.suppress(Exception):
                    self.recorder.stop()
            self.clip = None
            self._drop_pending()
            return self.state()

    # -- views
    def state(self):
        clip = self.clip
        view = None
        if clip is not None:
            view = {k: v for k, v in clip.items() if k != "pcm"}
            texts = [(m, (clip["stt"].get(m) or {}).get("text")) for m in self.models]
            marks = {}
            if len(texts) >= 2 and all(t is not None for _, t in texts):
                first = texts[0][1]
                for m, t in texts[1:]:
                    a, b = diff_words(first, t)
                    marks.setdefault(texts[0][0], a)
                    marks[m] = b
            view["marks"] = marks
        return {"recording": self.recording, "clip": view, "models": self.models, "demo": self.demo,
                "scenarios": list(SCENARIOS), "strength": _strength(self.cfg.get("cleanup_strength")),
                "warnings": self.warnings, "counts": self.counts(), "bench_dir": self.bench_dir}

    def counts(self):
        rows = read_jsonl(self.feedback_path)
        per = {s: 0 for s in SCENARIO_IDS}
        for r in rows:
            per[r.get("kind", "")] = per.get(r.get("kind", ""), 0) + 1
        typed = sum(1 for r in clips.load_manifest(self.clips_dir) if r.get("ref_verbatim"))
        return {"saved": len(rows), "per_scenario": per, "clips_in_folder": typed}

    def summary(self):
        rows = read_jsonl(self.feedback_path)
        by = {}
        for r in rows:
            k = r.get("kind") or "other"
            s = by.setdefault(k, {"clips": 0, **{c: 0 for c in CHOICES}})
            s["clips"] += 1
            if r.get("choice") in CHOICES:
                s[r["choice"]] += 1
        total = {"clips": len(rows), **{c: sum(1 for r in rows if r.get("choice") == c) for c in CHOICES}}
        notes = [{"id": r.get("id"), "kind": r.get("kind"), "choice": r.get("choice"), "note": r["note"]}
                 for r in rows if r.get("note")]
        order = [s for s in SCENARIO_IDS if s in by] + [k for k in by if k not in SCENARIO_IDS]
        return {"by_scenario": [dict(by[k], id=k, name=_scenario_name(k)) for k in order], "total": total,
                "notes": notes}

    def export(self):
        rows = read_jsonl(self.feedback_path)
        path = os.path.join(self.bench_dir, self.now().strftime("tuner-feedback-%Y%m%d.md"))
        _atomic_write(path, export_markdown(rows, self.summary(), self.now()).encode("utf-8"))
        return {"path": path, "clips": len(rows)}


def _scenario_name(sid):
    return next((s["name"] for s in SCENARIOS if s["id"] == sid), sid)


def _quote(text):
    if text is None:
        return "> (none)"
    if text == "":
        return "> (empty: nothing typed)"
    return "\n".join("> " + line if line else ">" for line in text.split("\n"))


def export_markdown(rows, summary, now):
    t = summary["total"]
    out = [f"# Vox Tuner feedback, {now.strftime('%Y-%m-%d %H:%M')}", "",
           f"{t['clips']} clips approved by the owner. Winner: old {t['old']}, new {t['new']}, forced {t['forced']}, "
           f"own version {t['edited']}. OLD = the frozen v1 prompt and guard; NEW = what Vox types today for the app; "
           "FORCED = today's cleanup with the neutral style even where Vox skips it (AI-agent scenarios only). "
           "No audio here; the clips are in the clips folder.", "", "## By scenario", "",
           "| Scenario | Clips | Old | New | Forced | Own version |", "|---|---|---|---|---|---|"]
    for s in summary["by_scenario"]:
        out.append(f"| {s['name']} | {s['clips']} | {s['old']} | {s['new']} | {s['forced']} | {s['edited']} |")
    out += ["", "## Notes", ""]
    out += [f"- {n['id']} ({_scenario_name(n['kind'])}, chose {n['choice']}): {n['note']}" for n in summary["notes"]] \
        or ["- (no notes)"]
    out += ["", "## Clips", ""]
    for r in rows:
        out += [f"### {r.get('id')}: {_scenario_name(r.get('kind'))} ({r.get('exe')}), {r.get('strength')}, "
                f"chose {r.get('choice')}", ""]
        for model, text in (r.get("stt") or {}).items():
            out += [f"Speech, {model}:", _quote(text), ""]
        out += ["Approved transcript:", _quote(r.get("ref_verbatim")), ""]
        for name in CANDIDATES:
            c = r.get(name)
            if c:
                how = "AI cleanup" if c.get("cleaned") else "no AI text (rules layer)"
                if c.get("rejected"):
                    how += ", guard rejected the AI answer"
                if c.get("error") and not c.get("rejected"):
                    how += f", {c['error']}"
                out += [f"{name.upper()} ({how}):", _quote(c.get("text")), ""]
        out += ["Final (approved):", _quote(r.get("ref_intended")), ""]
        if r.get("note"):
            out += [f"Note: {r['note']}", ""]
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------ HTTP

def make_handler(tuner, token, page, on_quit=None):
    routes = {
        ("GET", "/api/state"): lambda b: tuner.state(),
        ("GET", "/api/summary"): lambda b: tuner.summary(),
        ("POST", "/api/record/start"): lambda b: tuner.start(),
        ("POST", "/api/record/stop"): lambda b: tuner.stop(),
        ("POST", "/api/transcribe"): lambda b: tuner.transcribe(bool(b.get("force"))),
        ("POST", "/api/clean"): lambda b: tuner.clean(b.get("transcript"), b.get("scenario"), b.get("exe"),
                                                      b.get("strength")),
        ("POST", "/api/save"): lambda b: tuner.save(b.get("choice"), b.get("final"), b.get("transcript"), b.get("note"),
                                                    b.get("scenario"), b.get("exe"), b.get("strength")),
        ("POST", "/api/skip"): lambda b: tuner.skip(),
        ("POST", "/api/export"): lambda b: tuner.export(),
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "VoxTuner"
        sys_version = ""

        def log_message(self, fmt, *args):   # nothing is logged: request lines could carry texts
            pass

        def _send(self, status, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                 "connect-src 'self'; media-src blob:; img-src data:; base-uri 'none'; "
                                 "form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self):
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def _handle(self, method):
            if not self._host_ok():   # another site's name pointed at 127.0.0.1 (DNS rebinding)
                return self._send(403, {"error": "Forbidden."})
            path = urlparse(self.path).path
            if method == "GET" and path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            if not path.startswith("/api/"):
                return self._send(404, {"error": "Not found."})
            if not hmac.compare_digest(self.headers.get(TOKEN_HEADER, ""), token):
                return self._send(403, {"error": "This page's session token is missing or wrong. Open the address "
                                                 "the tuner printed in its window."})
            body = {}
            if method == "POST":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n < 0 or n > MAX_BODY:
                    return self._send(413, {"error": "Request too big."})
                if n:
                    try:
                        body = json.loads(self.rfile.read(n).decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        return self._send(400, {"error": "Unreadable request."})
                    if not isinstance(body, dict):
                        return self._send(400, {"error": "Unreadable request."})
            if (method, path) == ("GET", "/api/audio"):
                try:
                    return self._send(200, tuner.audio(), "audio/wav")
                except TunerError as e:
                    return self._send(e.status, {"error": e.message})
            if (method, path) == ("POST", "/api/quit"):
                self._send(200, {"ok": True})
                if on_quit:
                    threading.Thread(target=on_quit, daemon=True).start()
                return None
            fn = routes.get((method, path))
            if fn is None:
                return self._send(404, {"error": "Not found."})
            try:
                return self._send(200, fn(body))
            except TunerError as e:
                return self._send(e.status, {"error": e.message})
            except Exception as e:   # shown in the page; the clip stays in memory and in tuner-pending.wav
                return self._send(500, {"error": plain_error(e, tuner.keys)})
    return Handler


def make_server(tuner, port=0, token=None):
    """(server, url with the token, token): bound to 127.0.0.1 only. serve_forever() runs it."""
    token = token or secrets.token_urlsafe(24)
    with open(PAGE, "rb") as f:
        page = f.read()
    server = ThreadingHTTPServer(("127.0.0.1", port), None)
    server.daemon_threads = True
    server.RequestHandlerClass = make_handler(tuner, token, page, on_quit=server.shutdown)
    url = f"http://127.0.0.1:{server.server_address[1]}/?token={token}"   # a query: Windows may drop a #fragment
    return server, url, token


# ------------------------------------------------------------------ --demo: no microphone, no network

class DemoRecorder:
    """Stands in for the microphone: a soft tone as long as the "recording" lasted (1.5 to 8 s)."""

    def __init__(self, clock=time.perf_counter):
        self.clock, self.t0 = clock, None

    def start(self):
        self.t0 = self.clock()

    def stop(self):
        secs = min(8.0, max(1.5, self.clock() - (self.t0 or self.clock())))
        n = int(secs * core.SAMPLE_RATE)
        return b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / core.SAMPLE_RATE)))
                        for i in range(n))


DEMO_SAY = (
    ("um can you check why the login test fails on windows and fix it no wait just explain it first",
     "um can you check why the log in test fails on windows and fix it no wait just explain it first"),
    ("hi priya thanks for the update can you send me the march invoice by friday regards yuvraj",
     "hi priya thanks for the update can you send me the march invoice by friday regards yuvraj"),
    ("uh running late be there in ten minutes",
     "uh running late be there in 10 minutes"),
    ("note to self buy milk call the bank at ten thirty and renew the domain",
     "note to self by milk call the bank at ten thirty and renew the domain"),
)


def demo_stt(counter):
    def stt(cfg, pcm, model):
        pair = DEMO_SAY[counter["n"] % len(DEMO_SAY)]
        return pair[0] if model == DEFAULT_STT_MODELS[0] else pair[1]
    return stt


def demo_chat_reply(cfg, body, timeout=60, retry_timeouts=True):
    """A canned cleanup server: drops noises, capitalises, ends with a full stop; the v1 prompt forgets the weekday."""
    system, user = body["messages"][0]["content"], body["messages"][1]["content"]
    raw = user.split("<transcript>", 1)[-1].split("</transcript>", 1)[0].strip()
    words = [w for w in raw.split() if w.lower().strip(",.") not in ("um", "uh", "er")]
    text = " ".join(words)
    if "You clean up dictated text" in system:   # today's prompt
        text = re.sub(r"\b(monday|tuesday|wednesday|thursday|friday|windows|march|priya|yuvraj)\b",
                      lambda m: m.group(1).capitalize(), text)
    text = (text[:1].upper() + text[1:]).rstrip(".") + "."
    return text, "stop"


def _refuse_network(*a, **kw):
    raise core.ApiError(0, "demo mode sends nothing")


def demo_tuner(bench_dir, now=datetime.now):
    """A tuner with a fake microphone and canned speech and cleanup answers (real pipelines, no network): to try the page
    for free. Its clips go to their own folder."""
    core.chat_reply = demo_chat_reply   # the cleanup calls of both pipelines answer from the canned server
    core._post = _refuse_network        # and nothing can reach a real server
    cfg = dict(core.DEFAULT_CONFIG, api_key="", relay_proxy=False)
    counter = {"n": 0}
    stt = demo_stt(counter)
    tuner = Tuner(bench_dir, cfg, DemoRecorder(), stt=stt, demo=True, now=now,
                  warnings=["Demo mode: a fake microphone and canned answers, nothing is sent anywhere. Clips go to "
                            + bench_dir + "."])
    real_save = tuner.save

    def save(*a, **kw):
        out = real_save(*a, **kw)
        counter["n"] += 1
        return out
    tuner.save = save
    real_skip = tuner.skip

    def skip():
        counter["n"] += 1
        return real_skip()
    tuner.skip = skip
    return tuner


# ------------------------------------------------------------------ main

def real_tuner(bench_dir, args):
    from bench_cleanup import provider_settings, server_problem
    from bench_record import MicRecorder
    cfg = core.load_config()
    stt_cfg = provider_settings(cfg, args.provider, "stt") if args.provider else cfg
    warnings = [p for p in (server_problem(stt_cfg, "stt", args.provider),
                            server_problem(cfg, "llm", None)) if p]
    device = cfg.get("input_device", "")
    recorder = MicRecorder(device, say=lambda t: warnings.append(t))
    models = [m.strip() for m in args.stt_models.split(",") if m.strip()] or list(DEFAULT_STT_MODELS)
    return Tuner(bench_dir, cfg, recorder, models=models, stt_cfg=stt_cfg, warnings=warnings,
                 mic=getattr(recorder, "name", "") or "default")


def main(argv=None, open_browser=webbrowser.open):
    clips.safe_console()
    ap = argparse.ArgumentParser(description="Vox Tuner: speak, compare the old and new cleanup, approve one.")
    ap.add_argument("--stt-models", default=",".join(DEFAULT_STT_MODELS),
                    help="comma-separated speech models (default: %(default)s)")
    ap.add_argument("--provider", help="speech server preset (default: the one set in Vox)",
                    choices=[p["id"] for p in providers.PRESETS if p["base_url"]])
    ap.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: a free one)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--bench-dir", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    base = os.path.join(core.data_dir(), "bench")
    if args.demo:
        tuner = demo_tuner(args.bench_dir or os.path.join(base, "tuner-demo"))
    else:
        tuner = real_tuner(args.bench_dir or base, args)
    server, url, _ = make_server(tuner, args.port)
    print("Vox Tuner" + (" (demo: nothing is sent anywhere)" if args.demo else "") + " is running at:")
    print("  " + url)
    print("Keep this window open while you use the page. Quit in the page or Ctrl+C here stops it.")
    print(f"Approved clips: {tuner.bench_dir}")
    for w in tuner.warnings:
        print("Note: " + w)
    if not args.no_browser:
        open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    if tuner.clip is not None:
        print("A recording was not saved or skipped: it is kept and comes back next time.")
    print("Vox Tuner stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
