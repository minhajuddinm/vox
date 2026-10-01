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

    def foreground_exe(self):
        self.calls.append("foreground")
        if isinstance(self.foreground, Exception):
            raise self.foreground
        return self.foreground

    def clip_get(self):
        return self.clip

    def clip_set(self, text):
        self.calls.append(("set", text))
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


def test_clip_set_marks_the_text_as_private_and_sets_it_as_unicode_text(monkeypatch):
    user32 = FakeUser32()
    monkeypatch.setattr(paste, "_user32", user32)
    monkeypatch.setattr(paste, "_kernel32", FakeKernel32())
    sizes = iter(range(100, 200))
    monkeypatch.setattr(paste, "_global_from_bytes", lambda k, data: next(sizes))
    paste.SystemDeps().clip_set("hi")
    assert set(user32.registered) == {"ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                                      "CanUploadToCloudClipboard"}
    formats = [f for f, _ in user32.set_calls]
    assert 13 in formats and all(0xC001 <= f <= 0xC003 for f in formats if f != 13)
    assert len(formats) == 4
    assert user32.closed == 1


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
    e.notify = e.messages.append
    calls = []
    monkeypatch.setattr(paste, "paste_text", lambda *a, **k: calls.append(a) or result)
    return e, calls


def test_the_engine_says_so_when_the_window_changed_and_the_text_was_only_copied(monkeypatch):
    e, calls = _engine_with(monkeypatch, "copied")
    e.paste("Hello.")
    assert calls == [("Hello.", "notepad.exe", False)]      # keep_clipboard off when the setting is absent
    assert e.messages == ["Copied; the window changed"]


def test_the_engine_stays_quiet_when_the_text_was_pasted(monkeypatch):
    e, calls = _engine_with(monkeypatch, "pasted", keep_clipboard=True)
    e.paste("Hello.")
    assert calls == [("Hello.", "notepad.exe", True)] and e.messages == []
