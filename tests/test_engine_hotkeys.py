"""The engine's side of the shortcuts (task B1 and issue 38): tap or hold, the hands-free shortcut, Esc, paste and copy
last, edit by voice, the hotkey thread (R2-M1) and stale keys (R2-M2). Keys go through on_press/on_release as pynput
would call them; recording, the microphone and the clipboard are replaced. Needs the Windows runtime packages."""
import queue
import time

import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import engine as engine_mod  # noqa: E402
import hotkeys  # noqa: E402
import vox_core as core  # noqa: E402

Key = engine_mod.keyboard.Key
KeyCode = engine_mod.keyboard.KeyCode
SPACE, Z, X = Key.space, KeyCode.from_vk(0x5A, char="\x1a"), KeyCode.from_vk(0x58, char="\x18")


class InlineThread:
    def __init__(self, target=None, args=(), daemon=None, name=None, **kw):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    e = object.__new__(engine_mod.Engine)
    e.cfg = {"keep_history": False, "hotkey": ["ctrl_l", "cmd"], "note_hotkey": ""}
    e.recording = e.busy = e.hands_free = e.note_mode = False
    e.listening, e.pending, e.streaming, e.timing, e.target = None, None, None, None, ""
    e.pressed, e.last_tap_t, e.press_t, e.combo_was_down = set(), 0.0, 0.0, False
    e.hotkey = [engine_mod.KEY_ALIASES["ctrl"], engine_mod.KEY_ALIASES["cmd"]]
    e.note_hotkey = None
    e.chords = e._chords()
    e.calls, e.messages, e.states, e.flashes = [], [], [], []
    e.notify = lambda m, private=False: e.messages.append(m)
    e.set_state = e.states.append
    e.flash = e.flashes.append
    e.kb = type("K", (), {"tap": lambda self, k: e.calls.append("no-start-menu")})()
    e._rec_lock = engine_mod.threading.Lock()

    def start():
        e.calls.append("start")
        e.recording, e.command_mode, e.latched_t = True, False, 0.0

    def stop():
        e.calls.append(("stop", e.command_mode))
        e.recording = e.hands_free = e.command_mode = False

    def cancel():
        e.calls.append("cancel")
        e.recording = e.hands_free = e.command_mode = False

    e.start, e.stop, e.cancel = start, stop, cancel
    e.start_listening = lambda target=None: e.calls.append("listen")
    e.stop_listening = lambda *_: e.calls.append("stop-listening")
    monkeypatch.setattr(engine_mod.threading, "Thread", InlineThread)
    return e


def configure(e, **cfg):
    e.cfg.update(cfg)
    e.chords = e._chords()


def down(e, *keys, t=None):
    for k in keys:
        e._on_press(k, t)


def up(e, *keys, t=None):
    for k in keys:
        e._on_release(k, t)


# ------------------------------------------------------------------ tap or hold
def test_classic_a_quick_tap_is_thrown_away_and_a_hold_is_sent(eng):
    now = time.time()
    down(eng, Key.ctrl_l, Key.cmd, t=now)
    up(eng, Key.cmd, Key.ctrl_l, t=now + 0.1)
    assert eng.calls[-1] == "cancel" and not eng.hands_free
    down(eng, Key.ctrl_l, Key.cmd, t=now + 5)
    up(eng, Key.cmd, t=now + 6)
    assert eng.calls[-1] == ("stop", False)


def test_hold_or_tap_a_quick_tap_latches_hands_free_and_the_next_press_sends(eng):
    configure(eng, hotkey_style="hold_or_tap")
    now = time.time()
    down(eng, Key.ctrl_l, Key.cmd, t=now)
    up(eng, Key.cmd, Key.ctrl_l, t=now + 0.1)
    assert eng.recording and eng.hands_free                           # still recording: the pill shows the stop square
    down(eng, Key.ctrl_l, Key.cmd, t=now + 4)
    assert eng.calls[-1] == ("stop", False) and not eng.recording
    up(eng, Key.cmd, Key.ctrl_l, t=now + 4.1)
    assert eng.calls.count("start") == 1


