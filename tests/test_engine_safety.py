"""The engine never loses a dictation to an unexpected error, and a failing hotkey handler does not kill the hotkey.
Needs the Windows runtime packages (pynput, pystray, ...); skipped where they are missing (CI's test job)."""
import sqlite3

import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import pyperclip  # noqa: E402
from pynput import keyboard  # noqa: E402

import engine as engine_mod  # noqa: E402
import notes  # noqa: E402
import vox_core as core  # noqa: E402

PCM = b"\x10\x27" * core.SAMPLE_RATE


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    e = object.__new__(engine_mod.Engine)
    e.recording = e.busy = e.hands_free = e.note_mode = False
    e.cfg, e.target, e.pending = {"keep_history": False}, "notepad.exe", []
    e.messages, e.pasted, e.states = [], [], []
    e.overlay = None
    e.sync = type("S", (), {"trigger": lambda self: None})()
    e.notify = lambda m, private=False: e.messages.append(m)
    e.set_state = e.states.append
    e.paste = e.pasted.append
    return e


def ok_result(text="Call the dentist tomorrow."):
    return core.Result("um call the dentist tomorrow", text, True, "")


def test_an_unexpected_error_keeps_the_recording_for_retry(eng, monkeypatch):
    def boom(cfg, pcm, exe, label):
        raise RuntimeError("surprise")

    monkeypatch.setattr(core, "process_detailed", boom)
    eng._process(PCM, "notepad.exe")
    assert eng.pending == [(PCM, "notepad.exe", False)]
    assert len(eng.messages) == 1 and "Retry" in eng.messages[0]
    assert not eng.busy and eng.states[-1] == "idle"


def test_a_paste_that_raises_keeps_the_recording_and_still_writes_history(eng, monkeypatch):
    saved = []

    def busy_clipboard(text):
        raise pyperclip.PyperclipWindowsException("clipboard busy")

    eng.cfg["keep_history"] = True
    eng.paste = busy_clipboard
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result("Hello there."))
    monkeypatch.setattr(core, "add_history", saved.append)
    eng._process(PCM, "notepad.exe")
    assert eng.pending == [(PCM, "notepad.exe", False)]
    assert len(eng.messages) == 1 and "paste" in eng.messages[0]
    assert [e["text"] for e in saved] == ["Hello there."]


def test_a_note_that_cannot_be_saved_is_kept_as_a_note(eng, monkeypatch):
    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result())
    monkeypatch.setattr(notes, "add", locked)
    eng._process(PCM, "", note=True)
    assert eng.pending == [(PCM, "", True)]
    assert len(eng.messages) == 1


def test_a_delivered_dictation_is_not_kept(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result("Hello."))
    eng._process(PCM, "notepad.exe")
    assert eng.pending == [] and eng.pasted == ["Hello."]


# ---- ENG-1: every failed recording is kept until it is itself delivered ----------------------------------------------------

class InlineThread:
    def __init__(self, target=None, args=(), daemon=None, name=None, **kw):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def fails(cfg, pcm, exe, label):
    raise core.ApiError(429, "slow down")


def test_a_later_successful_dictation_does_not_drop_a_kept_one(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", fails)
    eng._process(PCM, "notepad.exe")
    a = eng.pending[0]
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result("Hi."))
    eng._process(b"\x10\x27" * 100, "notepad.exe")
    assert eng.pending == [a] and eng.pending[0] is a


