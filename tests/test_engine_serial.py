"""ENG-12 (rest): start, stop, cancel and the note toggle come from the hotkey, tray, control-server, time-limit and
watchdog threads. They run one at a time, so two microphone streams never open at once, a dictation the hotkey starts
is never turned into a note by the tray at the same moment, and a late time-limit stop never ends a newer recording.
Needs the Windows runtime packages (pynput, pystray, ...); skipped where they are missing (CI's test job)."""
import threading
import time

import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import engine as engine_mod  # noqa: E402
import vox_core as core  # noqa: E402


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    e = object.__new__(engine_mod.Engine)
    e._ctl_lock = threading.RLock()
    e._rec_lock = threading.Lock()
    e.recording = e.busy = e.hands_free = e.note_mode = False
    e.chunks, e.cfg, e.pending, e.streaming = [], {"keep_history": False, "stream_stt": False}, [], None
    e.messages, e.states, e.opened = [], [], []
    e.notify = lambda m, private=False: e.messages.append(m)
    e.set_state = e.states.append
    e.flash = lambda kind: None
    e._close_stream = lambda: None

    def capture():
        e.opened.append(threading.current_thread().name)
        time.sleep(0.05)   # opening a microphone takes a moment: the window a second start used to slip into
    e._begin_capture = capture
    monkeypatch.setattr(core, "warm", lambda cfg: None)
    monkeypatch.setattr(core, "endpoint_error", lambda cfg: "")
    monkeypatch.setattr(core, "key_missing", lambda cfg: False)
    monkeypatch.setattr(engine_mod, "foreground_app", lambda: "notepad.exe")
    return e


def _together(*calls):
    go = threading.Barrier(len(calls))
    threads = [threading.Thread(target=lambda c=c: (go.wait(), c())) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)


def test_two_starts_at_the_same_moment_open_one_stream(eng):
    _together(eng.start, eng.start, eng.start)
    assert eng.recording and len(eng.opened) == 1


def test_the_tray_note_and_the_hotkey_at_the_same_moment_open_one_stream(eng):
    for _ in range(20):
        eng.recording = eng.note_mode = False
        eng.opened.clear()
        _together(eng.toggle_note, eng.start)
        assert eng.recording and len(eng.opened) == 1


def test_a_note_toggle_never_marks_a_dictation_that_is_already_running(eng):
    eng.start()
    eng.toggle_note()
    assert eng.recording and not eng.note_mode and len(eng.opened) == 1


def test_a_late_time_limit_stop_does_not_end_the_next_recording(eng, monkeypatch):
    stops = []
    monkeypatch.setattr(eng, "_stop", lambda: stops.append(eng._rec_gen) or setattr(eng, "recording", False))
    eng.start()
    old = eng._rec_gen
    eng.recording = False          # that recording ended (key-up) ...
    eng.start()                    # ... and the next one started before the time-limit thread ran
    eng._stop_at_limit(old)
    assert eng.recording and stops == []
    eng._stop_at_limit(eng._rec_gen)   # its own limit does stop it
    assert not eng.recording and stops == [eng._rec_gen]


def test_the_time_limit_asks_for_one_stop_not_one_per_audio_block(eng, monkeypatch):
    import numpy as np
    spawned = []

    class NoThread:
        def __init__(self, target=None, args=(), daemon=None, **kw):
            spawned.append((target, args))

        def start(self):
            pass
    monkeypatch.setattr(engine_mod.threading, "Thread", NoThread)
    eng.start()
    eng.started_at = time.time() - engine_mod.MAX_SECONDS - 5
    block = np.zeros((160, 1), dtype=np.int16)
    for _ in range(50):
        eng._audio(block, 160, None, None)
    assert [(t.__name__, a) for t, a in spawned] == [("_stop_at_limit", (eng._rec_gen,))]
