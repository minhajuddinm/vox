"""Vox Notetaker.

Records your mic ("You") and the PC's audio ("Others", everyone on the call) separately, so your own
lines are always labelled correctly. No bot joins the call.

Live:   speech is cut at natural pauses (6 to 20 s pieces) and transcribed right away with Whisper
        turbo, so the transcript trails the conversation by about 10 to 15 seconds. You can ask the
        AI about anything said so far ("what was Peyman saying two minutes ago?").
After:  the recorded speech is transcribed again with the more accurate Whisper large-v3 model,
        speakers are named from context and the calendar, and detailed notes are written.
"""
import difflib
import json
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime

import numpy as np
import requests

import vox_core as core

log = logging.getLogger("vox.meeting")

SR = 16000
FRAME = 480                 # 30 ms VAD frame
MIN_CHUNK = 6.0             # seconds; do not cut live pieces shorter than this
MAX_CHUNK = 20.0            # force a cut here even without a pause
PAUSE = 0.6                 # a pause this long ends a live piece
MIN_SPEECH = 0.5            # pieces with less speech than this are not sent
FINAL_PIECE = 480.0         # seconds of speech per request in the final pass (well under Groq's 25 MB)
STT_GAP = 3.2               # seconds between Whisper calls (Groq free plan: 20 per minute)
DEFAULT_NOTES_MODEL = "openai/gpt-oss-120b"
DEFAULT_FINAL_STT = "whisper-large-v3"
HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "you", "bye", "okay",
                  "subtitles by the amaraorg community", "please subscribe"}


def meetings_dir():
    d = os.path.join(core.data_dir(), "meetings")
    os.makedirs(d, exist_ok=True)
    return d


def notes_export_dir(cfg):
    d = cfg.get("notes_folder") or os.path.join(os.path.expanduser("~"), "Documents", "Vox Notes")
    os.makedirs(d, exist_ok=True)
    return d


def _llm(cfg, system, user, max_tokens=4096, effort="medium"):
    model = cfg.get("notes_model") or DEFAULT_NOTES_MODEL
    body = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    base = core.api_base(cfg, "llm")
    extra = {"reasoning_effort": effort, "include_reasoning": False} if core.providers.reasoning_params(cfg, base, model) else {}
    body.update(extra)
    r = core.post_with_retry(f"{base}/chat/completions", headers=core.auth_headers(cfg, "llm"),
                   json=body, timeout=240, via_relay=core.providers.uses_relay(cfg))
    if extra and r.status_code in (400, 422):
        core.providers.remember_rejected(base, model)
        for k in extra:
            body.pop(k, None)
        r = core.post_with_retry(f"{base}/chat/completions", headers=core.auth_headers(cfg, "llm"),
                       json=body, timeout=240, via_relay=core.providers.uses_relay(cfg))
    return core.sanitize(core.providers.strip_think(core.check_response(r, core.providers.uses_relay(cfg))["choices"][0]["message"].get("content", "")))


# --------------------------------------------------------------------- prompts

NOTES_PROMPT = """You write detailed meeting notes from a transcript. Each line is "[mm:ss] Speaker: text". {me} is the note taker (always correct). Other names were inferred from context and may be marked "(likely)"; "Others" means an unidentified participant. The transcript comes from speech recognition, so fix obvious mis-hearings using context, but never invent content.
{context}
Write Markdown with exactly these sections:

# <short specific title, max 8 words>

## Summary
One paragraph of 4 to 8 sentences: purpose of the meeting, what was discussed, what was concluded, what happens next.

## Discussion
For each topic discussed, in order: a "### <topic>" heading, then 2 to 6 bullets with the substance: who said what, numbers, dates, options considered, concerns raised, and where it landed. Keep names, amounts, dates and technical terms exactly.

## Decisions
Bullets, each a decision that was actually made. Write "None recorded" if there were none.

## Action items
Checkbox bullets: "- [ ] Owner: task (due date if mentioned)". Include every commitment ("I'll send...", "can you...", "let's..."). Use {me} for the note taker. Write "None recorded" if there were none.

## Open questions
Bullets of questions raised and not resolved. Write "None" if there were none.

## Next steps
Bullets: follow-up meetings, deadlines, what each person is waiting on. Write "None" if there were none.

## Who said what
One bullet per speaker: "**Name**: their main points, proposals and commitments in 1 to 3 sentences". Keep "(likely)" for inferred names. Skip speakers with nothing substantive.

Rules: use only what is in the transcript. Do not invent facts, owners or dates. No preamble. Plain, direct wording.
Spell these names and terms exactly: {terms}."""

