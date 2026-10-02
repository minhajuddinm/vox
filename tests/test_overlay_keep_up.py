"""The pill's re-assert and repair (Overlay._keep_up / _assert / _rebuild in windows/overlay.py) with every Win32 call
and the Tk window faked: no window is created and nothing touches the screen."""
import logging
import types

import pytest

pytest.importorskip("tkinter")
import overlay  # noqa: E402
import overlay_guard as guard  # noqa: E402

AREA = (0, 0, 1920, 1040)
GOOD = {"visible": True, "iconic": False, "cloaked": 0, "rect": (894, 984, 1026, 1022), "monitors": [(0, 0, 1920, 1080)],
        "work": [AREA], "exstyle": guard.WS_EX_TOPMOST | guard.STYLE_BITS | guard.WS_EX_LAYERED}


class FakeWin:
    def __init__(self, hwnd):
        self.hwnd, self.geometries, self.mapped = hwnd, [], True

    def winfo_ismapped(self):
        return self.mapped

    def geometry(self, g=None):
        self.geometries.append(g)


class World:
    """The faked Windows: probes are answered from a queue (the last one repeats), calls are recorded."""

    def __init__(self, monkeypatch):
        self.probes, self.calls, self.area, self.fg = [dict(GOOD)], [], AREA, 0x99
        monkeypatch.setattr(overlay, "_hwnd", lambda win: win.hwnd)
        monkeypatch.setattr(overlay, "_probe", self.probe)
        monkeypatch.setattr(overlay, "_raise", lambda hwnd: self.calls.append(("raise", hwnd)))
        monkeypatch.setattr(overlay, "_show", lambda win, v: self.calls.append(("show", win.hwnd, v)))
        monkeypatch.setattr(overlay, "_work_area", lambda win: self.area)
        monkeypatch.setattr(overlay, "_win32_setup", lambda win: self.calls.append(("setup", win.hwnd)) or win.hwnd)
        api = types.SimpleNamespace(ShowWindow=lambda hwnd, cmd: self.calls.append(("showwindow", hwnd, cmd)),
                                    GetForegroundWindow=lambda: self.fg,
                                    SetForegroundWindow=lambda hwnd: self.calls.append(("foreground", hwnd)))
        monkeypatch.setattr(overlay, "_api", lambda: api)

    def probe(self, hwnd):
        p = self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]
        return {**p, "hwnd": hwnd}

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


@pytest.fixture
def pill(world):
    o = object.__new__(overlay.Overlay)
    o.w, o.h, o.scale = 132, 38, 1.0
    o.win, o.hwnd, o.area = FakeWin(0x10), 0x10, AREA
    o.asserted, o.last_state, o.rebuilds = 0.0, None, 0
    o.rebuild_limit, o.repair_log = guard.RateLimit(guard.REBUILD_SECONDS), guard.RateLimit(60.0)
    built = []

    def build():
        o.win = FakeWin(0x20 + len(built))
        o.hwnd = o.win.hwnd
        built.append(o.win.hwnd)
    o._build, o.built = build, built
    return o


def repair_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(("overlay repair", "overlay rebuilt"))]


# ---------------------------------------------------------------- the cheap re-assert
def test_a_healthy_pill_is_put_back_on_top_quietly(pill, world, caplog):
    caplog.set_level(logging.INFO, "vox.overlay")
    pill._assert("busy", 10.0)
    assert world.calls == [("raise", 0x10)] and pill.win.geometries == []
    assert repair_lines(caplog) == [] and pill.asserted == 10.0


def test_a_changed_work_area_places_the_pill_again(pill, world):
    world.area = (0, 0, 2560, 1400)   # a monitor or the taskbar changed
    pill._assert("rec", 1.0)
    assert pill.area == (0, 0, 2560, 1400) and pill.win.geometries == ["132x38+1214+1344"]


def test_a_forced_assert_places_and_shows_without_activating(pill, world):
    pill._assert("rec", 1.0, force=True)
    assert pill.win.geometries and ("showwindow", 0x10, overlay.SW_SHOWNOACTIVATE) in world.calls


# ---------------------------------------------------------------- repairs
def test_a_hidden_pill_that_comes_back_is_logged_as_fixed(pill, world, caplog):
    caplog.set_level(logging.INFO, "vox.overlay")
    world.probes = [{**GOOD, "visible": False}, dict(GOOD)]
    pill._assert("rec", 1.0)
    (line,) = repair_lines(caplog)
    assert line.startswith("overlay repair: hidden; after: fixed; state=rec hwnd=0x10")
    assert "work_area=(0, 0, 1920, 1040)" in line and "monitors=[(0, 0, 1920, 1080)]" in line
    assert pill.built == [] and "showwindow" in world.names()


