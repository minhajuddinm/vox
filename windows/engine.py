"""Background part of Vox: tray icon, global hotkey, recording, Groq pipeline, paste, overlay."""
import ctypes
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time

import numpy as np
import psutil
import pystray
import requests
import sounddevice as sd
from pynput import keyboard

import audio_devices
import logo
import notes
import paste as paste_mod
import relay_host
import streaming
import sync
import vox_core as core
import vcalendar
from meeting import Meeting
from overlay import Overlay

log = logging.getLogger("vox")

MIN_SECONDS = 0.4
MAX_SECONDS = 360
TAP_SECONDS = 0.3       # a press shorter than this is a tap
DOUBLE_TAP_GAP = 0.5    # second tap within this starts hands-free mode
# How long the pill shows a green check / a red ! (see Engine.flash). Keep equal to BubbleView.SENT_MS / ERROR_MS
# in android/src/com/minhaj/vox/BubbleView.java (tests/test_flash_constants.py checks it).
FLASH_SECONDS = {"sent": 0.7, "error": 1.8}

KEY_ALIASES = {
    "ctrl": {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r},
    "ctrl_l": {keyboard.Key.ctrl_l, keyboard.Key.ctrl},
    "ctrl_r": {keyboard.Key.ctrl_r},
    "cmd": {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r},
    "alt": {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr},
    "alt_r": {keyboard.Key.alt_r, keyboard.Key.alt_gr},
    "shift": {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r},
    "space": {keyboard.Key.space},
}


def foreground_app():
    """Exe name of the focused window (never its title, which can hold private text)."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name()
    except Exception:
        return ""


ICONS = {k: logo.draw(64, k) for k in ("idle", "rec", "busy")}


def window_command():
    """Command that opens the main window (same program, --window)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--window"]
    here = os.path.dirname(os.path.abspath(__file__))
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    return [exe, os.path.join(here, "vox_app.py"), "--window"]


def open_window():
    subprocess.Popen(window_command(), close_fds=True)


