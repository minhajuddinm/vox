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
import listen as listen_mod
import improve
import logo
import notes
import paste as paste_mod
import relay_host
import session as session_mod
import streaming
import sync
import timing as timing_mod
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


def key_vk(key):
    """Windows virtual-key code of a pynput key (a letter, digit or F key), or None. The code, not the char: with
    Ctrl held the char of N is a control character, and it changes between the press and the release."""
    return getattr(key, "vk", None) or getattr(getattr(key, "value", None), "vk", None)


ICONS = {k: logo.draw(64, k) for k in ("idle", "rec", "busy")}
ICONS["listen"] = ICONS["rec"]   # keep listening is a recording as far as the tray icon goes


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
    timing = None   # the timing.Timing of the recording in progress (the Speed card); None when there is none
    listening = None   # the listen.Listening of the keep-listening session in progress (also while it saves); None when none
    note_hotkey = None   # the session.NoteHotkey of the note shortcut; None when it is off or unusable
    note_key_down = False   # its main key is held (a key repeat must not toggle again)

    def __init__(self):
        self.cfg = core.load_config()
        self.cfg_mtime = self._mtime()
        self.hotkey = self._hotkey()
        self.note_hotkey = self._note_hotkey()
        self.pressed = set()
        self.recording = False
        self.busy = False
        self.pending = None           # (pcm, exe, note) of a dictation that could not be sent; kept for Retry
        self._rec_lock = threading.Lock()
        self.chunks = []
        self.stream = None
        self.target = ""
        self.started_at = 0.0
        self.state = "idle"   # read by the overlay: idle | rec | busy | listen
        self.level = 0.0
        self.overlay = None
        self.flash_kind = ""
        self.flash_until = 0.0
        self.hands_free = False
        self.streaming = None         # StreamingStt for the current recording (long ones are sent in pieces)
        self.note_mode = False        # the current recording is a voice note: saved, not pasted
        self.timing = None
        self.listening = None
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
                pystray.MenuItem(lambda _: "Stop listening" if self.listening else "Start listening", self.toggle_listening),
                pystray.MenuItem(lambda _: "%s (%s)" % ("Stop listening" if self.listening else "Start a note",
                                                        self.note_hotkey and self.note_hotkey.label),   # None: hidden below
                                 self.toggle_note_listening, visible=lambda _: self.note_hotkey is not None),
                pystray.MenuItem("Keep listening: Note", lambda *_: self.set_listen_target("note"), radio=True,
                                 checked=lambda _: session_mod.listen_target(self.cfg) == "note"),
                pystray.MenuItem("Keep listening: Type", lambda *_: self.set_listen_target("type"), radio=True,
                                 checked=lambda _: session_mod.listen_target(self.cfg) == "type"),
                pystray.MenuItem("Recover listening session", self.recover_listening, visible=lambda _: self.can_recover()),
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

    def _note_hotkey(self):
        hk, problem = session_mod.note_hotkey(self.cfg)
        if problem:
            log.warning("note shortcut %r is off: %s", self.cfg.get("note_hotkey"), problem)
        return hk

    def reload_if_changed(self):
        m = self._mtime()
        if m != self.cfg_mtime:
            self.cfg_mtime = m
            self.cfg = core.load_config()
            self.hotkey = self._hotkey()
            self.note_hotkey = self._note_hotkey()
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
        if self.listening:   # the session saves its note first, as with the meeting above
            self.stop_listening()
            deadline = time.time() + 180
            while self.listening and time.time() < deadline:
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

    def _save_setting(self, key, value):
        """Saves one setting to the file (and to our copy). False, after telling the user, when it cannot be saved."""
        try:
            cfg = core.load_config()   # the file, not our copy: the window may have saved settings since we read it
            cfg[key] = value
            core.save_config(cfg)
        except Exception as e:
            log.exception("could not save %s", key)
            self.notify(f"Could not save the setting: {e}")
            return False
        self.cfg[key] = value
        return True

    # ----------------------------------------------------------------- relay
    def start_relay(self):
        self.relay.port = relay_host.port_from(self.cfg)
        return self.relay.start()

    def toggle_relay(self, *_):
        """Tray item "Run relay on this PC": saves the choice (relay_run) and starts or stops the relay process."""
        on = not self.cfg.get("relay_run")
        if not self._save_setting("relay_run", on):
            return
        if on:
            self.start_relay()
        else:
            self.relay.stop()

    # --------------------------------------------------------------- hotkey
    # Hold the shortcut to talk, release to insert.
    # Double-tap it to keep listening (a note, or text typed as you pause, see listen.py); double-tap again, Esc or the
    # stop phrase ends it.
    def combo_down(self):
        return all(self.pressed & group for group in self.hotkey)

    def on_press(self, key):
        hk = self.note_hotkey
        if hk and key_vk(key) == hk.vk:   # the note shortcut's main key: never kept in `pressed` (its char varies)
            if not self.note_key_down and all(self.pressed & KEY_ALIASES[m] for m in hk.mods):
                self.note_key_down = True
                self.toggle_note_listening()
            return
        self.pressed.add(key)
        if key == keyboard.Key.esc and self.listening:
            self.stop_listening()   # ends it and saves what was said: audio is never thrown away
            return
        if key == keyboard.Key.esc and self.recording and self.hands_free:
            self.cancel()
            return
        if self.combo_down() and not self.combo_was_down:
            self.combo_was_down = True
            self.on_combo_down()

    def on_release(self, key):
        hk = self.note_hotkey
        if hk and key_vk(key) == hk.vk:
            self.note_key_down = False
            return
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
        if self.listening:   # one press could be part of another shortcut (Ctrl+Win+arrows): ending takes a double press
            if now - self.last_tap_t < DOUBLE_TAP_GAP:
                self.last_tap_t = 0.0
                self.stop_listening()
            else:
                self.last_tap_t = now
            return
        if self.recording and self.hands_free:
            self.hands_free = False
            self.stop()
            return
        if not self.recording:
            double = now - self.last_tap_t < DOUBLE_TAP_GAP
            self.press_t = now
            if double:
                self.last_tap_t = 0.0
                self.start_listening()
            else:
                self.start()

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
        tm = timing_mod.Timing()
        tm.mark("key_down")
        if not self._ready():
            return
        self.target = foreground_app()
        self.chunks = []
        self.started_at = time.time()
        self.streaming = streaming.StreamingStt(self.cfg) if self.cfg.get("stream_stt", True) else None
        if self.streaming:
            self.streaming.start()
        core.warm(self.cfg)   # open the server connections while the user speaks
        try:
            self._open_mic(self._audio)
            tm.mark("rec_start")
        except Exception as e:
            self.notify(f"Microphone error: {e}")
            self.flash("error")
            if self.streaming:
                self.streaming.cancel()
                self.streaming = None
            return
        self.timing = tm
        self.recording = True
        self.set_state("rec")
        log.info("recording started (app=%s)", self.target)

    def _ready(self):
        """False, after telling the user, when a settings problem stops any recording."""
        problem = core.endpoint_error(self.cfg) or (
            "Add your API key in Vox > Settings" if core.key_missing(self.cfg) else "")
        if problem:
            self.notify(problem)
            self.flash("error")
            open_window()
        return not problem

    def _open_mic(self, callback):
        """Starts the microphone (the chosen one when it is connected); `callback` gets every block. Raises on failure."""
        device = audio_devices.input_index(self.cfg.get("input_device"))
        if self.cfg.get("input_device") and device is None:
            self.notify("Your chosen microphone is not connected. Using the Windows default one.")
        self.stream = sd.InputStream(samplerate=core.SAMPLE_RATE, channels=1, dtype="int16",
                                     device=device, callback=callback)
        self.stream.start()

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
            self.timing = None
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
        tm, self.timing = self.timing, None
        if tm:
            tm.mark("key_up")
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
        threading.Thread(target=self._process, args=(pcm, self.target, note, streamer, tm), daemon=True).start()

    def toggle_note(self, *_):
        """Starts a voice note, or finishes the one being recorded (tray menu, window). The text is saved as a
        note instead of being pasted. Esc cancels; the dictation hotkey also finishes it."""
        if self.recording and self.note_mode:
            self.stop()
            return
        if self.recording or self.busy or self.listening:
            return
        self.note_mode = True
        self.start()
        if self.recording:
            self.hands_free = True   # keeps recording until finished
        else:
            self.note_mode = False

    def save_note(self, text, raw, secs):
        """Saves a voice note, asks the sync thread to send it and says so."""
        saved = notes.add(text, raw=raw, secs=secs, source=notes.SOURCE_NOTE, device=sync.device_name(self.cfg))
        self.sync.trigger()
        self.notify("Note saved: " + saved["title"])

    def retry_last(self, *_):
        """Sends again the last recording that could not be sent."""
        if self.busy or self.recording or self.listening or self.pending is None:
            return
        pcm, exe, note = self.pending
        self.busy = True
        self.set_state("busy")
        threading.Thread(target=self._process, args=(pcm, exe, note), daemon=True).start()

    def _process(self, pcm, exe, note=False, streamer=None, tm=None):
        secs = len(pcm) / (core.SAMPLE_RATE * 2)
        keep = " Your recording is kept: tray icon > Retry last dictation."
        try:
            label = "" if note else exe
            with core.timing_scope(tm):   # the network steps mark stt_start/stt_done and llm_start/llm_done on tm
                if streamer:
                    if tm:
                        tm.mark("stt_start")   # only the last piece is still to be sent
                    raw_streamed = streamer.finish()   # None: not cut into pieces, or it failed
                    if tm and raw_streamed is not None:
                        tm.mark("stt_done")
                else:
                    raw_streamed = None
                if raw_streamed is not None:
                    res = core.process_text(self.cfg, raw_streamed, label, label)
                else:
                    res = core.process_detailed(self.cfg, pcm, label, label)
            raw, text = res.raw, res.text
            outcome = ""   # what the pill shows once the result is in; set only when something was sent or saved
            self.pending = None
            if res.fidelity_fallback:
                log.warning("fidelity guard: the cleanup answer lost the spoken words, used the raw words (%d words)", len(raw.split()))
            if res.cleanup_error:
                self.notify(("Cleanup did not work, so Vox saved your words as spoken: " if note else "Cleanup did not work, so Vox pasted your words as spoken: ") + res.cleanup_error[:120])
            if text and note:
                self.save_note(text, raw, secs)
                outcome = "sent"
            elif text:
                outcome = "sent" if self.paste(text) else "error"   # the pill reflects the paste only
                if tm:
                    tm.mark("inserted")
                if self.cfg.get("keep_history", True):
                    try:
                        entry = {
                            "t": time.time(), "app": exe, "raw": raw, "text": text,
                            "words": len(text.split()), "secs": round(secs, 1),
                            **({"fidelity_fallback": True} if res.fidelity_fallback else {}),
                        }
                        if tm:   # where the time went, kept with the dictation (local only, see the Speed card)
                            entry["timing"] = tm.entry(**core.timing_info(self.cfg))
                        core.add_history(entry)
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

    # ------------------------------------------------------------ keep listening
    # The session (listen.py) does the work; these are the engine's side: the microphone, the pill state, the setting.
    def start_listening(self, target=None):
        """Double press: keeps listening to a Note or typed pieces (setting listen_target, or `target`) until it is stopped."""
        if self.recording or self.busy or self.listening or not self._ready():
            return
        target = target or session_mod.listen_target(self.cfg)
        try:
            buf = session_mod.SessionBuffer(session_mod.buffer_dir(), target)   # the audio on disk, for a crash
        except OSError:
            log.exception("could not open the listening buffer, going on without a copy of the audio")
            buf = None
        lis = listen_mod.Listening(self, self.cfg, target, buf)
        self.target = lis.exe   # Engine.paste checks the window against it
        core.warm(self.cfg)
        try:
            self._open_mic(lis.audio)
        except Exception as e:
            self.notify(f"Microphone error: {e}")
            self.flash("error")
            if buf:
                buf.discard()
            return
        self.listening = lis
        self.set_state("listen")
        lis.start()
        log.info("keep listening started (target=%s, app=%s)", target, lis.exe)

    def stop_listening(self, *_):
        """Ends the session: the rest of the audio is sent, then the note is saved (or the typed text is done)."""
        if self.listening:
            self.listening.stop()

    def toggle_listening(self, *_):
        if self.listening:
            self.stop_listening()
        elif session_mod.listen_target(self.cfg) == "type":   # the foreground window now is the taskbar, not the app
            self.notify("Type needs the app you are typing into: click in it and double-press the shortcut to start.")
        else:
            self.start_listening()

    def toggle_note_listening(self, *_):
        """The note shortcut and its tray entry: one press starts a note (a session with the target Note whatever
        the setting says), the next ends it and saves it. Waits while a session is saving."""
        if self.busy:
            return
        if self.listening:
            self.stop_listening()
        else:
            self.start_listening("note")

    def close_mic(self):
        self._close_stream()

    def listen_state(self, name):
        """Called by the session: "busy" while it finishes (hotkey and tray wait), "idle" when it is over."""
        self.busy = name == "busy"
        if name == "idle":
            self.listening = None
        self.set_state(name)

    def set_listen_target(self, target):
        """Tray menu "Keep listening: Note / Type"."""
        self._save_setting("listen_target", target)

    def can_recover(self):
        return not (self.listening or self.busy or self.recording) and bool(session_mod.recoverable(session_mod.buffer_dir()))

    def recover_listening(self, *_):
        """Turns the audio of a session that did not finish (a crash, a failed send) into a note."""
        found = session_mod.recoverable(session_mod.buffer_dir()) if self.can_recover() else []
        if not found:
            return
        path = found[0]["path"]

        def remove():
            try:
                os.remove(path)
            except OSError:
                pass

        lis = self.listening = listen_mod.Listening(self, self.cfg, "note", after=remove)
        self.listen_state("busy")
        lis.start()
        lis.replay(session_mod.load_pcm(path))
        self.notify("Recovering your listening session. The note appears when it is done.")

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

    def check_improve_reminder(self, now=None):
        """The weekly "Improve my cleanup" reminder: a tray message once a week while the switch is on. It never runs the
        improvement and sends nothing; turning the switch on only starts the week."""
        now = time.time() if now is None else now
        action = improve.remind_action(self.cfg, now)
        if not action:
            return
        cfg = core.load_config()   # the file, not our copy: the window may have saved settings since we read it
        cfg["improve_remind_last"] = now
        core.save_config(cfg)
        self.cfg["improve_remind_last"] = now
        if action == "remind":
            self.notify("It is time to look at Improve my cleanup (Vox > Settings). Nothing is sent until you press Run once and confirm.")

    def _watch_improve(self):
        while True:
            try:
                self.check_improve_reminder()
            except Exception:
                log.exception("improve reminder failed")
            time.sleep(3600)

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
        threading.Thread(target=self._watch_improve, daemon=True, name="improve").start()
        self.sync.start()
        self.icon.run_detached()
        log.info("engine started, hotkey=%s", self.cfg.get("hotkey"))
        if self.cfg.get("relay_run"):
            self.start_relay()
        if core.key_missing(self.cfg) or core.endpoint_error(self.cfg):
            open_window()
        if session_mod.recoverable(session_mod.buffer_dir()):
            self.notify("Vox found a listening session that did not finish. Tray icon > Recover listening session.")
        try:
            self.overlay = Overlay(self)
        except Exception:
            log.exception("overlay failed to start; running without it")
            threading.Event().wait()
        try:
            self.overlay.run()  # Tk must own the main thread
        except KeyboardInterrupt:   # Ctrl+C in the terminal: quit properly instead of running on without the pill
            self.quit()