def test_a_second_failure_is_kept_next_to_the_first(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", fails)
    eng._process(PCM, "a.exe")
    eng._process(PCM + PCM, "b.exe")
    assert [p[1] for p in eng.pending] == ["a.exe", "b.exe"]


def test_at_most_five_are_kept_and_the_oldest_goes(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", fails)
    for i in range(7):
        eng._process(PCM, "app%d.exe" % i)
    assert [p[1] for p in eng.pending] == ["app2.exe", "app3.exe", "app4.exe", "app5.exe", "app6.exe"]


def test_retry_sends_the_oldest_and_removes_only_it(eng, monkeypatch):
    monkeypatch.setattr(engine_mod.threading, "Thread", InlineThread)
    monkeypatch.setattr(core, "process_detailed", fails)
    eng._process(PCM, "a.exe")
    eng._process(PCM, "b.exe")
    sent = []
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: sent.append(exe) or ok_result("Hi."))
    eng.retry_last()
    assert sent == ["a.exe"] and [p[1] for p in eng.pending] == ["b.exe"]
    assert eng.target == "a.exe"   # the paste checks the window of that recording, not of the last one started


def test_a_failed_retry_moves_to_the_back_without_a_copy(eng, monkeypatch):
    monkeypatch.setattr(engine_mod.threading, "Thread", InlineThread)
    monkeypatch.setattr(core, "process_detailed", fails)
    eng._process(PCM, "a.exe")
    eng._process(PCM, "b.exe")
    eng.retry_last()
    assert [p[1] for p in eng.pending] == ["b.exe", "a.exe"]


def test_the_tray_item_says_how_many_are_waiting(eng):
    eng.pending = [(PCM, "a.exe", False)]
    assert eng.retry_label() == "Retry last dictation"
    eng.pending = [(PCM, "a.exe", False)] * 3
    assert eng.retry_label() == "Retry dictation (3 waiting)"


def test_the_tray_menu_is_built_again_when_a_recording_is_kept_or_delivered(eng, monkeypatch):
    """pystray builds the Windows menu only at start, after a click and on a left click: without update_menu the Retry
    item stays hidden after the first failure, and its count stays old after a retry (final review W-I2)."""
    monkeypatch.setattr(engine_mod.threading, "Thread", InlineThread)
    labels = []
    eng.icon = type("I", (), {"update_menu": lambda self: labels.append((bool(eng.pending), eng.retry_label()))})()
    monkeypatch.setattr(core, "process_detailed", fails)
    eng._process(PCM, "a.exe")
    eng._process(PCM, "b.exe")
    assert labels[-1] == (True, "Retry dictation (2 waiting)")
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result("Hi."))
    eng.retry_last()
    assert labels[-1] == (True, "Retry last dictation")
    assert any("tray icon > Retry." in m for m in eng.messages)


def test_set_state_builds_the_tray_menu_again():
    calls = []
    e = object.__new__(engine_mod.Engine)
    e.icon = type("I", (), {"update_menu": lambda self: calls.append(1), "icon": None})()
    engine_mod.Engine.set_state(e, "rec")
    assert calls == [1]


def hotkey_engine(eng):
    eng.pressed, eng.note_hotkey = set(), None
    eng.hotkey = [{keyboard.Key.cmd}]
    eng.combo_was_down = False
    eng.listening = None
    eng.stream = None
    return eng


def test_a_failing_hotkey_handler_does_not_raise_into_pynput(eng, monkeypatch):
    hotkey_engine(eng)

    def boom():
        raise OSError("icon failed")

    monkeypatch.setattr(eng, "on_combo_down", boom, raising=False)
    eng.on_press(keyboard.Key.cmd)   # pynput would stop the listener on a raise
    eng.on_release(keyboard.Key.cmd)


def test_set_state_survives_a_tray_icon_that_raises(eng):
    class Icon:
        @property
        def icon(self):
            return None

        @icon.setter
        def icon(self, value):
            raise OSError("DestroyIcon failed")

    eng.icon, eng.level, eng.flash_kind = Icon(), 0.0, ""
    del eng.set_state   # the real method
    eng.set_state("rec")
    assert eng.state == "rec"


# ---- private balloon texts stay out of the log (R2-M10) -------------------------------------------------------------------

def test_a_note_title_is_shown_in_the_balloon_but_not_written_to_the_log(tmp_path, monkeypatch, caplog):
    import logging
    shown = []
    e = object.__new__(engine_mod.Engine)
    e.icon = type("I", (), {"notify": lambda self, msg, title: shown.append(msg)})()
    e.sync = type("S", (), {"trigger": lambda self: None})()
    e.cfg = {}
    monkeypatch.setattr(notes, "add", lambda *a, **k: {"title": "Secret plan"})
    with caplog.at_level(logging.INFO, logger="vox"):
        e.save_note("Secret plan details", "Secret plan details", 3)
    assert shown == ["Note saved: Secret plan"]
    assert "Secret plan" not in caplog.text


def test_a_meeting_title_is_not_written_to_the_log_either(monkeypatch, caplog):
    import logging
    shown = []
    e = object.__new__(engine_mod.Engine)
    e.icon = type("I", (), {"notify": lambda self, msg, title: shown.append(msg)})()
    e.meeting = type("M", (), {"start": lambda self, ev: True, "last_error": ""})()
    with caplog.at_level(logging.INFO, logger="vox"):
        e.start_meeting(manual={"title": "Layoff planning", "attendees": []})
    assert any("Layoff planning" in m for m in shown)
    assert "Layoff planning" not in caplog.text


def test_an_ordinary_balloon_is_still_logged(caplog):
    import logging
    e = object.__new__(engine_mod.Engine)
    e.icon = type("I", (), {"notify": lambda self, msg, title: None})()
    with caplog.at_level(logging.INFO, logger="vox"):
        e.notify("Copied; the window changed")
    assert "Copied; the window changed" in caplog.text
