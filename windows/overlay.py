"""Small pill at the bottom of the screen: live waveform while recording, bouncing dots while processing, and a
short green check ("sent") or red ! ("error") when a dictation ends.

The window never takes focus and ignores the mouse, so pasting still goes to the app you were typing in.
Tk runs on the main thread and polls the shared state; other threads only set `state`, `level` and the flash
(`flash_kind` and its expiry `flash_until`; overlay_mode.py decides what shows).
"""
import logging
import math
import random
import sys
import time
import tkinter as tk

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


def _hwnd(root):
    return int(root.wm_frame(), 16)


def _win32_setup(root):
    """No focus, no taskbar button, click-through, always on top."""
    import ctypes
    GWL_EXSTYLE = -20
    WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TRANSPARENT = 0x8, 0x80, 0x08000000, 0x20
    hwnd = _hwnd(root)
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          style | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT)
    log.info("overlay hwnd=%s exstyle before=%#x after=%#x", hwnd, style, user32.GetWindowLongW(hwnd, GWL_EXSTYLE))


def _show(root, visible):
    """Show or hide without ever activating (stealing focus from) the window."""
    if sys.platform != "win32":
        if visible:
            root.deiconify()
        else:
            root.withdraw()
        return
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = _hwnd(root)
    if visible:
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
    else:
        user32.ShowWindow(hwnd, 0)  # SW_HIDE


def _work_area(root):
    """Screen area above the taskbar, in Tk pixels."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class Overlay:
    N_BARS = 11

    def __init__(self, app):
        self.app = app  # needs .state ("idle" | "rec" | "busy" | "listen"), .level (0..1), .flash_kind ("sent" | "error" | ""),
        #                 .flash_until and .listening (the running keep-listening session or None)
        self.root = tk.Tk()
        self.root.withdraw()
        self.scale = self.root.winfo_fpixels("1i") / 96.0
        s = self.scale
        self.w, self.h = int(132 * s), int(38 * s)

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY)
        try:
            self.root.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.visible = False

        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self._place()
        self.root.deiconify()
        self.root.update_idletasks()
        if sys.platform == "win32":
            _win32_setup(self.root)
        _show(self.root, False)
        self.root.report_callback_exception = self._report
        log.info("overlay ready %sx%s scale=%.2f geometry=%s", self.w, self.h, self.scale, self.root.geometry())

        self.t = 0.0
        self.smooth = 0.0
        self.heights = [0.0] * self.N_BARS
        self.hist = core.LevelHistory(self.N_BARS)
        self.sample_acc = 0
        self.phase = [random.random() * math.tau for _ in range(self.N_BARS)]
        self._shape()
        self.root.after(FPS_MS, self._tick)

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
        left, top, right, bottom = _work_area(self.root)
        x = left + (right - left - self.w) // 2
        y = bottom - self.h - int(18 * self.scale)
        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")

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
            self.t += FPS_MS / 1000
            meeting = getattr(self.app, "meeting", None)
            # Precedence (flash over the meeting timer, flash only while idle) lives in overlay_mode, which is tested.
            state = overlay_mode(self.app.state, self.app.flash_kind, self.app.flash_until, time.monotonic(),
                                 meeting is not None and meeting.active)
            want = state is not None
            if want:
                self._fit(state)
            if want and not self.visible:
                self._place()
                self.hist.reset()
                self.smooth = 0.0
                _show(self.root, True)
                self.visible = True
                log.info("overlay shown (%s)", state)
            elif not want and self.visible:
                _show(self.root, False)
                self.visible = False
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
