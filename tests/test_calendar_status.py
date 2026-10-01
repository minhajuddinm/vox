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


# ---- calendar text is one clean line and bounded (C-M4) --------------------------------------------------------------------

def test_a_google_event_with_200_attendees_and_a_messy_title_is_bounded_and_one_line():
    people = [{"email": "p%d@x.com" % i, "displayName": "Person %d\twith\nbreaks" % i} for i in range(200)]
    ev = item(people)
    ev["summary"] = "Sync\twith\nBob " + "x" * 300
    ev["organizer"] = {"email": "boss@x.com", "displayName": "Boss\n" + "y" * 300}
    out = gcal._parse_items([ev])[0]
    assert len(out["attendees"]) == 30
    assert all(len(a) <= 80 and all(ord(c) >= 32 for c in a) for a in out["attendees"])
    assert out["title"].startswith("Sync with Bob x") and len(out["title"]) == 120
    assert len(out["organizer"]) == 80 and "\n" not in out["organizer"]


def test_an_ics_title_with_an_escaped_newline_becomes_one_line_and_attendees_are_capped():
    pytest.importorskip("icalendar")
    pytest.importorskip("recurring_ical_events")
    from datetime import datetime, timezone
    bs = chr(92)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//t//EN", "BEGIN:VEVENT", "UID:u1", "DTSTAMP:20261001T000000Z",
             "DTSTART:20261002T100000Z", "DTEND:20261002T110000Z", "SUMMARY:Line1" + bs + "nLine2",
             "ORGANIZER;CN=" + "O" * 200 + ":mailto:boss@x.com"]
    lines += ["ATTENDEE;CN=Person %d:mailto:p%d@x.com" % (i, i) for i in range(200)]
    lines += ["END:VEVENT", "END:VCALENDAR", ""]
    out = vcalendar.parse("\r\n".join(lines), datetime(2026, 10, 1, tzinfo=timezone.utc),
                          datetime(2026, 10, 5, tzinfo=timezone.utc), "")[0]
    assert out["title"] == "Line1 Line2"
    assert len(out["attendees"]) == 30 and len(out["organizer"]) == 80


@pytest.fixture
def export_name():
    import sys
    import types
    stubbed = False
    try:
        import numpy  # noqa: F401
    except ImportError:   # CI's tests job has no numpy; meeting.py imports it at module level
        sys.modules["numpy"] = types.ModuleType("numpy")
        stubbed = True
    try:
        import meeting
        yield meeting.export_name
    finally:
        if stubbed:
            sys.modules.pop("numpy", None)
            sys.modules.pop("meeting", None)


def test_export_name_is_a_safe_single_line_file_name(export_name):
    assert export_name("Sync\twith\nBob") == "Sync with Bob"
    assert all(ord(c) >= 32 for c in export_name("a\x00b\x1fc"))
    assert not export_name("a" * 59 + ".").endswith(".")
    assert export_name("../..\\x: y?") == "x y"
    assert export_name("") == "Meeting" and export_name(" . ") == "Meeting"
    assert len(export_name("z" * 200)) == 60
