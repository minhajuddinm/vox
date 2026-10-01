"""Puts dictated text into the focused app (Windows), with three safeguards:
  - the text is only pasted when the window is still the one the dictation started in; otherwise it is left on
    the clipboard and the caller says so ("Copied; the window changed"), so it never lands in the wrong app;
  - Ctrl+V waits until the hotkey's modifiers are up, so it is not combined with Win;
  - the old clipboard is put back only if the clipboard still holds our text (something the user copied while
    the paste ran is never overwritten), and only when the keep_clipboard setting is off.
The real clipboard, window and key calls live in SystemDeps; tests pass their own `deps`."""
import ctypes
import logging
import os
import time
from ctypes import wintypes

log = logging.getLogger("vox")

PASTED = "pasted"
COPIED = "copied"

MODIFIER_WAIT = 2.0          # seconds to wait for the hotkey's keys to come up
SETTLE = 0.05                # after copying, before Ctrl+V
PASTE_WAIT = 1.0             # after Ctrl+V, before the restore: slow targets (Electron under load, RDP, VMs) read the
                             # clipboard late and would paste the OLD text if it were put back sooner. Trade-off: the
                             # dictation can stay on the clipboard for about a second.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_VK_SHIFT, _VK_CONTROL, _VK_MENU, _VK_LWIN, _VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
_MODIFIER_KEYS = (_VK_SHIFT, _VK_CONTROL, _VK_MENU, _VK_LWIN, _VK_RWIN)

_user32 = _kernel32 = _keyboard = None


def _api():
    """The Win32 functions, with their types set once (kept apart from ctypes.windll, which others share)."""
    global _user32, _kernel32
    if _user32 is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        _user32, _kernel32 = u, k
    return _user32, _kernel32


class SystemDeps:
    """The real thing: Win32 for the window and the keys, pyperclip for the clipboard, pynput for Ctrl+V."""

    def foreground_exe(self):
        """Lower-case exe name of the focused window ("" when there is none). Never reads the title.
        Raises OSError when the window's process cannot be opened."""
        user32, kernel32 = _api()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)

    def clip_get(self):
        """The clipboard text; None when it cannot be read."""
        import pyperclip
        try:
            return pyperclip.paste()
        except Exception:
            return None

    def clip_set(self, text):
        import pyperclip
        pyperclip.copy(text)

    def send_ctrl_v(self):
        global _keyboard
        from pynput import keyboard
        if _keyboard is None:
            _keyboard = keyboard.Controller()
        with _keyboard.pressed(keyboard.Key.ctrl):
            _keyboard.tap("v")

    def wait_modifiers_released(self):
        """Waits (at most MODIFIER_WAIT seconds) until Shift, Ctrl, Alt and Win are all up."""
        user32, _ = _api()
        deadline = time.monotonic() + MODIFIER_WAIT
        while time.monotonic() < deadline and any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_KEYS):
            time.sleep(0.02)

    def sleep(self, seconds):
        time.sleep(seconds)


def _window_changed(target_exe, deps):
    """True only when we know the focused window is not the one the dictation started in. A target that was never
    captured, a window that cannot be named, or a failed lookup all mean "cannot tell", so the paste goes ahead
    rather than losing the text."""
    if not target_exe:
        return False
    try:
        current = deps.foreground_exe()
    except Exception as e:
        log.warning("could not read the focused window, pasting anyway: %s", e)
        return False
    return bool(current) and current.lower() != target_exe.lower()


def paste_text(text, target_exe, keep_clipboard, deps=None):
    """Pastes `text` into the focused app. Returns "pasted", or "copied" when the focused window is no longer
    `target_exe` (the text is then left on the clipboard and Ctrl+V is not sent)."""
    deps = deps or SystemDeps()
    deps.wait_modifiers_released()
    if _window_changed(target_exe, deps):
        deps.clip_set(text)
        return COPIED
    old = deps.clip_get()
    deps.clip_set(text)
    deps.sleep(SETTLE)
    deps.send_ctrl_v()
    deps.sleep(PASTE_WAIT)
    # The text normally stays on the clipboard only when asked (keep_clipboard). Put the old one back unless it
    # is unknown or the user has copied something else in the meantime. "Unknown" includes '' : pyperclip returns
    # '' (not None) when the clipboard holds an image or files, and restoring '' would wipe them.
    if not keep_clipboard and isinstance(old, str) and old != "" and deps.clip_get() == text:
        deps.clip_set(old)
    return PASTED
