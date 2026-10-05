import requests

import vox_core as core


class FakeResp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def capture_posts(monkeypatch, responder):
    calls = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return responder(url, kw)

    monkeypatch.setattr(core.requests, "post", fake_post)
    return calls


def ok_responder(url, kw):
    if url.endswith("/audio/transcriptions"):
        return FakeResp({"text": " um so I will send it tomorrow "})
    return FakeResp({"choices": [{"message": {"content": "I will send it tomorrow."}}]})


# ------------------------------------------------------------------ defaults

def test_default_config_points_at_groq():
    assert core.DEFAULT_CONFIG["base_url"] == core.BASE == "https://api.groq.com/openai/v1"
    assert core.DEFAULT_CONFIG["stt_model"] == core.DEFAULT_STT
    assert core.DEFAULT_CONFIG["llm_model"] == core.DEFAULT_LLM


def test_api_base_defaults_and_normalizes():
    assert core.api_base({}) == core.BASE
    assert core.api_base({"base_url": ""}) == core.BASE
    assert core.api_base({"base_url": "  "}) == core.BASE
    assert core.api_base({"base_url": " http://100.64.0.1:8000/v1/ "}) == "http://100.64.0.1:8000/v1"


def test_old_config_without_base_url_still_uses_groq(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = tmp_path / "Vox"
    d.mkdir()
    (d / "config.json").write_text('{"api_key": "k"}', encoding="utf-8")
    cfg = core.load_config()
    assert core.api_base(cfg) == core.BASE


# --------------------------------------------------------------------- auth

def test_auth_header_with_key_and_without():
    assert core.auth_headers({"api_key": " abc "}) == {"Authorization": "Bearer abc"}
    assert core.auth_headers({"api_key": ""}) == {}
    assert core.auth_headers({}) == {}


# ------------------------------------------------------------- request URLs

def test_transcribe_uses_configured_base_and_model(monkeypatch):
    calls = capture_posts(monkeypatch, ok_responder)
    cfg = {"api_key": "k", "base_url": "http://laptop:8000/v1", "stt_model": "my-whisper"}
    assert core.transcribe(cfg, b"wav") == "um so I will send it tomorrow"
    assert calls[0]["url"] == "http://laptop:8000/v1/audio/transcriptions"
    assert calls[0]["data"]["model"] == "my-whisper"
    assert calls[0]["headers"] == {"Authorization": "Bearer k"}


def test_transcribe_segments_uses_configured_base(monkeypatch):
    calls = capture_posts(monkeypatch, lambda u, kw: FakeResp({"text": "hello"}))
    cfg = {"api_key": "", "base_url": "http://laptop:8000/v1"}
    segs = core.transcribe_segments(cfg, b"wav")
    assert segs[0]["text"] == "hello"
    assert calls[0]["url"] == "http://laptop:8000/v1/audio/transcriptions"
    assert calls[0]["headers"] == {}


def test_cleanup_uses_configured_base_and_model(monkeypatch):
    calls = capture_posts(monkeypatch, ok_responder)
    cfg = {"api_key": "k", "base_url": "http://laptop:11434/v1", "llm_model": "qwen3:4b"}
    out = core.cleanup(cfg, "um I will send it tomorrow", "neutral", "")
    assert out == "I will send it tomorrow."
    assert calls[0]["url"] == "http://laptop:11434/v1/chat/completions"
    assert calls[0]["json"]["model"] == "qwen3:4b"
    assert "reasoning_effort" not in calls[0]["json"]   # only sent for gpt-oss models


def test_groq_defaults_unchanged_when_nothing_configured(monkeypatch):
    calls = capture_posts(monkeypatch, ok_responder)
    cfg = dict(core.DEFAULT_CONFIG, api_key="k")
    core.process(cfg, b"\x00\x00" * 100, "notepad.exe", "Notepad")
    assert calls[0]["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert calls[1]["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert calls[1]["json"]["model"] == core.DEFAULT_LLM
    assert calls[1]["json"]["reasoning_effort"] == "low"


# ------------------------------------------------------ fallback on failure

def test_cleanup_http_error_falls_back_to_raw_transcript(monkeypatch):
    def responder(url, kw):
        if url.endswith("/audio/transcriptions"):
            return FakeResp({"text": "send it tomorrow please"})
        return FakeResp({"error": {"message": "boom"}}, status=500)

    capture_posts(monkeypatch, responder)
    cfg = dict(core.DEFAULT_CONFIG, api_key="k", base_url="http://laptop:8000/v1")
    raw, out = core.process(cfg, b"\x00\x00" * 100, "notepad.exe", "Notepad")
    assert raw == out == "send it tomorrow please"


def test_cleanup_connection_error_falls_back_to_raw_transcript(monkeypatch):
    def responder(url, kw):
        if url.endswith("/audio/transcriptions"):
            return FakeResp({"text": "send it tomorrow please"})
        raise requests.ConnectionError("down")

    capture_posts(monkeypatch, responder)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    cfg = dict(core.DEFAULT_CONFIG, api_key="k", base_url="http://laptop:8000/v1")
    raw, out = core.process(cfg, b"\x00\x00" * 100, "notepad.exe", "Notepad")
    assert raw == out == "send it tomorrow please"


# ---------------------------------------------------------------- check_key

def test_check_key_uses_base_url(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen.update(url=url, **kw)
        return FakeResp({}, status=200)

    monkeypatch.setattr(core.requests, "get", fake_get)
    assert core.check_key("k")
    assert seen["url"] == "https://api.groq.com/openai/v1/models"
    assert core.check_key("k", "http://laptop:8000/v1/")
    assert seen["url"] == "http://laptop:8000/v1/models"
    assert seen["allow_redirects"] is False   # the key never follows a redirect (final review RC-M6)
