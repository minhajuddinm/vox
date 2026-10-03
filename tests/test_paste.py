"""paste_text: the safer paste (window check, clipboard restored only if it still holds our text).
Everything goes through injected fakes, so no clipboard, window or key is touched."""
import logging

import pytest

import paste


class FakeDeps:
    def __init__(self, foreground="notepad.exe", clip="old"):
        self.foreground, self.clip, self.calls = foreground, clip, []
        self.during_wait = None     # runs while paste_text sleeps after Ctrl+V: the user doing something else
        self.snapshot, self.restore_error = None, None   # the full clipboard (all formats) taken before our text
        self.history = []           # the `history` argument of every clip_set call
        self.elevated, self.vox_elevated = False, False   # the window in front / Vox itself runs as administrator
        self.selection, self.seq = None, 1                # what Ctrl+C copies (None: nothing selected); clipboard counter

    def foreground_exe(self):
        self.calls.append("foreground")
        if isinstance(self.foreground, Exception):
            raise self.foreground
        return self.foreground

    def clip_get(self):
        return self.clip

    def clip_set(self, text, history=False):
        self.calls.append(("set", text))
        self.history.append(history)
        self.clip = text

    def clip_snapshot(self):
        self.calls.append("snapshot")
        return self.snapshot

    def clip_restore(self, snapshot):
        self.calls.append(("restore", snapshot))
        if self.restore_error:
            raise self.restore_error

    def send_ctrl_v(self):
        self.calls.append("ctrl_v")

    def send_ctrl_shift_v(self):
        self.calls.append("ctrl_shift_v")

    def send_shift_insert(self):
        self.calls.append("shift_insert")

    def send_ctrl_c(self):
        self.calls.append("ctrl_c")
        if self.selection is not None:
            self.clip, self.seq = self.selection, self.seq + 1

    def clip_sequence(self):
        return self.seq

    def foreground_elevated(self):
        return self.elevated

    def self_elevated(self):
        return self.vox_elevated

    def wait_modifiers_released(self):
        self.calls.append("modifiers")

    def sleep(self, seconds):
        self.calls.append("sleep")
        if self.during_wait and "ctrl_v" in self.calls:
            self.during_wait()


def test_window_unchanged_pastes_and_restores_the_old_clipboard():
    d = FakeDeps(clip="old")
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    assert d.calls.count("ctrl_v") == 1
    assert ("set", "Hello.") in d.calls and d.clip == "old"


def test_waits_for_the_modifiers_before_it_looks_at_the_window():
    d = FakeDeps()
    paste.paste_text("Hello.", "notepad.exe", False, deps=d)
    assert d.calls[:2] == ["modifiers", "foreground"]


def test_window_changed_leaves_the_text_on_the_clipboard_and_sends_nothing():
    d = FakeDeps(foreground="chrome.exe", clip="old")
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "copied"
    assert "ctrl_v" not in d.calls
    assert d.clip == "Hello."          # not restored: the text is the only copy the user has


def test_window_changed_still_leaves_the_text_when_keep_clipboard_is_on():
    d = FakeDeps(foreground="chrome.exe")
    assert paste.paste_text("Hello.", "notepad.exe", True, deps=d) == "copied"
    assert "ctrl_v" not in d.calls and d.clip == "Hello."


def test_exe_names_are_compared_ignoring_case():
    d = FakeDeps(foreground="notepad.exe")
    assert paste.paste_text("Hello.", "Notepad.EXE", False, deps=d) == "pasted"


def test_keep_clipboard_true_does_not_restore():
    d = FakeDeps(clip="old")
    assert paste.paste_text("Hello.", "notepad.exe", True, deps=d) == "pasted"
    assert d.calls.count("ctrl_v") == 1 and d.clip == "Hello."


def test_a_clipboard_the_user_changed_during_the_wait_is_not_overwritten():
    d = FakeDeps(clip="old")
    d.during_wait = lambda: setattr(d, "clip", "something the user just copied")
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    assert d.clip == "something the user just copied"
    assert ("set", "old") not in d.calls


def test_an_empty_old_clipboard_is_unknown_so_the_text_stays():
    # pyperclip returns '' (not None) when the clipboard holds an image or files: restoring '' would wipe it
    d = FakeDeps(clip="")
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    assert d.clip == "Hello." and ("set", "") not in d.calls


