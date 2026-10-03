"""The engine's wiring of the optional warm microphone (setting warm_mic): when it opens and closes, how a recording takes
its last 400 ms, and that nothing changes while the setting is off. A fake sounddevice: no real microphone is opened.
Needs the Windows runtime packages (pynput, pystray, ...); skipped where they are missing (CI's test job)."""
import types

import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import numpy as np  # noqa: E402  (the engine needs it too)

import engine as engine_mod  # noqa: E402
import vox_core as core  # noqa: E402


class FakeStream:
    def __init__(self, callback, device):
        self.callback, self.device = callback, device
        self.active = True
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped, self.active = True, False

    def close(self):
        self.closed = True

    def push(self, samples, value=1):
        self.callback(np.full((samples, 1), value, dtype=np.int16), samples, None, None)


class FakeSd:
    class PortAudioError(Exception):
        pass

    def __init__(self):
        self.streams, self.fail = [], None
        self.initialized = self.terminated = 0

    def InputStream(self, **kw):
        if self.fail:
            raise self.fail
        s = FakeStream(kw["callback"], kw["device"])
        self.streams.append(s)
        return s

    def _terminate(self):
        self.terminated += 1

    def _initialize(self):
        self.initialized += 1


@pytest.fixture
def sd(monkeypatch):
    fake = FakeSd()
    monkeypatch.setattr(engine_mod, "sd", fake)
    monkeypatch.setattr(engine_mod.audio_devices, "input_index", lambda name, sd_=None: 3 if name else None)
    return fake


@pytest.fixture
def eng(sd, monkeypatch):
    e = object.__new__(engine_mod.Engine)
    e.recording = e.busy = e.hands_free = e.note_mode = False
    e.listening, e.stream, e.streaming = None, None, None
    e.cfg = {"warm_mic": True, "input_device": "", "stream_stt": False, "keep_history": False}
    e.chunks, e.target, e.pending, e.timing = [], "notepad.exe", None, None
    e.started_at = 0.0
    e.level = 0.0
    e.messages, e.states = [], []
    e.notify = lambda m, private=False: e.messages.append(m)
    e.set_state = e.states.append
    e._rec_lock = engine_mod.threading.Lock()
    return e


def test_the_default_is_off():
    assert core.DEFAULT_CONFIG["warm_mic"] is False


def test_with_the_setting_off_nothing_is_opened_and_recording_opens_its_own_stream(eng, sd):
    eng.cfg["warm_mic"] = False
    eng._sync_warm()
    assert sd.streams == [] and eng.warm is None
    eng.started_at = 1e12
    eng._begin_capture()
    assert len(sd.streams) == 1 and eng._preroll == 0          # the normal stream, opened at key-down


def test_with_the_setting_on_the_stream_is_opened_and_kept_ready(eng, sd):
    eng._sync_warm()
    assert len(sd.streams) == 1 and sd.streams[0].started and eng.warm.is_open
    eng._sync_warm()
    assert len(sd.streams) == 1                                # asking again opens nothing new


def test_the_chosen_microphone_is_the_one_kept_open(eng, sd):
    eng.cfg["input_device"] = "USB Mic"
    eng._sync_warm()
    assert sd.streams[0].device == 3


def test_a_recording_gets_the_last_400_ms_before_the_key_and_then_the_live_audio(eng, sd):
    eng._sync_warm()
    s = sd.streams[0]
    s.push(16000, 1)                                           # a second of audio nobody is recording
    s.push(160, 2)
    eng.started_at = 1e12                                      # no length limit in this test
    eng._begin_capture()
    s.push(160, 3)
    assert len(sd.streams) == 1                                # no second stream was opened
    pcm = b"".join(eng.chunks)
    assert eng._preroll == 12800 and len(pcm) == 12800 + 320
    samples = np.frombuffer(pcm, dtype=np.int16)
    assert list(samples[:6240]) == [1] * 6240 and list(samples[6240:6400]) == [2] * 160   # before the key, oldest first
    assert list(samples[6400:]) == [3] * 160                                              # then the live audio


