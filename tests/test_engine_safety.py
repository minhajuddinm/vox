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
    e.cfg, e.target, e.pending = {"keep_history": False}, "notepad.exe", None
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
    assert eng.pending == (PCM, "notepad.exe", False)
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
    assert eng.pending == (PCM, "notepad.exe", False)
    assert len(eng.messages) == 1 and "paste" in eng.messages[0]
    assert [e["text"] for e in saved] == ["Hello there."]


def test_a_note_that_cannot_be_saved_is_kept_as_a_note(eng, monkeypatch):
    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result())
    monkeypatch.setattr(notes, "add", locked)
    eng._process(PCM, "", note=True)
    assert eng.pending == (PCM, "", True)
    assert len(eng.messages) == 1


def test_a_delivered_dictation_is_not_kept(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result("Hello."))
    eng.pending = (PCM, "notepad.exe", False)
    eng._process(PCM, "notepad.exe")
    assert eng.pending is None and eng.pasted == ["Hello."]


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
