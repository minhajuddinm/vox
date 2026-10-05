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
MAX_ATTENDEES = 30   # calendar text goes into prompts and file names: bounded and on one line (see core.one_line)
PARTSTAT = {"ACCEPTED": "accepted", "DECLINED": "declined", "TENTATIVE": "tentative", "NEEDS-ACTION": "needsAction", "": "needsAction"}


def cache_path():
    return os.path.join(core.data_dir(), "calendar.json")


MAX_REDIRECTS = 3
NOT_HTTPS = "Use the https:// address of your calendar (plain http only for this PC, your local network or Tailscale)."


def person_name(name, email):
    """How a person is shown, saved and sent to the AI servers: the display name, or else the part of the address before
    the @ ("jane.guest@x.org" -> "Jane Guest"). A display name that is itself an address (Google's iCal writes the
    address as CN for a guest without a name) counts as none: an e-mail address is never used as a name."""
    name = (name or "").strip().strip('"').strip()
    if name and "@" not in name:
        return name
    addr = name or (email or "").strip()
    return addr.split("@")[0].replace(".", " ").title().strip() if "@" in addr else addr


def _name(prop):
    """Display name for an ATTENDEE / ORGANIZER property."""
    if prop is None:
        return ""
    cn = prop.params.get("CN") if hasattr(prop, "params") else None
    return person_name(str(cn) if cn else "", str(prop).replace("mailto:", "").replace("MAILTO:", ""))


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
            n = core.one_line(_name(a), 80)
            if n and n not in people and len(people) < MAX_ATTENDEES:
                people.append(n)
        if my_status == "declined":
            continue
        org = core.one_line(_name(ev.get("ORGANIZER")), 80)
        desc = str(ev.get("DESCRIPTION", ""))
        loc = str(ev.get("LOCATION", ""))
        link = ""
        m = re.search(r"https://(?:meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com|teams\.live\.com)/\S+", desc + " " + loc)
        if m:
            link = m.group(0).rstrip(").,>\"'")
        out.append({
            "uid": str(ev.get("UID", "")) + "|" + s.isoformat(),
            "title": core.one_line(ev.get("SUMMARY", "(no title)"), 120),
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


def _https(url):
    url = (url or "").strip()
    return "https://" + url[len("webcal://"):] if url.lower().startswith("webcal://") else url


def url_problem(url):
    """Why this iCal address cannot be used, or '' (also for none). The address is a secret that gives anyone your
    events, so it goes only over https (webcal:// is https), or plain http to a private address (vox_core.is_private_host,
    the rule of the AI server addresses)."""
    u = urllib.parse.urlparse(_https(url))
    if not _https(url) or (u.hostname and (u.scheme == "https" or (u.scheme == "http" and core.is_private_host(u.hostname)))):
        return ""
    return NOT_HTTPS


def _get(url):
    """GET of the iCal address. A redirect is followed only to an address url_problem accepts: requests would follow one
    to plain http and send the secret path and the events in clear text."""
    for _ in range(MAX_REDIRECTS + 1):
        r = requests.get(url, timeout=20, allow_redirects=False)
        loc = (getattr(r, "headers", None) or {}).get("Location")
        if getattr(r, "status_code", 200) in (301, 302, 303, 307, 308) and loc:
            url = urllib.parse.urljoin(url, loc)
            if url_problem(url):
                raise ValueError("The calendar server sent Vox to an address that is not https.")
            continue
        r.raise_for_status()
        return r
    raise ValueError("The calendar server sent Vox elsewhere too many times.")


def fetch(cfg, force=False):
    """Events from 12 h ago to 7 days ahead, from Google sign-in if connected, else the iCal link. Cached 5 min."""
    import gcal
    url = _https(cfg.get("calendar_url"))
    if url and not gcal.connected() and url_problem(url):
        return {"events": [], "error": url_problem(url), "fetched": 0, "source": ""}
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
            r = _get(url)
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
