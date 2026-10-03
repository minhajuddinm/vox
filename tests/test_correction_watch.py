"""The Windows side of Learn from my corrections (correction_watch.py) with a fake desktop: no UI Automation, no window."""
import logging
import threading
import time

import pytest

import correction_watch as cw
import vox_core as core

REAL_MAKE_PROVIDER = cw.make_provider   # taken before conftest.py swaps it for each test

class Clock:
    def __init__(self):
        self.t = 50.0

    def __call__(self):
        return self.t


class FakeDesktop(cw.Provider):
    def __init__(self):
        self.window = 101
        self.text = None
        self.password = False
        self.reads = 0

    def foreground(self):
        return self.window

    def focused_text(self):
        self.reads += 1
        if self.password:
            return None, True
        return self.text, False


TYPED = "send it to Minhaj today"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(core, "_config_unread", False)   # an earlier test may leave "unreadable" set
    core.save_config(dict(core.DEFAULT_CONFIG, dictionary=["LoomXR"]))
    desk, clock, said = FakeDesktop(), Clock(), []
    w = cw.Watcher(desk, notify=lambda m, private=False: said.append((m, private)), clock=clock)
    return w, desk, clock, said


def test_a_fix_in_the_pasted_window_is_learned_and_announced_privately(setup):
    w, desk, clock, said = setup
    w.arm(TYPED, start_thread=False)
    desk.text = "Hi. " + TYPED
    w.poll_once()
    clock.t += 2
    desk.text = "Hi. send it to Minhajuddin today"
    w.poll_once()
    clock.t += 2
    w.poll_once()
    assert said == []        # a word made longer waits for the watch's last look (the user may still be typing it)
    desk.text = ""           # sent
    w.poll_once()
    cfg = core.load_config()
    assert cfg["dictionary"] == ["LoomXR", "Minhaj => Minhajuddin", "Minhajuddin"]
    assert [(e["wrong"], e["right"], e["word"]) for e in cfg["learned_log"]] == [("Minhaj", "Minhajuddin", True)]
    assert said == [("Learned: Minhaj -> Minhajuddin", True)]


def test_a_fix_just_before_send_is_learned_when_the_field_empties(setup):
    w, desk, clock, said = setup
    w.arm(TYPED, start_thread=False)
    desk.text = TYPED
    w.poll_once()
    clock.t += 2
    desk.text = "send it to Minhajuddin today"
    w.poll_once()
    clock.t += 0.5
    desk.text = ""
    w.poll_once()
    assert "Minhaj => Minhajuddin" in core.load_config()["dictionary"]
    assert not w.watch.armed


def test_another_window_in_front_is_never_read_and_ends_the_watch(setup):
    w, desk, clock, said = setup
    w.arm(TYPED, start_thread=False)
    desk.window = 202
    desk.text = "private text in another app"
    w.poll_once()
    assert desk.reads == 0 and not w.watch.armed


def test_a_password_control_ends_the_watch_without_learning(setup):
    w, desk, clock, said = setup
    w.arm(TYPED, start_thread=False)
    desk.password = True
    w.poll_once()
    assert not w.watch.armed and said == [] and core.load_config()["dictionary"] == ["LoomXR"]


def test_a_control_without_text_is_logged_once(setup, caplog):
    w, desk, clock, said = setup
    caplog.set_level(logging.INFO, logger="vox.learn")
    for _ in range(2):
        w.arm(TYPED, start_thread=False)
        w.poll_once()
    assert [r.getMessage() for r in caplog.records].count("auto-learn: control exposes no text") == 1


def test_the_log_never_holds_the_text(setup, caplog):
    w, desk, clock, said = setup
    caplog.set_level(logging.DEBUG)
    w.arm(TYPED, start_thread=False)
    desk.text = "Hi. send it to Minhajuddin today"
    w.poll_once()
    clock.t += 2
    w.poll_once()
    w.end()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "learned 1 corrections" in text and "Minhaj" not in text and "send it" not in text


def test_turned_off_learns_nothing(setup):
    w, desk, clock, said = setup
    core.save_config(dict(core.load_config(), auto_learn=False))
    w.arm(TYPED, start_thread=False)
    desk.text = "send it to Minhajuddin today"
    w.poll_once()
    clock.t += 2
    w.poll_once()
    w.end()
    assert core.load_config()["dictionary"] == ["LoomXR"] and said == []


def test_the_thread_runs_only_while_armed(setup):
    w, desk, clock, said = setup
    w.poll = 0.01
    desk.text = TYPED
    w.arm(TYPED)
    assert w._thread is not None
    w.end()
    deadline = time.time() + 5
    while w._thread is not None and time.time() < deadline:
        time.sleep(0.01)
    assert w._thread is None


def test_the_thread_stops_when_the_window_changes(setup):
    w, desk, clock, said = setup
    w.poll = 0.01
    desk.text = TYPED
    w.arm(TYPED)
    desk.window = 999
    deadline = time.time() + 5
    while w._thread is not None and time.time() < deadline:
        time.sleep(0.01)
    assert w._thread is None and not w.watch.armed


