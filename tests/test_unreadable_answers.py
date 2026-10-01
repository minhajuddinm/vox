"""A 200 answer the server got wrong (empty choices, null text) must not throw a dictation away."""
import pytest

import vox_core as core


class FakeResp:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def serve(monkeypatch, payload):
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: FakeResp(payload))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)


def test_empty_choices_fall_back_to_the_raw_words(monkeypatch):
    serve(monkeypatch, {"choices": []})
    cfg = dict(core.DEFAULT_CONFIG, api_key="k")
    r = core.process_text(cfg, "one two three four", "notepad.exe", "Notepad")
    assert r.text.lower().startswith("one two three four")
    assert r.cleanup_error and not r.cleaned


def test_a_cleanup_answer_of_the_wrong_shape_raises_api_error(monkeypatch):
    serve(monkeypatch, {"choices": [None]})
    with pytest.raises(core.ApiError):
        core.chat_text(dict(core.DEFAULT_CONFIG, api_key="k"), {"model": "m", "messages": []})


def test_an_unexpected_cleanup_exception_falls_back_to_the_raw_words(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("surprise")

    monkeypatch.setattr(core, "cleanup", boom)
    cfg = dict(core.DEFAULT_CONFIG, api_key="k")
    r = core.process_text(cfg, "one two three four", "notepad.exe", "Notepad")
    assert r.text and r.cleanup_error and "RuntimeError" in r.cleanup_error


@pytest.mark.parametrize("payload", [{"text": None}, {"text": 5}, ["x"]])
def test_a_speech_answer_the_server_got_wrong_raises_api_error(monkeypatch, payload):
    serve(monkeypatch, payload)
    with pytest.raises(core.ApiError):
        core.transcribe(dict(core.DEFAULT_CONFIG, api_key="k"), b"wav")
