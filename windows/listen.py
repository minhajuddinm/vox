"""Keep listening (the double-press mode), the running part: the microphone audio goes through a `ListenSession`
(session.py) that cuts it at pauses, each piece is turned into text in the background (streaming.piece_text), and the
text goes to the chosen target.

  Note target: when the session ends the whole transcript is cleaned in chunks (the fidelity guard applies to each
               chunk, which falls back to the spoken words when the answer lost them) and saved as one note.
  Type target: each piece is cleaned like a dictation and typed as it arrives, only while the focused app is the one
               the session started in; in any other window typing pauses and the text is kept for a note.

The engine (engine.py) owns the microphone and the pill and passes itself as `host`. Four threads, none of them the
audio callback (which only queues bytes): feed (cuts audio, writes the crash-safe buffer), stt (one piece after the
other, so the context is in order), type (Type target only) and the caller of `audio`/`stop`.
"""
import logging
import queue
import threading

import paste as paste_mod
import session as session_mod
import streaming
import timing as timing_mod
import vox_core as core

log = logging.getLogger("vox")

PAUSED = "Paused: wrong window"
RECOVER = "tray icon > Keep listening > Recover listening session"
MIC_SILENT_SECONDS = 5   # a live microphone delivers a block every ~0.1 s, even in silence: this long without one, it is dead
MIC_LOST = ("The microphone stopped sending sound (unplugged, taken by another app, or the PC slept). "
            "Listening ended and what you said before is saved.")
REPLAY_BLOCK = 32000   # bytes per queue item when saved audio is played back into a session (1 s)


def clean_note(cfg, text):
    """The transcript cleaned chunk by chunk (session.chunk_text) and put back together. Each chunk goes through
    the normal pipeline, so the guard falls back to that chunk's spoken words when the answer lost them."""
    out = ""
    for joiner, chunk in session_mod.chunk_text(text):
        cleaned = core.process_text(cfg, chunk, "", "").text
        if cleaned:
            out += (joiner if out else "") + cleaned
    return out


