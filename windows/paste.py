"""Puts dictated text into the focused app (Windows), with these safeguards:
  - the text is only pasted when the window is still the one the dictation started in; otherwise it is left on
    the clipboard and the caller says so ("Copied; the window changed"), so it never lands in the wrong app;
  - a window that runs as administrator while Vox does not would silently drop the keys: the text is only copied and
    the caller says so (BLOCKED);
  - the paste waits until the hotkey's modifiers are up, so it is not combined with Win; terminals get Ctrl+Shift+V
    (in a terminal Ctrl+V is a control character), PuTTY and mintty Shift+Insert (they ignore Ctrl+Shift+V by default),
    everything else Ctrl+V;
  - the old clipboard (every format that can be copied as plain memory: text, rich text, cells, images, files) is
    put back only if the clipboard still holds our text (something the user copied while the paste ran is never
    overwritten), and only when the keep_clipboard setting is off (otherwise it is not even read);
  - the dictation is always marked "do not upload to the cloud clipboard"; unless the clipboard_history setting is on
    (the default) it is also marked "exclude from history", so Win+V does not keep it either.
`copy_selection` is the other direction, for edit by voice: Ctrl+C, read the text, put the old clipboard back.
The real clipboard, window and key calls live in SystemDeps; tests pass their own `deps`."""
import ctypes
import logging
import os
import re
import threading
import time
from ctypes import wintypes

log = logging.getLogger("vox")

PASTED = "pasted"
COPIED = "copied"
BLOCKED = "blocked"          # only copied: the window runs as administrator and Vox does not (R2-M6)

# Terminals read Ctrl+V as a control character; they paste with Ctrl+Shift+V (lower-case exe names), except the two in
# SHIFT_INSERT_TERMINALS: PuTTY and mintty (Git Bash) ignore Ctrl+Shift+V by default and paste with Shift+Insert.
SHIFT_INSERT_TERMINALS = frozenset({"mintty.exe", "putty.exe"})
TERMINALS = frozenset({"windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
                       "alacritty.exe", "wezterm-gui.exe"}) | SHIFT_INSERT_TERMINALS
COPY_WAIT = 0.5              # seconds to wait for the app to answer Ctrl+C (edit by voice)
# Editors whose Ctrl+C copies the whole current line (with its line break) when nothing is selected: in them, one line
# ending in a line break is taken as "nothing selected" (VS Code editor.emptySelectionClipboard, Visual Studio, JetBrains
# IDEs, Sublime Text copy_with_empty_selection; all on by default).
LINE_COPY_EDITORS = frozenset({"code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "vscodium.exe",
                               "devenv.exe", "sublime_text.exe", "idea64.exe", "pycharm64.exe", "webstorm64.exe",
                               "clion64.exe", "rider64.exe", "goland64.exe", "phpstorm64.exe", "rubymine64.exe",
                               "datagrip64.exe", "studio64.exe", "fleet.exe"})

MODIFIER_WAIT = 2.0          # seconds to wait for the hotkey's keys to come up
SETTLE = 0.05                # after copying, before Ctrl+V
PASTE_WAIT = 1.0             # after Ctrl+V, before the restore: slow targets (Electron under load, RDP, VMs) read the
                             # clipboard late and would paste the OLD text if it were put back sooner. Trade-off: the
                             # dictation can stay on the clipboard for about a second.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_VK_SHIFT, _VK_CONTROL, _VK_MENU, _VK_LWIN, _VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