def test_hold_or_tap_a_long_hold_is_still_push_to_talk(eng):
    configure(eng, hotkey_style="hold_or_tap")
    now = time.time()
    down(eng, Key.ctrl_l, Key.cmd, t=now)
    up(eng, Key.cmd, t=now + 0.8)
    assert eng.calls[-1] == ("stop", False) and not eng.hands_free


def test_hold_or_tap_a_double_press_still_starts_keep_listening(eng):
    configure(eng, hotkey_style="hold_or_tap")
    now = time.time()
    down(eng, Key.ctrl_l, Key.cmd, t=now)
    up(eng, Key.cmd, Key.ctrl_l, t=now + 0.1)                         # latched...
    down(eng, Key.ctrl_l, Key.cmd, t=now + 0.3)                       # ...but this is the second tap
    assert eng.calls[-2:] == ["cancel", "listen"]


# ------------------------------------------------------------------ the hands-free shortcut
def test_ctrl_win_space_latches_the_dictation_that_ctrl_win_started(eng):
    now = time.time()
    down(eng, Key.ctrl_l, Key.cmd, t=now)
    down(eng, SPACE, t=now + 0.05)
    assert eng.recording and eng.hands_free
    up(eng, SPACE, Key.cmd, Key.ctrl_l, t=now + 0.1)
    assert eng.recording                                              # letting go does not end it
    down(eng, Key.ctrl_l, Key.cmd, t=now + 5)
    down(eng, SPACE, t=now + 5.05)                                    # the same chord again ends it, once
    assert eng.calls.count(("stop", False)) == 1 and not eng.recording


def test_a_hands_free_shortcut_without_the_dictation_keys_starts_and_ends_by_itself(eng):
    configure(eng, hands_free_hotkey="alt+space")
    down(eng, Key.alt_l, SPACE)
    assert eng.calls == ["start"] and eng.hands_free
    up(eng, SPACE)
    down(eng, SPACE)
    assert eng.calls[-1] == ("stop", False)


def test_space_alone_or_a_repeat_does_nothing_and_is_not_remembered(eng):
    down(eng, SPACE)
    assert eng.calls == [] and eng.pressed == set()
    down(eng, Key.ctrl_l, Key.cmd)
    down(eng, SPACE, SPACE, SPACE)                                     # key repeat while held
    assert eng.hands_free and eng.calls.count("start") == 1


def test_the_hands_free_shortcut_waits_while_busy_or_listening(eng):
    eng.busy = True
    down(eng, Key.ctrl_l, Key.cmd, SPACE)
    assert eng.calls == ["no-start-menu"]


# ------------------------------------------------------------------ Esc
def test_esc_cancels_a_held_recording_without_sending(eng):
    down(eng, Key.ctrl_l, Key.cmd)
    down(eng, Key.esc)
    assert eng.calls[-1] == "cancel" and not eng.recording
    up(eng, Key.esc, Key.cmd, Key.ctrl_l)
    assert ("stop", False) not in eng.calls and eng.flashes == []


def test_esc_cancels_a_hands_free_recording(eng):
    down(eng, Key.ctrl_l, Key.cmd, SPACE)
    up(eng, SPACE, Key.cmd, Key.ctrl_l)
    down(eng, Key.esc)
    assert eng.calls[-1] == "cancel"


def test_esc_with_nothing_running_does_nothing(eng):
    down(eng, Key.esc)
    assert eng.calls == []


# ------------------------------------------------------------------ paste and copy last
@pytest.fixture
def clip(eng, monkeypatch):
    seen = []
    monkeypatch.setattr(engine_mod.paste_mod, "paste_text", lambda text, target, keep, **kw: seen.append(("paste", text, target)) or "pasted")
    monkeypatch.setattr(engine_mod.paste_mod.SystemDeps, "clip_set", lambda self, text, history=False: seen.append(("copy", text)))
    return seen


def test_paste_last_pastes_the_last_dictation_into_whatever_window_is_in_front(eng, clip):
    eng.last_text = "Hello there."
    down(eng, Key.shift, Key.alt_l, Z)
    assert clip == [("paste", "Hello there.", "")]
    down(eng, Z)                                                      # a key repeat does not paste again
    assert len(clip) == 1


