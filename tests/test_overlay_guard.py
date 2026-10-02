"""Keeping the pill on screen (windows/overlay_guard.py): the pure checks behind the overlay's re-assert and the engine's
watchdog. No Tk, no Win32: the probe is a plain dict."""
import threading

import overlay_guard as g

SCREEN = (0, 0, 1920, 1080)
PILL = (894, 1010, 1026, 1048)
GOOD = {"visible": True, "iconic": False, "cloaked": 0, "rect": PILL, "monitors": [SCREEN],
        "exstyle": g.WS_EX_TOPMOST | g.STYLE_BITS | g.WS_EX_LAYERED, "tk_mapped": True}


def probe(**kw):
    return {**GOOD, **kw}


# ---------------------------------------------------------------- assert timing
def test_an_assert_is_due_every_half_second():
    assert g.assert_due(10.5, 10.0) and g.assert_due(11.0, 10.0)
    assert not g.assert_due(10.4, 10.0)
    assert g.assert_due(0.1, 0.0 - g.ASSERT_SECONDS)   # never asserted: due at once


# ---------------------------------------------------------------- on screen
def test_a_pill_inside_a_monitor_is_on_screen():
    assert g.on_screen(PILL, [SCREEN])


def test_a_pill_outside_every_monitor_is_off_screen():
    assert not g.on_screen((3000, 1010, 3132, 1048), [SCREEN])
    assert not g.on_screen((894, 1200, 1026, 1238), [SCREEN])


def test_a_pill_mostly_off_the_edge_is_off_screen_and_half_on_is_on():
    assert not g.on_screen((1900, 1010, 2032, 1048), [SCREEN])   # 20 of 132 px on screen
    assert g.on_screen((1854, 1010, 1986, 1048), [SCREEN])       # exactly half


def test_a_pill_split_over_two_monitors_counts_both():
    right = (1920, 0, 3840, 1080)
    assert g.on_screen((1850, 1010, 1982, 1048), [SCREEN, right])


def test_monitors_left_of_and_above_the_primary_have_negative_coordinates():
    left = (-2560, -360, 0, 1080)
    assert g.on_screen((-1300, 1000, -1168, 1038), [SCREEN, left])


def test_an_empty_rect_is_never_on_screen():
    assert not g.on_screen((10, 10, 10, 10), [SCREEN])


# ---------------------------------------------------------------- problems
def test_a_healthy_window_has_no_problems():
    assert g.problems(GOOD) == []


def test_each_problem_is_named():
    assert g.problems(probe(visible=False)) == ["hidden"]
    assert g.problems(probe(iconic=True)) == ["minimised"]
    assert g.problems(probe(cloaked=2)) == ["cloaked"]
    assert g.problems(probe(rect=(5000, 0, 5132, 38))) == ["off-screen"]
    assert g.problems(probe(exstyle=g.STYLE_BITS)) == ["not topmost"]
    assert g.problems(probe(exstyle=g.WS_EX_TOPMOST)) == ["style lost"]
    assert g.problems(probe(tk_mapped=False)) == ["tk unmapped"]


def test_unknown_facts_are_not_problems():
    """A probe call that failed gives None: no false alarm (the failure itself is logged where it happened)."""
    assert g.problems(probe(cloaked=None, rect=None, exstyle=None, tk_mapped=None)) == []
    assert g.problems(probe(monitors=[])) == []


def test_only_problems_that_keep_the_pill_off_the_screen_rebuild_it():
    assert g.needs_rebuild(["not topmost", "style lost", "tk unmapped"]) == []
    assert g.needs_rebuild(["hidden", "not topmost", "cloaked"]) == ["hidden", "cloaked"]
    assert g.needs_rebuild(["minimised", "off-screen"]) == ["minimised", "off-screen"]


def test_describe_names_everything_needed_to_find_the_cause():
    text = g.describe(probe(cloaked=2, hwnd=0x1234, work=[(0, 0, 1920, 1040)]), (0, 0, 1920, 1040), "rec")
    for part in ("state=rec", "hwnd=0x1234", "rect=(894, 1010, 1026, 1048)", "work_area=(0, 0, 1920, 1040)",
                 "monitors=[(0, 0, 1920, 1080)]", "works=[(0, 0, 1920, 1040)]", "cloaked=2", "exstyle=0x"):
        assert part in text, part


# ---------------------------------------------------------------- rate limit
def test_a_rate_limit_allows_once_per_period_and_per_key():
    r = g.RateLimit(60)
    assert r.allow("a", 0) and not r.allow("a", 59) and r.allow("a", 60)
    assert r.allow("b", 1)


def test_a_rate_limit_counts_what_it_held_back():
    r = g.RateLimit(60)
    r.allow("a", 0), r.allow("a", 1), r.allow("a", 2)
    assert r.allow("a", 61) and r.held_back("a") == 2 and r.held_back("a") == 0


# ---------------------------------------------------------------- tick watchdog
def test_a_tick_is_stalled_only_during_a_recording_and_after_the_limit():
    assert g.tick_stalled(10.0, 13.5, True)
    assert not g.tick_stalled(10.0, 12.9, True)
    assert not g.tick_stalled(10.0, 100.0, False)   # idle: nothing to show, a stall does not matter
    assert not g.tick_stalled(0.0, 100.0, True)     # never ticked yet (the pill is still starting)


def test_a_thread_dump_names_every_thread_and_its_stack():
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, name="dump-me", daemon=True)
    t.start()
    try:
        text = g.thread_dump()
    finally:
        stop.set()
    assert "dump-me" in text and "MainThread" in text and "wait" in text


# ---------------------------------------------------------------- stuck states
MAX_AGE = 1140.0


def ok(state, recording=False, busy=False, listening=None, age=1.0):
    return g.state_consistent(state, recording, busy, listening, age, MAX_AGE)


def test_states_that_match_the_engine_flags_are_consistent():
    assert ok("idle")
    assert ok("rec", recording=True) and ok("busy", busy=True) and ok("listen", listening=object())


def test_a_recording_state_without_a_recording_is_stuck():
    assert not ok("rec")


def test_a_recording_older_than_the_longest_allowed_is_stuck():
    """The audio callback ends a recording at its time limit; still recording past it means the callback stopped."""
    assert ok("rec", recording=True, age=MAX_AGE)
    assert not ok("rec", recording=True, age=MAX_AGE + 1)


def test_busy_without_work_and_listen_without_a_session_are_stuck():
    assert not ok("busy") and not ok("listen")


def test_a_stuck_state_is_reported_only_after_the_grace_period_without_change():
    w = g.StuckWatch(grace=10)
    assert not w.update("rec", 5.0, False, 100.0)
    assert not w.update("rec", 5.0, False, 109.9)
    assert w.update("rec", 5.0, False, 110.0)


def test_a_state_that_changed_or_became_consistent_starts_the_grace_again():
    w = g.StuckWatch(grace=10)
    w.update("rec", 5.0, False, 100.0)
    assert not w.update("rec", 7.0, False, 111.0)    # a new rec (another set_state): its own grace starts
    assert not w.update("rec", 7.0, True, 125.0)     # consistent again: forgotten
    assert not w.update("rec", 7.0, False, 126.0)
    assert w.update("rec", 7.0, False, 136.0)
