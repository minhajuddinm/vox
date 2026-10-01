"""The pill's short "sent" (green check) and "error" (red !) signals: Engine.flash, when it is set and cleared,
and which events raise it. Needs the Windows runtime packages (pynput, pystray, ...); skipped where they are
missing (CI's test job)."""
import time

import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import engine as engine_mod  # noqa: E402
import notes  # noqa: E402
import vox_core as core  # noqa: E402


class InlineThread:
    def __init__(self, target=None, args=(), daemon=None, **kw):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class FakeIcon:
    icon = None


@pytest.fixture
def eng(tmp_path, monkeypatch):
    """An Engine with a pill (overlay) and the real set_state / flash / paste; the outside world is replaced."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    e = object.__new__(engine_mod.Engine)
    e.recording = e.busy = e.hands_free = e.note_mode = False
    e.chunks, e.cfg, e.target, e.pending = [], {"keep_history": False, "stream_stt": False}, "notepad.exe", None
    e.messages = []
    e.state, e.level = "idle", 0.0
    e.streaming = None
    e.icon = FakeIcon()
    e.overlay = object()                 # any overlay at all: the pill exists
    e.sync = type("S", (), {"trigger": lambda self: None})()
    e.notify = e.messages.append
    e._rec_lock = engine_mod.threading.Lock()
    e._close_stream = lambda: None
    monkeypatch.setattr(engine_mod.threading, "Thread", InlineThread)
    monkeypatch.setattr(engine_mod, "open_window", lambda: None)
    return e


def speech(seconds=1.0):
    return [b"\x10\x27" * int(core.SAMPLE_RATE * seconds)]   # loud enough to pass the silence gate


def ok_result(text="Hello."):
    return core.Result("hello", text, True, "")


def dictate(e, monkeypatch, result=None, raises=None, pasted="pasted"):
    """One full dictation through stop() -> _process(), with the network and the paste replaced."""
    def process(cfg, pcm, exe, label):
        if raises:
            raise raises
        return result or ok_result()

    monkeypatch.setattr(core, "process_detailed", process)
    monkeypatch.setattr(engine_mod.paste_mod, "paste_text", lambda text, target, keep: pasted)
    e.recording = True
    e.chunks = speech()
    e.stop()


# ------------------------------------------------------------- the flash itself
def test_flash_lasts_700_ms_for_sent_and_1800_ms_for_error(eng):
    before = time.monotonic()
    eng.flash("sent")
    after = time.monotonic()
    assert eng.flash_kind == "sent" and before + 0.7 <= eng.flash_until <= after + 0.7
    before = time.monotonic()
    eng.flash("error")
    after = time.monotonic()
    assert eng.flash_kind == "error" and before + 1.8 <= eng.flash_until <= after + 1.8


def test_expiry_returns_to_the_real_state(eng):
    eng.state = "idle"
    eng.flash("sent")
    assert eng.active_flash(eng.flash_until - 0.01) == "sent"
    assert eng.active_flash(eng.flash_until) == ""           # over: the overlay shows the real state again
    assert eng.active_flash(eng.flash_until + 5) == ""
    assert eng.state == "idle"                                # the engine state was never touched


def test_a_flash_does_not_change_the_engine_state(eng):
    for state in ("idle", "busy", "rec"):
        eng.state = state
        eng.flash("error")
        assert eng.state == state


def test_a_new_recording_cancels_a_flash(eng):
    eng.flash("error")
    eng.set_state("rec")
    assert eng.flash_kind == "" and eng.active_flash() == ""
    eng.set_state("idle")
    assert eng.active_flash() == ""                           # and it does not come back


def test_sending_also_cancels_a_flash(eng):
    eng.flash("sent")
    eng.set_state("busy")
    assert eng.active_flash() == ""


def test_going_back_to_idle_keeps_the_flash(eng):
    """The result is flashed while the state is still busy; the return to idle right after must not erase it."""
    eng.set_state("busy")
    eng.flash("sent")
    eng.set_state("idle")
    assert eng.active_flash() == "sent"


def test_a_later_flash_replaces_an_earlier_one(eng):
    eng.flash("error")
    eng.flash("sent")
    assert eng.active_flash() == "sent"


def test_no_flash_when_the_overlay_is_disabled(eng):
    eng.overlay = None
    eng.flash("error")
    eng.flash("sent")
    assert eng.flash_kind == "" and eng.active_flash() == ""


def test_an_unknown_kind_is_a_bug(eng):
    with pytest.raises(KeyError):
        eng.flash("party")


def test_error_flash_followed_by_a_start_works(eng, monkeypatch):
    class Stream:
        def __init__(self, **kw):
            self.kw = kw

        def start(self):
            pass

    monkeypatch.setattr(engine_mod.sd, "InputStream", Stream)
    monkeypatch.setattr(core, "warm", lambda cfg: None)
    monkeypatch.setattr(core, "endpoint_error", lambda cfg: "")
    monkeypatch.setattr(core, "key_missing", lambda cfg: False)
    monkeypatch.setattr(engine_mod, "foreground_app", lambda: "notepad.exe")
    eng.flash("error")
    eng.start()
    assert eng.recording and eng.state == "rec"
    assert eng.active_flash() == ""                           # the recording replaced the red !
    assert isinstance(eng.stream, Stream) and eng.messages == []


# ------------------------------------------------------- what raises a flash
def test_a_pasted_dictation_flashes_sent(eng, monkeypatch):
    dictate(eng, monkeypatch)
    assert eng.active_flash() == "sent" and eng.state == "idle" and eng.messages == []


def test_a_dictation_that_only_reached_the_clipboard_flashes_error_and_keeps_its_balloon(eng, monkeypatch):
    dictate(eng, monkeypatch, pasted="copied")
    assert eng.active_flash() == "error"
    assert eng.messages == ["Copied; the window changed"]


def test_a_saved_note_flashes_sent(eng, monkeypatch):
    eng.note_mode = True
    dictate(eng, monkeypatch, result=ok_result("Call the dentist."))
    assert eng.active_flash() == "sent" and notes.count() == 1
    assert eng.messages == ["Note saved: Call the dentist."]


def test_a_failed_send_flashes_error_and_keeps_its_balloon(eng, monkeypatch):
    dictate(eng, monkeypatch, raises=core.ApiError(401, "no"))
    assert eng.active_flash() == "error" and eng.state == "idle"
    assert eng.messages and eng.messages[0].startswith("The server rejected the API key")
    assert eng.pending is not None


@pytest.mark.parametrize("error", [core.ApiError(429, "slow down"), core.ApiError(503, "down"),
                                   engine_mod.requests.ConnectionError("offline")])
def test_every_send_failure_flashes_error(eng, monkeypatch, error):
    dictate(eng, monkeypatch, raises=error)
    assert eng.active_flash() == "error" and len(eng.messages) == 1


def test_an_unexpected_failure_flashes_error(eng, monkeypatch):
    dictate(eng, monkeypatch, raises=RuntimeError("bug"))
    assert eng.active_flash() == "error" and eng.state == "idle"


def test_cleanup_falling_back_to_the_raw_words_still_flashes_sent(eng, monkeypatch):
    dictate(eng, monkeypatch, result=core.Result("hello", "hello", True, "timeout"))
    assert eng.active_flash() == "sent"
    assert len(eng.messages) == 1 and eng.messages[0].startswith("Cleanup did not work")


def test_nothing_heard_flashes_error_and_keeps_its_balloon(eng, monkeypatch):
    eng.recording = True
    eng.chunks = [b"\x00\x00" * core.SAMPLE_RATE]             # a second of silence
    eng.stop()
    assert eng.active_flash() == "error" and eng.state == "idle"
    assert eng.messages and eng.messages[0].startswith("Vox did not hear anything")


def test_a_too_short_recording_flashes_nothing(eng):
    eng.recording = True
    eng.chunks = [b"\x00\x00" * 10]
    eng.stop()
    assert eng.active_flash() == "" and eng.messages == []


def test_cancelling_flashes_nothing(eng):
    eng.recording = True
    eng.cancel()
    assert eng.active_flash() == ""


def test_a_missing_key_flashes_error_and_keeps_its_balloon(eng, monkeypatch):
    monkeypatch.setattr(core, "endpoint_error", lambda cfg: "")
    monkeypatch.setattr(core, "key_missing", lambda cfg: True)
    eng.start()
    assert eng.messages == ["Add your API key in Vox > Settings"]
    assert eng.active_flash() == "error" and not eng.recording


def test_a_microphone_error_flashes_error_and_keeps_its_balloon(eng, monkeypatch):
    def broken(**kw):
        raise OSError("no microphone")

    monkeypatch.setattr(engine_mod.sd, "InputStream", broken)
    monkeypatch.setattr(core, "warm", lambda cfg: None)
    monkeypatch.setattr(core, "endpoint_error", lambda cfg: "")
    monkeypatch.setattr(core, "key_missing", lambda cfg: False)
    monkeypatch.setattr(engine_mod, "foreground_app", lambda: "notepad.exe")
    eng.start()
    assert eng.messages == ["Microphone error: no microphone"]
    assert eng.active_flash() == "error" and not eng.recording


def test_no_call_site_needs_an_overlay(eng, monkeypatch):
    """Without a pill every path above still works (and nothing is flashed)."""
    eng.overlay = None
    dictate(eng, monkeypatch, pasted="copied")
    assert eng.messages == ["Copied; the window changed"] and eng.active_flash() == ""


# ------------------------------------------------ errors after the paste never flash error
def test_a_history_failure_after_a_successful_paste_still_flashes_sent(eng, monkeypatch):
    eng.cfg["keep_history"] = True

    def boom(entry):
        raise OSError("disk full")

    monkeypatch.setattr(core, "add_history", boom)
    dictate(eng, monkeypatch)
    assert eng.active_flash() == "sent" and eng.state == "idle" and eng.pending is None


def test_paste_itself_does_not_flash(eng, monkeypatch):
    monkeypatch.setattr(engine_mod.paste_mod, "paste_text", lambda text, target, keep: "pasted")
    assert eng.paste("Hi.") is True
    assert eng.active_flash() == ""
    monkeypatch.setattr(engine_mod.paste_mod, "paste_text", lambda text, target, keep: "copied")
    assert eng.paste("Hi.") is False and eng.messages == ["Copied; the window changed"]
    assert eng.active_flash() == ""


def test_a_new_engine_starts_with_no_flash(monkeypatch):
    monkeypatch.setattr(engine_mod.core, "load_config", lambda: {})
    monkeypatch.setattr(engine_mod.Engine, "_mtime", lambda self: 0)
    monkeypatch.setattr(engine_mod.Engine, "_hotkey", lambda self: set())
    e = engine_mod.Engine()
    assert e.overlay is None and e.flash_kind == "" and e.flash_until == 0.0
    assert "flash_kind" in vars(e) and "flash_until" in vars(e) and "overlay" in vars(e)