class SlowDesktop(FakeDesktop):
    """A desktop whose focused_text blocks until the test lets it go (a slow app answering UI Automation)."""

    def __init__(self):
        super().__init__()
        self.reading = threading.Event()     # set when a read has started
        self.release = threading.Event()     # the read returns when this is set
        self.fail = None

    def focused_text(self):
        self.reading.set()
        assert self.release.wait(10), "the test never released the read"
        if self.fail:
            raise self.fail
        return super().focused_text()


def _poll_in_thread(w):
    errors = []

    def run():
        try:
            w.poll_once()
        except Exception as e:
            errors.append(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, errors


def _timed(fn, limit=2.0):
    """Runs fn in a thread; True when it finished within `limit` seconds."""
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    t.join(limit)
    return not t.is_alive()


def test_a_slow_read_does_not_hold_the_lock_so_the_next_dictation_is_not_stalled(setup):
    w, _, clock, said = setup
    desk = SlowDesktop()
    w.provider = desk
    w.arm(TYPED, start_thread=False)
    desk.text = TYPED
    poller, errors = _poll_in_thread(w)
    try:
        assert desk.reading.wait(5)
        assert _timed(lambda: w.arm("a second dictation", start_thread=False)), "arm() waited for the slow read"
        assert _timed(w.end), "end() waited for the slow read"
    finally:
        desk.release.set()
        poller.join(5)
    assert errors == [] and not w.watch.armed


def test_what_a_slow_read_returns_after_a_new_dictation_is_not_applied_to_the_new_watch(setup):
    w, _, clock, said = setup
    desk = SlowDesktop()
    w.provider = desk
    w.arm(TYPED, start_thread=False)
    desk.text = "Hi. send it to Minhajuddin today"       # a fix of the first dictation
    poller, errors = _poll_in_thread(w)
    assert desk.reading.wait(5)
    w.arm("something else entirely", start_thread=False)    # the next dictation arms a new watch meanwhile
    desk.release.set()
    poller.join(5)
    assert errors == []
    assert w.watch.armed and w.watch.inserted == "something else entirely"
    assert w.watch._snapshot is None                           # the late text was not taken for the new watch
    assert said == []


def test_a_slow_read_that_finds_the_watch_still_armed_is_used_as_before(setup):
    w, _, clock, said = setup
    desk = SlowDesktop()
    w.provider = desk
    w.arm(TYPED, start_thread=False)
    desk.text = TYPED
    desk.release.set()
    w.poll_once()
    assert w.watch._snapshot == TYPED


def test_a_failed_slow_read_of_a_watch_that_is_gone_does_not_end_the_new_one(setup):
    w, _, clock, said = setup
    desk = SlowDesktop()
    w.provider = desk
    w.arm(TYPED, start_thread=False)
    desk.fail = RuntimeError("uia")
    poller, errors = _poll_in_thread(w)
    assert desk.reading.wait(5)
    w.arm("another dictation", start_thread=False)
    desk.release.set()
    poller.join(5)
    assert errors == [] and w.watch.armed and w.watch.inserted == "another dictation"


def test_a_failed_read_of_the_current_watch_still_raises_for_the_thread_to_end_it(setup):
    w, _, clock, said = setup
    desk = SlowDesktop()
    w.provider = desk
    w.arm(TYPED, start_thread=False)
    desk.fail = RuntimeError("uia")
    desk.release.set()
    with pytest.raises(RuntimeError):
        w.poll_once()


def test_module_arm_does_nothing_without_a_provider(monkeypatch):
    monkeypatch.setattr(cw, "make_provider", lambda: None)
    monkeypatch.setattr(cw, "_watcher", None)
    monkeypatch.setattr(cw, "_tried", False)
    cw.arm("text", {})
    assert cw._watcher is None


def test_module_arm_respects_the_setting(monkeypatch):
    desk = FakeDesktop()
    monkeypatch.setattr(cw, "make_provider", lambda: desk)
    monkeypatch.setattr(cw, "_watcher", None)
    monkeypatch.setattr(cw, "_tried", False)
    cw.arm(TYPED, {"auto_learn": False})
    assert cw._watcher is not None and not cw._watcher.watch.armed
    cw._watcher.poll = 60
    cw.arm(TYPED, {"auto_learn": True}, notify=print)
    assert cw._watcher.watch.armed and cw._watcher.watch.app == 101
    cw._watcher.end()


def test_make_provider_needs_windows(monkeypatch):
    monkeypatch.setattr(cw.sys, "platform", "linux")
    assert REAL_MAKE_PROVIDER() is None


def test_make_provider_loads_nothing_until_the_first_read(monkeypatch):
    monkeypatch.setattr(cw.sys, "platform", "win32")
    monkeypatch.setattr(cw.importlib.util, "find_spec", lambda name: object())
    p = REAL_MAKE_PROVIDER()
    assert isinstance(p, cw.UiaProvider) and p._uia is None   # constructing it touches no UI Automation


def test_tests_never_get_the_real_ui_automation():
    """conftest.py swaps make_provider for every test: nothing reads the real desktop."""
    assert cw.make_provider() is None
