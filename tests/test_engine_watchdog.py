"""The engine's overlay watchdog (Engine.check_overlay, run every second by a daemon thread): a thread dump when the
pill's Tk tick stops during a recording, and a reset of a state that no longer matches the engine's flags. Needs the
Windows runtime packages; skipped where they are missing."""
import logging
import time

import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import engine as engine_mod  # noqa: E402
import overlay_guard as guard  # noqa: E402


class FakeIcon:
    icon = None


@pytest.fixture
def eng():
    e = object.__new__(engine_mod.Engine)
    e.recording = e.busy = False
    e.listening = None
    e.icon = FakeIcon()
    e.state, e.level, e.state_since = "idle", 0.0, 0.0
    e.stopped = []
    e.stop = lambda: e.stopped.append(True)
    return e


def warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_set_state_records_when_the_state_was_set(eng):
    before = time.monotonic()
    eng.set_state("rec")
    assert eng.state == "rec" and before <= eng.state_since <= time.monotonic()


# ---------------------------------------------------------------- the Tk tick
def test_a_stalled_tick_during_a_recording_logs_one_thread_dump(eng, caplog):
    eng.overlay = type("O", (), {"last_tick": 100.0})()
    eng.state, eng.recording, eng.state_since = "rec", True, 99.0
    w = guard.StuckWatch()
    assert eng.check_overlay(104.0, w, False) is True
    assert eng.check_overlay(105.0, w, True) is True    # still stalled: not dumped again
    (msg,) = warnings(caplog)
    assert msg.startswith("overlay tick has not run for 4.0 s (state rec); threads:") and "MainThread" in msg


def test_a_tick_that_runs_again_re_arms_the_dump(eng):
    eng.overlay = type("O", (), {"last_tick": 104.5})()
    eng.state, eng.recording = "rec", True
    assert eng.check_overlay(105.0, guard.StuckWatch(), True) is False


def test_no_dump_while_idle_or_without_a_pill(eng, caplog):
    eng.overlay = type("O", (), {"last_tick": 1.0})()
    assert eng.check_overlay(100.0, guard.StuckWatch(), False) is False
    eng.overlay, eng.state, eng.recording = None, "rec", True
    assert eng.check_overlay(100.0, guard.StuckWatch(), False) is False
    assert warnings(caplog) == []


# ---------------------------------------------------------------- stuck states
def run(eng, times, watch=None):
    watch = watch or guard.StuckWatch()
    for t in times:
        eng.check_overlay(t, watch, False)


def test_a_recording_state_left_behind_is_reset_to_idle_after_the_grace(eng, caplog):
    eng.overlay = None
    eng.state, eng.state_since = "rec", 50.0   # recording is False: whatever ended it did not reset the state
    run(eng, [100.0, 105.0])
    assert eng.state == "rec"
    run(eng, [100.0, 110.0])
    assert eng.state == "idle" and eng.stopped == []
    assert any(m.startswith("state 'rec' stuck") for m in warnings(caplog))


def test_busy_without_work_and_listen_without_a_session_are_reset(eng):
    eng.overlay = None
    for state in ("busy", "listen"):
        eng.state, eng.state_since = state, 1.0
        run(eng, [100.0, 110.0])
        assert eng.state == "idle", state


def test_a_recording_past_its_time_limit_is_stopped_like_the_limit_would(eng):
    """The audio callback ends a recording at its limit; when it stopped calling, the watchdog ends it (the audio so
    far is sent, as at the limit)."""
    eng.overlay = None
    eng.state, eng.recording, eng.state_since = "rec", True, 0.0
    limit = engine_mod.MAX_SECONDS * 3 + engine_mod.STUCK_MARGIN
    run(eng, [limit - 1, limit + 20])
    assert eng.stopped == []
    run(eng, [limit + 1, limit + 1 + guard.STUCK_SECONDS])
    assert eng.stopped == [True]


def test_consistent_states_are_never_touched(eng):
    eng.overlay = None
    for state, flags in (("rec", {"recording": True}), ("busy", {"busy": True}), ("listen", {"listening": object()})):
        eng.recording = eng.busy = False
        eng.listening = None
        eng.state, eng.state_since = state, 1.0
        for k, v in flags.items():
            setattr(eng, k, v)
        run(eng, [100.0, 200.0])
        assert eng.state == state and eng.stopped == []
