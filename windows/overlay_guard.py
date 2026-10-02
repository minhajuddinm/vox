"""Keeps the pill on screen. Pure checks (no Tk, no Win32) used by overlay.py, which re-asserts the visible pill
every half second, and by engine.py's watchdog thread; unit tested in tests/test_overlay_guard.py.

A probe is a dict of what Windows says about the pill window: visible, iconic, cloaked (DWMWA_CLOAKED: nonzero when
Windows keeps the window off the screen, e.g. it is on another virtual desktop), rect and monitors as
(left, top, right, bottom), exstyle, and tk_mapped (whether Tk thinks the pill is mapped; Tk draws only mapped windows,
so an unmapped pill is shown but fully transparent). A value of None means "could not be read" and is never a problem.
"""
import sys
import threading
import traceback

ASSERT_SECONDS = 0.5     # a visible pill is put back on top, and checked, this often
REBUILD_SECONDS = 10.0   # at most one window rebuild this often
MAX_REBUILDS = 3         # rebuilds while the pill stays visible; more would only flicker
STALL_SECONDS = 3.0      # the Tk tick not running this long while not idle logs a thread dump
STUCK_SECONDS = 10.0     # a state that does not match the engine's flags this long is reset
FORCE_STATES = ("rec", "listen", "meet")   # entering one of these re-asserts the pill in full, even if already visible

WS_EX_TOPMOST = 0x8
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
STYLE_BITS = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT   # what overlay._win32_setup adds
REBUILD_ON = ("hidden", "minimised", "cloaked", "off-screen")          # still there after a re-assert: rebuild


def assert_due(now, last, period=ASSERT_SECONDS):
    return now - last >= period


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0


def on_screen(rect, monitors, share=0.5):
    """True when at least `share` of `rect` lies on the monitors (which do not overlap each other)."""
    area = (rect[2] - rect[0]) * (rect[3] - rect[1])
    return area > 0 and sum(_overlap(rect, m) for m in monitors) >= share * area


def problems(p):
    """What keeps the pill from being seen, in a fixed order; [] when nothing does."""
    out = []
    if p.get("visible") is False:
        out.append("hidden")
    if p.get("iconic"):
        out.append("minimised")
    if p.get("cloaked"):
        out.append("cloaked")
    if p.get("rect") is not None and p.get("monitors") and not on_screen(p["rect"], p["monitors"]):
        out.append("off-screen")
    style = p.get("exstyle")
    if style is not None and not style & WS_EX_TOPMOST:
        out.append("not topmost")
    if style is not None and style & STYLE_BITS != STYLE_BITS:
        out.append("style lost")
    if p.get("tk_mapped") is False:
        out.append("tk unmapped")
    return out


def needs_rebuild(found):
    return [x for x in found if x in REBUILD_ON]


def describe(p, work_area, state):
    """One log line's worth of facts about the pill window."""
    style = p.get("exstyle")
    hwnd = p.get("hwnd")
    return ("state=%s hwnd=%s visible=%s iconic=%s cloaked=%s tk_mapped=%s rect=%s work_area=%s monitors=%s works=%s "
            "exstyle=%s" % (state, hex(hwnd) if hwnd else hwnd, p.get("visible"), p.get("iconic"), p.get("cloaked"),
                            p.get("tk_mapped"), p.get("rect"), work_area, p.get("monitors"), p.get("work"),
                            "%#x" % style if style is not None else None))


class RateLimit:
    """allow(key, now) is True at most once per `seconds` for each key; held_back(key) counts the refusals since."""

    def __init__(self, seconds):
        self.seconds = seconds
        self._last, self._held = {}, {}

    def allow(self, key, now):
        last = self._last.get(key)
        if last is not None and now - last < self.seconds:
            self._held[key] = self._held.get(key, 0) + 1
            return False
        self._last[key] = now
        return True

    def held_back(self, key):
        return self._held.pop(key, 0)


def tick_stalled(last_tick, now, active, limit=STALL_SECONDS):
    """The overlay's 33 ms tick has not run for `limit` seconds while something should be on the pill."""
    return bool(active) and last_tick > 0 and now - last_tick > limit


def thread_dump():
    """Every thread's name and stack, as text for the log (what faulthandler prints, through logging)."""
    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():
        parts.append("Thread %s (%s):\n%s" % (names.get(ident, "?"), ident, "".join(traceback.format_stack(frame))))
    return "\n".join(parts)


def state_consistent(state, recording, busy, listening, age, max_age):
    """Whether Engine.state matches the flags that should go with it. `age`: seconds since the state was set; a
    recording older than `max_age` (the longest the audio callback allows) means the callback stopped."""
    if state == "rec":
        return bool(recording) and age <= max_age
    if state == "busy":
        return bool(busy)
    if state == "listen":
        return listening is not None
    return True


class StuckWatch:
    """update(...) is True once the same state (same name, same time set) has been inconsistent for `grace` s."""

    def __init__(self, grace=STUCK_SECONDS):
        self.grace = grace
        self._mark = None   # ((state, since), first seen inconsistent)

    def update(self, state, since, consistent, now):
        if consistent:
            self._mark = None
            return False
        key = (state, since)
        if self._mark is None or self._mark[0] != key:
            self._mark = (key, now)
            return False
        return now - self._mark[1] >= self.grace
