"""Where the timing marks are set on the Windows side: vox_core (speech to text and cleanup, through a thread-local
scope, so the pipeline functions keep their signatures) and the engine (key down, recording, key up, inserted, and the
history entry). Nothing here touches the network or a microphone."""
import threading

import pytest

import timing
import vox_core as core


class Marks:
    """Stands in for a Timing: remembers the order of the marks."""
    def __init__(self):
        self.names = []

    def mark(self, name, at_ms=None):
        self.names.append(name)


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setattr(core, "transcribe", lambda cfg, wav, context="": "um call the dentist tomorrow please")
    monkeypatch.setattr(core, "cleanup", lambda cfg, raw, style, label: "Call the dentist tomorrow, please.")


def test_a_full_pipeline_marks_stt_then_cleanup(pipeline):
    m = Marks()
    with core.timing_scope(m):
        res = core.process_detailed({"cleanup": True}, b"\x00\x00" * 100, "notepad.exe", "notepad.exe")
    assert res.cleaned and m.names == ["stt_start", "stt_done", "llm_start", "llm_done"]


def test_skipped_cleanup_sets_no_llm_marks(pipeline):
    m = Marks()
    with core.timing_scope(m):
        core.process_detailed({"cleanup": False}, b"\x00\x00" * 100, "notepad.exe", "notepad.exe")
    assert m.names == ["stt_start", "stt_done"]


def test_a_cleanup_that_fails_still_closes_its_mark(monkeypatch):
    def boom(cfg, raw, style, label):
        raise core.ApiError(500, "down")

    monkeypatch.setattr(core, "cleanup", boom)
    m = Marks()
    with core.timing_scope(m):
        res = core.process_text({"cleanup": True}, "call the dentist tomorrow please", "x.exe", "x.exe")
    assert res.cleanup_error and m.names == ["llm_start", "llm_done"]


def test_no_scope_means_no_marks_and_no_error(pipeline):
    assert core.process_detailed({"cleanup": True}, b"\x00\x00" * 100, "x.exe", "x.exe").cleaned


def test_the_scope_ends_and_belongs_to_one_thread(pipeline):
    m, other = Marks(), Marks()
    seen = []

    def work():
        with core.timing_scope(other):
            seen.append(True)
            core.process_text({"cleanup": True}, "call the dentist tomorrow please", "x.exe", "x.exe")

    with core.timing_scope(m):
        t = threading.Thread(target=work)
        t.start()
        t.join()
    core.process_text({"cleanup": True}, "call the dentist tomorrow please", "x.exe", "x.exe")
    assert m.names == [] and other.names == ["llm_start", "llm_done"]


def test_timing_info_names_the_models_and_where_the_request_went():
    cfg = {"base_url": "https://api.groq.com/openai/v1", "api_key": "k", "stt_model": "whisper-large-v3-turbo",
           "llm_model": "openai/gpt-oss-20b"}
    info = core.timing_info(cfg)
    assert info == {"stt_model": "whisper-large-v3-turbo", "llm_model": "openai/gpt-oss-20b",
                    "provider": "api.groq.com", "relay": False}
    relay = core.timing_info({"relay_proxy": True, "relay_url": "http://100.64.0.1:8765", "relay_token": "t"})
    assert relay["relay"] is True and relay["provider"] == "relay"


def test_timing_info_never_raises_on_a_broken_config():
    assert core.timing_info({"relay_proxy": True}) ["relay"] in (True, False)
    assert set(core.timing_info(None)) == {"stt_model", "llm_model", "provider", "relay"}


def test_the_window_bridge_returns_the_speed_view(tmp_path, monkeypatch):
    ui_app = pytest.importorskip("ui_app")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    api = object.__new__(ui_app.Api)
    assert api.get_speed()["count"] == 0                      # no history file yet: an empty card, not an error
    t = timing.Timing()
    for name, at in dict(key_down=0, rec_start=30, key_up=1030, stt_start=1035, stt_done=1535, llm_start=1540,
                         llm_done=1840, inserted=1870).items():
        t.mark(name, at)
    core.add_history({"t": 5.0, "app": "notepad.exe", "words": 3, "timing": t.entry("w", "l", "groq", False)})
    core.add_history({"t": 6.0, "app": "notepad.exe", "words": 3})          # an old entry without timing
    v = api.get_speed()
    assert v["count"] == 1 and v["biggest"] == "stt" and v["last"][0]["stages"]["total"] == 840
    assert v["models"][0]["stt_model"] == "w"