_MODIFIER_KEYS = (_VK_SHIFT, _VK_CONTROL, _VK_MENU, _VK_LWIN, _VK_RWIN)

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 2
CLIP_OPEN_WAIT = 0.5         # seconds to keep trying while another program has the clipboard open
CLIP_SNAPSHOT_LIMIT = 64_000_000   # bytes; a clipboard bigger than this is not copied (the text-only path is used)
CLIP_FORMAT_LIMIT = 16_000_000     # bytes; one format bigger than this is left out of the copy (and logged)
# Formats that are not plain memory (GDI handles) cannot be copied this way; the DIB formats cover images.
# CF_TEXT and CF_OEMTEXT are skipped because Windows makes them from CF_UNICODETEXT.
_CF_SKIP = {1, 2, 3, 7, 9, 14}
# Registered formats that tell Windows clipboard history (Win+V), the cloud clipboard and clipboard monitors to
# ignore this content: (name, value). The first only needs to exist, the other two are DWORD 0.
_NO_CLOUD_FORMAT = ("CanUploadToCloudClipboard", b"\x00\x00\x00\x00")   # always set: a dictation never goes to the cloud
_NO_HISTORY_FORMATS = (("ExcludeClipboardContentFromMonitorProcessing", b"\x01"),
                       ("CanIncludeInClipboardHistory", b"\x00\x00\x00\x00"))
_PRIVATE_FORMATS = _NO_HISTORY_FORMATS + (_NO_CLOUD_FORMAT,)

_user32 = _kernel32 = _keyboard = None
# dwExtraInfo of every key Vox sends (its paste keys, the hotkey's Start-menu tap): the hotkey hook drops exactly these, so
# Vox's own Ctrl+Shift+V never looks like the user's shortcut (ENG-5), while keys that other programs send (PowerToys
# remaps, a mouse button macro, Voice Access, a software KVM) still count.
VOX_KEY_TAG = 0x566F7821   # "Vox!"


def keyboard_controller():
    """pynput's keyboard Controller, with VOX_KEY_TAG on every key it sends (Windows; elsewhere the plain Controller)."""
    global _keyboard
    if _keyboard is None:
        from pynput import keyboard
        if not isinstance(keyboard.Controller, type):   # a stand-in (tests): use it as it is
            return keyboard.Controller()
        try:
            from pynput._util.win32 import INPUT, INPUT_union, KEYBDINPUT, SendInput
        except (ImportError, AttributeError, OSError):
            _keyboard = keyboard.Controller()
            return _keyboard

        class Tagged(keyboard.Controller):
            def _handle(self, key, is_press):
                try:
                    ki = KEYBDINPUT(dwExtraInfo=VOX_KEY_TAG, **key._parameters(is_press))
                except ValueError:   # a character outside one UTF-16 unit: Vox never sends one, pynput's own way
                    return super()._handle(key, is_press)
                SendInput(1, ctypes.byref(INPUT(type=INPUT.KEYBOARD, value=INPUT_union(ki=ki))), ctypes.sizeof(INPUT))
        _keyboard = Tagged()
    return _keyboard


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
        u.GetClipboardSequenceNumber.restype = wintypes.DWORD
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        u.OpenClipboard.argtypes = [wintypes.HWND]
        u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.restype = wintypes.BOOL
        u.EnumClipboardFormats.argtypes = [wintypes.UINT]
        u.EnumClipboardFormats.restype = wintypes.UINT
        u.GetClipboardData.argtypes = [wintypes.UINT]
        u.GetClipboardData.restype = wintypes.HANDLE
        u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u.SetClipboardData.restype = wintypes.HANDLE
        u.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        u.RegisterClipboardFormatW.restype = wintypes.UINT
        u.CreateWindowExA.argtypes = [wintypes.DWORD, wintypes.LPCSTR, wintypes.LPCSTR, wintypes.DWORD, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                      wintypes.HINSTANCE, wintypes.LPVOID]
        u.CreateWindowExA.restype = wintypes.HWND
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.DestroyWindow.restype = wintypes.BOOL
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = wintypes.HGLOBAL
        k.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k.GlobalLock.restype = wintypes.LPVOID
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k.GlobalUnlock.restype = wintypes.BOOL
        k.GlobalSize.argtypes = [wintypes.HGLOBAL]
        k.GlobalSize.restype = ctypes.c_size_t
        k.GlobalFree.argtypes = [wintypes.HGLOBAL]
        k.GlobalFree.restype = wintypes.HGLOBAL
        _user32, _kernel32 = u, k
    return _user32, _kernel32