class Listening:
    """One running session. `host` offers notify(msg), paste(text) -> bool, save_note(text, raw, secs),
    close_mic(), listen_state("busy" | "idle") and flash(kind). `focus` gives the focused app's exe name.
    `after` runs when the session ended with nothing lost (the recovery of an old session removes its file)."""

    def __init__(self, host, cfg, target, buffer=None, focus=None, after=None):
        self.host, self.cfg, self.target, self.buffer, self.after = host, cfg, target, buffer, after
        self._focus = focus or paste_mod.SystemDeps().foreground_exe
        self.exe = self._window()
        self.session = session_mod.ListenSession(target)
        self.paused = False
        self.timings, self.latency_ms = [], []   # per piece: the Timing (seg_end, seg_text) and the wait in ms
        self.done = threading.Event()
        self._lock = threading.Lock()            # guards the session, which the feed and stt threads both use
        self._q, self._stt_q, self._out = queue.Queue(), queue.Queue(), queue.Queue()
        self._typer = None
        self._ctx = ""                           # the end of the text so far, the context of the next piece
        self._typed = False
        self._held = []                          # (spoken, cleaned) of the pieces that were not typed
        self._failed = False
        self._halted = False
        self._mic_lost = False

    # ------------------------------------------------------------ what the pill shows
    @property
    def seconds(self):
        return self.session.seconds

    @property
    def message(self):
        return PAUSED if self.paused else self.session.warning

    # ----------------------------------------------------------------- control
    def start(self):
        self.session.start()
        if self.target == "type":
            self._typer = threading.Thread(target=self._type_loop, name="vox-listen-type", daemon=True)
            self._typer.start()
        threading.Thread(target=self._feed_loop, name="vox-listen-feed", daemon=True).start()
        threading.Thread(target=self._stt_loop, name="vox-listen-stt", daemon=True).start()

    def audio(self, indata, frames, t, status):
        """The microphone callback: only queues the bytes."""
        self._q.put(bytes(indata))

    def stop(self):
        """Ends the session: the microphone is closed, the rest of the audio is sent, then the note is saved."""
        self._halt()
        self._q.put(None)

    def replay(self, pcm):
        """Plays saved audio (a session that did not finish) into this session and ends it."""
        for i in range(0, len(pcm), REPLAY_BLOCK):
            self._q.put(pcm[i:i + REPLAY_BLOCK])
        self._q.put(None)

    def _halt(self):
        with self._lock:
            if self._halted:
                return
            self._halted = True
        self.host.close_mic()
        self.host.listen_state("busy")

    def _window(self):
        try:
            return self._focus()
        except Exception:   # a window that cannot be opened (an elevated app): unknown
            return ""

    # ------------------------------------------------------------------ threads
    def _feed_loop(self):
        s = self.session
        try:
            while True:
                try:
                    pcm = self._q.get(timeout=MIC_SILENT_SECONDS)
                except queue.Empty:   # no audio and no stop: end through the normal path so what was heard is saved
                    self._mic_lost, pcm = True, None
                    self.host.notify(MIC_LOST)
                if pcm is not None:
                    self._keep(pcm)
                with self._lock:
                    segs = s.stop() if pcm is None else s.feed(pcm)
                for seg in segs:
                    tm = timing_mod.Timing()
                    tm.mark("seg_end")
                    self.timings.append(tm)
                    self._stt_q.put((seg, tm))
                if pcm is None or s.seconds >= session_mod.MAX_SECONDS:
                    break
        except Exception:
            log.exception("keep listening: cutting the audio failed")
            self._failed = True
        finally:
            self._halt()
            self._stt_q.put(None)

    def _keep(self, pcm):
        if self.buffer:
            try:
                self.buffer.append(pcm)
            except OSError:
                log.exception("keep listening: could not write the audio buffer, going on without it")
                self.buffer = None

    def _stt_loop(self):
        s = self.session
        try:
            while True:
                item = self._stt_q.get()
                if item is None:
                    break
                seg, tm = item
                text = self._transcribe(seg)
                tm.mark("seg_text")
                self.latency_ms.append(tm.get("seg_text") - tm.get("seg_end"))
                log.info("keep listening: piece %d is text %d ms after it ended", seg.id, self.latency_ms[-1])
                with self._lock:
                    was = s.state
                    ready = s.on_text(seg.id, text)
                    heard_stop = was == "listening" and s.state == "stopping"   # the stop phrase, or the time limit
                if self._typer:
                    for t in ready:
                        self._out.put(t)
                if heard_stop:
                    self.stop()
        except Exception:
            log.exception("keep listening: the text of a piece could not be handled")
            self._failed = True
        finally:
            self._finish()

    def _transcribe(self, seg):
        try:
            text = streaming.piece_text(self.cfg, seg.pcm, self._ctx)
        except Exception as e:   # the server, the network: this piece is missing from the text, the audio is kept
            log.warning("keep listening: a piece could not be transcribed: %s", e)
            if not self._failed:
                self.host.notify("Part of what you said could not be sent (%s). Your audio is kept: %s." % (str(e)[:80], RECOVER))
            self._failed = True
            return ""
        if text:
            self._ctx = (self._ctx + " " + text).strip()[-streaming.CONTEXT_CHARS:]
        return text

    def _type_loop(self):
        while True:
            try:
                raw = self._out.get(timeout=1.0 if self.paused else None)
            except queue.Empty:   # paused: notice when the user is back in the chosen app
                self.paused = not session_mod.same_target(self.exe, self._window())
                continue
            if raw is None:
                return
            try:
                self._type(raw)
            except Exception:
                log.exception("keep listening: typing failed, the text is kept for a note")
                self._held.append((raw, raw))

    def _type(self, raw):
        text = core.process_text(self.cfg, raw, self.exe, self.exe).text   # cleanup_min_words and the guard apply
        if not text:
            return
        if session_mod.same_target(self.exe, self._window()) and self.host.paste((" " if self._typed else "") + text):
            self._typed, self.paused = True, False
            return
        if not self.paused:
            self.paused = True
            self.host.notify("Vox paused typing: the window changed. Go back to %s to carry on; what you say meanwhile "
                             "is kept for a note." % self.exe if self.exe else
                             "Vox paused typing: it could not tell which app you started in. What you say is kept for a note.")
        self._held.append((raw, text))

    # ------------------------------------------------------------------- the end
    def _finish(self):
        """Everything said is text: type what is left, save the note, report. Runs once, on the stt thread."""
        s = self.session
        kind = "error" if self._failed or self._mic_lost else "sent"
        try:
            if self._typer:
                self._out.put(None)
                self._typer.join()
            heard = s.text()
            if not heard:
                self.host.notify("Vox did not hear anything.")
                kind = "error"
            elif self.target == "note":
                try:
                    note = clean_note(self.cfg, heard)
                except Exception:   # a bug in the cleanup must not lose the note: save the spoken words
                    log.exception("keep listening: the cleanup failed, saving the spoken words")
                    note = heard
                self.host.save_note(note or heard, heard, s.seconds)
            elif self._held:
                self.host.save_note(" ".join(t for _, t in self._held), " ".join(r for r, _ in self._held), s.seconds)
                self.host.notify("What was not typed is saved as a note.")
        except Exception:
            log.exception("keep listening: could not finish the session")
            self._failed, kind = True, "error"
            self.host.notify("Vox could not save the listening session. Your audio is kept: %s." % RECOVER)
        finally:
            try:   # whatever happens to the audio file, the pill and the hotkey must be released below
                if self.buffer:
                    if self._failed:
                        self.buffer.close()
                    else:
                        self.buffer.discard()
                if self.after and not self._failed:
                    self.after()
            except Exception:
                log.exception("keep listening: could not tidy up the audio buffer")
            self.host.flash(kind)
            self.host.listen_state("idle")
            self.done.set()
