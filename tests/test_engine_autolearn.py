"""The engine starts Learn from my corrections only after a real paste. Needs the Windows runtime packages; skipped where
they are missing (CI's test job)."""
import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import engine as engine_mod  # noqa: E402


@pytest.fixture
def eng(monkeypatch):
    e = object.__new__(engine_mod.Engine)
    e.cfg, e.target, e.messages = {"auto_learn": True}, "notepad.exe", []
    e.notify = lambda m, private=False: e.messages.append(m)
    armed = []
    e.quiet = []

    def arm(text, cfg, notify=None, quiet=None):
        armed.append((text, cfg, notify))
        e.quiet.append(quiet)

    monkeypatch.setattr(engine_mod.correction_watch, "arm", arm)
    return e, armed


def test_a_pasted_dictation_arms_the_watch(eng, monkeypatch):
    e, armed = eng
    monkeypatch.setattr(engine_mod.paste_mod, "paste_text", lambda text, target, keep, **kw: engine_mod.paste_mod.PASTED)
    assert e.paste("Hi there.") is True
    assert armed == [("Hi there.", e.cfg, e.notify)]


def test_the_watch_reads_nothing_while_vox_records(eng, monkeypatch):
    e, armed = eng
    monkeypatch.setattr(engine_mod.paste_mod, "paste_text", lambda text, target, keep, **kw: engine_mod.paste_mod.PASTED)
    e.recording, e.listening = False, None
    e.paste("Hi there.")
    (quiet,) = e.quiet
    assert quiet() is False
    e.recording = True
    assert quiet() is True
    e.recording, e.listening = False, object()
    assert quiet() is True


def test_a_dictation_that_only_reached_the_clipboard_does_not(eng, monkeypatch):
    e, armed = eng
    monkeypatch.setattr(engine_mod.paste_mod, "paste_text", lambda text, target, keep, **kw: engine_mod.paste_mod.COPIED)
    assert e.paste("Hi there.") is False
    assert armed == []
