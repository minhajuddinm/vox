import pytest
import requests

import vox_core as core


class FakeResp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def script(monkeypatch, stt_text, cleanup_response):
    def fake_post(url, **kw):
        if url.endswith("/audio/transcriptions"):
            return FakeResp({"text": stt_text})
        r = cleanup_response
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(core.requests, "post", fake_post)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)


CFG = dict(core.DEFAULT_CONFIG, api_key="k", app_styles={"code.exe": "raw"})
AUDIO = b"\x00\x00" * 100


def ok(text):
    return FakeResp({"choices": [{"message": {"content": text}}]})


def test_raw_style_still_turns_spoken_new_line_into_a_line_break(monkeypatch):
    script(monkeypatch, "ls -la new line cd ..", ok("SHOULD NOT BE USED"))
    r = core.process_detailed(CFG, AUDIO, "code.exe", "code")
    assert r.text == "ls -la\ncd .." and not r.cleaned and r.cleanup_error == ""


def test_cleanup_off_also_uses_the_spoken_commands(monkeypatch):
    script(monkeypatch, "hello new paragraph world again", ok("x"))
    r = core.process_detailed(dict(CFG, cleanup=False), AUDIO, "notepad.exe", "Notepad")
    assert r.text == "hello\n\nworld again" and not r.cleaned


def test_cleanup_result_is_used_as_is_and_not_touched_again(monkeypatch):
    script(monkeypatch, "hello new line there my friend", ok("Hello,\nthere my friend."))
    r = core.process_detailed(CFG, AUDIO, "notepad.exe", "Notepad")
    assert r.text == "Hello,\nthere my friend." and r.cleaned and r.cleanup_error == ""


def test_failed_cleanup_falls_back_to_raw_with_commands_and_reports_why(monkeypatch):
    script(monkeypatch, "one new line two three", FakeResp({"error": {"message": "boom"}}, status=401))
    r = core.process_detailed(CFG, AUDIO, "notepad.exe", "Notepad")
    assert r.text == "one\ntwo three" and not r.cleaned and "boom" in r.cleanup_error


def test_network_failure_in_cleanup_is_reported_too(monkeypatch):
    script(monkeypatch, "one two three four", requests.ConnectionError("down"))
    r = core.process_detailed(CFG, AUDIO, "notepad.exe", "Notepad")
    assert r.text == "one two three four" and "down" in r.cleanup_error


def test_runaway_cleanup_answer_is_rejected_and_reported(monkeypatch):
    script(monkeypatch, "one two three", ok("x" * 500))
    r = core.process_detailed(CFG, AUDIO, "notepad.exe", "Notepad")
    assert r.text == "One two three" and r.cleanup_error and r.fidelity_fallback   # the guard's fallback starts with a capital


def test_nothing_said_gives_an_empty_result(monkeypatch):
    script(monkeypatch, "", ok("x"))
    r = core.process_detailed(CFG, AUDIO, "notepad.exe", "Notepad")
    assert (r.raw, r.text, r.cleaned, r.cleanup_error) == ("", "", False, "")


def test_process_keeps_its_two_value_shape(monkeypatch):
    script(monkeypatch, "one two three", ok("One two three."))
    assert core.process(CFG, AUDIO, "notepad.exe", "Notepad") == ("one two three", "One two three.")


@pytest.mark.parametrize("text,expected", [
    ("newline is one word", "newline is one word"),
    ("Wait? new line ok", "Wait?\nok"),
    ("  padded new line end  ", "padded\nend"),
    ("", ""),
    (None, ""),
])
def test_apply_spoken_commands_edges(text, expected):
    assert core.apply_spoken_commands(text) == expected