def test_copy_last_is_off_by_default_and_copies_when_set(eng, clip):
    eng.last_text = "Hello there."
    down(eng, Key.shift, Key.alt_l, X)
    assert clip == []
    configure(eng, copy_last_hotkey="shift+alt+x")
    up(eng, X)
    down(eng, X)
    assert clip == [("copy", "Hello there.")] and "clipboard" in eng.messages[-1]


def test_with_nothing_dictated_yet_paste_last_says_so(eng, clip):
    down(eng, Key.shift, Key.alt_l, Z)
    assert clip == [] and "Nothing" in eng.messages[-1]


def test_the_last_dictation_comes_from_the_history_after_a_restart(eng, clip):
    eng.cfg["keep_history"] = True
    core.add_history({"t": 1, "app": "a.exe", "raw": "x", "text": "From the history."})
    core.add_history({"t": 2, "app": "a.exe", "raw": "", "text": ""})
    assert eng.last_dictation() == "From the history."
    eng.cfg["keep_history"] = False
    assert eng.last_dictation() == ""


def test_a_window_running_as_administrator_is_explained_once(eng, monkeypatch):
    eng.cfg["keep_clipboard"] = False
    monkeypatch.setattr(engine_mod.paste_mod, "paste_text", lambda text, target, keep, **kw: "blocked")
    assert eng.paste("Hi.") is False and eng.paste("Hi.") is False
    assert "administrator" in eng.messages[0] and len(eng.messages[0]) > len(eng.messages[1])


# ------------------------------------------------------------------ edit by voice
def test_edit_by_voice_is_off_by_default(eng):
    down(eng, Key.ctrl_l, Key.cmd, Key.alt_l)
    assert eng.recording and not eng.command_mode


def test_adding_alt_just_after_ctrl_win_turns_the_recording_into_an_instruction(eng):
    configure(eng, command_hotkey="ctrl+cmd+alt")
    now = time.time()
    down(eng, Key.ctrl_l, Key.cmd, t=now)
    down(eng, Key.alt_l, t=now + 0.2)
    assert eng.command_mode and eng.calls.count("start") == 1
    up(eng, Key.alt_l, t=now + 3)
    assert eng.calls[-1] == ("stop", True)
    up(eng, Key.cmd, Key.ctrl_l, t=now + 3.1)
    assert eng.calls.count(("stop", True)) == 1


def test_alt_pressed_first_starts_an_instruction_too(eng):
    configure(eng, command_hotkey="ctrl+cmd+alt")
    now = time.time()
    down(eng, Key.alt_l, Key.ctrl_l, Key.cmd, t=now)
    assert eng.command_mode
    up(eng, Key.ctrl_l, t=now + 2)
    assert eng.calls[-1] == ("stop", True)


def test_alt_long_after_the_dictation_started_is_not_an_instruction(eng):
    configure(eng, command_hotkey="ctrl+cmd+alt")
    now = time.time()
    down(eng, Key.ctrl_l, Key.cmd, t=now)
    down(eng, Key.alt_l, t=now + 3)
    assert not eng.command_mode


def test_an_instruction_tap_is_cancelled_not_latched(eng):
    configure(eng, command_hotkey="ctrl+cmd+alt", hotkey_style="hold_or_tap")
    now = time.time()
    down(eng, Key.alt_l, Key.ctrl_l, Key.cmd, t=now)
    up(eng, Key.cmd, t=now + 0.1)
    assert eng.calls[-1] == "cancel" and not eng.hands_free


def test_a_standalone_edit_shortcut_starts_and_stops_by_itself(eng):
    configure(eng, command_hotkey="shift+alt", paste_last_hotkey="", hotkey=["ctrl_r"])
    eng.hotkey = [engine_mod.KEY_ALIASES["ctrl_r"]]
    down(eng, Key.shift, Key.alt_l)
    assert eng.calls == ["start"] and eng.command_mode
    up(eng, Key.shift)
    assert eng.calls[-1] == ("stop", True)


# ------------------------------------------------------------------ R2-M2: stale keys
def test_keys_whose_release_was_lost_are_forgotten_after_a_pause(eng):
    now = time.time()
    down(eng, Key.cmd, t=now)                                        # Win+L: the PC locks, the key-up never arrives
    eng.key_state = lambda key: False                                 # after unlocking nothing is really held
    down(eng, Key.ctrl_l, t=now + 60)
    assert "start" not in eng.calls and eng.pressed == {Key.ctrl_l}


