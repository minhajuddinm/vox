"""Google Calendar sign-in (OAuth 2.0 for desktop apps, loopback redirect + PKCE) and event reading.

Works for Gmail and Google Workspace (school/work) accounts, unless the Workspace admin blocks the app.
Only read access to events is requested. The tokens are stored protected by the Windows login (secret.py) in %APPDATA%\\Vox\\google_token.json.
"""
import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone

import requests

import secret
import vcalendar
import vox_core as core

log = logging.getLogger("vox.gcal")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPES = "openid email https://www.googleapis.com/auth/calendar.events.readonly"


# ------------------------------------------------------------------ client id
def _client():
    """OAuth client of the Vox Google Cloud project: google_client.json next to the app (or bundled)."""
    here = os.path.dirname(os.path.abspath(sys.argv[0] or __file__))
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for p in (os.path.join(here, "google_client.json"), os.path.join(base, "google_client.json"),
              os.path.join(core.data_dir(), "google_client.json")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            c = data.get("installed") or data.get("web") or data
            return c["client_id"], c.get("client_secret", "")
    return None, None


def available():
    return _client()[0] is not None


def token_path():
    return os.path.join(core.data_dir(), "google_token.json")


def connected():
    return os.path.exists(token_path())


TOKEN_FIELDS = ("refresh_token", "access_token")


def _load_token():
    """The saved tokens in plain text ({} when the file is missing or unreadable). A token file written by an older
    Vox in plain text is read as it is and protected at once."""
    try:
        with open(token_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    tok = dict(raw)
    legacy = False
    for k in TOKEN_FIELDS:
        v = raw.get(k)
        if isinstance(v, str) and v:
            legacy = legacy or not secret.is_protected(v)
            tok[k] = secret.unprotect(v)
    if legacy and secret.available():
        try:
            _save_token(tok)
        except OSError:
            log.warning("could not protect the saved Google token")
    return tok


def _save_token(tok):
    """Writes the tokens protected by the Windows login, through a temp file so a crash cannot cut the file."""
    on_disk = dict(tok, **{k: secret.protect(tok.get(k) or "") for k in TOKEN_FIELDS})
    tmp = token_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(on_disk, f)
    os.replace(tmp, token_path())


def account():
    return _load_token().get("email", "")


def disconnect():
    try:
        tok = _load_token()
        requests.post("https://oauth2.googleapis.com/revoke", data={"token": tok.get("refresh_token")}, timeout=10)   # not in the URL: proxies log URLs
    except Exception:
        pass
    try:
        os.remove(token_path())
    except OSError:
        pass
    try:
        os.remove(os.path.join(core.data_dir(), "calendar.json"))
    except OSError:
        pass


# ---------------------------------------------------------------------- login
_PAGE = """<!doctype html><meta charset="utf-8"><title>Vox</title>
<body style="font:16px system-ui;display:grid;place-items:center;height:90vh;background:#F7F7F5;color:#1B1B1F">
<div style="text-align:center"><h2>{title}</h2><p>{msg}</p><p style="color:#6B6B73">You can close this tab and go back to Vox.</p></div>"""


def connect(timeout=240):
    """Opens the browser for Google sign-in and waits for the result. Returns {"ok", "email"|"error"}."""
    client_id, client_secret = _client()
    if not client_id:
        return {"ok": False, "error": "This Vox build has no Google sign-in configured (google_client.json missing)."}
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404)
                self.end_headers()
                return
            if q.get("state", [""])[0] != state:
                result["error"] = "Sign-in state mismatch. Try again."
            elif "error" in q:
                err = q["error"][0]
                result["error"] = ("Your Google account's administrator blocked this app." if "admin" in err
                                   else "Sign-in was cancelled." if err == "access_denied" else f"Google said: {err}")
            else:
                result["code"] = q["code"][0]
            ok = "code" in result
            page = _PAGE.format(title="Calendar connected" if ok else "Not connected",
                                msg="Vox can now read your meetings." if ok else result.get("error", ""))
            data = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            done.set()

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    params = {
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent",
    }
    webbrowser.open(AUTH_URL + "?" + urllib.parse.urlencode(params))
    finished = done.wait(timeout)
    srv.shutdown()
    if not finished:
        return {"ok": False, "error": "Timed out waiting for Google sign-in."}
    if "code" not in result:
        return {"ok": False, "error": result.get("error", "Sign-in failed.")}
    r = requests.post(TOKEN_URL, data={
        "code": result["code"], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier,
    }, timeout=20)
    if r.status_code != 200:
        return {"ok": False, "error": f"Token exchange failed: {r.text[:200]}"}
    tok = r.json()
    email = _email_from_id_token(tok.get("id_token", ""))
    save = {"refresh_token": tok.get("refresh_token"), "access_token": tok.get("access_token"),
            "expires": time.time() + tok.get("expires_in", 3600) - 60, "email": email}
    _save_token(save)
    try:
        os.remove(os.path.join(core.data_dir(), "calendar.json"))
    except OSError:
        pass
    log.info("google calendar connected")   # never the account address: the log holds no personal data
    return {"ok": True, "email": email}


def _email_from_id_token(idt):
    try:
        payload = idt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""


def _access_token():
    tok = _load_token()
    if not tok.get("refresh_token"):   # no file, a damaged one, or tokens of another Windows user or PC
        raise RuntimeError("Google sign-in expired. Connect again in Vox > Notes.")
    if tok.get("access_token") and time.time() < tok.get("expires", 0):
        return tok["access_token"]
    client_id, client_secret = _client()
    r = requests.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret,
                                       "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError("Google sign-in expired. Connect again in Vox > Notes.")
    j = r.json()
    tok["access_token"] = j["access_token"]
    tok["expires"] = time.time() + j.get("expires_in", 3600) - 60
    _save_token(tok)
    return tok["access_token"]


# --------------------------------------------------------------------- events
def _ts(d):
    if not d or "dateTime" not in d:
        return None                     # all-day events are skipped
    return datetime.fromisoformat(d["dateTime"].replace("Z", "+00:00")).timestamp()


def events():
    """Events from 12 h ago to 7 days ahead, in the same shape as vcalendar.parse()."""
    now = datetime.now(timezone.utc)
    params = {"timeMin": (now - timedelta(hours=12)).isoformat(), "timeMax": (now + timedelta(days=7)).isoformat(),
              "singleEvents": "true", "orderBy": "startTime", "maxResults": "150"}
    r = requests.get(EVENTS_URL, params=params, headers={"Authorization": f"Bearer {_access_token()}"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Google Calendar {r.status_code}: {r.text[:150]}")
    return _parse_items(r.json().get("items", []))


MAX_ATTENDEES = 30   # calendar text goes into prompts and file names: bounded and on one line (see core.one_line)


def _parse_items(items):
    """Google event items in the shape of vcalendar.parse(). An event you declined is dropped; `my_status` is your
    answer ("accepted" when it is your own event or Google gives no answer)."""
    out = []
    for ev in items:
        s, e = _ts(ev.get("start")), _ts(ev.get("end"))
        if s is None or ev.get("status") == "cancelled":
            continue
        mine = next((a for a in ev.get("attendees", []) if a.get("self")), None)
        my_status = "accepted" if mine is None or (ev.get("organizer") or {}).get("self") else mine.get("responseStatus", "accepted")
        if my_status == "declined":
            continue
        people = []
        for a in ev.get("attendees", []):
            if a.get("self") or a.get("resource") or a.get("responseStatus") == "declined":
                continue
            n = core.one_line(vcalendar.person_name(a.get("displayName"), a.get("email", "")), 80)
            if n and n not in people and len(people) < MAX_ATTENDEES:
                people.append(n)
        link = ev.get("hangoutLink", "")
        for ep in (ev.get("conferenceData") or {}).get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                link = link or ep.get("uri", "")
        org = ev.get("organizer", {})
        out.append({"uid": ev.get("id", "") + "|" + str(s), "title": core.one_line(ev.get("summary", "(no title)"), 120),
                    "start": s, "end": e or s, "attendees": people, "my_status": my_status,
                    "organizer": "" if org.get("self") else core.one_line(vcalendar.person_name(org.get("displayName"), org.get("email", "")), 80),
                    "link": link})
    return out
