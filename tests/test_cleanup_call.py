"""The cleanup request on Windows matches the phone's (ApiClient.cleanup, Latency): temperature 0, max_tokens with room for
hidden reasoning, a wait that grows with the words and is not repeated after a read timeout, and an answer cut off at
max_tokens (finish_reason "length") counted as a failed cleanup, so the spoken words are used."""
import pytest
import requests

import vox_core as core


class Reply:
    def __init__(self, content="Hello there.", finish="stop", status=200):
        self.status_code, self._content, self._finish = status, content, finish
        self.text = ""

    def json(self):
        return {"choices": [{"message": {"content": self._content}, "finish_reason": self._finish}]}


CFG = {"api_key": "k", "cleanup": True, "cleanup_min_words": 1}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(core.time, "sleep", lambda s: None)


def test_the_cleanup_request_uses_temperature_0_and_the_phones_token_bound(monkeypatch):
    seen = []
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.append(kw) or Reply())
    core.cleanup(dict(CFG), "hello there", "neutral", "")
    body = seen[0]["json"]
    assert body["temperature"] == 0
    assert body["max_tokens"] == core.cleanup_max_tokens("hello there", True) == 256 + 768


def test_a_model_that_does_not_think_gets_no_headroom(monkeypatch):
    seen = []
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.append(kw) or Reply())
    core.cleanup(dict(CFG, llm_model="llama-3.3-70b-versatile"), "hello there", "neutral", "")
    assert seen[0]["json"]["max_tokens"] == 256


def test_the_wait_grows_with_the_words_and_is_capped(monkeypatch):
    seen = []
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.append(kw) or Reply())
    core.cleanup(dict(CFG), "one two three four five", "neutral", "")
    assert seen[0]["timeout"] == pytest.approx(20.3)
    assert core.cleanup_read_ms(10_000) == 60_000


def test_a_read_timeout_is_not_sent_again(monkeypatch):
    calls = []

    def slow(url, **kw):
        calls.append(url)
        raise requests.ReadTimeout("slow")

    monkeypatch.setattr(core.requests, "post", slow)
    res = core.process_text(dict(CFG), "hello there friend", "", "")
    assert len(calls) == 1
    assert res.text and not res.cleaned and res.cleanup_error


def test_a_dropped_connection_is_still_retried(monkeypatch):
    outcomes = [requests.ConnectionError("reset"), Reply()]

    def post(url, **kw):
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o

    monkeypatch.setattr(core.requests, "post", post)
    assert core.cleanup(dict(CFG), "hello there", "neutral", "") == "Hello there."


def test_the_speech_request_still_retries_a_read_timeout(monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            raise requests.ReadTimeout("slow")
        return type("R", (), {"status_code": 200, "json": lambda self: {"text": "hi"}, "text": ""})()

    monkeypatch.setattr(core.requests, "post", post)
    assert core.transcribe({"api_key": "k"}, core.pcm_to_wav(b"\x00\x00" * 1600)) == "hi"
    assert len(calls) == 2


def test_an_answer_cut_off_at_max_tokens_falls_back_to_the_spoken_words(monkeypatch):
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Reply("Hello the", finish="length"))
    res = core.process_text(dict(CFG), "hello there friend", "", "")
    assert not res.cleaned and "cut off" in res.cleanup_error and res.text == "hello there friend"


@pytest.mark.parametrize("model, thinks", [("openai/gpt-oss-20b", True), ("qwen/qwen3-32b", True), ("deepseek-r1-distill", True),
                                           ("my-thinking-model", True), ("llama-3.3-70b-versatile", False), ("", False)])
def test_which_models_may_think(model, thinks):
    assert core.may_think(model) is thinks