class Engine:
    # Class-level defaults ONLY for tests that build an Engine with object.__new__ (no __init__); __init__ sets
    # the real values. flash_kind: "sent" | "error" | "" (what the pill signals for a moment, read by the
    # overlay); flash_until: time.monotonic() when flash_kind stops showing.
    overlay = None
    flash_kind = ""
    flash_until = 0.0

    def __init__(self):
        self.cfg = core.load_config()
        self.cfg_mtime = self._mtime()
        self.hotkey = self._hotkey()
        self.pressed = set()
        self.recording = False
        self.busy = False
        self.pending = None           # (pcm, exe, note) of a dictation that could not be sent; kept for Retry
        self._rec_lock = threading.Lock()
        self.chunks = []
        self.stream = None
        self.target = ""
        self.started_at = 0.0
        self.state = "idle"   # read by the overlay: idle | rec | busy
        self.level = 0.0
        self.overlay = None
        self.flash_kind = ""
        self.flash_until = 0.0
        self.hands_free = False
        self.streaming = None         # StreamingStt for the current recording (long ones are sent in pieces)
        self.note_mode = False        # the current recording is a voice note: saved, not pasted
        self.combo_was_down = False
        self.press_t = 0.0
        self.last_tap_t = 0.0
        self.meeting = Meeting(lambda: self.cfg)
        self.sync = sync.SyncWorker(lambda: self.cfg)
        self.relay = relay_host.RelayHost(relay_host.default_data_dir(), relay_host.port_from(self.cfg), notify=self.notify)
        self.kb = keyboard.Controller()
        self.icon = pystray.Icon(
            "Vox", ICONS["idle"], "Vox",
            menu=pystray.Menu(
                pystray.MenuItem("Open Vox", lambda *_: open_window(), default=True),
                pystray.MenuItem("Retry last dictation", self.retry_last, visible=lambda _: self.pending is not None),
                pystray.MenuItem(lambda _: "Finish voice note" if self.note_mode and self.recording else "New voice note",
                                 self.toggle_note),
                pystray.MenuItem(lambda _: "Stop meeting notes" if self.meeting.active else "Start meeting notes",
                                 self.toggle_meeting),
                pystray.MenuItem("Run relay on this PC", self.toggle_relay, checked=lambda _: bool(self.cfg.get("relay_run"))),
                pystray.MenuItem("Quit Vox", self.quit),
            ),
        )

    # ---------------------------------------------------------------- config
    def _mtime(self):
        try:
            return os.path.getmtime(core.config_path())
        except OSError:
            return 0

    def _hotkey(self):
        keys = [k for k in self.cfg.get("hotkey", ["ctrl", "cmd"]) if k in KEY_ALIASES]
        return [KEY_ALIASES[k] for k in keys] or [KEY_ALIASES["ctrl"], KEY_ALIASES["cmd"]]

    def reload_if_changed(self):
        m = self._mtime()
        if m != self.cfg_mtime:
            self.cfg_mtime = m
            self.cfg = core.load_config()
            self.hotkey = self._hotkey()
            log.info("settings reloaded, hotkey=%s", self.cfg.get("hotkey"))
            self.sync.trigger()   # a changed profile setting goes to the relay; a run with nothing new changes nothing

    def _watch_config(self):
        while True:
            time.sleep(1.0)
            try:
                if not self.recording:
                    self.reload_if_changed()
            except Exception:
                log.exception("config reload failed")

    # ------------------------------------------------------------------ misc
    def quit(self, *_):
        log.info("quit")
        m = self.meeting
        if m.active:
            m.stop()
        if m.processing:   # let the meeting notes finish saving instead of losing them
            self.notify("Saving your meeting notes before quitting...")
            deadline = time.time() + 180
            while m.processing and time.time() < deadline:
                time.sleep(0.5)
        self.sync.stop()
        self.relay.stop()
        self.icon.stop()
        if self.overlay:
            self.overlay.stop()
        try:
            os.remove(os.path.join(core.data_dir(), "engine.json"))   # holds the control token
        except OSError:
            pass
        os._exit(0)

    def notify(self, msg):
        log.info("notify: %s", msg)
        try:
            self.icon.notify(msg, "Vox")
        except Exception:
            pass

    def set_state(self, name):
        self.state = name
        if name != "rec":
            self.level = 0.0
        if name != "idle":
            self.flash_kind = ""   # a new recording or send replaces whatever the pill was signalling
        self.icon.icon = ICONS[name]

    def flash(self, kind):
        """Makes the pill show "sent" (green check, 0.7 s) or "error" (red !, 1.8 s), then go back to the real
        state. Only a signal: the state is unchanged and the tray balloon keeps the words. Does nothing without
        a pill. Any thread may call it; the overlay reads flash_kind and flash_until on the Tk thread."""
        seconds = FLASH_SECONDS[kind]
        if self.overlay is None:
            return
        self.flash_until = time.monotonic() + seconds   # the deadline before the kind
        self.flash_kind = kind

    def active_flash(self, now=None):
        """The flash the pill should show at `now` ("sent", "error", or "" when there is none or it has run out)."""
        kind = self.flash_kind
        if not kind:
            return ""
        return kind if (time.monotonic() if now is None else now) < self.flash_until else ""

    # ----------------------------------------------------------------- relay
    def start_relay(self):
        self.relay.port = relay_host.port_from(self.cfg)
        return self.relay.start()

    def toggle_relay(self, *_):
        """Tray item "Run relay on this PC": saves the choice (relay_run) and starts or stops the relay process."""
        on = not self.cfg.get("relay_run")
        try:
            cfg = core.load_config()   # the file, not our copy: the window may have saved settings since we read it
            cfg["relay_run"] = on
            core.save_config(cfg)
        except Exception as e:
            log.exception("could not save relay_run")
            self.notify(f"Could not save the relay setting: {e}")
            return
        self.cfg["relay_run"] = on
        if on:
            self.start_relay()
        else:
            self.relay.stop()

    # --------------------------------------------------------------- hotkey
    # Hold the shortcut to talk, release to insert.
    # Double-tap it for hands-free: recording continues until you press it once more (Esc cancels).
    def combo_down(self):
        return all(self.pressed & group for group in self.hotkey)

    def on_press(self, key):
        self.pressed.add(key)
        if key == keyboard.Key.esc and self.recording and self.hands_free:
            self.cancel()
            return
        if self.combo_down() and not self.combo_was_down:
            self.combo_was_down = True
            self.on_combo_down()

    def on_release(self, key):
        self.pressed.discard(key)
        if self.combo_was_down and not self.combo_down():
            self.combo_was_down = False
            self.on_combo_up()

    def on_combo_down(self):
        if any(self.pressed & KEY_ALIASES["cmd"]):
            # Tap an unassigned key so Windows does not open the Start menu when Win is released.
            self.kb.tap(keyboard.KeyCode.from_vk(0xE8))
        if self.busy:
            return
        now = time.time()
        if self.recording and self.hands_free:
            self.hands_free = False
            self.stop()
            return
        if not self.recording:
            double = now - self.last_tap_t < DOUBLE_TAP_GAP
            self.press_t = now
            self.start()
            if double and self.recording:
                self.hands_free = True
                self.last_tap_t = 0.0
                log.info("hands-free mode")

    def on_combo_up(self):
        if not self.recording or self.hands_free:
            return
        if time.time() - self.press_t < TAP_SECONDS:
            self.last_tap_t = time.time()   # a tap: wait for a possible second tap
            self.cancel()
        else:
            self.stop()

    # ------------------------------------------------------------ recording
    def start(self):
        problem = core.endpoint_error(self.cfg) or (
            "Add your API key in Vox > Settings" if core.key_missing(self.cfg) else "")
        if problem:
            self.notify(problem)
            self.flash("error")
            open_window()
            return
        self.target = foreground_app()
        self.chunks = []
        self.started_at = time.time()
        self.streaming = streaming.StreamingStt(self.cfg) if self.cfg.get("stream_stt", True) else None
        if self.streaming:
            self.streaming.start()
        core.warm(self.cfg)   # open the server connections while the user speaks
        try:
            device = audio_devices.input_index(self.cfg.get("input_device"))
            if self.cfg.get("input_device") and device is None:
                self.notify("Your chosen microphone is not connected. Using the Windows default one.")
            self.stream = sd.InputStream(samplerate=core.SAMPLE_RATE, channels=1, dtype="int16",
                                         device=device, callback=self._audio)
            self.stream.start()
        except Exception as e:
            self.notify(f"Microphone error: {e}")
            self.flash("error")
            if self.streaming:
                self.streaming.cancel()
                self.streaming = None
            return
        self.recording = True
        self.set_state("rec")
        log.info("recording started (app=%s)", self.target)

    def _audio(self, indata, frames, t, status):
        self.chunks.append(bytes(indata))
        if self.streaming:
            self.streaming.feed(indata)
        rms = float(np.sqrt(np.mean(np.square(indata.astype(np.float32))))) / 32768.0
        self.level = core.level_from_rms(rms)
        limit = MAX_SECONDS * (3 if self.hands_free else 1)
        if time.time() - self.started_at > limit:
            threading.Thread(target=self.stop, daemon=True).start()

    def _close_stream(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

    def _end_recording(self):
        """Atomically leaves the recording state; False when it had already ended (the audio callback and
        the hotkey can both ask to stop)."""
        with self._rec_lock:
            if not self.recording:
                return False
            self.recording = False
            self.hands_free = False
        self._close_stream()
        return True

    def cancel(self):
        """Discard the current recording."""
        self.note_mode = False
        if self._end_recording():
            self._drop_streaming()
            self.set_state("idle")

    def _drop_streaming(self):
        s, self.streaming = self.streaming, None
        if s:
            s.cancel()

    def stop(self):
        if not self._end_recording():
            return
        note, self.note_mode = self.note_mode, False
        streamer, self.streaming = self.streaming, None
        pcm = b"".join(self.chunks)
        if len(pcm) < core.SAMPLE_RATE * 2 * MIN_SECONDS:
            if streamer:
                streamer.cancel()
            self.set_state("idle")
            return
        if core.is_silent(pcm):
            if streamer:
                streamer.cancel()
            self.notify(f"Vox did not hear anything (loudest sound {core.peak_level(pcm)} of 32768). Check the microphone in Vox > Settings.")
            self.set_state("idle")
            self.flash("error")
            return
        self.busy = True
        self.set_state("busy")
        threading.Thread(target=self._process, args=(pcm, self.target, note, streamer), daemon=True).start()

    def toggle_note(self, *_):
        """Starts a voice note, or finishes the one being recorded (tray menu, window). The text is saved as a
        note instead of being pasted. Esc cancels; the dictation hotkey also finishes it."""
        if self.recording and self.note_mode:
            self.stop()
            return
        if self.recording or self.busy:
            return
        self.note_mode = True
        self.start()
        if self.recording:
            self.hands_free = True   # keeps recording until finished
        else:
            self.note_mode = False

    def retry_last(self, *_):
        """Sends again the last recording that could not be sent."""
        if self.busy or self.recording or self.pending is None:
            return
        pcm, exe, note = self.pending
        self.busy = True
        self.set_state("busy")
        threading.Thread(target=self._process, args=(pcm, exe, note), daemon=True).start()

    def _process(self, pcm, exe, note=False, streamer=None):
        secs = len(pcm) / (core.SAMPLE_RATE * 2)
        keep = " Your recording is kept: tray icon > Retry last dictation."
        try:
            label = "" if note else exe
            raw_streamed = streamer.finish() if streamer else None   # None: not cut into pieces, or it failed
            if raw_streamed is not None:
                res = core.process_text(self.cfg, raw_streamed, label, label)
            else:
                res = core.process_detailed(self.cfg, pcm, label, label)
            raw, text = res.raw, res.text
            outcome = ""   # what the pill shows once the result is in; set only when something was sent or saved
            self.pending = None
            if res.cleanup_error:
                self.notify(("Cleanup did not work, so Vox saved your words as spoken: " if note else "Cleanup did not work, so Vox pasted your words as spoken: ") + res.cleanup_error[:120])
            if text and note:
                saved = notes.add(text, raw=raw, secs=secs, source=notes.SOURCE_NOTE, device=sync.device_name(self.cfg))
                self.sync.trigger()
                self.notify("Note saved: " + saved["title"])
                outcome = "sent"
            elif text:
                outcome = "sent" if self.paste(text) else "error"   # the pill reflects the paste only
                if self.cfg.get("keep_history", True):
                    try:
                        core.add_history({
                            "t": time.time(), "app": exe, "raw": raw, "text": text,
                            "words": len(text.split()), "secs": round(secs, 1),
                        })
                    except Exception:   # the text already landed: log it, never flash error over "sent"
                        log.exception("could not save the history entry")
            if outcome:
                self.flash(outcome)
        except core.ApiError as e:
            log.error("api error: %s", e)
            self.pending = (pcm, exe, note)
            if e.code == 401:
                if core.providers.uses_relay(self.cfg):
                    self.notify(core.providers.explain(401, "llm", via_relay=True) + keep)
                else:
                    self.notify("The server rejected the API key. Check Vox > Settings." + keep)
            elif e.code == 429:
                self.notify("Rate limit reached. Try again shortly." + keep)
            else:
                self.notify(str(e) + keep)
            self.flash("error")
        except requests.RequestException as e:
            self.pending = (pcm, exe, note)
            self.notify(f"Network error: {e}." + keep)
            self.flash("error")
        except Exception:
            log.exception("processing failed")
            self.flash("error")
        finally:
            self.busy = False
            self.set_state("idle")

    def paste(self, text):
        """True when the text was pasted into the window; False when it only reached the clipboard (said so in a
        balloon). Does not flash: _process flashes from this result once everything else is done."""
        # paste.py checks the window is still the one the dictation started in, sends Ctrl+V, and restores the
        # old clipboard only when keep_clipboard is off and the clipboard still holds our text.
        if paste_mod.paste_text(text, self.target, self.cfg.get("keep_clipboard", False)) == paste_mod.COPIED:
            self.notify("Copied; the window changed")
            return False
        return True

    # -------------------------------------------------------------- meeting
    def _event(self, uid=None):
        """Calendar event by uid, or the one happening now."""
        try:
            events = vcalendar.fetch(self.cfg).get("events", [])
        except Exception:
            return None
        if uid:
            return next((e for e in events if e["uid"] == uid), None)
        return vcalendar.current_event(events)

    def start_meeting(self, uid=None, manual=None):
        ev = self._event(uid) if not manual else None
        if manual and (manual.get("title") or manual.get("attendees")):
            ev = {"uid": "", "title": (manual.get("title") or "").strip()[:120],
                  "attendees": [a.strip() for a in manual.get("attendees", []) if a.strip()][:30],
                  "organizer": "", "link": ""}
        if self.meeting.start(ev):
            what = f"'{ev['title']}'" if ev else "Meeting"
            self.notify(f"{what} notes started. Let others know you are recording.")
            return True
        self.notify(self.meeting.last_error or "Could not start meeting notes")
        return False

    def toggle_meeting(self, *_):
        if self.meeting.active:
            self.meeting.stop()
            self.notify("Meeting ended. Writing your notes...")
        else:
            self.start_meeting()

    def _watch_calendar(self):
        """Reminds you (or auto-starts notes) when a calendar meeting with other people begins."""
        reminded = set()
        while True:
            time.sleep(30)
            try:
                if not (self.cfg.get("calendar_url") or os.path.exists(os.path.join(core.data_dir(), "google_token.json"))) \
                        or self.meeting.active or self.meeting.processing:
                    continue
                now = time.time()
                for ev in vcalendar.fetch(self.cfg).get("events", []):
                    if not ev["attendees"] or ev["uid"] in reminded or not (ev["start"] - 60 <= now <= ev["start"] + 180):
                        continue
                    reminded.add(ev["uid"])
                    if self.cfg.get("auto_notes"):
                        self.start_meeting(ev["uid"])
                    else:
                        self.notify(f"'{ev['title']}' is starting. Tray icon > Start meeting notes, or open Vox.")
                    break
            except Exception:
                log.exception("calendar watch failed")

    # --------------------------------------------------- control server
    def _serve(self):
        """Local HTTP API used by the Vox window (127.0.0.1 only, random port, secret token)."""
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        token = secrets.token_hex(16)
        engine = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.headers.get("X-Vox-Token") != token:
                    return self._send(403, {"error": "forbidden"})
                m = engine.meeting
                try:
                    if self.path == "/meeting/start":
                        n = int(self.headers.get("Content-Length") or 0)
                        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                        ok = engine.start_meeting(body.get("uid"), body.get("manual"))
                        return self._send(200, {"ok": ok, "error": m.last_error})
                    if self.path == "/note/toggle":
                        engine.toggle_note()
                        return self._send(200, {"recording": engine.recording and engine.note_mode, "busy": engine.busy})
                    if self.path == "/note/status":
                        return self._send(200, {"recording": engine.recording and engine.note_mode, "busy": engine.busy})
                    if self.path == "/sync/now":
                        engine.sync.trigger()
                        return self._send(200, engine.sync.status())
                    if self.path == "/sync/status":
                        return self._send(200, engine.sync.status())
                    if self.path == "/meeting/stop":
                        return self._send(200, {"ok": m.stop()})
                    if self.path == "/meeting/status":
                        return self._send(200, m.status())
                    if self.path == "/meeting/catchup":
                        return self._send(200, {"text": m.catch_up()})
                    if self.path == "/meeting/ask":
                        n = int(self.headers.get("Content-Length") or 0)
                        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                        return self._send(200, {"text": m.ask_live(body.get("q", ""))})
                    return self._send(404, {"error": "unknown"})
                except Exception as e:
                    log.exception("control %s failed", self.path)
                    return self._send(500, {"error": str(e)})

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        with open(os.path.join(core.data_dir(), "engine.json"), "w", encoding="utf-8") as f:
            json.dump({"port": srv.server_address[1], "token": token, "pid": os.getpid()}, f)
        srv.serve_forever()

    # ------------------------------------------------------------------ run
    def run(self):
        listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        listener.daemon = True
        listener.start()
        threading.Thread(target=self._watch_config, daemon=True).start()
        threading.Thread(target=self._serve, daemon=True, name="control").start()
        threading.Thread(target=self._watch_calendar, daemon=True, name="calendar").start()
        self.sync.start()
        self.icon.run_detached()
        log.info("engine started, hotkey=%s", self.cfg.get("hotkey"))
        if self.cfg.get("relay_run"):
            self.start_relay()
        if core.key_missing(self.cfg) or core.endpoint_error(self.cfg):
            open_window()
        try:
            self.overlay = Overlay(self)
        except Exception:
            log.exception("overlay failed to start; running without it")
            threading.Event().wait()
        try:
            self.overlay.run()  # Tk must own the main thread
        except KeyboardInterrupt:   # Ctrl+C in the terminal: quit properly instead of running on without the pill
            self.quit()
