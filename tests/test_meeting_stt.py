"""Meeting recorder STT retry rule: a read timeout through the relay is never sent again (windows/meeting.py `_stt`)."""
import sys
import types

import pytest
import requests

import vox_core as core

RELAY = {"relay_proxy": True, "relay_url": "https://yuvipi.tail1234.ts.net", "relay_token": "RELAY-TOKEN"}


@pytest.fixture
def meeting_mod(monkeypatch):
    # meeting.py imports numpy at module level. CI's tests job has no numpy, so stub it for this import only.
    stubbed = False
    try:
        import numpy  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
        stubbed = True
    try:
        import meeting
        monkeypatch.setattr(meeting.time, "sleep", lambda s: None)
        yield meeting
    finally:
        if stubbed:
            sys.modules.pop("meeting", None)   # never leave a stub-bound meeting module for other tests


def _meeting(meeting_mod, monkeypatch, cfg, exc):
    calls = []

    def fake(cfg_, wav, prompt=None, model=None):
        calls.append(1)
        raise exc

    monkeypatch.setattr(core, "transcribe_segments", fake)
    monkeypatch.setattr(core, "pcm_to_wav", lambda pcm: b"wav")
    m = meeting_mod.Meeting(lambda: cfg)
    monkeypatch.setattr(m, "_pace", lambda: None)
    return m, calls


def test_a_read_timeout_through_the_relay_is_sent_once(meeting_mod, monkeypatch):
    m, calls = _meeting(meeting_mod, monkeypatch, RELAY, requests.ReadTimeout("slow"))
    assert m._stt(b"pcm", "") is None
    assert len(calls) == 1
    assert "Network" in m.last_error


def test_a_read_timeout_without_the_relay_keeps_the_four_attempts(meeting_mod, monkeypatch):
    m, calls = _meeting(meeting_mod, monkeypatch, {}, requests.ReadTimeout("slow"))
    assert m._stt(b"pcm", "") is None
    assert len(calls) == 4


def test_connect_errors_and_connect_timeouts_through_the_relay_are_still_retried(meeting_mod, monkeypatch):
    for exc in (requests.ConnectionError("down"), requests.ConnectTimeout("no route")):
        m, calls = _meeting(meeting_mod, monkeypatch, RELAY, exc)
        assert m._stt(b"pcm", "") is None
        assert len(calls) == 4, exc


def test_a_relay_502_is_still_retried_by_the_meeting_loop(meeting_mod, monkeypatch):
    m, calls = _meeting(meeting_mod, monkeypatch, RELAY, core.ApiError(502, "bad gateway"))
    assert m._stt(b"pcm", "") is None
    assert len(calls) == 4