def test_a_pill_still_off_the_screen_after_the_repair_is_rebuilt(pill, world, caplog):
    caplog.set_level(logging.INFO, "vox.overlay")
    world.probes = [{**GOOD, "cloaked": 2}, {**GOOD, "cloaked": 2}, dict(GOOD)]
    pill._assert("rec", 1.0)
    repair, rebuilt = repair_lines(caplog)
    assert repair.startswith("overlay repair: cloaked; after: cloaked;") and "cloaked=2" in repair
    assert rebuilt.startswith("overlay rebuilt (cloaked): hwnd 16 -> 32; now: ok;")
    assert pill.built == [0x20] and ("show", 0x20, True) in world.calls


def test_problems_that_do_not_hide_the_pill_are_logged_but_never_rebuild(pill, world, caplog):
    caplog.set_level(logging.INFO, "vox.overlay")
    pill.win.mapped = False   # Tk thinks the pill is unmapped (so it would not draw it): logged for the diagnosis
    world.probes = [{**GOOD, "exstyle": guard.STYLE_BITS}]
    pill._assert("rec", 1.0)
    (line,) = repair_lines(caplog)
    assert line.startswith("overlay repair: not topmost, tk unmapped; after: not topmost, tk unmapped;")
    assert pill.built == []


def test_rebuilds_are_rate_limited_and_capped_while_the_pill_stays_up(pill, world):
    world.probes = [{**GOOD, "visible": False}]   # never fixed
    for t in (1.0, 2.0, 11.0, 21.0, 31.0, 41.0):
        pill._assert("rec", t)
    assert len(pill.built) == guard.MAX_REBUILDS == 3   # at 1, 11 and 21 s; then the cap holds


def test_the_same_repair_is_logged_once_a_minute(pill, world, caplog):
    caplog.set_level(logging.INFO, "vox.overlay")
    world.probes = [{**GOOD, "exstyle": guard.STYLE_BITS}]
    for t in (1.0, 1.5, 2.0, 61.0):
        pill._assert("rec", t)
    lines = repair_lines(caplog)
    assert len(lines) == 2 and lines[1].endswith("(2 like this held back)")


def test_a_new_frame_window_gets_the_styles_again(pill, world, caplog):
    caplog.set_level(logging.INFO, "vox.overlay")
    pill.win.hwnd = 0x30   # Tk recreated its frame window
    pill._assert("busy", 1.0)
    assert ("setup", 0x30) in world.calls and pill.hwnd == 0x30
    assert repair_lines(caplog)[0].startswith("overlay repair: new window; after: fixed;")


def test_a_rebuild_that_took_the_focus_gives_it_back(pill, world):
    world.probes = [{**GOOD, "visible": False}]
    pill._build = lambda: (setattr(pill, "win", FakeWin(0x40)), setattr(pill, "hwnd", 0x40),
                           setattr(world, "fg", 0x40) if world.fg == 0x99 else None)
    pill._rebuild("rec", ["hidden"])
    assert ("foreground", 0x99) in world.calls


# ---------------------------------------------------------------- when to assert
def test_starting_a_recording_forces_a_full_assert_even_when_the_pill_is_up(pill, world):
    pill.last_state, pill.asserted = "sent", 1.0          # a flash was showing, asserted just now
    pill._keep_up("rec", 1.01)
    assert pill.win.geometries and "showwindow" in world.names()


def test_listening_and_meeting_notes_force_it_too_but_busy_does_not(pill, world):
    for state in ("listen", "meet"):
        pill.last_state, pill.asserted, world.calls = "busy", 1.0, []
        pill._keep_up(state, 1.01)
        assert "showwindow" in world.names(), state
    pill.last_state, pill.asserted, world.calls = "rec", 1.0, []
    pill._keep_up("busy", 1.01)
    assert world.calls == []


def test_a_steady_state_is_asserted_every_half_second(pill, world):
    pill.last_state, pill.asserted = "rec", 1.0
    pill._keep_up("rec", 1.4)
    assert world.calls == []
    pill._keep_up("rec", 1.5)
    assert world.calls == [("raise", 0x10)]
