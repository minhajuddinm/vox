"""The engine asks PortAudio for a fresh device list when a chosen microphone is missing or fails to open
(a mic plugged in or replugged after Vox started). Needs the Windows runtime packages; skipped without them."""
import types

import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import engine as engine_mod  # noqa: E402


class FakeSd:
    class PortAudioError(Exception):
        pass

    def __init__(self, fail_first=False):
        self.initialized = 0
        self.terminated = 0
        self.opened = []
        self.fail_first = fail_first

    def _terminate(self):
        self.terminated += 1

    def _initialize(self):
        self.initialized += 1

    def InputStream(self, **kw):
        self.opened.append(kw["device"])
        if self.fail_first and len(self.opened) == 1:
            raise self.PortAudioError("Invalid device")
        return types.SimpleNamespace(start=lambda: None)


@pytest.fixture
def eng(monkeypatch):
    e = object.__new__(engine_mod.Engine)
    e.recording, e.listening, e.stream = False, None, None
    e.cfg = {"input_device": "USB Mic"}
    e.messages = []
    e.notify = e.messages.append
    return e


def _devices_after_refresh(sd):
    """The device list as PortAudio shows it: the USB mic exists (index 3) only once PortAudio was re-initialised."""
    return lambda name, sd_=None: 3 if sd.initialized else None


def test_a_mic_plugged_in_after_start_is_found_after_a_refresh(eng, monkeypatch):
    sd = FakeSd()
    monkeypatch.setattr(engine_mod, "sd", sd)
    monkeypatch.setattr(engine_mod.audio_devices, "input_index", _devices_after_refresh(sd))
    eng._open_mic(lambda *a: None)
    assert sd.opened == [3]
    assert eng.messages == []


def test_a_mic_that_is_really_missing_still_says_so_and_uses_the_default(eng, monkeypatch):
    sd = FakeSd()
    monkeypatch.setattr(engine_mod, "sd", sd)
    monkeypatch.setattr(engine_mod.audio_devices, "input_index", lambda name, sd_=None: None)
    eng._open_mic(lambda *a: None)
    assert sd.opened == [None] and sd.initialized == 1
    assert len(eng.messages) == 1 and "not connected" in eng.messages[0]


def test_a_replugged_mic_whose_old_index_fails_is_retried_with_the_new_index(eng, monkeypatch):
    sd = FakeSd(fail_first=True)
    monkeypatch.setattr(engine_mod, "sd", sd)
    monkeypatch.setattr(engine_mod.audio_devices, "input_index", lambda name, sd_=None: 7 if not sd.initialized else 2)
    eng._open_mic(lambda *a: None)
    assert sd.opened == [7, 2] and sd.initialized == 1


def test_the_device_list_is_not_reset_while_a_recording_is_open(eng, monkeypatch):
    sd = FakeSd()
    eng.recording = True
    monkeypatch.setattr(engine_mod, "sd", sd)
    monkeypatch.setattr(engine_mod.audio_devices, "input_index", lambda name, sd_=None: None)
    eng._open_mic(lambda *a: None)
    assert sd.terminated == 0 and sd.initialized == 0
