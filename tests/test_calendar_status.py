"""A declined invite never reminds or records; one you have not answered only reminds."""
import pytest

import gcal
import vcalendar


def item(attendees, organizer=None):
    return {"id": "e1", "summary": "Sync", "status": "confirmed",
            "start": {"dateTime": "2026-10-02T10:00:00Z"}, "end": {"dateTime": "2026-10-02T11:00:00Z"},
            "attendees": attendees, "organizer": organizer or {"email": "boss@x.com"}}


ME = {"email": "me@x.com", "self": True}
BOB = {"email": "bob@x.com", "displayName": "Bob"}


def test_a_declined_google_event_is_dropped():
    assert gcal._parse_items([item([dict(ME, responseStatus="declined"), BOB])]) == []


def test_an_unanswered_google_event_is_marked_needs_action():
    out = gcal._parse_items([item([dict(ME, responseStatus="needsAction"), BOB])])
    assert [e["my_status"] for e in out] == ["needsAction"]


def test_an_accepted_or_own_google_event_is_accepted():
    assert gcal._parse_items([item([dict(ME, responseStatus="accepted"), BOB])])[0]["my_status"] == "accepted"
    own = item([dict(ME, responseStatus="needsAction"), BOB], organizer={"email": "me@x.com", "self": True})
    assert gcal._parse_items([own])[0]["my_status"] == "accepted"
    assert gcal._parse_items([item([BOB])])[0]["my_status"] == "accepted"   # no own attendee entry: cannot tell


def ics(partstat):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
            "DTSTAMP:20261001T000000Z\r\nDTSTART:20261002T100000Z\r\nDTEND:20261002T110000Z\r\nSUMMARY:Sync\r\n"
            "ORGANIZER:mailto:boss@x.com\r\n"
            "ATTENDEE;PARTSTAT=%s:mailto:me@x.com\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:bob@x.com\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n" % partstat)


def parse(partstat, me="me@x.com"):
    pytest.importorskip("icalendar")
    pytest.importorskip("recurring_ical_events")
    from datetime import datetime, timezone
    return vcalendar.parse(ics(partstat), datetime(2026, 10, 1, tzinfo=timezone.utc),
                           datetime(2026, 10, 5, tzinfo=timezone.utc), me)


def test_a_declined_ics_event_is_dropped():
    assert parse("DECLINED") == []


@pytest.mark.parametrize("partstat,expected", [("NEEDS-ACTION", "needsAction"), ("TENTATIVE", "tentative"), ("ACCEPTED", "accepted")])
def test_ics_participation_status(partstat, expected):
    assert [e["my_status"] for e in parse(partstat)] == [expected]


def test_without_my_email_the_status_is_accepted():
    assert [e["my_status"] for e in parse("NEEDS-ACTION", me="")] == ["accepted"]


@pytest.fixture
def calendar_action():
    for mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
        pytest.importorskip(mod)
    import engine
    return engine.calendar_action


def test_the_watcher_starts_only_accepted_meetings(calendar_action):
    now = 1000.0
    ev = {"attendees": ["A"], "start": now, "my_status": "accepted"}
    assert calendar_action(ev, {"auto_notes": True}, now) == "start"
    assert calendar_action(dict(ev, my_status="needsAction"), {"auto_notes": True}, now) == "remind"
    assert calendar_action(dict(ev, my_status="tentative"), {"auto_notes": True}, now) == "remind"
    assert calendar_action(ev, {"auto_notes": False}, now) == "remind"
    assert calendar_action({k: v for k, v in ev.items() if k != "my_status"}, {"auto_notes": True}, now) == "start"
    assert calendar_action(dict(ev, attendees=[]), {"auto_notes": True}, now) is None
    assert calendar_action(dict(ev, start=now + 600), {"auto_notes": True}, now) is None