def test_keys_that_are_really_held_survive_a_pause(eng):
    now = time.time()
    down(eng, Key.ctrl_l, Key.cmd, t=now)
    eng.key_state = lambda key: True
    down(eng, SPACE, t=now + 10)                                      # a long hold without key repeat, then Space
    assert eng.hands_free and Key.cmd in eng.pressed


def test_a_hold_whose_release_was_lost_ends_at_the_next_key(eng):
    now = time.time()
    down(eng, Key.ctrl_l, Key.cmd, t=now)
    eng.key_state = lambda key: False
    down(eng, Key.shift, t=now + 30)
    assert eng.calls[-1] == ("stop", False) and not eng.combo_was_down


# ------------------------------------------------------------------ R2-M1: the hook only queues
def test_with_the_hotkey_thread_the_hook_only_queues_the_key(eng):
    eng._hotkey_q = queue.Queue()
    eng.on_press(Key.ctrl_l)
    eng.on_press(Key.cmd)
    eng.on_release(Key.cmd)
    assert eng.calls == [] and eng.pressed == set()                   # nothing ran inside the hook
    items = [eng._hotkey_q.get_nowait() for _ in range(3)]
    assert [(d, k) for d, k, _ in items] == [(True, Key.ctrl_l), (True, Key.cmd), (False, Key.cmd)]
    for d, k, t in items:
        (eng._on_press if d else eng._on_release)(k, t)
    assert eng.calls[-1] == "cancel"                                  # handled later, with the times of the events


def test_the_hotkey_thread_survives_a_failing_handler(eng, monkeypatch):
    eng._hotkey_q = queue.Queue()
    eng.stream = None
    seen = []

    def boom(key, t=None):
        seen.append(key)
        if len(seen) == 1:
            raise OSError("icon failed")
        raise SystemExit   # ends the loop for the test

    monkeypatch.setattr(eng, "_on_press", boom)
    eng.on_press(Key.ctrl_l)
    eng.on_press(Key.cmd)
    with pytest.raises(SystemExit):
        eng._hotkey_loop()
    assert seen == [Key.ctrl_l, Key.cmd]


def test_the_engine_reads_the_shortcuts_from_the_settings(eng):
    assert set(eng.chords) == {"hands_free_hotkey", "paste_last_hotkey"}
    configure(eng, paste_last_hotkey="ctrl+cmd+v")                    # holds the dictation shortcut: off, logged
    assert "paste_last_hotkey" not in eng.chords


def test_the_tap_threshold_is_shared_with_the_pure_rule():
    assert engine_mod.TAP_SECONDS == hotkeys.HOLD_SECONDS == 0.3


# ------------------------------------------------------------------ what a dictation leaves behind
def test_a_pasted_dictation_becomes_the_last_dictation_and_a_note_does_not(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: core.Result("raw", "Typed text.", True, ""))
    eng.paste = lambda text: True
    eng._process(b"\x10\x27" * 16000, "notepad.exe")
    assert eng.last_text == "Typed text."
    eng.save_note = lambda *a: None
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: core.Result("raw", "A note.", True, ""))
    eng._process(b"\x10\x27" * 16000, "", note=True)
    assert eng.last_text == "Typed text."


def test_the_history_timing_says_how_many_pieces_were_streamed_and_the_upload_format(eng, monkeypatch):
    import timing
    eng.cfg["keep_history"] = True
    eng.paste = lambda text: True
    monkeypatch.setattr(core, "process_text", lambda cfg, raw, exe, label, segments=None: core.Result(raw, "Streamed.", True, ""))
    monkeypatch.setattr(core, "upload_format", lambda cfg: "flac")

    class Streamer:
        pieces, early = 3, 2

        def finish(self):
            return "streamed raw"

    eng._process(b"\x10\x27" * 16000, "notepad.exe", False, Streamer(), timing.Timing())
    entry = core.read_history()[-1]["timing"]
    assert entry["pieces"] == 3 and entry["upload"] == "flac"