def _global_from_bytes(kernel32, data):
    """A new movable global memory block holding `data` (the clipboard takes ownership of it); None on failure."""
    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
    if not handle:
        return None
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        return None
    try:
        ctypes.memmove(ptr, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)
    return handle


class _Clipboard:
    """Opens the clipboard for the length of a `with`, trying for CLIP_OPEN_WAIT seconds while another program has it
    open. `owner` gives the clipboard a hidden window as owner, which writing needs (a null owner makes
    SetClipboardData fail after EmptyClipboard)."""

    def __init__(self, owner):
        self.owner, self.hwnd = owner, None

    def __enter__(self):
        user32, _ = _api()
        if self.owner:
            self.hwnd = user32.CreateWindowExA(0, b"STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
        deadline = time.monotonic() + CLIP_OPEN_WAIT
        while not user32.OpenClipboard(self.hwnd):
            if time.monotonic() >= deadline:
                self._destroy(user32)
                raise OSError("the clipboard is in use by another program")
            time.sleep(0.01)
        return self

    def _destroy(self, user32):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None

    def __exit__(self, *exc):
        user32, _ = _api()
        user32.CloseClipboard()
        self._destroy(user32)


def paste_chord(exe):
    """The keys that paste into the app `exe` (an exe name): ("shift", "insert") for PuTTY and mintty,
    ("ctrl", "shift", "v") for any other terminal, else ("ctrl", "v")."""
    exe = (exe or "").lower()
    if exe in SHIFT_INSERT_TERMINALS:
        return ("shift", "insert")
    return ("ctrl", "shift", "v") if exe in TERMINALS else ("ctrl", "v")


# What a paste into a terminal must not carry (SEC-1): a line break runs the line before it in shells without bracketed
# paste (cmd and PowerShell in conhost, old bash), and ESC or a C1 control can end bracketed paste early.
_LINE_BREAKS = re.compile(r"[ \t]*(?:\r\n|[\r\n\x0b\x0c\x85  ])[ \t]*")
_CONTROLS = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x9f]")


def terminal_text(text):
    """`text` as it may be pasted into a terminal: every line break (and tab) becomes a space and the other control
    characters (ESC included) are dropped, so the paste never presses Enter. The user presses Enter. A break at the very
    end is dropped rather than made a space."""
    text = text.rstrip("\r\n\x0b\x0c\x85  ")
    return _CONTROLS.sub("", _LINE_BREAKS.sub(" ", text).replace("\t", " "))


def wait_released(is_down, clock, sleep, limit, step=0.02):
    """Waits until `is_down()` is false, for at most `limit` seconds. True when the keys came up."""
    deadline = clock() + limit
    while is_down():
        if clock() >= deadline:
            return False
        sleep(step)
    return True


def _read_global(handle, size):
    """The bytes of a clipboard memory block (`handle`, `size` bytes); None when it cannot be locked."""
    _, kernel32 = _api()
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        kernel32.GlobalUnlock(handle)


def _token_elevated(kernel32, advapi32, process):
    """True/False whether a process handle's token is elevated; None when it cannot be read."""
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
        return None
    try:
        elevated, size = wintypes.DWORD(), wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, 20, ctypes.byref(elevated), 4, ctypes.byref(size)):   # TokenElevation
            return None
        return bool(elevated.value)
    finally:
        kernel32.CloseHandle(token)