PART_PROMPT = """You condense one part of a long meeting transcript into detailed notes for a later summary. Keep every topic, decision, number, date, name, commitment ("X will..."), question and disagreement, with the [mm:ss] time where it happened. Bullets only, no preamble."""

ATTRIBUTE_PROMPT = """You label speakers in a meeting transcript. Lines tagged "You" are the note taker ({me}) and are always correct. Lines tagged "Others" come from the other participants through the computer's audio, possibly several different people.
{context}
For every line numbered in OTHERS, decide who most likely said it, using: people addressing each other by name ("thanks, Sara"), introductions, replies to a question aimed at someone, the attendee list, and topic continuity. Use a name from the attendee list when you can. If you cannot tell, use "Unknown".

Return only JSON: {{"speakers": {{"<line number>": "<name or Unknown>", ...}}}} with one entry for every OTHERS line number."""

LIVE_ASK_PROMPT = """You help someone during a live meeting. They may have zoned out and want to catch up. Answer their question using only the transcript so far. Lines are "[mm:ss] Speaker: text"; "You" is the person asking; "Others" is everyone else on the call. The meeting is now at {now}, so "two minutes ago" means around {two_ago}.
{context}
Answer in 1 to 5 short sentences or bullets. Quote key phrases when useful and give the [mm:ss] time they were said. If the transcript does not contain the answer, say so. No preamble."""

MEETING_ASK_PROMPT = """You answer questions about one meeting using its notes and transcript below. Lines are "[mm:ss] Speaker: text". Answer in a few sentences or bullets and give the [mm:ss] time for each fact. If the meeting does not contain the answer, say so. No preamble."""


# ------------------------------------------------------------------ recording

