"""Small pill at the bottom of the screen: live waveform while recording, bouncing dots while processing, and a
short green check ("sent") or red ! ("error") when a dictation ends.

The window never takes focus and ignores the mouse, so pasting still goes to the app you were typing in.
Tk runs on the main thread and polls the shared state; other threads only set `state`, `level` and the flash
(`flash_kind` and its expiry `flash_until`; overlay_mode.py decides what shows).

While the pill is visible it is re-asserted every half second (put back on top, re-placed when the work area changed)
and checked; a window Windows keeps off the screen (hidden, minimised, cloaked, off every monitor) is rebuilt. The
checks are pure functions in overlay_guard.py; documentation/11-logs-and-diagnostics.md explains the log lines.
"""
import logging
import math
import random
import sys
import time
import tkinter as tk

import overlay_guard as guard
import vox_core as core
from overlay_mode import overlay_mode, pill_clock

KEY = "#010203"          # colour made fully transparent (gives the pill rounded corners)
BG = "#161618"
EDGE = "#3A3A3F"
BAR = "#F2F2F2"
REC_DOT = "#FF453A"
BUSY = "#F5B83D"
SENT = "#30D158"
WIDE = 230               # the pill's width while listening (px at 96 dpi): it holds a message; the usual pill is 132
FPS_MS = 33               # about 30 frames a second is plenty for a meter
SAMPLE_MS = 80            # one meter bar per 80 ms of voice (about a syllable)
log = logging.getLogger("vox.overlay")

WIN = sys.platform == "win32"
GWL_EXSTYLE = -20
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
DWMWA_CLOAKED = 14
_API = None
_failures = guard.RateLimit(60.0)   # one warning a minute for each Win32 call that keeps failing


def _api():
    """The user32 and dwmapi functions the pill uses, with argument and result types (a handle is 64 bits; without
    types ctypes passes ints as C ints and nobody looks at the BOOL result). On private WinDLL objects so the types do
    not change ctypes.windll.user32 for the rest of Vox; use_last_error so a failure is logged with its code."""
    global _API
    if _API is None:
        import ctypes
        import types
        from ctypes import wintypes as w

        def fn(dll, name, restype, *argtypes):
            f = getattr(dll, name)
            f.restype, f.argtypes = restype, argtypes
            return f

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]

        u = ctypes.WinDLL("user32", use_last_error=True)
        enum_proc = ctypes.WINFUNCTYPE(w.BOOL, w.HMONITOR, w.HDC, ctypes.POINTER(w.RECT), w.LPARAM)
        i = ctypes.c_int
        api = types.SimpleNamespace(
            ctypes=ctypes, RECT=w.RECT, DWORD=w.DWORD, MONITORINFO=MONITORINFO, MONITORENUMPROC=enum_proc,
            GetWindowLongW=fn(u, "GetWindowLongW", w.LONG, w.HWND, i),
            SetWindowLongW=fn(u, "SetWindowLongW", w.LONG, w.HWND, i, w.LONG),
            SetWindowPos=fn(u, "SetWindowPos", w.BOOL, w.HWND, w.HWND, i, i, i, i, w.UINT),
            ShowWindow=fn(u, "ShowWindow", w.BOOL, w.HWND, i),   # returns the previous visibility, not success
            IsWindowVisible=fn(u, "IsWindowVisible", w.BOOL, w.HWND),
            IsIconic=fn(u, "IsIconic", w.BOOL, w.HWND),
            GetWindowRect=fn(u, "GetWindowRect", w.BOOL, w.HWND, ctypes.POINTER(w.RECT)),
            GetForegroundWindow=fn(u, "GetForegroundWindow", w.HWND),
            SetForegroundWindow=fn(u, "SetForegroundWindow", w.BOOL, w.HWND),
            EnumDisplayMonitors=fn(u, "EnumDisplayMonitors", w.BOOL, w.HDC, ctypes.POINTER(w.RECT), enum_proc, w.LPARAM),
            GetMonitorInfoW=fn(u, "GetMonitorInfoW", w.BOOL, w.HMONITOR, ctypes.POINTER(MONITORINFO)),
            SystemParametersInfoW=fn(u, "SystemParametersInfoW", w.BOOL, w.UINT, w.UINT, ctypes.c_void_p, w.UINT),
            DwmGetWindowAttribute=None)
        try:
            api.DwmGetWindowAttribute = fn(ctypes.WinDLL("dwmapi"), "DwmGetWindowAttribute", ctypes.c_long, w.HWND,
                                           w.DWORD, ctypes.c_void_p, w.DWORD)
        except (OSError, AttributeError):
            pass
        _API = api
    return _API