def _put(user32, kernel32, fmt, data):
    """Sets one clipboard format to `data`; raises OSError when Windows refuses it."""
    handle = _global_from_bytes(kernel32, data)
    if not handle:
        raise OSError("out of memory for clipboard format %s" % fmt)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        raise OSError("SetClipboardData failed for format %s" % fmt)


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

    def clip_set(self, text, history=False):
        """Puts `text` on the clipboard (replacing everything on it), marked so that the cloud clipboard skips it and,
        unless `history` is true, Windows clipboard history (Win+V) skips it too. Raises OSError when the clipboard
        cannot be opened or written."""
        user32, kernel32 = _api()
        with _Clipboard(owner=True):
            user32.EmptyClipboard()
            _put(user32, kernel32, _CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
            for name, value in (_PRIVATE_FORMATS if not history else (_NO_CLOUD_FORMAT,)):
                try:
                    fmt = user32.RegisterClipboardFormatW(name)
                    if fmt:
                        _put(user32, kernel32, fmt, value)
                except OSError as e:
                    log.warning("clipboard history marker %s not set: %s", name, e)

    def clip_snapshot(self):
        """Every copyable format on the clipboard as [(format, bytes)], or None when it cannot be read, is empty or is
        too big. Used to put the user's clipboard back after the paste. A single format over CLIP_FORMAT_LIMIT is left
        out (logged with its number and size only)."""
        user32, kernel32 = _api()
        out, total = [], 0
        try:
            with _Clipboard(owner=False):
                fmt = user32.EnumClipboardFormats(0)
                while fmt:
                    # skipped: GDI-handle formats, the owner-display / DSP range and the private / GDI object ranges
                    if fmt not in _CF_SKIP and not 0x80 <= fmt <= 0x8E and not 0x200 <= fmt <= 0x3FF:
                        handle = user32.GetClipboardData(fmt)
                        size = kernel32.GlobalSize(handle) if handle else 0
                        if size > CLIP_FORMAT_LIMIT:
                            log.info("clipboard format %s (%d bytes) is too big to keep, it is not put back", fmt, size)
                        elif size:
                            data = _read_global(handle, size)
                            if data is not None:
                                out.append((fmt, data))
                                total += size
                                if total > CLIP_SNAPSHOT_LIMIT:
                                    return None
                    fmt = user32.EnumClipboardFormats(fmt)
        except OSError as e:
            log.warning("could not copy the clipboard before pasting: %s", e)
            return None
        return out or None

    def clip_restore(self, snapshot):
        """Puts a clip_snapshot() back, replacing what is on the clipboard now. It is marked private (ENG-9): the old
        item already went to Win+V history and the cloud clipboard once, if at all. A marker the snapshot holds keeps
        its own value."""
        user32, kernel32 = _api()
        with _Clipboard(owner=True):
            user32.EmptyClipboard()
            for fmt, data in snapshot:
                try:
                    _put(user32, kernel32, fmt, data)
                except OSError as e:
                    log.warning("clipboard format %s not restored: %s", fmt, e)
            held = {fmt for fmt, _ in snapshot}
            for name, value in _PRIVATE_FORMATS:
                try:
                    fmt = user32.RegisterClipboardFormatW(name)
                    if fmt and fmt not in held:
                        _put(user32, kernel32, fmt, value)
                except OSError as e:
                    log.warning("clipboard history marker %s not set: %s", name, e)

    def _send(self, vk, shift=False):
        """Ctrl (+ Shift) + the key `vk`. The virtual key, not the letter: on a layout with no Latin letters (Cyrillic,
        Greek, Hebrew, Arabic) the letter would be sent as a character and the shortcut would not work. Tagged
        (VOX_KEY_TAG) so the hotkey hook ignores them."""
        from pynput import keyboard
        kb = keyboard_controller()
        with kb.pressed(keyboard.Key.ctrl):
            if shift:
                with kb.pressed(keyboard.Key.shift):
                    kb.tap(keyboard.KeyCode.from_vk(vk))
            else:
                kb.tap(keyboard.KeyCode.from_vk(vk))

    def send_ctrl_v(self):
        self._send(0x56)

    def send_ctrl_shift_v(self):
        self._send(0x56, shift=True)

    def send_shift_insert(self):
        """Shift+Insert (PuTTY and mintty). pynput's Key.insert is the extended key, so it is not read as numpad 0."""
        from pynput import keyboard
        kb = keyboard_controller()
        with kb.pressed(keyboard.Key.shift):
            kb.tap(keyboard.Key.insert)

    def send_ctrl_c(self):
        self._send(0x43)

    def clip_sequence(self):
        """Windows' clipboard change counter: it changes whenever anything is copied."""
        user32, _ = _api()
        return user32.GetClipboardSequenceNumber()

    def wait_modifiers_released(self):
        """Waits (at most MODIFIER_WAIT seconds) until Shift, Ctrl, Alt and Win are all up. Logs when they did not."""
        user32, _ = _api()
        if not wait_released(lambda: any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_KEYS),
                             time.monotonic, time.sleep, MODIFIER_WAIT):
            log.warning("modifier keys still held after %.1f s, going on", MODIFIER_WAIT)

    def foreground_elevated(self):
        """True when the focused window's program runs as administrator (elevated), False when not, None when it
        cannot be told (an unreadable process: the paste goes ahead as before)."""
        user32, kernel32 = _api()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not process:
            return None
        try:
            return _token_elevated(kernel32, _advapi(), process)
        finally:
            kernel32.CloseHandle(process)

    def self_elevated(self):
        """True when Vox itself runs as administrator."""
        _, kernel32 = _api()
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        return bool(_token_elevated(kernel32, _advapi(), kernel32.GetCurrentProcess()))

    def sleep(self, seconds):
        time.sleep(seconds)