class _Source(threading.Thread):
    """Records one device, cuts speech into pieces at natural pauses, and keeps every speech piece on disk
    (raw 16 kHz int16) for the final high-accuracy pass."""

    def __init__(self, who, make_recorder, meeting):
        super().__init__(daemon=True, name=f"rec-{who}")
        self.who = who
        self.make_recorder = make_recorder
        self.meeting = meeting
        self.error = None
        self.level = 0.0
        self.speaking = False
        self.pieces = []            # [(start_seconds, byte_offset, n_samples)] of speech saved to disk
        self.raw_path = os.path.join(meeting.folder(), f"{who.lower()}.raw")
        self.samples = 0            # samples read so far
        self.t0 = 0.0               # meeting time of sample 0

    def run(self):
        noise = 0.004
        buf, buf_start, speech_frames, silence_run = [], None, 0, 0.0
        try:
            with self.make_recorder() as rec, open(self.raw_path, "wb") as raw:
                self.t0 = self.meeting.elapsed()
                while self.meeting.active:
                    block = rec.record(numframes=SR // 10)[:, 0].astype(np.float32)   # 100 ms
                    n = len(block)
                    if not n:
                        continue
                    for i in range(0, n - FRAME + 1, FRAME):
                        fr = block[i:i + FRAME]
                        rms = float(np.sqrt(np.mean(fr * fr)))
                        # slowly track background noise so the threshold adapts to each room and device
                        noise = min(max(noise * 0.995 + rms * 0.005 if rms < noise * 3 else noise * 1.0005, 0.0015), 0.05)
                        voiced = rms > max(0.006, noise * 3.0)
                        self.level = min(1.0, rms * 12)
                        self.speaking = voiced
                        if buf_start is None:
                            buf_start = self.samples + i
                        buf.append(fr)
                        if voiced:
                            speech_frames += 1
                            silence_run = 0.0
                        else:
                            silence_run += FRAME / SR
                    self.samples += n
                    dur = sum(len(b) for b in buf) / SR
                    if (dur >= MIN_CHUNK and silence_run >= PAUSE) or dur >= MAX_CHUNK:
                        self._emit(buf, buf_start, speech_frames, raw)
                        buf, buf_start, speech_frames, silence_run = [], None, 0, 0.0
                    elif not speech_frames and dur >= 2.0:
                        buf, buf_start, silence_run = [], None, 0.0   # drop pure silence early
                if buf:
                    self._emit(buf, buf_start, speech_frames, raw)
        except Exception as e:
            self.error = str(e)
            log.exception("%s recorder failed", self.who)
        finally:
            self.level = 0.0

    def _emit(self, buf, start_sample, speech_frames, raw):
        if speech_frames * FRAME / SR < MIN_SPEECH:
            return
        audio = np.concatenate(buf)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        start = self.t0 + start_sample / SR
        self.pieces.append((start, raw.tell(), len(audio)))
        raw.write(pcm)
        raw.flush()
        self.meeting.q.put((self.who, start, pcm))


# ---------------------------------------------------------------- text helpers

def _norm(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def _good(seg):
    """Drops Whisper's typical silence/noise hallucinations."""
    t = _norm(seg["text"])
    if not t or t in HALLUCINATIONS:
        return False
    if seg["compression"] > 2.4:                       # repetitive loops ("either the either the...")
        return False
    if seg["logprob"] < -1.0 and seg["no_speech"] > 0.5:
        return False
    if seg["logprob"] < -1.4:
        return False
    return True


def _dedupe_repeats(segs):
    out = []
    for s in segs:
        if out and _norm(out[-1]["text"]) == _norm(s["text"]):
            continue
        out.append(s)
    return out


def _similar(a, b):
    """True when two lines are the same words (mic echo of the call audio)."""
    a, b = _norm(a), _norm(b)
    if len(a) < 15 or len(b) < 15:
        return a == b and len(a) >= 6
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.8


class Meeting:
    def __init__(self, get_cfg):
        self.get_cfg = get_cfg
        self.active = False
        self.processing = False
        self.stage = ""
        self.id = None
        self.started = 0.0
        self.stopped_at = 0.0
        self.entries = []          # {"t": seconds, "who": "You"|"Others", "text": str, "name"?: str}
        self.q = queue.Queue()
        self.sources = []
        self.worker = None
        self.last_error = ""
        self.event = None          # calendar event dict or None
        self.qa = []               # live questions and answers
        self.lock = threading.Lock()
        self.ctl = threading.Lock()      # start and stop can come from the tray, the window and the calendar at once
        self._last_call = 0.0

    # ------------------------------------------------------------ status
    def elapsed(self):
        if not self.started:
            return 0.0
        return (self.stopped_at or time.time()) - self.started

    def folder(self):
        d = os.path.join(meetings_dir(), self.id)
        os.makedirs(d, exist_ok=True)
        return d

    def status(self):
        with self.lock:
            return {
                "active": self.active, "processing": self.processing, "stage": self.stage, "id": self.id,
                "title": (self.event or {}).get("title", ""), "attendees": (self.event or {}).get("attendees", []),
                "elapsed": round(self.elapsed()), "entries": list(self.entries), "error": self.last_error,
                "sources": {s.who: (s.error or "ok") for s in self.sources},
                "levels": {s.who: round(s.level, 3) for s in self.sources},
                "backlog": self.q.qsize(), "qa": list(self.qa),
            }

    # ------------------------------------------------------------- control
    def start(self, event=None):
        with self.ctl:
            return self._start(event)

    def _start(self, event=None):
        if self.active or self.processing:
            return False
        import soundcard as sc
        cfg = self.get_cfg()
        problem = core.endpoint_error(cfg) or ("Add your API key first" if core.key_missing(cfg) else "")
        if problem:
            self.last_error = problem
            return False
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.started = time.time()
        self.stopped_at = 0.0
        self.entries, self.qa = [], []
        self.last_error = ""
        self.stage = ""
        self.event = event
        self.q = queue.Queue()
        self.active = True

        def mic():
            return sc.default_microphone().recorder(samplerate=SR, channels=1)

        def system():
            spk = sc.default_speaker()
            return sc.get_microphone(id=str(spk.name), include_loopback=True).recorder(samplerate=SR, channels=1)

        self.sources = [_Source("You", mic, self), _Source("Others", system, self)]
        for s in self.sources:
            s.start()
        self.worker = threading.Thread(target=self._transcribe_loop, daemon=True, name="meeting-stt")
        self.worker.start()
        try:
            log.info("meeting %s started (%s); mic=%s speaker=%s", self.id, (event or {}).get("title", "no event"),
                     sc.default_microphone().name, sc.default_speaker().name)
        except Exception:
            pass
        return True

    def stop(self):
        """Stops recording; the slow final pass and notes run in a thread."""
        with self.ctl:
            return self._stop()

    def _stop(self):
        if not self.active:
            return False
        self.active = False
        self.stopped_at = time.time()
        self.processing = True
        self.stage = "Finishing the live transcript"
        threading.Thread(target=self._finish, daemon=True, name="meeting-finish").start()
        return True

    # ------------------------------------------------------- live transcript
    def _pace(self):
        wait = STT_GAP - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _context_prompt(self, who):
        """Whisper prompt: attendee names plus the last sentences from the same source."""
        names = ", ".join((self.event or {}).get("attendees", [])[:12])
        prev = " ".join(e["text"] for e in self.entries if e["who"] == who)[-500:]
        head = f"Meeting with {names}. " if names else ""
        return (head + prev).strip()

    def _stt(self, pcm, prompt, model=None):
        for attempt in range(4):
            try:
                self._pace()
                return core.transcribe_segments(self.get_cfg(), core.pcm_to_wav(pcm), prompt=prompt, model=model)
            except core.ApiError as e:
                self.last_error = "Rate limit, catching up..." if e.code == 429 else str(e)
                log.warning("stt failed (%s), retry %s", e, attempt)
                time.sleep(6 * (attempt + 1) if e.code == 429 else 3)
            except requests.RequestException as e:
                self.last_error = f"Network: {e}"
                log.warning("stt network error (%s), retry %s", e, attempt)
                time.sleep(3)
        return None

    def _transcribe_loop(self):
        while self.active or not self.q.empty() or any(s.is_alive() for s in self.sources):
            try:
                who, start, pcm = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            segs = self._stt(pcm, self._context_prompt(who))
            if segs is None:
                continue
            self.last_error = ""
            segs = _dedupe_repeats([s for s in segs if _good(s)])
            new = [{"t": round(start + s["start"]), "who": who, "text": s["text"]} for s in segs]
            if new:
                with self.lock:
                    self._add(new)
                self._save_transcript()

    def _add(self, new):
        """Adds entries and drops mic echo: when your mic picks up the other side through your speakers,
        the same words appear under You and Others. The Others copy is kept."""
        for e in new:
            near = [x for x in self.entries if abs(x["t"] - e["t"]) <= 15 and x["who"] != e["who"]]
            if e["who"] == "You" and any(_similar(e["text"], x["text"]) for x in near):
                continue
            if e["who"] == "Others":
                self.entries = [x for x in self.entries
                                if not (x["who"] == "You" and abs(x["t"] - e["t"]) <= 15 and _similar(x["text"], e["text"]))]
            if self.entries and self.entries[-1]["who"] == e["who"] and _norm(self.entries[-1]["text"]) == _norm(e["text"]):
                continue
            self.entries.append(e)
        self.entries.sort(key=lambda x: x["t"])

    # ----------------------------------------------------------- final pass
    def _final_pass(self):
        """Re-transcribes the recorded speech with Whisper large-v3 in long pieces (more context, fewer
        errors at piece edges). Falls back to the live transcript for any source that fails."""
        cfg = self.get_cfg()
        model = cfg.get("final_stt_model") or DEFAULT_FINAL_STT
        final = []
        for s in self.sources:
            live = [e for e in self.entries if e["who"] == s.who]
            if not s.pieces or not os.path.exists(s.raw_path):
                final.extend(live)
                continue
            groups, cur, cur_len = [], [], 0
            for p in s.pieces:
                if cur and cur_len + p[2] > FINAL_PIECE * SR:
                    groups.append(cur)
                    cur, cur_len = [], 0
                cur.append(p)
                cur_len += p[2]
            if cur:
                groups.append(cur)
            got, ok = [], True
            with open(s.raw_path, "rb") as f:
                for gi, g in enumerate(groups, 1):
                    self.stage = f"Improving the transcript ({'your mic' if s.who == 'You' else 'call audio'} {gi}/{len(groups)})"
                    gap = b"\x00\x00" * int(SR * 0.4)
                    parts, spans, pos = [], [], 0.0     # spans map positions in the joined audio to meeting time
                    for (start, off, n) in g:
                        f.seek(off)
                        parts.append(f.read(n * 2))
                        parts.append(gap)
                        spans.append((pos, pos + n / SR, start))
                        pos += n / SR + 0.4
                    pcm = b"".join(parts)
                    prev = " ".join(e["text"] for e in got)[-300:]
                    segs = self._stt(pcm, prev or self._context_prompt(s.who), model=model)
                    if segs is None:
                        ok = False
                        break
                    for sg in _dedupe_repeats([x for x in segs if _good(x)]):
                        t = sg["start"]
                        span = next((sp for sp in spans if sp[0] <= t < sp[1] + 0.4), spans[-1])
                        got.append({"t": round(span[2] + max(0.0, t - span[0])), "who": s.who, "text": sg["text"]})
            final.extend(got if ok and got else live)
        with self.lock:   # status() reads the entries from other threads
            self.entries = []
            self._add(sorted(final, key=lambda e: e["t"]))

    # -------------------------------------------------------------- labels
    def _me(self):
        return (self.get_cfg().get("your_name") or "").strip() or "You"

    def _label(self, e):
        if e["who"] == "You":
            return self._me()
        name = e.get("name")
        return f"{name} (likely)" if name and name != "Unknown" else "Others"

    def transcript_text(self, since=0, named=True, entries=None):
        lines = []
        for e in (self.entries if entries is None else entries):
            if e["t"] >= since:
                who = self._label(e) if named else e["who"]
                lines.append(f"[{e['t'] // 60:02d}:{e['t'] % 60:02d}] {who}: {e['text']}")
        return "\n".join(lines)

    def _context(self):
        ev = self.event or {}
        parts = []
        if ev.get("title"):
            parts.append(f"Meeting: {ev['title']}.")
        if ev.get("attendees"):
            parts.append("Attendees (other than the note taker): " + ", ".join(ev["attendees"]) + ".")
        if ev.get("organizer"):
            parts.append(f"Organizer: {ev['organizer']}.")
        return ("\n" + " ".join(parts) + "\n") if parts else ""

    def attribute_speakers(self):
        """Guesses a name for every 'Others' line. Works in windows so long meetings fit."""
        cfg = self.get_cfg()
        if not any(e["who"] == "Others" for e in self.entries):
            return
        win = 220
        for w0 in range(0, len(self.entries), win):
            lo, hi = max(0, w0 - 30), min(len(self.entries), w0 + win)
            lines, others = [], []
            for i in range(lo, hi):
                e = self.entries[i]
                lines.append(f"{i}. {e['who']}: {e['text']}")
                if e["who"] == "Others" and i >= w0:
                    others.append(str(i))
            if not others:
                continue
            user = "TRANSCRIPT\n" + "\n".join(lines) + "\n\nOTHERS: " + ", ".join(others)
            try:
                raw = _llm(cfg, ATTRIBUTE_PROMPT.format(me=self._me(), context=self._context()), user, max_tokens=4000)
                m = re.search(r"\{.*\}", raw, re.S)
                data = json.loads(m.group(0)) if m else {}
                for k, v in (data.get("speakers") or {}).items():
                    i = int(k)
                    if 0 <= i < len(self.entries) and self.entries[i]["who"] == "Others":
                        self.entries[i]["name"] = str(v).strip()[:60] or "Unknown"
            except Exception:
                log.exception("speaker attribution failed for window %s", w0)

    # ------------------------------------------------------------ live AI
    def ask_live(self, question):
        """Answers a question about the meeting so far ("what was said about the budget?")."""
        question = (question or "").strip()
        if not question:
            return ""
        with self.lock:
            text = self.transcript_text(named=False)
        now = int(self.elapsed())
        if not text:
            answer = "Nothing has been transcribed yet. Text appears about 10 to 15 seconds after it is said."
        else:
            two = max(0, now - 120)
            system = LIVE_ASK_PROMPT.format(now=f"{now // 60:02d}:{now % 60:02d}", two_ago=f"{two // 60:02d}:{two % 60:02d}",
                                            context=self._context())
            try:
                answer = _llm(self.get_cfg(), system, text[-60000:] + f"\n\nQUESTION: {question}", max_tokens=1200, effort="low")
            except Exception as e:
                answer = f"Could not answer right now ({e})."
        with self.lock:
            self.qa.append({"t": now, "q": question, "a": answer})
        return answer

    def catch_up(self, minutes=10):
        return self.ask_live(f"What did I miss in the last {minutes} minutes? Topics, decisions, and anything asked of me.")

    # --------------------------------------------------------------- saving
    def _save_transcript(self):
        with open(os.path.join(self.folder(), "transcript.json"), "w", encoding="utf-8") as f:
            json.dump({"id": self.id, "started": self.started, "entries": self.entries, "qa": self.qa}, f, ensure_ascii=False)

    def _notes(self, cfg, transcript):
        terms = ", ".join(((self.event or {}).get("attendees", []) + core.dictionary_terms(cfg))[:150]) or "none"
        system = NOTES_PROMPT.format(terms=terms, me=self._me(), context=self._context())
        if len(transcript) <= 60000:
            return _llm(cfg, system, transcript, max_tokens=6000)
        # long meeting: condense parts first, then write the notes from the condensed parts
        lines, parts, cur = transcript.split("\n"), [], ""
        for ln in lines:
            if len(cur) + len(ln) > 40000:
                parts.append(cur)
                cur = ""
            cur += ln + "\n"
        if cur:
            parts.append(cur)
        condensed = []
        for i, p in enumerate(parts, 1):
            self.stage = f"Writing notes (part {i}/{len(parts)})"
            condensed.append(_llm(cfg, PART_PROMPT, p, max_tokens=3000))
        self.stage = "Writing notes"
        return _llm(cfg, system, "CONDENSED NOTES OF THE WHOLE MEETING, IN ORDER\n\n" + "\n\n".join(condensed),
                    max_tokens=6000)

    def _finish(self):
        cfg = self.get_cfg()
        try:
            for s in self.sources:
                s.join(timeout=5)
            if self.worker:
                self.worker.join(timeout=300)
            duration = round(self.elapsed())
            self._save_transcript()
            if cfg.get("final_pass", True) and any(s.pieces for s in self.sources):
                try:
                    self._final_pass()
                    self._save_transcript()
                except Exception:
                    log.exception("final pass failed; keeping the live transcript")
            if self.entries:
                self.stage = "Naming speakers"
                self.attribute_speakers()
                self._save_transcript()
            transcript = self.transcript_text()
            self.stage = "Writing notes"
            if transcript:
                try:
                    notes = self._notes(cfg, transcript)
                except Exception as e:
                    log.exception("notes failed")
                    notes = f"# Meeting {self.id}\n\nNotes could not be generated ({e}). The transcript is below."
            else:
                errs = "; ".join(f"{s.who}: {s.error}" for s in self.sources if s.error)
                notes = f"# Meeting {self.id}\n\nNo speech was captured." + (f" Recording error: {errs}" if errs else "")
            m = re.search(r"^#\s+(.+)$", notes, re.M)
            title = (self.event or {}).get("title") or (m.group(1).strip() if m else f"Meeting {self.id}")
            if m and (self.event or {}).get("title"):
                notes = notes.replace(m.group(0), f"# {title}", 1)
                m = re.search(r"^#\s+(.+)$", notes, re.M)
            when = datetime.fromtimestamp(self.started)
            who = ", ".join([self._me()] + (self.event or {}).get("attendees", []))
            header = f"*{when:%A %d %B %Y, %H:%M} · {max(1, round(duration / 60))} min · {who}*\n\n"
            body = notes.replace(m.group(0), m.group(0) + "\n\n" + header, 1) if m else header + notes
            body = re.sub(r"\n{3,}", "\n\n", body)
            full = body.rstrip() + "\n\n## Transcript\n\n" + (transcript or "_empty_") + "\n"
            with open(os.path.join(self.folder(), "notes.md"), "w", encoding="utf-8") as f:
                f.write(full)
            meta = {"id": self.id, "title": title, "started": self.started, "duration": duration,
                    "words": sum(len(e["text"].split()) for e in self.entries),
                    "attendees": (self.event or {}).get("attendees", [])}
            safe = re.sub(r'[<>:"/\\|?*]+', "", title)[:60].strip() or "Meeting"
            export = os.path.join(notes_export_dir(cfg), f"{when:%Y-%m-%d %H%M} {safe}.md")
            with open(export, "w", encoding="utf-8") as f:
                f.write(full)
            meta["export"] = export
            with open(os.path.join(self.folder(), "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f)
            if not cfg.get("keep_audio"):
                for s in self.sources:
                    try:
                        os.remove(s.raw_path)
                    except OSError:
                        pass
            log.info("meeting %s saved to %s (%s lines)", self.id, export, len(self.entries))
        except Exception:
            log.exception("finishing meeting failed")
        finally:
            self.stage = ""
            self.processing = False


def ask_meeting(cfg, mid, question):
    """Question about one saved meeting (its notes and transcript)."""
    text = read_notes(mid)
    if len(text) > 90000:   # keep the summary at the top and as much transcript as fits
        text = text[:25000] + "\n...\n" + text[-65000:]
    return _llm(cfg, MEETING_ASK_PROMPT, text + f"\n\nQUESTION: {question}", max_tokens=1500, effort="low")


def list_meetings():
    out = []
    for d in sorted(os.listdir(meetings_dir()), reverse=True):
        p = os.path.join(meetings_dir(), d, "meta.json")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    out.append(json.load(f))
            except ValueError:
                pass
    return out


def read_notes(mid):
    p = os.path.join(meetings_dir(), os.path.basename(mid), "notes.md")
    with open(p, encoding="utf-8") as f:
        return f.read()


def delete_meeting(mid):
    import shutil
    shutil.rmtree(os.path.join(meetings_dir(), os.path.basename(mid)), ignore_errors=True)


# ------------------------------------------------------------ meeting page data

def _folder_of(mid):
    return os.path.join(meetings_dir(), os.path.basename(mid))


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def detail(mid, cfg):
    """Everything the meeting page needs: meta, summary (without transcript), speaker-labelled lines, your notes."""
    d = _folder_of(mid)
    meta = _read_json(os.path.join(d, "meta.json"), {})
    notes = read_notes(mid)
    summary = notes.split("\n## Transcript", 1)[0].strip()
    # drop the title and the italic header line; the page shows them separately
    summary = re.sub(r"^#\s+.*\n+", "", summary, count=1)
    summary = re.sub(r"^\*[^\n]*\*\s*\n+", "", summary, count=1)
    tr = _read_json(os.path.join(d, "transcript.json"), {"entries": []})
    me = (cfg.get("your_name") or "").strip() or "You"
    lines = []
    for e in tr.get("entries", []):
        if e["who"] == "You":
            who, likely = me, False
        elif e.get("name") and e["name"] != "Unknown":
            who, likely = e["name"], True
        else:
            who, likely = "Others", False
        lines.append({"t": e["t"], "who": who, "me": e["who"] == "You", "likely": likely, "text": e["text"]})
    user = ""
    p = os.path.join(d, "my_notes.md")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            user = f.read()
    return {"meta": meta, "summary": summary, "lines": lines, "my_notes": user,
            "done": meta.get("done", []), "me": me}


def save_my_notes(mid, text):
    with open(os.path.join(_folder_of(mid), "my_notes.md"), "w", encoding="utf-8") as f:
        f.write(text)


def set_done(mid, index, done):
    p = os.path.join(_folder_of(mid), "meta.json")
    meta = _read_json(p, {})
    s = set(meta.get("done", []))
    (s.add if done else s.discard)(int(index))
    meta["done"] = sorted(s)
    _write_json(p, meta)


def rename(mid, title):
    p = os.path.join(_folder_of(mid), "meta.json")
    meta = _read_json(p, {})
    meta["title"] = title.strip()[:120] or meta.get("title", "")
    _write_json(p, meta)
    return meta["title"]


ASK_PROMPT = """You answer questions about the user's past meetings using only the meeting records below.
Each record starts with [M<n>] and the meeting title and date. Transcript lines look like "[mm:ss] Speaker: text".
Answer in a few sentences or bullets. After every fact, cite its source as [M<n> mm:ss] (or [M<n>] when it comes from the summary).
If the records do not contain the answer, say so plainly. No preamble."""


def _score(text, words):
    t = text.lower()
    return sum(t.count(w) for w in words)


def ask(cfg, question):
    """Search every meeting, send the most relevant ones to the notes model, return answer + sources."""
    words = [w for w in re.findall(r"[a-z0-9]{3,}", question.lower())
             if w not in {"the", "and", "did", "what", "who", "was", "were", "about", "with", "when", "that", "this", "have", "said", "say", "does"}]
    scored = []
    for m in list_meetings():
        try:
            text = read_notes(m["id"])
        except OSError:
            continue
        s = _score(m.get("title", ""), words) * 5 + _score(text, words)
        scored.append((s, m["started"], m, text))
    if not scored:
        return {"answer": "No meetings recorded yet.", "sources": []}
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    picked = [x for x in scored if x[0] > 0][:4] or scored[:2]   # nothing matched: use the latest two
    parts, sources = [], []
    for i, (_s, started, m, text) in enumerate(picked, 1):
        when = datetime.fromtimestamp(started).strftime("%a %d %b %Y %H:%M")
        parts.append(f"[M{i}] {m.get('title', '')} ({when})\n{text[:24000]}")
        sources.append({"ref": f"M{i}", "id": m["id"], "title": m.get("title", ""), "started": started})
    answer = _llm(cfg, ASK_PROMPT, "\n\n".join(parts) + f"\n\nQUESTION: {question}", max_tokens=1500)
    return {"answer": answer, "sources": sources}