def test_the_live_audio_is_fed_to_the_streamer_in_order_too(eng, sd):
    fed = []
    eng.streaming = types.SimpleNamespace(feed=lambda b: fed.append(bytes(b)))
    eng._sync_warm()
    s = sd.streams[0]
    s.push(160, 1)
    eng.started_at = 1e12
    eng._begin_capture()
    s.push(160, 2)
    assert [len(b) for b in fed] == [320, 320] and fed[0] == np.full(160, 1, dtype=np.int16).tobytes()


def test_nothing_is_kept_by_the_engine_while_not_recording(eng, sd):
    eng._sync_warm()
    sd.streams[0].push(16000, 1)
    assert eng.chunks == []                                    # only the ring holds audio, and only the last 400 ms
    assert len(eng.warm.ring) == 12800


def test_the_end_of_a_recording_lets_go_of_the_audio_but_keeps_the_stream_open(eng, sd):
    eng._sync_warm()
    s = sd.streams[0]
    eng.started_at = 1e12
    eng._begin_capture()
    eng.recording = True
    assert eng._end_recording() is True
    n = len(eng.chunks)
    s.push(160, 4)
    assert len(eng.chunks) == n                                # not recorded any more
    assert not s.stopped and not s.closed and eng.warm.is_open
    assert len(eng.warm.ring) == 320                           # back in the ring


def test_turning_the_setting_off_closes_the_stream(eng, sd):
    eng._sync_warm()
    s = sd.streams[0]
    eng.cfg["warm_mic"] = False
    eng._sync_warm()
    assert s.stopped and s.closed and eng.warm is None


def test_a_changed_microphone_reopens_the_stream_on_the_new_one(eng, sd):
    eng._sync_warm()
    eng.cfg["input_device"] = "USB Mic"
    eng._sync_warm()
    first, second = sd.streams
    assert first.closed and first.device is None and second.device == 3


def test_a_failed_open_falls_back_to_the_normal_stream_with_one_notification(eng, sd):
    sd.fail = sd.PortAudioError("Device unavailable")
    eng._sync_warm()
    eng._sync_warm()
    assert len(eng.messages) == 1 and "ready" in eng.messages[0]
    assert not eng.warm.is_open
    eng.started_at = 1e12
    sd.fail = None
    eng._begin_capture()                                       # the normal path opens its own stream
    assert len(sd.streams) == 1 and eng._preroll == 0


def test_a_chosen_microphone_that_is_not_there_is_left_to_the_normal_path_to_explain(eng, sd, monkeypatch):
    monkeypatch.setattr(engine_mod.audio_devices, "input_index", lambda name, sd_=None: None)
    eng.cfg["input_device"] = "USB Mic"
    eng._sync_warm()
    assert sd.streams == [] and eng.messages == []


def test_the_audio_device_list_is_not_rebuilt_while_the_warm_stream_is_open(eng, sd):
    eng._sync_warm()
    assert eng._refresh_audio() is False
    assert sd.terminated == 0 and sd.initialized == 0


def test_a_tap_that_records_only_the_400_ms_from_before_is_still_too_short(eng, sd, monkeypatch):
    """The 400 ms in front must not turn an accidental tap into a dictation."""
    eng._sync_warm()
    sd.streams[0].push(16000, 9000)                            # loud room
    eng.started_at = 1e12
    eng._begin_capture()
    eng.recording = True
    eng.stop()                                                 # released at once: only the 400 ms and nothing after
    assert eng.busy is False and eng.messages == []
    assert eng.states[-1] == "idle"


def test_quitting_closes_the_warm_stream(eng, sd, monkeypatch):
    eng._sync_warm()
    s = sd.streams[0]
    eng.meeting = types.SimpleNamespace(active=False, processing=False)
    eng.sync = types.SimpleNamespace(stop=lambda: None)
    eng.relay = types.SimpleNamespace(stop=lambda: None)
    eng.icon = types.SimpleNamespace(stop=lambda: None)
    eng.overlay = None
    monkeypatch.setattr(engine_mod.os, "remove", lambda p: None)
    exits = []
    monkeypatch.setattr(engine_mod.os, "_exit", exits.append)
    eng.quit()
    assert s.closed and exits == [0]