_advapi32 = None


def _advapi():
    global _advapi32
    if _advapi32 is None:
        a = ctypes.WinDLL("advapi32", use_last_error=True)
        a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        a.OpenProcessToken.restype = wintypes.BOOL
        a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD)]
        a.GetTokenInformation.restype = wintypes.BOOL
        _advapi32 = a
    return _advapi32


def _focused(deps):
    """The focused window's exe name ("" when there is none or it cannot be read; a failure is logged)."""
    try:
        return deps.foreground_exe() or ""
    except Exception as e:
        log.warning("could not read the focused window, pasting anyway: %s", e)
        return ""


def _blocked(deps):
    """True when the focused window runs as administrator and Vox does not, so Windows would drop the keys. Anything
    that cannot be told counts as not blocked (the paste goes ahead as before)."""
    try:
        return deps.foreground_elevated() is True and not deps.self_elevated()
    except Exception as e:
        log.warning("could not tell whether the window runs as administrator: %s", e)
        return False


def _window_changed(target_exe, current):
    """True only when we know the focused window (`current`) is not the one the dictation started in. A target that
    was never captured, a window that cannot be named, or a failed lookup all mean "cannot tell", so the paste goes
    ahead rather than losing the text."""
    return bool(target_exe) and bool(current) and current.lower() != target_exe.lower()


def paste_text(text, target_exe, keep_clipboard, deps=None, clipboard_history=True, background=False):
    """Pastes `text` into the focused app. Returns "pasted"; "copied" when the focused window is no longer
    `target_exe` ("" means any window); "blocked" when it runs as administrator and Vox does not. In the last two
    cases the text is left on the clipboard and no paste keys are sent. Into a terminal the text goes as one line
    (terminal_text). `clipboard_history` (the setting) lets
    Windows clipboard history (Win+V) keep the dictation; the old text put back afterwards is never added to it.
    `background`: returns once the paste keys are sent and puts the old clipboard back on a thread (wait_restored)."""
    deps = deps or SystemDeps()
    wait_restored()   # the clipboard of the paste before this one is back first
    deps.wait_modifiers_released()
    current = _focused(deps)
    if _window_changed(target_exe, current):
        deps.clip_set(text, clipboard_history)
        return COPIED
    if (current or target_exe or "").lower() in TERMINALS:
        text = terminal_text(text)   # SEC-1: no line break that would run a command the user never confirmed
    if _blocked(deps):
        deps.clip_set(text, clipboard_history)
        return BLOCKED
    # The old clipboard is only read when it will be put back: reading it makes the program that copied it render
    # every delayed format (a big Office copy can take seconds), for nothing when keep_clipboard is on.
    old = snapshot = None
    if not keep_clipboard:
        old = deps.clip_get()
        snapshot = deps.clip_snapshot()
    deps.clip_set(text, clipboard_history)
    deps.sleep(SETTLE)
    chord = paste_chord(current or target_exe)
    if chord == ("shift", "insert"):
        deps.send_shift_insert()
    elif chord == ("ctrl", "shift", "v"):
        deps.send_ctrl_shift_v()
    else:
        deps.send_ctrl_v()
    if keep_clipboard:
        return PASTED   # nothing is put back, so nothing to wait for (ENG-3)
    if background:
        global _restoring
        _restoring = threading.Thread(target=_restore, args=(deps, text, old, snapshot), daemon=True, name="vox-restore")
        _restoring.start()
    else:
        _restore(deps, text, old, snapshot)
    return PASTED


