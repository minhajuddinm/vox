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
