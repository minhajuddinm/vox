"""Background part of Vox: tray icon, global hotkey, recording, Groq pipeline, paste, overlay."""
import ctypes
import json
import logging
import os
import queue
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
import command as command_mod
import hotkeys
import correction_watch
import listen as listen_mod
import improve
import logo
import notes
import overlay_guard
import paste as paste_mod
import relay_host
import session as session_mod
import streaming
import sync
import timing as timing_mod
import vox_core as core
import warm_mic
import vcalendar
from meeting import Meeting, recover_unfinished
from overlay import Overlay

log = logging.getLogger("vox")

MIN_SECONDS = 0.4
MAX_SECONDS = 360
TAP_SECONDS = hotkeys.HOLD_SECONDS   # a press shorter than this is a tap
DOUBLE_TAP_GAP = 0.5    # second tap within this starts keep listening
STALE_GAP = 2.0         # no key event for this long: the keys we think are held are checked against the keyboard (R2-M2)
COMMAND_JOIN_SECONDS = 1.0   # the edit-by-voice key may join a dictation shortcut press this long after it went down
AUDIO_REFRESH_SECONDS = 30   # PortAudio's device list is rebuilt at most this often (issue 49, Windows 3)
# How long the pill shows a green check / a red ! (see Engine.flash). Keep equal to BubbleView.SENT_MS / ERROR_MS
# in android/src/com/minhaj/vox/BubbleView.java (tests/test_flash_constants.py checks it).
FLASH_SECONDS = {"sent": 0.7, "error": 1.8}
STUCK_MARGIN = 60       # a recording this long past its longest limit (hands-free) means the audio callback stopped
MAX_PENDING = 5         # failed recordings kept for Retry, oldest first (as Android's PendingQueue.MAX_KEPT)
QUIT_BUSY_WAIT = 120          # seconds Quit waits for a dictation that is still being sent (issue 63)
PASTE_RESTORE_QUIT_WAIT = 3.0   # seconds Quit waits for the old clipboard of the last paste to be put back
BUSY_TOLD_GAP = 5.0     # seconds between two "still sending" balloons for presses ignored while busy
# The once-a-second watchdog ran this late: Python was frozen for longer than Windows' keyboard hook timeout (at most 1 s
# since Windows 10 1709), and Windows removes a hook that times out without telling anyone, so it is installed again (ENG-2).
HOOK_STALL_SECONDS = 2.0
# AltGr reaches the hook as a Left Ctrl that Windows makes up (scan code 0x21D) and a Right Alt at the same moment: that
# Ctrl is not the user's, so AltGr is never Ctrl+Alt (ENG-6, issue 63). ALTGR_GAP: the most time between the two.
ALTGR_CTRL_SCAN = 0x21D
ALTGR_GAP = 0.02

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


def key_is_down(key):
    """Whether the keyboard says `key` is held now (GetAsyncKeyState). True when it cannot tell, so nothing is dropped."""
    vk = key_vk(key)
    if not vk:
        return True
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return True


