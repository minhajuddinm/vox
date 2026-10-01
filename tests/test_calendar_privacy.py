"""The secret iCal address is a bearer secret: it must not reach the logs, calendar.json or config.json."""
import json
import logging
import types

import pytest
import requests

import gcal
import secret
import vcalendar
import vox_core as core

URL = "https://calendar.google.com/calendar/ical/me%40x.com/private-SECRET123/basic.ics"


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(secret, "_backend", (lambda b: b[::-1], lambda b: b[::-1]))
    monkeypatch.setattr(gcal, "connected", lambda: False)
    return tmp_path / "Vox"


def failing_get(exc):
    def get(url, **kw):
        if isinstance(exc, requests.HTTPError):
            resp = types.SimpleNamespace(status_code=404, content=b"",
                                         raise_for_status=lambda: (_ for _ in ()).throw(exc))
            return resp
        raise exc
    return get


@pytest.mark.parametrize("exc", [
    requests.ConnectionError("HTTPSConnectionPool: Max retries exceeded with url: /calendar/ical/me%40x.com/private-SECRET123/basic.ics"),
    requests.HTTPError("404 Client Error: Not Found for url: " + URL, response=types.SimpleNamespace(status_code=404)),
])
def test_a_failed_fetch_does_not_leak_the_address(appdata, monkeypatch, caplog, exc):
    monkeypatch.setattr(vcalendar.requests, "get", failing_get(exc))
    with caplog.at_level(logging.DEBUG):
        data = vcalendar.fetch({"calendar_url": URL}, force=True)
    assert data["error"]
    assert "SECRET123" not in json.dumps(data)
    assert "SECRET123" not in (appdata / "calendar.json").read_text(encoding="utf-8")
    assert "SECRET123" not in caplog.text


def test_disconnecting_the_ical_calendar_removes_the_cache(appdata, monkeypatch):
    monkeypatch.setattr(vcalendar.requests, "get", failing_get(requests.ConnectionError("x")))
    vcalendar.fetch({"calendar_url": URL}, force=True)
    assert (appdata / "calendar.json").exists()
    vcalendar.fetch({"calendar_url": ""})
    assert not (appdata / "calendar.json").exists()


def test_the_address_is_protected_in_config_json(appdata):
    core.save_config(dict(core.DEFAULT_CONFIG, calendar_url="https://x/private-S"))
    raw = (appdata / "config.json").read_text(encoding="utf-8")
    assert "private-S" not in raw
    assert core.load_config()["calendar_url"] == "https://x/private-S"


# ---- a failed fetch keeps the events it already had (C-M1) ---------------------------------------------------------------

def _good_cache(appdata, url=URL):
    import hashlib
    import time
    source = "ics:" + hashlib.sha256(url.encode()).hexdigest()[:16]
    ev = {"uid": "a|x", "title": "Standup", "start": time.time() + 120, "end": time.time() + 1800, "attendees": ["Ann"],
          "organizer": "", "link": "", "my_status": "accepted"}
    appdata.mkdir(parents=True, exist_ok=True)
    (appdata / "calendar.json").write_text(json.dumps({"source": source, "events": [ev], "error": "", "fetched": time.time()}),
                                           encoding="utf-8")
    return ev


def test_a_failed_fetch_keeps_the_events_of_the_last_good_one_and_retries_soon(appdata, monkeypatch):
    import time
    ev = _good_cache(appdata)
    calls = []

    def get(url, **kw):
        calls.append(url)
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(vcalendar.requests, "get", get)
    data = vcalendar.fetch({"calendar_url": URL}, force=True)
    assert data["events"] == [ev] and data["error"]
    assert len(calls) == 1
    vcalendar.fetch({"calendar_url": URL})          # a normal call right after: served from the cache, no new request
    assert len(calls) == 1
    real = time.time
    monkeypatch.setattr(vcalendar.time, "time", lambda: real() + 31)
    data = vcalendar.fetch({"calendar_url": URL})   # about 30 s later: tried again, not after 5 minutes
    assert len(calls) == 2 and data["events"] == [ev]


def test_a_failed_fetch_for_another_calendar_does_not_borrow_the_old_events(appdata, monkeypatch):
    _good_cache(appdata, url="https://x/other-calendar.ics")
    monkeypatch.setattr(vcalendar.requests, "get", failing_get(requests.ConnectionError("offline")))
    assert vcalendar.fetch({"calendar_url": URL}, force=True)["events"] == []
