"""Calendar via a private iCal (ICS) link: Google Calendar's "Secret address in iCal format",
or Outlook's published ICS link. Read-only, no sign-in, works for anyone you share Vox with."""
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

import vox_core as core

log = logging.getLogger("vox.calendar")
CACHE_SECONDS = 300
PARTSTAT = {"ACCEPTED": "accepted", "DECLINED": "declined", "TENTATIVE": "tentative", "NEEDS-ACTION": "needsAction", "": "needsAction"}


def cache_path():
    return os.path.join(core.data_dir(), "calendar.json")


def _name(prop):
    """Display name for an ATTENDEE / ORGANIZER property."""
    if prop is None:
        return ""
    cn = prop.params.get("CN") if hasattr(prop, "params") else None
    if cn:
        return str(cn).strip().strip('"')
    email = str(prop).replace("mailto:", "").replace("MAILTO:", "")
    return email.split("@")[0].replace(".", " ").title() if "@" in email else email


def _email(prop):
    return str(prop).replace("mailto:", "").replace("MAILTO:", "").strip().lower()


def _to_utc(dt):
    if not isinstance(dt, datetime):          # all-day event (date only)
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()                  # floating time: treat as local
    return dt.astimezone(timezone.utc)


def parse(ics_text, start, end, my_email=""):
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(ics_text)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        s, e = _to_utc(ev.get("DTSTART").dt), _to_utc(ev.get("DTEND").dt if ev.get("DTEND") else ev.get("DTSTART").dt)
        if s is None:
            continue
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        atts = ev.get("ATTENDEE") or []
        if not isinstance(atts, list):
            atts = [atts]
        people = []
        my_status = "accepted"   # unchanged behaviour when we cannot tell (no email set, or not on the list)
        for a in atts:
            if my_email and _email(a) == my_email.lower():
                if _email(ev.get("ORGANIZER")) != my_email.lower():
                    my_status = PARTSTAT.get(str(a.params.get("PARTSTAT", "")).upper(), "needsAction")
                continue
            if str(a.params.get("CUTYPE", "")).upper() in ("RESOURCE", "ROOM"):
                continue
            n = _name(a)
            if n and n not in people:
                people.append(n)
        if my_status == "declined":
            continue
        org = _name(ev.get("ORGANIZER"))
        desc = str(ev.get("DESCRIPTION", ""))
        loc = str(ev.get("LOCATION", ""))
        link = ""
        m = re.search(r"https://(?:meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com|teams\.live\.com)/\S+", desc + " " + loc)
        if m:
            link = m.group(0).rstrip(").,>\"'")
        out.append({
            "uid": str(ev.get("UID", "")) + "|" + s.isoformat(),
            "title": str(ev.get("SUMMARY", "(no title)")),
            "start": s.timestamp(), "end": (e or s).timestamp(),
            "attendees": people, "organizer": org, "link": link, "my_status": my_status,
        })
    out.sort(key=lambda x: x["start"])
    return out


def _safe_error(e, url):
    """The error text without the address: requests puts the whole (secret) URL into its messages."""
    if isinstance(e, requests.HTTPError) and getattr(e, "response", None) is not None:
        return "Calendar server answered %s" % e.response.status_code
    if isinstance(e, requests.RequestException):
        return "Could not reach %s (%s)" % (urllib.parse.urlparse(url).hostname or "the calendar server", type(e).__name__)
    return str(e)[:200]


def fetch(cfg, force=False):
    """Events from 12 h ago to 7 days ahead, from Google sign-in if connected, else the iCal link. Cached 5 min."""
    import gcal
    url = (cfg.get("calendar_url") or "").strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    # the cache key never holds the address itself: the secret iCal link must not be written to calendar.json
    source = "google:" + gcal.account() if gcal.connected() else ("ics:" + hashlib.sha256(url.encode()).hexdigest()[:16] if url else "")
    if not source:
        try:
            os.remove(cache_path())   # disconnected: the events of the old calendar go too
        except OSError:
            pass
        return {"events": [], "error": "", "fetched": 0, "source": ""}
    cached = {}
    try:
        with open(cache_path(), encoding="utf-8") as f:
            cached = json.load(f)
        if not force and cached.get("source") == source and time.time() - cached.get("fetched", 0) < CACHE_SECONDS:
            return cached
    except (OSError, ValueError, AttributeError):
        pass
    now = datetime.now(timezone.utc)
    try:
        if source.startswith("google:"):
            events = gcal.events()
        else:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            events = parse(r.content, now - timedelta(hours=12), now + timedelta(days=7), cfg.get("my_email", ""))
        data = {"source": source, "events": events, "error": "", "fetched": time.time()}
    except Exception as e:
        msg = _safe_error(e, url)
        log.warning("calendar fetch failed: %s", msg)
        # keep the events of the last good fetch (the same calendar): an empty list would hide the next meeting from the
        # watcher for the whole cache time. Looks stale after 30 s, so the next normal call tries again.
        old = cached.get("events") if isinstance(cached, dict) and cached.get("source") == source else None
        data = {"source": source, "events": old if isinstance(old, list) else [], "error": msg,
                "fetched": time.time() - CACHE_SECONDS + 30}
    with open(cache_path(), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def current_event(events, now=None, early=300):
    """The meeting happening now (or starting within `early` seconds). Prefers ones with attendees."""
    now = now or time.time()
    live = [e for e in events if e["start"] - early <= now <= e["end"] and e["end"] - e["start"] < 12 * 3600]
    live.sort(key=lambda e: (not e["attendees"], abs(e["start"] - now)))
    return live[0] if live else None
