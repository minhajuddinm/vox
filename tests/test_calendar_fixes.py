"""Calendar fixes of the v2 review: attendee e-mail addresses never become names (PRV-1), the secret iCal address goes only
over https or to a private address, redirects included (DAT-15 / SEC-12), and the Google revoke keeps the token out of
the URL (SEC-12). No network."""
import types

import pytest

import gcal
import secret
import vcalendar

ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
       "DTSTAMP:20261001T000000Z\r\nDTSTART:20261002T100000Z\r\nDTEND:20261002T110000Z\r\nSUMMARY:Sync\r\n"
       "ORGANIZER;CN=boss.person@example.com:mailto:boss.person@example.com\r\n"
       "ATTENDEE;CN=jane.guest@example.org;PARTSTAT=ACCEPTED:mailto:jane.guest@example.org\r\n"
       "ATTENDEE;CN=Bob Named;PARTSTAT=ACCEPTED:mailto:bob@example.org\r\n"
       "ATTENDEE;CN=\"carl@x.io\";PARTSTAT=ACCEPTED:mailto:other@example.org\r\n"
       "ATTENDEE;PARTSTAT=ACCEPTED:mailto:dee.dee@example.org\r\n"
       "END:VEVENT\r\nEND:VCALENDAR\r\n")


@pytest.mark.parametrize("name, email, expected", [
    ("Bob Named", "bob@example.org", "Bob Named"),
    ("jane.guest@example.org", "jane.guest@example.org", "Jane Guest"),   # Google's iCal: CN is the address
    ("carl@x.io", "other@example.org", "Carl"),
    ("", "dee.dee@example.org", "Dee Dee"),
    ("", "", ""),
    (None, "ext.boss@partner.com", "Ext Boss"),
])
def test_a_person_is_shown_by_name_or_by_the_part_before_the_at(name, email, expected):
    assert vcalendar.person_name(name, email) == expected


def test_ical_attendees_and_organizer_carry_no_address():
    pytest.importorskip("icalendar")
    pytest.importorskip("recurring_ical_events")
    from datetime import datetime, timezone
    ev = vcalendar.parse(ICS, datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 10, 5, tzinfo=timezone.utc))[0]
    assert ev["attendees"] == ["Jane Guest", "Bob Named", "Carl", "Dee Dee"]
    assert ev["organizer"] == "Boss Person"
    assert "@" not in repr(ev)


def test_a_google_organizer_without_a_name_is_not_its_address():
    item = {"id": "e1", "summary": "Sync", "status": "confirmed",
            "start": {"dateTime": "2026-10-02T10:00:00Z"}, "end": {"dateTime": "2026-10-02T11:00:00Z"},
            "attendees": [{"email": "a.b@x.com", "displayName": "c@x.com"}], "organizer": {"email": "ext.boss@partner.com"}}
    ev = gcal._parse_items([item])[0]
    assert ev["organizer"] == "Ext Boss" and ev["attendees"] == ["C"]


# ---- the secret iCal address: https only (or a private address), also after a redirect -------------------------------

@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(secret, "_backend", (lambda b: b[::-1], lambda b: b[::-1]))
    monkeypatch.setattr(gcal, "connected", lambda: False)
    return tmp_path / "Vox"


@pytest.mark.parametrize("url, refused", [
    ("http://calendar.example.com/private-SECRET/basic.ics", True),
    ("ftp://calendar.example.com/x.ics", True),
    ("https://calendar.example.com/private-SECRET/basic.ics", False),
    ("webcal://calendar.example.com/private-SECRET/basic.ics", False),
    ("http://192.168.1.20/cal.ics", False),
    ("", False),
])
def test_the_address_rule(url, refused):
    assert bool(vcalendar.url_problem(url)) is refused


def test_a_plain_http_address_is_never_fetched(appdata, monkeypatch):
    calls = []
    monkeypatch.setattr(vcalendar.requests, "get", lambda url, **kw: calls.append(url))
    data = vcalendar.fetch({"calendar_url": "http://calendar.example.com/private-SECRET/basic.ics"}, force=True)
    assert calls == [] and data["events"] == [] and "https" in data["error"] and "SECRET" not in data["error"]


def _reply(status, location=None, content=b""):
    return types.SimpleNamespace(status_code=status, headers={"Location": location} if location else {}, content=content,
                                 raise_for_status=lambda: None)


def test_a_redirect_to_plain_http_is_not_followed(appdata, monkeypatch):
    calls = []

    def get(url, **kw):
        calls.append((url, kw.get("allow_redirects")))
        return _reply(302, "http://evil.example.net/steal")

    monkeypatch.setattr(vcalendar.requests, "get", get)
    data = vcalendar.fetch({"calendar_url": "https://calendar.example.com/private-SECRET/basic.ics"}, force=True)
    assert calls == [("https://calendar.example.com/private-SECRET/basic.ics", False)]
    assert data["error"] and "evil" not in data["error"]


def test_a_redirect_to_https_is_followed(appdata, monkeypatch):
    pytest.importorskip("icalendar")
    pytest.importorskip("recurring_ical_events")
    calls = []

    def get(url, **kw):
        calls.append(url)
        return _reply(301, "https://other.example.com/cal.ics") if len(calls) == 1 else _reply(200, content=ICS.encode())

    monkeypatch.setattr(vcalendar.requests, "get", get)
    data = vcalendar.fetch({"calendar_url": "https://calendar.example.com/private-SECRET/basic.ics"}, force=True)
    assert calls == ["https://calendar.example.com/private-SECRET/basic.ics", "https://other.example.com/cal.ics"]
    assert data["error"] == ""


def test_connecting_a_plain_http_address_is_refused_and_not_saved(appdata, monkeypatch):
    import sys
    from unittest.mock import MagicMock
    for name in ("pyperclip", "webview", "numpy"):
        try:
            __import__(name)
        except ImportError:
            monkeypatch.setitem(sys.modules, name, MagicMock())
    import ui_app
    import vox_core as core
    monkeypatch.setattr(vcalendar.requests, "get", lambda url, **kw: (_ for _ in ()).throw(AssertionError("fetched")))
    api = ui_app.Api.__new__(ui_app.Api)
    res = api.connect_calendar("http://calendar.example.com/private-SECRET/basic.ics")
    assert res["error"] and not res["connected"]
    assert core.load_config()["calendar_url"] == ""


def test_the_google_revoke_sends_the_token_in_the_body(appdata, monkeypatch):
    gcal._save_token({"refresh_token": "rt-SECRET", "access_token": "at", "email": "me@x.com"})
    sent = []
    monkeypatch.setattr(gcal.requests, "post", lambda url, **kw: sent.append((url, kw)))
    gcal.disconnect()
    url, kw = sent[0]
    assert "rt-SECRET" not in url and "params" not in kw and kw["data"] == {"token": "rt-SECRET"}


def test_connecting_google_does_not_log_the_account_address():
    # PRV-13 of the v2 review: vox.log is described as holding app names and server errors only
    import inspect
    import re
    for call in re.findall(r"log\.\w+\((.*)\)", inspect.getsource(gcal)):
        assert "email" not in call