def _restore(deps, text, old, snapshot):
    """After the paste: waits PASTE_WAIT, then puts the old clipboard back. Never raises: Ctrl+V was already sent."""
    deps.sleep(PASTE_WAIT)
    # The text normally stays on the clipboard only when asked (keep_clipboard). Put the old one back unless it
    # is unknown or the user has copied something else in the meantime. With a snapshot every format comes back
    # (cells, rich text, images, files). Without one (unreadable or too big) only old text can be put back: "unknown"
    # includes '' there, because pyperclip returns '' (not None) when the clipboard holds an image or files, and
    # restoring '' would wipe them.
    try:
        if deps.clip_get() == text:
            if snapshot:
                deps.clip_restore(snapshot)
            elif isinstance(old, str) and old != "":
                deps.clip_set(old)
    except Exception as e:
        log.warning("could not put the old clipboard back: %s", e)


_restoring = None   # the thread putting the old clipboard back after a background paste (paste_text background=True)


def wait_restored(timeout=None):
    """Waits until the clipboard of the last background paste is put back (at most `timeout` seconds). Everything that
    reads or writes the clipboard calls it first, so a new paste never takes the last dictation for the old clipboard."""
    t = _restoring
    if t is not None and t.is_alive():
        t.join(timeout)


def copy_selection(target_exe, deps=None):
    """Edit by voice: the text selected in the focused app, as (text, "") or (None, what to tell the user). Sends Ctrl+C
    once the shortcut's keys are up, waits for the clipboard to change, reads the text and puts the old clipboard back
    (only when the copy happened). Refused in a terminal (there Ctrl+C stops the running program) and when the focused
    window is no longer `target_exe`."""
    deps = deps or SystemDeps()
    wait_restored()
    deps.wait_modifiers_released()
    current = _focused(deps)
    if _window_changed(target_exe, current):
        return None, "The window changed, so Vox did not edit anything."
    if (current or target_exe or "").lower() in TERMINALS:
        return None, "Edit by voice does not work in a terminal (Ctrl+C would stop the running program)."
    if _blocked(deps):
        return None, "The window in front runs as administrator, so Vox cannot copy from it or paste into it."
    old = deps.clip_get()
    snapshot = deps.clip_snapshot()
    before = deps.clip_sequence()
    deps.send_ctrl_c()
    waited = 0.0
    while deps.clip_sequence() == before and waited < COPY_WAIT:
        deps.sleep(0.02)
        waited += 0.02
    if deps.clip_sequence() == before:
        return None, "Select the text to change first, then hold the edit shortcut and say what to change."
    text = deps.clip_get()
    try:
        if snapshot:
            deps.clip_restore(snapshot)
        elif isinstance(old, str) and old != "":
            deps.clip_set(old)
    except Exception as e:
        log.warning("could not put the old clipboard back after copying the selection: %s", e)
    if not isinstance(text, str) or not text.strip():
        return None, "Select some text first: Vox could not read any text from the selection."
    if (current or target_exe or "").lower() in LINE_COPY_EDITORS and _one_line_with_break(text):
        return None, ("Select the text to change first. With nothing selected this editor copies the whole line (to edit "
                      "one whole line, select it without its line break).")
    return text, ""


def _one_line_with_break(text):
    """True for one line of text followed by its line break: what a line-copying editor puts there for no selection."""
    body = text[:-2] if text.endswith("\r\n") else text[:-1] if text.endswith("\n") else None
    return body is not None and "\n" not in body and "\r" not in body