def test_a_non_text_old_clipboard_value_is_not_restored():
    d = FakeDeps(clip=b"bytes")
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    assert d.clip == "Hello."


def test_the_wait_before_the_restore_is_one_second():
    d = FakeDeps(clip="old")
    d.sleeps = []
    d.sleep = lambda seconds: d.sleeps.append(seconds)
    paste.paste_text("Hello.", "notepad.exe", False, deps=d)
    assert paste.PASTE_WAIT == 1.0 and d.sleeps[-1] == paste.PASTE_WAIT


def test_an_unreadable_old_clipboard_cannot_be_restored_so_the_text_stays():
    d = FakeDeps(clip=None)
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    assert d.calls.count("ctrl_v") == 1 and d.clip == "Hello."


def test_a_window_lookup_that_fails_counts_as_unchanged_and_is_logged(caplog):
    d = FakeDeps(foreground=OSError("access denied"), clip="old")
    with caplog.at_level(logging.WARNING, logger="vox"):
        assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    assert d.calls.count("ctrl_v") == 1 and d.clip == "old"
    assert any("access denied" in r.getMessage() for r in caplog.records)


def test_no_target_or_no_current_window_name_means_nothing_to_compare():
    d = FakeDeps(foreground="chrome.exe")
    assert paste.paste_text("Hello.", "", False, deps=d) == "pasted"     # the target was never captured
    d = FakeDeps(foreground="")
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"   # no foreground window found


def test_the_clipboard_is_not_kept_unless_asked():
    import vox_core
    assert vox_core.DEFAULT_CONFIG["keep_clipboard"] is False


# ---- clipboard_history: dictations may be kept in Win+V history (default on) ---------------------------------------------

def test_dictations_are_allowed_in_clipboard_history_by_default():
    import vox_core
    assert vox_core.DEFAULT_CONFIG["clipboard_history"] is True


def test_paste_text_asks_for_history_by_default_and_not_for_the_old_text_put_back():
    d = FakeDeps(clip="old")
    paste.paste_text("Hello.", "notepad.exe", False, deps=d)
    assert d.history == [True, False]      # the dictation (history allowed), then the old text put back (kept private)


def test_paste_text_with_history_off_keeps_every_clip_set_private():
    d = FakeDeps(clip="old")
    paste.paste_text("Hello.", "notepad.exe", False, deps=d, clipboard_history=False)
    assert d.history == [False, False]


def test_a_copied_only_dictation_honours_the_history_setting_too():
    d = FakeDeps(foreground="chrome.exe")
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "copied" and d.history == [True]
    d = FakeDeps(foreground="chrome.exe")
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d, clipboard_history=False) == "copied"
    assert d.history == [False]


# ---- the whole clipboard is put back, not only its text (R2-M3) ----------------------------------------------------------

def test_the_whole_clipboard_is_restored_from_the_snapshot_taken_before_our_text():
    d = FakeDeps(clip="old")
    d.snapshot = object()
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    assert ("restore", d.snapshot) in d.calls
    assert d.calls.index("snapshot") < d.calls.index(("set", "Hello."))
    assert ("set", "old") not in d.calls          # not the text-only path


def test_a_clipboard_that_held_an_image_is_restored_from_the_snapshot_although_it_has_no_text():
    d = FakeDeps(clip="")
    d.snapshot = [(8, b"dib bytes")]
    paste.paste_text("Hello.", "notepad.exe", False, deps=d)
    assert ("restore", d.snapshot) in d.calls


def test_the_snapshot_is_not_restored_when_the_clipboard_changed_meanwhile():
    d = FakeDeps(clip="old")
    d.snapshot = object()
    d.during_wait = lambda: setattr(d, "clip", "something the user just copied")
    paste.paste_text("Hello.", "notepad.exe", False, deps=d)
    assert ("restore", d.snapshot) not in d.calls


def test_the_snapshot_is_not_restored_when_the_clipboard_is_kept():
    d = FakeDeps(clip="old")
    d.snapshot = object()
    paste.paste_text("Hello.", "notepad.exe", True, deps=d)
    assert ("restore", d.snapshot) not in d.calls


def test_a_failing_restore_is_logged_and_the_paste_still_counts(caplog):
    d = FakeDeps(clip="old")
    d.snapshot, d.restore_error = object(), OSError("clipboard busy")
    with caplog.at_level(logging.WARNING, logger="vox"):
        assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    assert any("clipboard" in r.getMessage() for r in caplog.records)


