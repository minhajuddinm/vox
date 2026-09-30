"""Voice-note mode of the engine: recording toggle, saving instead of pasting, retry keeps the note flag.
Needs the Windows runtime packages (pynput, pystray, ...); skipped where they are missing (CI's test job)."""
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


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    e = object.__new__(engine_mod.Engine)
    e.recording = e.busy = e.hands_free = e.note_mode = False
    e.chunks, e.cfg, e.target, e.pending = [], {"keep_history": False}, "notepad.exe", None
    e.messages, e.pasted, e.states = [], [], []
    e.sync = type("S", (), {"triggered": 0, "trigger": lambda self: setattr(self, "triggered", self.triggered + 1)})()
    e.notify = e.messages.append
    e.set_state = e.states.append
    e.paste = e.pasted.append
    e._rec_lock = engine_mod.threading.Lock()
    e._close_stream = lambda: None

    def fake_start(self=e):
        self.recording = True

    monkeypatch.setattr(e, "start", fake_start)
    monkeypatch.setattr(engine_mod.threading, "Thread", InlineThread)
    return e


def speech(seconds=1.0):
    return [b"\x10\x27" * int(core.SAMPLE_RATE * seconds)]   # loud enough to pass the silence gate


def ok_result(text="Call the dentist tomorrow."):
    return core.Result("um call the dentist tomorrow", text, True, "")


def test_toggle_starts_a_hands_free_note_and_a_second_toggle_finishes_it(eng, monkeypatch):
    seen = []
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: seen.append((exe, label)) or ok_result())
    eng.toggle_note()
    assert eng.recording and eng.note_mode and eng.hands_free
    eng.chunks = speech()
    eng.toggle_note()
    assert not eng.recording and not eng.note_mode
    assert seen == [("", "")]                                    # no app name: notes use the default style
    assert eng.pasted == [] and eng.messages == ["Note saved: Call the dentist tomorrow."]
    assert [n["text"] for n in notes.search("dentist")] == ["Call the dentist tomorrow."]
    assert eng.sync.triggered == 1   # a saved note asks the sync thread to send it


def test_a_normal_dictation_still_pastes_and_saves_no_note(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result("Hello."))
    eng.start()
    eng.chunks = speech()
    eng.stop()
    assert eng.pasted == ["Hello."] and notes.count() == 0


def test_toggle_is_ignored_while_busy_or_recording_normally(eng):
    eng.busy = True
    eng.toggle_note()
    assert not eng.note_mode and not eng.recording
    eng.busy, eng.recording = False, True
    eng.toggle_note()
    assert not eng.note_mode   # a normal dictation is in progress: leave it alone


def test_cancel_and_too_short_recordings_leave_note_mode(eng):
    eng.toggle_note()
    eng.cancel()
    assert not eng.note_mode and eng.states[-1] == "idle"
    eng.toggle_note()
    eng.chunks = [b"\x00\x00" * 10]
    eng.stop()
    assert not eng.note_mode and not eng.recording


def test_failed_note_is_kept_for_retry_as_a_note(eng, monkeypatch):
    def boom(cfg, pcm, exe, label):
        raise core.ApiError(503, "down")

    monkeypatch.setattr(core, "process_detailed", boom)
    eng.toggle_note()
    eng.chunks = speech()
    eng.stop()
    assert eng.pending[2] is True and notes.count() == 0
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result("Second try."))
    eng.retry_last()
    assert eng.pending is None and [n["text"] for n in notes.search("")] == ["Second try."] and eng.pasted == []


def test_silent_note_is_not_saved(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: core.Result("", "", False, ""))
    eng.toggle_note()
    eng.chunks = speech()
    eng.stop()
    assert notes.count() == 0 and eng.pasted == []