def _check(ok, what):
    """`ok` as a bool; a failure is logged (with the Windows error code) at most once a minute per call."""
    if not ok:
        err = _api().ctypes.get_last_error()
        if _failures.allow(what, time.monotonic()):
            log.warning("overlay: %s failed (Windows error %d; %d more since the last warning)", what, err,
                        _failures.held_back(what))
    return bool(ok)


def _rect(r):
    return (r.left, r.top, r.right, r.bottom)


def _hwnd(win):
    return int(win.wm_frame(), 16)


def _win32_setup(win):
    """No focus, no taskbar button, click-through, always on top. Returns the window handle."""
    api = _api()
    hwnd = _hwnd(win)
    style = api.GetWindowLongW(hwnd, GWL_EXSTYLE)
    api.ctypes.set_last_error(0)
    if not api.SetWindowLongW(hwnd, GWL_EXSTYLE, style | guard.WS_EX_TOPMOST | guard.STYLE_BITS):
        _check(api.ctypes.get_last_error() == 0, "SetWindowLongW")   # 0 can be a real old style: only an error code fails
    log.info("overlay hwnd=%s exstyle before=%#x after=%#x", hwnd, style & 0xFFFFFFFF,
             api.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF)
    return hwnd


def _raise(hwnd):
    """Puts the pill on top of the other topmost windows and shows it, without activating it."""
    _check(_api().SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW),
           "SetWindowPos")


def _show(win, visible):
    """Show or hide without ever activating (stealing focus from) the window."""
    if not WIN:
        if visible:
            win.deiconify()
        else:
            win.withdraw()
        return
    hwnd = _hwnd(win)
    if visible:
        _raise(hwnd)
        _api().ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    else:
        _api().ShowWindow(hwnd, SW_HIDE)


def _work_area(win):
    """Screen area above the taskbar (main monitor), in Tk pixels."""
    if WIN:
        api = _api()
        r = api.RECT()
        if _check(api.SystemParametersInfoW(0x30, 0, api.ctypes.byref(r), 0), "SystemParametersInfoW"):  # SPI_GETWORKAREA
            return _rect(r)
    return 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()


def _probe(hwnd):
    """What Windows says about the pill window, as overlay_guard reads it; None for what could not be read."""
    api = _api()
    ct = api.ctypes
    r = api.RECT()
    rect = _rect(r) if _check(api.GetWindowRect(hwnd, ct.byref(r)), "GetWindowRect") else None
    monitors, works = [], []

    def each(hmon, hdc, lprc, data):
        mi = api.MONITORINFO()
        mi.cbSize = ct.sizeof(mi)
        if api.GetMonitorInfoW(hmon, ct.byref(mi)):
            monitors.append(_rect(mi.rcMonitor))
            works.append(_rect(mi.rcWork))
        return True

    _check(api.EnumDisplayMonitors(None, None, api.MONITORENUMPROC(each), 0), "EnumDisplayMonitors")
    cloaked = None
    if api.DwmGetWindowAttribute is not None:
        v = api.DWORD()
        if api.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ct.byref(v), ct.sizeof(v)) == 0:
            cloaked = v.value
    ct.set_last_error(0)
    style = api.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style = None if style == 0 and ct.get_last_error() else style & 0xFFFFFFFF
    return {"hwnd": hwnd, "visible": bool(api.IsWindowVisible(hwnd)), "iconic": bool(api.IsIconic(hwnd)),
            "cloaked": cloaked, "rect": rect, "monitors": monitors, "work": works, "exstyle": style}


