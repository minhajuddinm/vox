"""paste_text: the safer paste (window check, clipboard restored only if it still holds our text).
Everything goes through injected fakes, so no clipboard, window or key is touched."""
import logging

import pytest

import paste


class FakeDeps:
    def __init__(self, foreground="notepad.exe", clip="old"):
        self.foreground, self.clip, self.calls = foreground, clip, []
        self.during_wait = None     # runs while paste_text sleeps after Ctrl+V: the user doing something else

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


def test_an_empty_old_clipboard_is_restored_as_empty():
    d = FakeDeps(clip="")
    assert paste.paste_text("Hello.", "notepad.exe", False, deps=d) == "pasted"
    assert d.clip == ""


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