# ---- the dictation stays out of Win+V history and the cloud clipboard (R2-M4) --------------------------------------------

class FakeUser32:
    def __init__(self):
        self.registered, self.set_calls, self.closed = [], [], 0

    def OpenClipboard(self, hwnd):
        return 1

    def EmptyClipboard(self):
        return 1

    def CloseClipboard(self):
        self.closed += 1
        return 1

    def RegisterClipboardFormatW(self, name):
        self.registered.append(name)
        return 0xC000 + len(self.registered)

    def SetClipboardData(self, fmt, handle):
        self.set_calls.append((fmt, handle))
        return handle

    def CreateWindowExA(self, *args):
        return 1

    def DestroyWindow(self, hwnd):
        return 1


class FakeKernel32:
    def GlobalFree(self, handle):
        return 0


def _clip_set(monkeypatch, history):
    user32 = FakeUser32()
    monkeypatch.setattr(paste, "_user32", user32)
    monkeypatch.setattr(paste, "_kernel32", FakeKernel32())
    sizes, user32.sent = iter(range(100, 200)), []
    monkeypatch.setattr(paste, "_global_from_bytes", lambda k, data: user32.sent.append(data) or next(sizes))
    paste.SystemDeps().clip_set("hi", history=history)
    return user32


def test_clip_set_marks_the_text_as_private_and_sets_it_as_unicode_text(monkeypatch):
    user32 = _clip_set(monkeypatch, history=False)
    assert set(user32.registered) == {"ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                                      "CanUploadToCloudClipboard"}
    formats = [f for f, _ in user32.set_calls]
    assert 13 in formats and all(0xC001 <= f <= 0xC003 for f in formats if f != 13)
    assert len(formats) == 4
    assert user32.closed == 1


def test_clip_set_is_private_unless_history_is_asked_for(monkeypatch):
    user32 = FakeUser32()
    monkeypatch.setattr(paste, "_user32", user32)
    monkeypatch.setattr(paste, "_kernel32", FakeKernel32())
    monkeypatch.setattr(paste, "_global_from_bytes", lambda k, data: 5)
    paste.SystemDeps().clip_set("hi")
    assert len(user32.registered) == 3


def test_clip_set_with_history_allowed_leaves_out_the_history_markers_but_blocks_the_cloud(monkeypatch):
    user32 = _clip_set(monkeypatch, history=True)
    assert user32.registered == ["CanUploadToCloudClipboard"]      # no exclude / no history marker: Win+V may keep it
    assert [f for f, _ in user32.set_calls] == [13, 0xC001] and user32.closed == 1
    assert user32.sent[1:] == [b"\x00\x00\x00\x00"]                     # the cloud marker is DWORD 0


def test_clip_set_raises_and_closes_the_clipboard_when_the_text_cannot_be_set(monkeypatch):
    user32 = FakeUser32()
    user32.SetClipboardData = lambda fmt, handle: None
    monkeypatch.setattr(paste, "_user32", user32)
    monkeypatch.setattr(paste, "_kernel32", FakeKernel32())
    monkeypatch.setattr(paste, "_global_from_bytes", lambda k, data: 5)
    with pytest.raises(OSError):
        paste.SystemDeps().clip_set("hi")
    assert user32.closed == 1


# ---- Ctrl+V does not depend on the keyboard layout (R2-M5) ---------------------------------------------------------------

def test_ctrl_v_is_sent_as_a_virtual_key_so_a_non_latin_layout_still_pastes(monkeypatch):
    import contextlib
    import sys
    import types
    taps = []

    class FakeKeyCode:
        def __init__(self, vk):
            self.vk = vk

        @classmethod
        def from_vk(cls, vk):
            return cls(vk)

    class FakeController:
        @contextlib.contextmanager
        def pressed(self, key):
            taps.append(("down", key))
            yield
            taps.append(("up", key))

        def tap(self, key):
            taps.append(("tap", key))

    fake = types.ModuleType("pynput")
    fake.keyboard = types.SimpleNamespace(Controller=FakeController, KeyCode=FakeKeyCode, Key=types.SimpleNamespace(ctrl="ctrl"))
    monkeypatch.setitem(sys.modules, "pynput", fake)
    monkeypatch.setattr(paste, "_keyboard", None)
    paste.SystemDeps().send_ctrl_v()
    assert [t[0] for t in taps] == ["down", "tap", "up"]
    assert getattr(taps[1][1], "vk", None) == 0x56


def _engine_with(monkeypatch, result, **cfg):
    """An Engine that has only what paste() needs; paste_text is replaced and its arguments recorded."""
    for mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
        pytest.importorskip(mod)
    import engine as engine_mod
    e = object.__new__(engine_mod.Engine)
    e.cfg, e.target, e.messages = cfg, "notepad.exe", []
    e.notify = lambda m, private=False: e.messages.append(m)
    calls = []
    monkeypatch.setattr(paste, "paste_text", lambda *a, **k: calls.append(a + (k,) if k else a) or result)
    return e, calls


def test_the_engine_says_so_when_the_window_changed_and_the_text_was_only_copied(monkeypatch):
    e, calls = _engine_with(monkeypatch, "copied")
    e.paste("Hello.")
    assert calls == [("Hello.", "notepad.exe", False, {"clipboard_history": True})]   # defaults when the settings are absent
    assert e.messages == ["Copied; the window changed"]


def test_the_engine_stays_quiet_when_the_text_was_pasted(monkeypatch):
    e, calls = _engine_with(monkeypatch, "pasted", keep_clipboard=True)
    e.paste("Hello.")
    assert calls == [("Hello.", "notepad.exe", True, {"clipboard_history": True})] and e.messages == []


def test_the_engine_passes_clipboard_history_off_to_every_paste(monkeypatch):
    e, calls = _engine_with(monkeypatch, "pasted", clipboard_history=False)
    e.paste("Hello.")
    assert calls == [("Hello.", "notepad.exe", False, {"clipboard_history": False})]


# ---- terminals paste with Ctrl+Shift+V (task B2) --------------------------------------------------------------------------
@pytest.mark.parametrize("exe", ["WindowsTerminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
                                 "alacritty.exe", "wezterm-gui.exe"])
def test_a_terminal_gets_ctrl_shift_v(exe):
    assert paste.paste_chord(exe) == ("ctrl", "shift", "v")
    d = FakeDeps(foreground=exe.lower())
    assert paste.paste_text("ls -la", exe.lower(), False, deps=d) == "pasted"
    assert "ctrl_shift_v" in d.calls and "ctrl_v" not in d.calls


# PuTTY and mintty (Git Bash) ignore Ctrl+Shift+V by default and paste with Shift+Insert (issue 49, Windows 7)
@pytest.mark.parametrize("exe", ["putty.exe", "mintty.exe", "PuTTY.exe", "MinTTY.exe"])
def test_putty_and_mintty_get_shift_insert(exe):
    assert paste.paste_chord(exe) == ("shift", "insert")
    d = FakeDeps(foreground=exe.lower())
    assert paste.paste_text("ls -la", exe.lower(), False, deps=d) == "pasted"
    assert "shift_insert" in d.calls and "ctrl_v" not in d.calls and "ctrl_shift_v" not in d.calls


def test_shift_insert_is_sent_as_the_extended_insert_key(monkeypatch):
    import contextlib
    import sys
    import types
    taps = []

    class FakeController:
        @contextlib.contextmanager
        def pressed(self, key):
            taps.append(("down", key))
            yield
            taps.append(("up", key))

        def tap(self, key):
            taps.append(("tap", key))

    fake = types.ModuleType("pynput")
    fake.keyboard = types.SimpleNamespace(Controller=FakeController,
                                          Key=types.SimpleNamespace(ctrl="ctrl", shift="shift", insert="insert"))
    monkeypatch.setitem(sys.modules, "pynput", fake)
    monkeypatch.setattr(paste, "_keyboard", None)
    paste.SystemDeps().send_shift_insert()
    assert taps == [("down", "shift"), ("tap", "insert"), ("up", "shift")]


@pytest.mark.parametrize("exe", ["notepad.exe", "code.exe", "chrome.exe", "", None])
def test_everything_else_gets_ctrl_v(exe):
    assert paste.paste_chord(exe) == ("ctrl", "v")


def test_the_chord_follows_the_window_in_front_when_no_target_was_captured():
    d = FakeDeps(foreground="windowsterminal.exe")
    paste.paste_text("hi", "", False, deps=d)                          # paste-last: any window
    assert "ctrl_shift_v" in d.calls


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


def test_the_wait_for_held_keys_ends_when_they_come_up():
    clock, states = FakeClock(), iter([True, True, True, False])
    assert paste.wait_released(lambda: next(states), clock, clock.sleep, limit=2.0) is True
    assert abs(clock.now - 0.06) < 1e-9                               # three short sleeps


def test_the_wait_for_held_keys_is_bounded():
    clock = FakeClock()
    assert paste.wait_released(lambda: True, clock, clock.sleep, limit=0.4) is False
    assert 0.4 <= clock.now <= 0.42


def test_keys_already_up_do_not_wait_at_all():
    clock = FakeClock()
    assert paste.wait_released(lambda: False, clock, clock.sleep, limit=2.0) is True and clock.now == 0.0


# ---- issue 49, Windows 5: no clipboard snapshot when it is not going to be restored ----------------------------------------
def test_with_keep_clipboard_on_the_old_clipboard_is_never_read():
    d = FakeDeps(clip="old")
    d.clip_get = lambda: d.calls.append("read") or d.clip
    paste.paste_text("Hello.", "notepad.exe", True, deps=d)
    assert "snapshot" not in d.calls and d.calls.count("read") == 0


def test_with_keep_clipboard_off_the_snapshot_is_still_taken():
    d = FakeDeps(clip="old")
    paste.paste_text("Hello.", "notepad.exe", False, deps=d)
    assert "snapshot" in d.calls


class SnapUser32(FakeUser32):
    def __init__(self, formats):
        super().__init__()
        self.formats = formats   # [(fmt, size)]

    def EnumClipboardFormats(self, fmt):
        ids = [f for f, _ in self.formats]
        if fmt == 0:
            return ids[0] if ids else 0
        i = ids.index(fmt)
        return ids[i + 1] if i + 1 < len(ids) else 0

    def GetClipboardData(self, fmt):
        return fmt   # the handle is the format number


class SnapKernel32(FakeKernel32):
    def __init__(self, sizes):
        self.sizes = sizes

    def GlobalSize(self, handle):
        return self.sizes[handle]


def test_a_format_over_the_size_limit_is_left_out_and_logged(monkeypatch, caplog):
    formats = [(13, 10), (0xC123, paste.CLIP_FORMAT_LIMIT + 1), (8, 20)]
    monkeypatch.setattr(paste, "_user32", SnapUser32(formats))
    monkeypatch.setattr(paste, "_kernel32", SnapKernel32(dict(formats)))
    read = []
    monkeypatch.setattr(paste, "_read_global", lambda handle, size: read.append(handle) or b"x" * size)
    with caplog.at_level(logging.INFO, logger="vox"):
        snap = paste.SystemDeps().clip_snapshot()
    assert [f for f, _ in snap] == [13, 8] and 0xC123 not in read      # never even read
    assert any("too big" in r.getMessage() for r in caplog.records)


def test_a_clipboard_over_the_total_limit_gives_no_snapshot(monkeypatch):
    big = paste.CLIP_FORMAT_LIMIT
    formats = [(0xC001, big), (0xC002, big), (0xC003, big), (0xC004, big), (0xC005, big)]
    monkeypatch.setattr(paste, "_user32", SnapUser32(formats))
    monkeypatch.setattr(paste, "_kernel32", SnapKernel32(dict(formats)))
    monkeypatch.setattr(paste, "_read_global", lambda handle, size: b"")
    assert paste.SystemDeps().clip_snapshot() is None


# ---- R2-M6: a window that runs as administrator ------------------------------------------------------------------------
def test_an_elevated_window_gets_the_text_on_the_clipboard_and_no_keys():
    d = FakeDeps(clip="old")
    d.elevated = True
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "blocked"
    assert "ctrl_v" not in d.calls and d.clip == "Hello."


def test_an_elevated_vox_pastes_into_an_elevated_window_and_unknown_means_paste():
    d = FakeDeps()
    d.elevated, d.vox_elevated = True, True
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    d = FakeDeps()
    d.elevated = None                                                  # cannot tell: as before
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"


def test_a_failing_elevation_check_does_not_stop_the_paste():
    d = FakeDeps()

    def boom():
        raise OSError("access denied")

    d.foreground_elevated = boom
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"


# ---- edit by voice: copying the selection ----------------------------------------------------------------------------
def test_copy_selection_reads_the_selection_and_puts_the_old_clipboard_back():
    d = FakeDeps(clip="old")
    d.selection = "the selected words"
    d.snapshot = [(13, b"old")]
    assert paste.copy_selection("notepad.exe", deps=d) == ("the selected words", "")
    assert d.calls.index("modifiers") < d.calls.index("ctrl_c")
    assert ("restore", d.snapshot) in d.calls


def test_copy_selection_without_a_snapshot_puts_old_text_back():
    d = FakeDeps(clip="old")
    d.selection = "sel"
    assert paste.copy_selection("notepad.exe", deps=d)[0] == "sel"
    assert d.clip == "old"


def test_nothing_selected_means_the_clipboard_never_changed_and_nothing_is_restored():
    d = FakeDeps(clip="old")
    d.sleep = lambda s: None
    text, problem = paste.copy_selection("notepad.exe", deps=d)
    assert text is None and "Select" in problem
    assert not any(isinstance(c, tuple) and c[0] == "restore" for c in d.calls)


def test_copy_selection_refuses_a_terminal_a_changed_window_and_an_elevated_one():
    d = FakeDeps(foreground="windowsterminal.exe")
    d.selection = "x"
    assert paste.copy_selection("windowsterminal.exe", deps=d)[0] is None and "ctrl_c" not in d.calls
    d = FakeDeps(foreground="chrome.exe")
    assert "changed" in paste.copy_selection("notepad.exe", deps=d)[1]
    d = FakeDeps()
    d.elevated = True
    assert "administrator" in paste.copy_selection("notepad.exe", deps=d)[1]


@pytest.mark.parametrize("exe", ["Code.exe", "devenv.exe", "sublime_text.exe", "idea64.exe", "pycharm64.exe", "cursor.exe"])
@pytest.mark.parametrize("line", ["    count = count + 1\n", "int x = 0;\r\n"])
def test_an_editor_that_copies_the_whole_line_on_an_empty_selection_counts_as_nothing_selected(exe, line):
    """Final fixes (windows 3): with nothing selected these editors copy the current line and its line break; editing
    that and pasting it at the caret garbled the line."""
    d = FakeDeps(foreground=exe, clip="old")
    d.selection = line
    d.snapshot = [(13, b"old")]
    text, problem = paste.copy_selection(exe, deps=d)
    assert text is None and "Select" in problem and "line" in problem
    assert ("restore", d.snapshot) in d.calls                       # the old clipboard is back


def test_a_real_selection_in_an_editor_still_works():
    for sel in ("count = count + 1", "a\nb\n", "first line\nsecond line"):
        d = FakeDeps(foreground="Code.exe", clip="old")
        d.selection = sel
        assert paste.copy_selection("Code.exe", deps=d) == (sel, "")
    d = FakeDeps(foreground="notepad.exe", clip="old")          # an app that does not copy lines: a whole line is a selection
    d.selection = "one whole line\r\n"
    assert paste.copy_selection("notepad.exe", deps=d) == ("one whole line\r\n", "")


def test_a_selection_of_only_spaces_is_no_selection():
    d = FakeDeps(clip="old")
    d.selection = "   "
    assert paste.copy_selection("notepad.exe", deps=d)[0] is None


def test_ctrl_shift_v_and_ctrl_c_are_sent_as_virtual_keys(monkeypatch):
    import contextlib
    import sys
    import types
    taps = []

    class FakeKeyCode:
        def __init__(self, vk):
            self.vk = vk

        @classmethod
        def from_vk(cls, vk):
            return cls(vk)

    class FakeController:
        @contextlib.contextmanager
        def pressed(self, key):
            taps.append(("down", key))
            yield
            taps.append(("up", key))

        def tap(self, key):
            taps.append(("tap", getattr(key, "vk", key)))

    fake = types.ModuleType("pynput")
    fake.keyboard = types.SimpleNamespace(Controller=FakeController, KeyCode=FakeKeyCode,
                                          Key=types.SimpleNamespace(ctrl="ctrl", shift="shift"))
    monkeypatch.setitem(sys.modules, "pynput", fake)
    monkeypatch.setattr(paste, "_keyboard", None)
    paste.SystemDeps().send_ctrl_shift_v()
    assert taps == [("down", "ctrl"), ("down", "shift"), ("tap", 0x56), ("up", "shift"), ("up", "ctrl")]
    taps.clear()
    paste.SystemDeps().send_ctrl_c()
    assert taps == [("down", "ctrl"), ("tap", 0x43), ("up", "ctrl")]
