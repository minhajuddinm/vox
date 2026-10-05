"""Issue 63 (Windows): the engine reads config.json again after a load that could not open it, and Quit lets a dictation
that is still being sent finish. Needs the Windows runtime packages; skipped where they are missing (CI's test job)."""
import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import engine as engine_mod  # noqa: E402
import vox_core as core  # noqa: E402


@pytest.fixture
def eng(monkeypatch):
    e = object.__new__(engine_mod.Engine)
    e.cfg, e.cfg_mtime = {"hotkey": ["ctrl_l", "cmd"], "api_key": "k", "note_hotkey": ""}, 1.0
    e.sync = type("S", (), {"trigger": lambda self: None, "stop": lambda self: None})()
    return e


def test_a_config_that_could_not_be_opened_keeps_the_settings_and_is_read_again(eng, monkeypatch):
    monkeypatch.setattr(engine_mod.Engine, "_mtime", lambda self: 2.0)
    monkeypatch.setattr(core, "load_config", lambda: dict(core.DEFAULT_CONFIG))
    monkeypatch.setattr(core, "config_is_fallback", lambda: True)
    eng.reload_if_changed()
    assert eng.cfg["api_key"] == "k" and eng.cfg_mtime == 1.0   # not the defaults, and the next tick tries again
    monkeypatch.setattr(core, "load_config", lambda: dict(core.DEFAULT_CONFIG, api_key="k2"))
    monkeypatch.setattr(core, "config_is_fallback", lambda: False)
    eng.reload_if_changed()
    assert eng.cfg["api_key"] == "k2" and eng.cfg_mtime == 2.0


def test_a_start_on_the_defaults_reads_the_file_at_the_next_tick(monkeypatch):
    monkeypatch.setattr(engine_mod.keyboard, "Controller", lambda: object())
    monkeypatch.setattr(core, "load_config", lambda: dict(core.DEFAULT_CONFIG))
    monkeypatch.setattr(core, "config_is_fallback", lambda: True)
    monkeypatch.setattr(engine_mod.Engine, "_mtime", lambda self: 5.0)
    e = engine_mod.Engine()
    assert e.cfg_mtime is None   # never equal to the file's time: reload_if_changed reads it again


def quitting(eng, monkeypatch, order):
    eng.listening, eng.warm, eng.overlay = None, None, None
    eng.meeting = type("M", (), {"active": False, "processing": False})()
    eng.relay = type("R", (), {"stop": lambda self: None})()
    eng.icon = type("I", (), {"stop": lambda self: None})()
    eng.notify = lambda m, private=False: order.append(m)
    monkeypatch.setattr(engine_mod.os, "_exit", lambda code: order.append("exit"))


def test_quit_lets_a_dictation_that_is_being_sent_finish(eng, monkeypatch):
    order = []
    quitting(eng, monkeypatch, order)
    eng.busy = True
    ticks = []

    def sleep(s):
        ticks.append(s)
        if len(ticks) == 3:
            eng.busy = False   # the text was pasted
            order.append("pasted")

    monkeypatch.setattr(engine_mod.time, "sleep", sleep)
    eng.quit()
    assert order[-2:] == ["pasted", "exit"] and any("Finishing" in m for m in order if isinstance(m, str))


def test_quit_does_not_wait_when_nothing_is_being_sent(eng, monkeypatch):
    order = []
    quitting(eng, monkeypatch, order)
    eng.busy = False
    monkeypatch.setattr(engine_mod.time, "sleep", lambda s: order.append("slept"))
    eng.quit()
    assert order == ["exit"]