def calendar_action(ev, cfg, now):
    """What the calendar watcher does for an event: "start" (record automatically), "remind" or None. Only a meeting
    you accepted (or cannot tell about) starts by itself; an invite you have not answered only reminds."""
    if not ev.get("attendees") or not (ev["start"] - 60 <= now <= ev["start"] + 180):
        return None
    if cfg.get("auto_notes") and ev.get("my_status", "accepted") == "accepted":
        return "start"
    return "remind"


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
    busy = False       # a recording is being sent (or a session saves): presses, Retry and recovery wait
    note_hotkey = None   # the session.NoteHotkey of the note shortcut; None when it is off or unusable
    note_key_down = False   # its main key is held (a key repeat must not toggle again)
    state_since = 0.0   # time.monotonic() when set_state last ran (the overlay watchdog's stuck-state check)
    chords = {}          # setting -> hotkeys.Chord of the extra shortcuts that are usable (hotkeys.check)
    chord_keys_down = frozenset()   # the extra shortcuts whose main key is held (a key repeat must not fire again)
    command_mode = False    # the recording in progress is an instruction for edit by voice
    command_was_down = False
    latched_t = 0.0      # when a tap latched hands-free dictation (hotkey_style hold_or_tap); 0 when it did not
    last_text = ""       # the last dictated text (paste-last and copy-last shortcuts); memory only
    event_t = None       # time.time() of the key event being handled (it may wait in the hotkey queue)
    last_key_t = 0.0     # time of the key event before it
    key_state = None     # key -> held now? (tests); None: key_is_down
    _hotkey_q = None     # key events wait here for the hotkey thread; None: handled at once (tests)
    _audio_refresh_t = float("-inf")
    _mic_missing_told = ""   # the chosen microphone we already said is missing
    _told_elevated = False
    warm = None          # warm_mic.WarmMic while the warm_mic setting is on (windows/warm_mic.py)
    _preroll = 0         # bytes at the front of this recording that came from before the key-down (warm microphone)

    def __init__(self):
        self.cfg = core.load_config()
        # None after a load that could not open config.json (issue 63): the next tick of _watch_config reads it again
        self.cfg_mtime = None if core.config_is_fallback() else self._mtime()
        self.hotkey = self._hotkey()
        self.note_hotkey = self._note_hotkey()
        self.chords = self._chords()
        self.pressed = set()
        self.recording = False
        self.busy = False
        self.pending = []             # [(pcm, exe, note)] of the dictations that could not be sent, oldest first; for Retry
        self._rec_lock = threading.Lock()
        self.chunks = []
        self.stream = None
        self.target = ""
        self.started_at = 0.0
        self.state = "idle"   # read by the overlay: idle | rec | busy | listen
        self.state_since = time.monotonic()
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
        self.kb = paste_mod.keyboard_controller()   # its keys carry VOX_KEY_TAG: the hook ignores them
        self.icon = pystray.Icon(
            "Vox", ICONS["idle"], "Vox",
            menu=pystray.Menu(
                pystray.MenuItem("Open Vox", lambda *_: open_window(), default=True),
                pystray.MenuItem(lambda _: self.retry_label(), self.retry_last, visible=lambda _: bool(self.pending)),
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

    def _chords(self):
        out = {}
        for name, (chord, problem) in hotkeys.check(self.cfg).items():
            if problem:
                log.warning("%s %r is off: %s", name, self.cfg.get(name), problem)
            elif chord:
                out[name] = chord
        return out

    def reload_if_changed(self):
        m = self._mtime()
        if m != self.cfg_mtime:
            cfg = core.load_config()
            if core.config_is_fallback():   # could not open it for a moment: keep what we have and try again next tick
                log.warning("settings not reloaded: config.json could not be opened, trying again")
                return
            self.cfg_mtime = m
            self.cfg = cfg
            self.hotkey = self._hotkey()
            self.note_hotkey = self._note_hotkey()
            self.chords = self._chords()
            log.info("settings reloaded, hotkey=%s", self.cfg.get("hotkey"))
            self.sync.trigger()   # a changed profile setting goes to the relay; a run with nothing new changes nothing

    def _watch_config(self):
        while True:
            time.sleep(1.0)
            try:
                if not self.recording:
                    self.reload_if_changed()
                    self._sync_warm()
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
        if self.busy:   # a dictation still being sent: let it land instead of losing its audio (issue 63)
            self.notify("Finishing your dictation before quitting...")
            deadline = time.time() + QUIT_BUSY_WAIT
            while self.busy and time.time() < deadline:
                time.sleep(0.2)
        paste_mod.wait_restored(PASTE_RESTORE_QUIT_WAIT)   # the old clipboard of the last paste is put back first
        if self.warm is not None:
            self.warm.close()   # the microphone is let go, and the audio it held with it
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

    def notify(self, msg, private=False):
        """A tray balloon. `private` texts (note and meeting titles) are shown but not written to the log."""
        log.info("notify: %s", "(private text not logged)" if private else msg)
        try:
            self.icon.notify(msg, "Vox")
        except Exception:
            pass

    def set_state(self, name):
        self.state_since = time.monotonic()
        self.state = name
        if name != "rec":
            self.level = 0.0
        if name != "idle":
            self.flash_kind = ""   # a new recording or send replaces whatever the pill was signalling
        try:
            self.icon.icon = ICONS[name]   # pystray can raise here (DestroyIcon); it is called from several threads
        except Exception:
            log.exception("could not change the tray icon")

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
            # the file, not our copy: the window may have saved settings since we read it (one locked step)
            core.update_config(lambda cfg: cfg.__setitem__(key, value))
        except Exception as e:
            log.exception("could not save %s", key)
            self.notify(f"Could not save the setting: {e}")
            return False
        self.cfg[key] = value
        return True

    # ------------------------------------------------------- overlay watchdog
    def _watch_overlay(self):
        stuck, dumped = overlay_guard.StuckWatch(), False
        while True:
            time.sleep(1.0)
            try:
                dumped = self.check_overlay(time.monotonic(), stuck, dumped)
            except Exception:
                log.exception("overlay watchdog failed")
            try:
                self.check_hook(time.monotonic())
            except Exception:
                log.exception("keyboard hook check failed")

    _listener = None      # the pynput keyboard.Listener (the low-level keyboard hook); None before run()
    _hook_check_t = None  # time.monotonic() of the last check_hook

    def install_hook(self):
        """Installs the keyboard hook (a new pynput listener), stopping the old one first."""
        old, self._listener = self._listener, None
        if old is not None:
            try:
                old.stop()
            except Exception:
                log.exception("could not stop the old keyboard hook")
        lis = keyboard.Listener(on_press=self.on_press, on_release=self.on_release, win32_event_filter=self._hook_filter)
        lis.daemon = True
        lis.start()
        self._listener = lis

    def check_hook(self, now):
        """Every second (the watchdog thread): installs the keyboard hook again when its listener stopped, or when this
        check itself ran HOOK_STALL_SECONDS late. Python was frozen then (a .NET property read, a long GIL hold), and
        Windows silently removes a hook that did not answer within its timeout; the shortcut would be dead until a
        restart. Installing it again when it was not removed costs nothing (ENG-2)."""
        last, self._hook_check_t = self._hook_check_t, now
        lis = self._listener
        if lis is None:
            return
        stalled = last is not None and now - last > HOOK_STALL_SECONDS
        if lis.is_alive() and not stalled:
            return
        log.warning("keyboard hook %s: installing it again", "may have been removed after %.1f s without Python" %
                    (now - last) if stalled else "stopped")
        self.install_hook()

    def check_overlay(self, now, stuck, dumped):
        """Once a second (daemon thread): logs every thread's stack once when the pill's Tk tick has not run for
        STALL_SECONDS while not idle, and ends a state that no longer matches the engine's flags (overlay_guard).
        Returns whether the current stall was already dumped."""
        ov, state = self.overlay, self.state
        last_tick = getattr(ov, "last_tick", 0.0) if ov is not None else 0.0
        if overlay_guard.tick_stalled(last_tick, now, state != "idle"):
            if not dumped:
                log.warning("overlay tick has not run for %.1f s (state %s); threads:\n%s", now - last_tick, state,
                            overlay_guard.thread_dump())
            dumped = True
        else:
            dumped = False
        since = self.state_since
        ok = overlay_guard.state_consistent(state, self.recording, self.busy, self.listening, now - since,
                                            MAX_SECONDS * 3 + STUCK_MARGIN)
        if stuck.update(state, since, ok, now) and self.state_since == since:
            log.warning("state %r stuck for %.0f s (recording=%s busy=%s listening=%s): ending it", state, now - since,
                        self.recording, self.busy, self.listening is not None)
            if state == "rec" and self.recording:
                self.stop()   # the audio callback stopped calling: end it as its time limit would
            else:
                self.set_state("idle")
        return dumped

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
    # Hold the shortcut to talk, release to insert. A quick tap: thrown away (hotkey_style classic) or hands-free
    # dictation until the next press (hold_or_tap). Double-tap it to keep listening (a note, or text typed as you pause,
    # see listen.py); double-tap again or say the stop phrase to end it and keep what was said. Esc cancels a recording
    # or a listening session without sending anything more. The extra shortcuts (hotkeys.py): hands-free, paste last,
    # copy last, edit by voice.
    # pynput calls on_press/on_release inside Windows' keyboard hook, which must answer quickly (LowLevelHooksTimeout,
    # R2-M1): they only queue the key, and the "vox-hotkey" thread does the work (opening the microphone can be slow).
    def combo_down(self):
        return all(self.pressed & group for group in self.hotkey)

    def _mods_down(self, mods):
        return all(self.pressed & KEY_ALIASES[m] for m in mods)

    def _command_down(self):
        c = self.chords.get("command_hotkey")
        return bool(c) and self._mods_down(c.mods)

    def _now(self):
        """The time of the key event being handled (it may have waited in the queue), else now."""
        return self.event_t if self.event_t is not None else time.time()

    _hook_held = frozenset()   # keys down as the keyboard hook saw them (hook thread only), for the Start-menu tap

    def _win_chord_held(self, keys):
        """True when `keys` hold Win and the dictation shortcut, or an edit shortcut that has Win in it."""
        if not any(keys & KEY_ALIASES["cmd"]):
            return False
        if self.hotkey and all(keys & group for group in self.hotkey):
            return True
        c = self.chords.get("command_hotkey") if getattr(self, "chords", None) else None
        return bool(c) and "cmd" in c.mods and self._mods_in(keys, c.mods)

    @staticmethod
    def _mods_in(keys, mods):
        return all(keys & KEY_ALIASES[m] for m in mods)

    def _hook_tap(self, key, down):
        """Inside the keyboard hook, while Win is still down: when this key completes a shortcut that holds Win, an
        unassigned key is tapped so that Windows does not open the Start menu when Win comes up. On the hotkey thread
        the tap could come after Win was already released (a slow microphone start in front of it). Never raises."""
        try:
            before = self._win_chord_held(self._hook_held)
            self._hook_held = self._hook_held | {key} if down else self._hook_held - {key}
            if down and not before and self._win_chord_held(self._hook_held):
                self.kb.tap(keyboard.KeyCode.from_vk(0xE8))
        except Exception:
            log.exception("start-menu tap failed")

    @staticmethod
    def _hook_filter(msg, data):
        """win32_event_filter of the keyboard listener, inside the hook (quick, never raises): False drops the event
        before Vox sees it (Windows still gets it). Drops the keys Vox sends itself (dwExtraInfo is
        paste.VOX_KEY_TAG: its paste keys and its Start-menu tap, so with the Ctrl+Shift preset Vox's own Ctrl+Shift+V
        does not start a recording, ENG-5) and the Left Ctrl that Windows makes up for AltGr. Keys other programs send
        (PowerToys remaps, a mouse button macro, Voice Access) still count: they are often how a user holds the shortcut."""
        try:
            if (data.dwExtraInfo or 0) == paste_mod.VOX_KEY_TAG:
                return False
            return not (data.vkCode in (0x11, 0xA2) and data.scanCode == ALTGR_CTRL_SCAN)
        except Exception:
            return True

    def on_press(self, key, injected=False):
        # pynput passes `injected` (sent by a program). It is not used: Vox's own keys never get here (_hook_filter), and
        # keys from other programs (a PowerToys remap, a mouse button macro) are the user's.
        self._hook_tap(key, True)
        q = self._hotkey_q
        if q is not None:
            q.put((True, key, time.time()))
            return
        try:
            self._on_press(key)
        except Exception:   # pynput stops the listener when a handler raises: keep the hotkey alive
            self._hotkey_failed()

    def on_release(self, key, injected=False):
        self._hook_tap(key, False)
        q = self._hotkey_q
        if q is not None:
            q.put((False, key, time.time()))
            return
        try:
            self._on_release(key)
        except Exception:
            self._hotkey_failed()

    def _hotkey_loop(self):
        """The hotkey thread: handles the queued key events in order, never inside the keyboard hook."""
        while True:
            down, key, t = self._hotkey_q.get()
            try:
                if down:
                    self._on_press(key, t)
                else:
                    self._on_release(key, t)
            except Exception:
                self._hotkey_failed()

    def _hotkey_failed(self):
        log.exception("hotkey handler failed")
        try:
            if getattr(self, "stream", None) is not None and not self.recording and not self.listening:
                self._close_stream()   # a half-started recording must not keep the microphone open
        except Exception:
            log.exception("could not close the microphone after a hotkey failure")

    def _check_gap(self):
        """Key-ups can get lost (Win+L locks the PC before the keys come up, a secure desktop, a sleeping PC), and a key
        left in `pressed` would complete the shortcut later by itself (R2-M2). After a quiet spell longer than STALE_GAP
        (a key that is really held repeats, so it sends events) the keys are checked against the keyboard."""
        t = self._now()
        last, self.last_key_t = self.last_key_t, t
        if not last or t - last <= STALE_GAP:
            return
        held = self.key_state or key_is_down
        stale = {k for k in self.pressed if not held(k)}
        if not stale:
            return
        log.info("hotkey: %d key(s) were not really held after a pause, forgotten", len(stale))
        self.pressed -= stale
        self.note_key_down, self.chord_keys_down = False, frozenset()
        if self.combo_was_down and not self.combo_down():
            self.combo_was_down = False
            self.on_combo_up()
        if self.command_was_down and not self._command_down():
            self.command_was_down = False
            self.on_command_up()

    def _keyed(self, vk):
        """The shortcuts with a main key whose key is `vk`: [(setting, chord)] (the note shortcut is "note_hotkey")."""
        if not vk:
            return []
        out = [("note_hotkey", self.note_hotkey)] if self.note_hotkey and self.note_hotkey.vk == vk else []
        return out + [(n, c) for n, c in self.chords.items() if c.vk is not None and c.vk == vk]

    combo_other_key = False   # another key went down while the dictation keys were held: that press was another shortcut
    _ctrl_l_t = float("-inf")  # when the Left Ctrl last went down (AltGr check)

    def _on_press(self, key, t=None):
        self.event_t = time.time() if t is None else t
        try:
            self._check_gap()
            if key == keyboard.Key.ctrl_l and key not in self.pressed:
                self._ctrl_l_t = self.event_t
            elif key in (keyboard.Key.alt_gr, keyboard.Key.alt_r) and keyboard.Key.ctrl_l in self.pressed                     and self.event_t - self._ctrl_l_t <= ALTGR_GAP:
                self.pressed.discard(keyboard.Key.ctrl_l)   # AltGr's made-up Left Ctrl, when the hook filter missed it
            if self.combo_was_down and not any(key in group for group in self.hotkey):
                self.combo_other_key = True
            keyed = self._keyed(key_vk(key))
            if keyed:   # the main key of a shortcut: never kept in `pressed` (its char varies with the modifiers)
                for name, chord in keyed:
                    if self._mods_down(chord.mods):
                        self._shortcut(name)
                        break
                return
            self.pressed.add(key)
            if key == keyboard.Key.esc and (self.listening or self.recording):
                self.cancel_any()
                return
            if self.combo_down() and not self.combo_was_down:
                self.combo_was_down, self.combo_other_key = True, False
                self.on_combo_down()
            if self._command_down() and not self.command_was_down:
                self.command_was_down = True
                self.on_command_down()
        finally:
            self.event_t = None

    def _on_release(self, key, t=None):
        self.event_t = time.time() if t is None else t
        try:
            self._check_gap()
            keyed = self._keyed(key_vk(key))
            if keyed:
                if any(n == "note_hotkey" for n, _ in keyed):
                    self.note_key_down = False
                self.chord_keys_down = self.chord_keys_down - {n for n, _ in keyed}
                return
            self.pressed.discard(key)
            if self.combo_was_down and not self.combo_down():
                self.combo_was_down = False
                self.on_combo_up()
            if self.command_was_down and not self._command_down():
                self.command_was_down = False
                self.on_command_up()
        finally:
            self.event_t = None

    def _shortcut(self, name):
        """A shortcut with a main key went down with its modifiers. A key repeat does nothing."""
        if name == "note_hotkey":
            if not self.note_key_down:
                self.note_key_down = True
                self.toggle_note_listening()
            return
        if name in self.chord_keys_down:
            return
        self.chord_keys_down = self.chord_keys_down | {name}
        if name == "hands_free_hotkey":
            self.on_hands_free()
        elif name == "paste_last_hotkey":
            self.insert_last(copy_only=False)
        elif name == "copy_last_hotkey":
            self.insert_last(copy_only=True)

    def on_combo_down(self):
        # The tap that keeps the Start menu closed is sent in the hook (_hook_tap), not here.
        if self.busy:
            self._say_busy()
            return
        now = self._now()
        if self.listening:   # one press could be part of another shortcut (Ctrl+Win+arrows): ending takes a double press
            if now - self.last_tap_t < DOUBLE_TAP_GAP:
                self.last_tap_t = 0.0
                self.stop_listening()
            else:
                self.last_tap_t = now
            return
        if self.recording and self.hands_free:
            if self.latched_t and now - self.latched_t < DOUBLE_TAP_GAP and not self.note_mode:
                # hold_or_tap: the tap that latched was the first of a double press, so keep listening instead
                self.latched_t = self.last_tap_t = 0.0
                self.cancel()
                self.start_listening()
                return
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

    _busy_told_t = float("-inf")

    def _say_busy(self):
        """A press while the last recording is still being sent does nothing: say so (at most every BUSY_TOLD_GAP s)."""
        now = time.monotonic()
        if now - self._busy_told_t >= BUSY_TOLD_GAP:
            self._busy_told_t = now
            self.notify("Vox is still sending the last recording. Press again when the pill is gone.")

    def on_combo_up(self):
        if not self.recording or self.hands_free:
            return
        now = self._now()
        action = hotkeys.tap_action(hotkeys.style(self.cfg), now - self.press_t, self.command_mode)
        if action == "stop":
            self.stop()
            return
        if self.combo_other_key:   # a short press with another key in it was another shortcut (Ctrl+Win+Left, ...): no tap
            self.cancel()
            return
        self.last_tap_t = now   # a tap: wait for a possible second tap
        if action == "latch":
            self.hands_free, self.latched_t = True, now   # keeps recording until the next press (the pill shows a square)
            log.info("hands-free dictation (tap)")
        else:
            self.cancel()

    def on_hands_free(self):
        """The hands-free shortcut (off by default; for example Ctrl+Win+H): starts hands-free dictation, latches the dictation that the
        held Ctrl+Win just started, or ends a hands-free one (what was said is sent)."""
        if self.busy or self.listening:
            return
        if self.recording:
            if self.hands_free:
                self.hands_free = False
                self.stop()
            elif not self.command_mode:
                self.hands_free = True
                log.info("hands-free dictation (shortcut)")
            return
        if self.combo_was_down:   # the held dictation keys already did their part: they just ended a dictation
            return
        self.start()
        if self.recording:
            self.hands_free = True

    def on_command_down(self):
        """The edit-by-voice keys are all down: the recording becomes (or starts as) an instruction."""
        if self.busy or self.listening:   # (the Start-menu tap was sent in the hook: _hook_tap)
            return
        now = self._now()
        if self.recording:
            if not (self.hands_free or self.note_mode or self.command_mode) and now - self.press_t < COMMAND_JOIN_SECONDS:
                self.command_mode = True
            return
        self.press_t = now
        self.start()
        if self.recording:
            self.command_mode = True

    def on_command_up(self):
        if self.recording and self.command_mode:
            self.stop()

    def cancel_any(self):
        """Esc: cancels the recording, or the listening session, without sending anything more. No flash: the pill
        simply goes away."""
        if self.listening:
            if not self.busy:
                self.listening.cancel()
            return
        if self.recording:
            self.cancel()
            log.info("recording cancelled (Esc)")

    # ------------------------------------------------------- last dictation
    def last_dictation(self):
        """The text of the last dictation: from memory, else the newest history entry (when history is kept)."""
        if self.last_text:
            return self.last_text
        if not self.cfg.get("keep_history", True):
            return ""
        try:
            for h in reversed(core.read_history()):
                if isinstance(h, dict) and isinstance(h.get("text"), str) and h["text"].strip():
                    return h["text"]
        except Exception:
            log.exception("could not read the history for the last dictation")
        return ""

    def insert_last(self, copy_only=False):
        """The paste-last and copy-last shortcuts. In a thread: the paste waits for the shortcut's keys to come up."""
        threading.Thread(target=self._insert_last, args=(copy_only,), daemon=True, name="vox-last").start()

    def _insert_last(self, copy_only):
        text = self.last_dictation()
        if not text:
            self.notify("Nothing to paste yet: dictate something first.")
            return
        try:
            if copy_only:
                paste_mod.wait_restored()   # a restore still to come would put the old clipboard over it
                paste_mod.SystemDeps().clip_set(text, self.cfg.get("clipboard_history", True))
                self.notify("Your last dictation is on the clipboard.")
                return
            if paste_mod.paste_text(text, "", self.cfg.get("keep_clipboard", False),
                                    clipboard_history=self.cfg.get("clipboard_history", True)) == paste_mod.BLOCKED:
                self._say_elevated()
        except Exception:
            log.exception("could not insert the last dictation")
            self.notify("Vox could not use the clipboard (another program has it open). Try again.")

    # ------------------------------------------------------------ recording
    def start(self):
        tm = timing_mod.Timing()
        t = self.event_t   # the key event's time.time(): it may have waited in the hotkey queue (ENG-10)
        tm.mark("key_down", None if t is None else (time.monotonic() - max(0.0, time.time() - t)) * 1000)
        if not self._ready():
            return
        self.target = foreground_app()
        self.chunks = []
        self.started_at = time.time()
        self.command_mode, self.latched_t = False, 0.0
        self.streaming = streaming.StreamingStt(self.cfg) if self.cfg.get("stream_stt", True) else None
        if self.streaming:
            self.streaming.start()
        core.warm(self.cfg)   # open the server connections while the user speaks
        try:
            self._begin_capture()
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
        name = self.cfg.get("input_device")
        device = audio_devices.input_index(name)
        if name and device is None and self._refresh_audio():   # plugged in after Vox started?
            device = audio_devices.input_index(name)
        if name and device is None:
            self._mic_missing(name)
        elif name:
            self._mic_missing_told = ""   # it is back: say so again the next time it goes missing
        try:
            self._start_stream(device, callback)
        except sd.PortAudioError:
            if self._refresh_audio():   # a replugged microphone has a new number
                self._start_stream(audio_devices.input_index(name), callback)
            elif device is not None:    # the list cannot be refreshed now: the Windows default microphone
                self._mic_missing(name)
                self._start_stream(None, callback)
            else:
                raise

    # ------------------------------------------------------ warm microphone (setting warm_mic, windows/warm_mic.py)
    def _sync_warm(self):
        """Keeps the warm microphone in line with the settings: open on the chosen microphone while `warm_mic` is on
        (reopened when the microphone changed or the stream died, a failed open is retried now and then), closed when off."""
        if not self.cfg.get("warm_mic"):
            if self.warm is not None:
                self.warm.close()
                self.warm = None
            return
        if self.warm is None:
            self.warm = warm_mic.WarmMic(self._open_warm_stream, self.notify)
        self.warm.ensure(self.cfg.get("input_device") or "")

    def _open_warm_stream(self, callback):
        """The stream the warm microphone keeps: the chosen microphone only (a missing one is for `_open_mic` to explain)."""
        name = self.cfg.get("input_device")
        device = audio_devices.input_index(name)
        if name and device is None:
            raise warm_mic.NotAvailable(name)
        stream = sd.InputStream(samplerate=core.SAMPLE_RATE, channels=1, dtype="int16", device=device, callback=callback)
        try:
            stream.start()
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise
        return stream

    def _begin_capture(self):
        """Feeds `_audio` from the warm microphone when it is ready (the last 400 ms before the key-down come first),
        else from a stream opened now, as always."""
        self._preroll = 0
        warm = self.warm
        if warm is not None and warm.attach(self._audio, prime=self._prime):
            return
        self._open_mic(self._audio)

    def _prime(self, held):
        self._preroll = len(held)
        self._audio(np.frombuffer(held, dtype=np.int16).reshape(-1, 1), None, None, None)

    def _mic_missing(self, name):
        """Says once (until the microphone is back) that the chosen microphone is not there."""
        if self._mic_missing_told != name:
            self._mic_missing_told = name
            self.notify("Your chosen microphone is not connected. Using the Windows default one.")

    def _start_stream(self, device, callback):
        stream = sd.InputStream(samplerate=core.SAMPLE_RATE, channels=1, dtype="int16", device=device, callback=callback)
        try:
            stream.start()
        except Exception:   # sounddevice has no __del__: a stream that did not start stays open unless closed (ENG-12)
            try:
                stream.close()
            except Exception:
                pass
            raise
        self.stream = stream

    def _refresh_audio(self):
        """PortAudio lists the devices once, when it starts. Starts it again so a microphone plugged in later shows up.
        Only while none of our streams is open, and at most once every AUDIO_REFRESH_SECONDS (restarting PortAudio takes
        time, and a missing microphone would otherwise restart it for every dictation); True when it was done."""
        now = time.monotonic()
        if self.recording or self.listening or now - self._audio_refresh_t < AUDIO_REFRESH_SECONDS                 or (self.warm is not None and (self.warm.is_open or self.warm.busy)):   # restarting PortAudio would kill the warm stream, or pull it from under its open (ENG-13)
            return False
        self._audio_refresh_t = now
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            log.exception("could not refresh the audio device list")
            return False
        return True

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
        if self.warm is not None and self.warm.detach():
            return   # the recording was fed by the warm microphone: it stays open for the next one
        stream = self.stream   # read once: a slow stop must not close a stream a newer recording opened meanwhile
        try:
            stream.stop()
            stream.close()
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
        self.note_mode = self.command_mode = False
        self.latched_t = 0.0
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
        command, self.command_mode = self.command_mode, False
        self.latched_t = 0.0
        streamer, self.streaming = self.streaming, None
        tm, self.timing = self.timing, None
        if tm:
            tm.mark("key_up")
        if command and streamer:   # an instruction is short: sent whole
            streamer.cancel()
            streamer = None
        pcm = b"".join(self.chunks)
        self.chunks = []   # up to 35 MB after a long recording: not kept until the next one (ENG-12)
        if len(pcm) - self._preroll < core.SAMPLE_RATE * 2 * MIN_SECONDS:   # the 400 ms before the key do not count
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
        if command:
            threading.Thread(target=self._process_command, args=(pcm, self.target), daemon=True).start()
            return
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
        self.notify("Note saved: " + saved["title"], private=True)

    def retry_label(self):
        """The tray item that sends a kept recording again; it says how many wait when there is more than one."""
        n = len(self.pending)
        return "Retry last dictation" if n <= 1 else "Retry dictation (%d waiting)" % n

    def retry_last(self, *_):
        """Sends again the oldest recording that could not be sent (ENG-1: each failed one is kept until it is delivered)."""
        if self.busy or self.recording or self.listening or not self.pending:
            return
        kept = self.pending[0]
        pcm, exe, note = kept
        self.busy = True
        self.target = exe   # the paste checks the window of that recording, not the one of the last recording started
        self.set_state("busy")
        threading.Thread(target=self._process, args=(pcm, exe, note, None, None, kept), daemon=True).start()

    def _keep(self, pcm, exe, note, kept):
        """Keeps a recording that could not be sent. A retried one (`kept`) goes to the back of the line, a new one is
        added; at most MAX_PENDING are kept (the oldest goes). A dictation that succeeds never clears another one."""
        if kept is not None:
            self.pending = [p for p in self.pending if p is not kept] + [kept]
            return
        self.pending = (self.pending + [(pcm, exe, note)])[-MAX_PENDING:]

    def _delivered(self, kept):
        """The retried recording `kept` (None: a new dictation) got through: only it leaves the kept ones."""
        if kept is not None:
            self.pending = [p for p in self.pending if p is not kept]

    def _process(self, pcm, exe, note=False, streamer=None, tm=None, kept=None):
        secs = len(pcm) / (core.SAMPLE_RATE * 2)
        keep = " Your recording is kept: tray icon > Retry last dictation."
        delivered = False   # the text was pasted or the note saved: nothing left to retry
        pieces = 0          # pieces sent to speech-to-text in the background (streaming.py)
        try:
            label = "" if note else exe
            with core.timing_scope(tm):   # the network steps mark stt_start/stt_done and llm_start/llm_done on tm
                if streamer:
                    if tm:
                        tm.mark("stt_start")   # only the last piece is still to be sent
                    raw_streamed = streamer.finish()   # None: not cut into pieces, or it failed
                    if raw_streamed is not None:
                        pieces = getattr(streamer, "pieces", 0)
                        log.info("streaming: %d pieces, %d sent while speaking", pieces, getattr(streamer, "early", 0))
                    if tm and raw_streamed is not None:
                        tm.mark("stt_done")
                else:
                    raw_streamed = None
                partial = streamer.partial() if raw_streamed is None and hasattr(streamer, "partial") else None
                if raw_streamed is not None:   # with the pieces' segment times, so pauses still make paragraphs
                    res = core.process_text(self.cfg, raw_streamed, label, label, segments=getattr(streamer, "segments", None))
                elif partial:   # a piece failed: keep the text before it, send only the rest (ENG-7)
                    said, done = partial
                    log.info("streaming: a piece failed, sending only the rest (%d of %d bytes)", len(pcm) - done, len(pcm))
                    raw = (said + " " + core.transcribe_rest(self.cfg, pcm[done:], said)).strip()
                    if tm:
                        tm.mark("stt_done")
                    res = core.process_text(self.cfg, raw, label, label)
                else:
                    res = core.process_detailed(self.cfg, pcm, label, label)
            raw, text = res.raw, res.text
            outcome = ""   # what the pill shows once the result is in; set only when something was sent or saved
            keep_pending = False
            if not text:   # silence phrases ("Thank you.") and empty answers are dropped: never without a word
                self.notify("Vox heard no usable words in that recording (a lone \"Thank you\" counts as silence). Speak a little longer, or check the microphone.")
                outcome = "error"
            if res.fidelity_fallback:
                log.warning("fidelity guard: the cleanup answer lost the spoken words, used the raw words (%d words)", len(raw.split()))
            if res.cleanup_error:
                self.notify(("Cleanup did not work, so Vox saved your words as spoken: " if note else "Cleanup did not work, so Vox pasted your words as spoken: ") + res.cleanup_error[:120])
            if text and note:
                self.save_note(text, raw, secs)
                delivered = True
                outcome = "sent"
            elif text:
                self.last_text = text   # for the paste-last and copy-last shortcuts (memory only)
                try:
                    outcome = "sent" if self.paste(text) else "error"   # the pill reflects the paste only
                    delivered = True
                except Exception:   # e.g. the clipboard stayed busy: the text is not lost, the recording is kept
                    log.exception("paste failed")
                    keep_pending = True
                    outcome = "error"
                    self.notify("Vox could not paste (the clipboard is busy)." + keep)
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
                            entry["timing"] = tm.entry(**core.timing_info(self.cfg), pieces=pieces,
                                                       upload=core.upload_format(self.cfg))
                        core.add_history(entry)
                    except Exception:   # the text already landed: log it, never flash error over "sent"
                        log.exception("could not save the history entry")
            if keep_pending:
                self._keep(pcm, exe, note, kept)
            else:
                self._delivered(kept)
                delivered = True
            if outcome:
                self.flash(outcome)
        except core.ApiError as e:
            log.error("api error: %s", e)
            self._keep(pcm, exe, note, kept)
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
            self._keep(pcm, exe, note, kept)
            self.notify(f"Network error: {e}." + keep)
            self.flash("error")
        except Exception as e:
            log.exception("processing failed")
            if not delivered:   # a dictation is never thrown away on an unexpected error
                self._keep(pcm, exe, note, kept)
                self.notify("Something went wrong (%s)." % type(e).__name__ + keep)
            self.flash("error")
        finally:
            self.busy = False
            self.set_state("idle")

    def paste(self, text):
        """True when the text was pasted into the window; False when it only reached the clipboard (said so in a
        balloon). Does not flash: _process flashes from this result once everything else is done."""
        # paste.py checks the window is still the one the dictation started in, sends Ctrl+V, and restores the
        # old clipboard only when keep_clipboard is off and the clipboard still holds our text. Every dictation goes
        # through here (hold-to-talk, the keep-listening Type target, a recovered session), so clipboard_history applies to all.
        # It returns once the paste keys are sent: the old clipboard comes back on a thread (ENG-3), and the next paste or
        # copy waits for it (paste.wait_restored).
        result = paste_mod.paste_text(text, self.target, self.cfg.get("keep_clipboard", False),
                                      clipboard_history=self.cfg.get("clipboard_history", True), background=True)
        if result == paste_mod.COPIED:
            self.notify("Copied; the window changed")
            return False
        if result == paste_mod.BLOCKED:
            self._say_elevated()
            return False
        correction_watch.arm(text, self.cfg, self.notify,   # Learn from my corrections: watch this field for the user's fixes
                             quiet=lambda: self.recording or self.listening is not None)   # no reads while recording (ENG-2)
        return True

    def _say_elevated(self):
        """The window in front runs as administrator and Vox does not (R2-M6): Windows drops keys sent to it, so the
        text was only put on the clipboard. The long explanation once, a short line after that."""
        if self._told_elevated:
            self.notify("Copied: the window runs as administrator. Press Ctrl+V.")
            return
        self._told_elevated = True
        self.notify("The window in front runs as administrator, so Windows does not let Vox paste into it. Your text "
                    "is on the clipboard: press Ctrl+V.")

    def _process_command(self, pcm, exe):
        """Edit by voice (command_hotkey, experimental): the selected text and the spoken instruction go to the cleanup
        server, and the answer is pasted over the selection. On any failure the selection is left as it was."""
        try:
            selection, problem = paste_mod.copy_selection(exe)
            if problem:
                self.notify(problem)
                self.flash("error")
                return
            instruction = core.transcribe(self.cfg, core.upload_audio(self.cfg, pcm))
            if not instruction or core.is_silence_hallucination(instruction):
                self.notify("Vox did not hear what to change. Your text is unchanged.")
                self.flash("error")
                return
            edited = command_mod.edit(self.cfg, selection, instruction)
            result = paste_mod.paste_text(edited, exe, self.cfg.get("keep_clipboard", False),
                                          clipboard_history=self.cfg.get("clipboard_history", True))
            if result == paste_mod.PASTED:
                log.info("edit by voice: applied (%d -> %d characters)", len(selection), len(edited))
                self.flash("sent")
                return
            if result == paste_mod.BLOCKED:
                self._say_elevated()
            else:
                self.notify("The window changed, so the edited text is only on the clipboard.")
            self.flash("error")
        except command_mod.Refused as e:
            log.info("edit by voice: refused (%s)", e)
            self.notify("Edit not applied: %s. Your text is unchanged." % e)
            self.flash("error")
        except core.ApiError as e:
            log.error("edit by voice: api error %s", e.code)
            self.notify("Edit not applied (%s). Your text is unchanged." % str(e)[:120])
            self.flash("error")
        except requests.RequestException as e:
            self.notify("Edit not applied (network error: %s). Your text is unchanged." % type(e).__name__)
            self.flash("error")
        except Exception as e:
            log.exception("edit by voice failed")
            self.notify("Edit not applied (%s). Your text is unchanged." % type(e).__name__)
            self.flash("error")
        finally:
            self.busy = False
            self.set_state("idle")

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

        try:   # read the audio first: the session's silence timer must not run while a slow read is still going
            pcm = session_mod.load_pcm(path)
        except OSError:
            log.exception("could not read the saved listening session")
            self.notify("Could not read the saved session; it is kept.")
            return
        lis = self.listening = listen_mod.Listening(self, self.cfg, "note", after=remove)
        self.listen_state("busy")
        lis.start()
        lis.replay(pcm)
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
            self.notify(f"{what} notes started. Let others know you are recording.", private=True)
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
                    action = calendar_action(ev, self.cfg, now)
                    if not action or ev["uid"] in reminded:
                        continue
                    reminded.add(ev["uid"])
                    if action == "start":
                        self.start_meeting(ev["uid"])
                    else:
                        self.notify(f"'{ev['title']}' is starting. Tray icon > Start meeting notes, or open Vox.", private=True)
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
        # the file, not our copy: the window may have saved settings since we read it (one locked step)
        core.update_config(lambda cfg: cfg.__setitem__("improve_remind_last", now))
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
        try:
            recover_unfinished(self.cfg)   # a meeting cut off by a quit or a crash gets its live transcript back
        except Exception:
            log.exception("could not recover unfinished meetings")
        self._hotkey_q = queue.Queue()   # from here on the hook only queues the keys (R2-M1)
        threading.Thread(target=self._hotkey_loop, daemon=True, name="vox-hotkey").start()
        self.install_hook()
        threading.Thread(target=self._watch_config, daemon=True).start()
        threading.Thread(target=self._serve, daemon=True, name="control").start()
        threading.Thread(target=self._watch_calendar, daemon=True, name="calendar").start()
        threading.Thread(target=self._watch_improve, daemon=True, name="improve").start()
        threading.Thread(target=self._watch_overlay, daemon=True, name="overlay-watch").start()
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
