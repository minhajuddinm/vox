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
    e.chunks, e.cfg, e.target, e.pending = [], {"keep_history": False}, "notepad.exe", []
    e.messages, e.pasted, e.states = [], [], []
    e.streaming = None
    e.sync = type("S", (), {"triggered": 0, "trigger": lambda self: setattr(self, "triggered", self.triggered + 1)})()
    e.notify = lambda m, private=False: e.messages.append(m)
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
    assert eng.pending[0][2] is True and notes.count() == 0
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result("Second try."))
    eng.retry_last()
    assert eng.pending == [] and [n["text"] for n in notes.search("")] == ["Second try."] and eng.pasted == []


def test_silent_note_is_not_saved(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: core.Result("", "", False, ""))
    eng.toggle_note()
    eng.chunks = speech()
    eng.stop()
    assert notes.count() == 0 and eng.pasted == []


class FakeStreamer:
    def __init__(self, text):
        self.text, self.cancelled, self.finished = text, False, 0

    def finish(self):
        self.finished += 1
        return self.text

    def cancel(self):
        self.cancelled = True


def test_a_streamed_recording_skips_the_whole_transcription(eng, monkeypatch):
    def whole(*a, **k):
        raise AssertionError("the whole recording should not be transcribed again")

    monkeypatch.setattr(core, "process_detailed", whole)
    seen = []
    monkeypatch.setattr(core, "process_text", lambda cfg, raw, exe, label, segments=None: seen.append(segments) or
                        core.Result(raw, "Streamed text.", True, ""))
    eng.start()
    eng.streaming = FakeStreamer("streamed raw")
    eng.streaming.segments = [{"start": 0.0, "end": 1.0, "text": "streamed"}, {"start": 3.0, "end": 4.0, "text": "raw"}]
    streamer = eng.streaming
    eng.chunks = speech()
    eng.stop()
    assert eng.pasted == ["Streamed text."] and streamer.finished == 1 and eng.streaming is None
    assert seen == [streamer.segments]      # final fixes (windows 1): the pause times reach the paragraph breaks


def test_when_streaming_gives_nothing_the_whole_recording_is_transcribed(eng, monkeypatch):
    monkeypatch.setattr(core, "process_detailed", lambda cfg, pcm, exe, label: ok_result("From the whole recording."))
    eng.start()
    eng.streaming = FakeStreamer(None)
    eng.chunks = speech()
    eng.stop()
    assert eng.pasted == ["From the whole recording."]


def test_cancel_and_too_short_recordings_drop_the_streamer(eng):
    eng.start()
    eng.streaming = s1 = FakeStreamer("x")
    eng.cancel()
    assert s1.cancelled and eng.streaming is None
    eng.start()
    eng.streaming = s2 = FakeStreamer("x")
    eng.chunks = [b"\x00\x00" * 10]
    eng.stop()
    assert s2.cancelled and eng.streaming is None


def test_a_401_through_the_relay_tells_the_user_to_check_the_relay_token(eng, monkeypatch):
    def refused(cfg, pcm, exe, label):
        raise core.ApiError(401, "API 401: unauthorised")

    monkeypatch.setattr(core, "process_detailed", refused)
    eng.cfg = {"keep_history": False, "relay_proxy": True, "relay_url": "https://your-pi.your-tailnet.ts.net", "relay_token": "T"}
    eng.start()
    eng.chunks = speech()
    eng.stop()
    assert "relay token" in eng.messages[-1] and "relay page" in eng.messages[-1]
    eng.cfg = {"keep_history": False}   # no relay: the old wording stays
    eng.start()
    eng.chunks = speech()
    eng.stop()
    assert eng.messages[-1].startswith("The server rejected the API key. Check Vox > Settings.")