class Overlay:
    N_BARS = 11

    def __init__(self, app):
        self.app = app  # needs .state ("idle" | "rec" | "busy" | "listen"), .level (0..1), .flash_kind ("sent" | "error" | ""),
        #                 .flash_until and .listening (the running keep-listening session or None)
        self.root = tk.Tk()   # never shown: it owns the Tk loop; the pill is a Toplevel so it can be rebuilt
        self.root.withdraw()
        self.root.report_callback_exception = self._report
        self.scale = self.root.winfo_fpixels("1i") / 96.0
        s = self.scale
        self.w, self.h = int(132 * s), int(38 * s)
        self.visible = False
        self.win = self.canvas = self.hwnd = None
        self.area = None            # the work area the pill was last placed in
        self.asserted = 0.0         # time.monotonic() of the last re-assert
        self.last_state = None      # what the previous tick showed
        self.rebuilds = 0           # rebuilds since the pill was last shown
        self.rebuild_limit = guard.RateLimit(guard.REBUILD_SECONDS)
        self.repair_log = guard.RateLimit(60.0)
        self.last_tick = time.monotonic()   # heartbeat, read by the engine's watchdog thread
        self._build()
        log.info("overlay ready %sx%s scale=%.2f geometry=%s", self.w, self.h, self.scale, self.win.geometry())

        self.t = 0.0
        self.smooth = 0.0
        self.heights = [0.0] * self.N_BARS
        self.hist = core.LevelHistory(self.N_BARS)
        self.sample_acc = 0
        self.phase = [random.random() * math.tau for _ in range(self.N_BARS)]
        self._shape()
        self.root.after(FPS_MS, self._tick)

    def _build(self):
        """Creates the pill window (hidden) and drops the old one, if any. A new window gets a new handle and lands on
        the current virtual desktop."""
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=KEY)
        try:
            win.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        canvas = tk.Canvas(win, width=self.w, height=self.h, bg=KEY, highlightthickness=0, bd=0)
        canvas.pack()
        old, self.win, self.canvas = self.win, win, canvas
        self._place()
        win.deiconify()
        win.update_idletasks()
        if WIN:
            self.hwnd = _win32_setup(win)
        _show(win, False)
        if old is not None:
            old.destroy()

    def _shape(self):
        """The check mark and the "!" never change size: their coordinates are worked out when the pill's width is set,
        not every frame."""
        s = self.scale
        cx, cy = self.w / 2, self.h / 2
        self.check = (cx - 7.5 * s, cy + 0.5 * s, cx - 2.5 * s, cy + 5.5 * s, cx + 7.5 * s, cy - 5.5 * s)
        self.bang = (cx, cy - 9 * s, cx, cy + 2.5 * s)
        self.bang_dot = (cx - 1.9 * s, cy + 6.1 * s, cx + 1.9 * s, cy + 9.9 * s)

    def _fit(self, state):
        """The pill is wider while listening, and back to its usual width for everything else."""
        w = int((WIDE if state == "listen" else 132) * self.scale)
        if w != self.w:
            self.w = w
            self.canvas.config(width=w)
            self._shape()
            self._place()

    def _place(self):
        self.area = left, top, right, bottom = _work_area(self.win)
        x = left + (right - left - self.w) // 2
        y = bottom - self.h - int(18 * self.scale)
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")

    # ------------------------------------------------------- staying on screen
    def _keep_up(self, state, now):
        """Every tick while the pill is wanted: a full re-assert when a recording, a listening session or meeting
        notes start (even if the pill was already up), else a cheap one every ASSERT_SECONDS."""
        entered = state in guard.FORCE_STATES and state != self.last_state
        if entered or guard.assert_due(now, self.asserted):
            self._assert(state, now, force=entered)

    def _look(self, hwnd):
        p = _probe(hwnd)
        p["tk_mapped"] = bool(self.win.winfo_ismapped())   # Tk draws only a mapped window
        return p

    def _assert(self, state, now, force=False):
        """Checks the pill window, puts it back on top (re-placed when the work area changed) and repairs what was
        wrong, logged at INFO with the facts; a window still off the screen after that is rebuilt."""
        self.asserted = now
        hwnd = _hwnd(self.win)
        before = self._look(hwnd)
        found = guard.problems(before)
        if hwnd != self.hwnd:
            found.append("new window")   # Tk made a new frame window: our extended styles are not on it
        if hwnd != self.hwnd or "style lost" in found:
            self.hwnd = _win32_setup(self.win)
        if force or found or _work_area(self.win) != self.area:
            self._place()
        _raise(hwnd)
        if force or found:
            _api().ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        if not found:
            return
        left = guard.problems(self._look(hwnd))
        key = tuple(found)
        if self.repair_log.allow(key, now):
            log.info("overlay repair: %s; after: %s; %s (%d like this held back)", ", ".join(found),
                     ", ".join(left) or "fixed", guard.describe(before, self.area, state), self.repair_log.held_back(key))
        bad = guard.needs_rebuild(left)
        if bad and self.rebuilds < guard.MAX_REBUILDS and self.rebuild_limit.allow("rebuild", now):
            self._rebuild(state, bad)

    def _rebuild(self, state, why):
        """A new pill window in place of one Windows keeps off the screen; it never keeps the focus."""
        self.rebuilds += 1
        api = _api()
        fg, old = api.GetForegroundWindow(), self.hwnd
        self._build()
        _show(self.win, True)
        if fg and api.GetForegroundWindow() == self.hwnd:   # the new window took the focus: give it back
            api.SetForegroundWindow(fg)
            log.warning("overlay rebuild took the focus; gave it back")
        p = self._look(self.hwnd)
        log.info("overlay rebuilt (%s): hwnd %s -> %s; now: %s; %s", ", ".join(why), old, self.hwnd,
                 ", ".join(guard.problems(p)) or "ok", guard.describe(p, self.area, state))

    # ------------------------------------------------------------- drawing
    def _pill(self, fill, outline):
        c, w, h = self.canvas, self.w, self.h
        r = h / 2
        c.create_oval(0, 0, h, h - 1, fill=outline, outline="")
        c.create_oval(w - h, 0, w - 1, h - 1, fill=outline, outline="")
        c.create_rectangle(r, 0, w - r, h - 1, fill=outline, outline="")
        i = max(1, int(self.scale))
        c.create_oval(i, i, h - i, h - 1 - i, fill=fill, outline="")
        c.create_oval(w - h + i, i, w - 1 - i, h - 1 - i, fill=fill, outline="")
        c.create_rectangle(r, i, w - r, h - 1 - i, fill=fill, outline="")

    def _draw_recording(self):
        c, s = self.canvas, self.scale
        self.smooth += (self.app.level - self.smooth) * 0.5
        self.sample_acc += FPS_MS
        if self.sample_acc >= SAMPLE_MS:   # the bars scroll: newest voice on the right
            self.sample_acc = 0
            self.hist.push(self.smooth)
        cx, cy = 17 * s, self.h / 2
        if getattr(self.app, "hands_free", False):
            # hands-free: a stop square means "press the shortcut again to finish"
            q = 4.2 * s
            c.create_rectangle(cx - q, cy - q, cx + q, cy + q, fill=REC_DOT, outline="")
        else:
            # hold mode: red dot, gently pulsing
            pr = (3.2 + 0.8 * math.sin(self.t * 6)) * s
            c.create_oval(cx - pr, cy - pr, cx + pr, cy + pr, fill=REC_DOT, outline="")
        # bars
        gap, bw = 7.5 * s, 3 * s
        x0 = 32 * s
        mid = (self.N_BARS - 1) / 2
        for i in range(self.N_BARS):
            target = 0.12 + 0.88 * self.hist.values[i]
            self.heights[i] += (target - self.heights[i]) * 0.5
            bh = max(bw, self.heights[i] * (self.h - 14 * s))
            x = x0 + i * gap
            c.create_line(x, cy - bh / 2, x, cy + bh / 2, fill=BAR, width=bw, capstyle=tk.ROUND)

    def _draw_busy(self):
        c, s = self.canvas, self.scale
        cy = self.h / 2
        for i in range(3):
            y = cy - 4 * s * max(0.0, math.sin(self.t * 7 - i * 0.7))
            x = self.w / 2 + (i - 1) * 13 * s
            r = 3.4 * s
            c.create_oval(x - r, y - r, x + r, y + r, fill=BUSY, outline="")

    def _draw_sent(self):
        self.canvas.create_line(*self.check, fill=SENT, width=3 * self.scale, capstyle=tk.ROUND, joinstyle=tk.ROUND)

    def _draw_error(self):
        c = self.canvas
        c.create_line(*self.bang, fill=REC_DOT, width=3 * self.scale, capstyle=tk.ROUND)
        c.create_oval(*self.bang_dot, fill=REC_DOT, outline="")

    def _draw_meeting(self):
        c, s = self.canvas, self.scale
        cy = self.h / 2
        pr = (3.4 + 0.9 * math.sin(self.t * 3)) * s
        cx = 19 * s
        c.create_oval(cx - pr, cy - pr, cx + pr, cy + pr, fill=REC_DOT, outline="")
        label = "Notes  " + pill_clock(self.app.meeting.elapsed())
        c.create_text(self.w / 2 + 8 * s, cy, text=label, fill=BAR, font=("Segoe UI", 10, "bold"))

    def _draw_listening(self):
        c, s = self.canvas, self.scale
        cx, cy, q = 17 * s, self.h / 2, 4.2 * s
        c.create_rectangle(cx - q, cy - q, cx + q, cy + q, fill=REC_DOT, outline="")   # stop square: double-press to end
        lis = self.app.listening
        text = (lis.message or "Listening  " + pill_clock(lis.seconds)) if lis else "Listening"
        c.create_text(32 * s, cy, anchor="w", text=text, fill=BAR, font=("Segoe UI", 10, "bold"))

    # ---------------------------------------------------------------- loop
    def _tick(self):
        try:
            now = self.last_tick = time.monotonic()
            self.t += FPS_MS / 1000
            meeting = getattr(self.app, "meeting", None)
            # Precedence (flash over the meeting timer, flash only while idle) lives in overlay_mode, which is tested.
            state = overlay_mode(self.app.state, self.app.flash_kind, self.app.flash_until, now,
                                 meeting is not None and meeting.active)
            want = state is not None
            if want:
                self._fit(state)
            if want and not self.visible:
                self._place()
                self.hist.reset()
                self.smooth = 0.0
                self.rebuilds = 0
                _show(self.win, True)
                self.visible = True
                log.info("overlay shown (%s)", state)
            elif not want and self.visible:
                _show(self.win, False)
                self.visible = False
            if want and WIN:
                self._keep_up(state, now)
            self.last_state = state
            if self.visible:
                self.canvas.delete("all")
                self._pill(BG, EDGE)
                if state == "busy":
                    self._draw_busy()
                elif state == "meet":
                    self._draw_meeting()
                elif state == "listen":
                    self._draw_listening()
                elif state == "sent":
                    self._draw_sent()
                elif state == "error":
                    self._draw_error()
                else:
                    self._draw_recording()
        except Exception:
            log.exception("overlay tick failed")
        self.root.after(FPS_MS, self._tick)

    def _report(self, exc, val, tb):
        log.error("Tk callback error", exc_info=(exc, val, tb))

    def run(self):
        self.root.mainloop()

    def stop(self):
        self.root.after(0, self.root.destroy)
