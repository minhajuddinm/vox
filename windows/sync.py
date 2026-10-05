"""Sync voice notes with a relay (relay/relay.py): send what changed here, fetch what changed elsewhere.

Offline-first: the notes always work locally; syncing is best effort and every failure just becomes a message.
Protocol: documentation/14-relay.md. Rules: the newer `updated_at` wins, deletes travel as markers, the relay's
sequence number is the cursor for "what is new".
"""
import json
import logging
import math
import platform
import re
import threading
import time

import requests

import notes
import vox_core as core

log = logging.getLogger("vox.sync")
TIMEOUT = 15          # seconds per request
INTERVAL = 90         # seconds between background syncs
PUSH_BATCH = 100
NOT_A_RELAY = "That address did not answer like a Vox relay."
MAX_NOTES = 1_000_000_000   # the most notes the Test connection answer shows (Android twin: RelayCheck.MAX_NOTES)
VERSION_OK = re.compile(r"[0-9A-Za-z.+_-]{1,20}")   # what a relay version may look like (see relay_check)
_session = requests.Session()


class SyncError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status   # the HTTP status, 0 when there was none (network failure, answer that is not a relay's)

    @property
    def permanent(self):
        """True when sending the same thing again will always fail: a 4xx other than 401, 403 and 429. The relay refuses
        that note itself, so one note must not stop the others (Android twin: RelayApi.RelayError.permanent)."""
        return 400 <= self.status < 500 and self.status not in (401, 403, 429)


def device_name(cfg):
    """The name this PC shows on the relay's page and puts on the notes it records."""
    return ((cfg.get("device_name") or "").strip() or platform.node() or "windows-pc")[:60]


def settings(cfg):
    """(relay address, token, device name) when syncing is on and filled in, otherwise None."""
    if not cfg.get("relay_sync"):
        return None
    url = (cfg.get("relay_url") or "").strip().rstrip("/")
    token = (cfg.get("relay_token") or "").strip()
    if not url or not token:
        return None
    return url, token, device_name(cfg)


def origin_of(url):
    """The relay address as the sync state is tied to it: no trailing slash, scheme and host in lower case."""
    u = (url or "").strip().rstrip("/")
    i = u.find("://")
    if i < 0:
        return u.lower()
    j = u.find("/", i + 3)
    return u.lower() if j < 0 else u[:j].lower() + u[j:]


PROFILE_SKIPPED = "skipped, config unreadable"   # sync_profile's answer while config.json cannot be opened


def follow_relay(url):
    """The sync state (cursor, profile version and snapshot, which notes the relay has) describes one relay. When the
    address changes, start from zero and send everything again, notes and delete markers. An install with no address
    saved yet keeps its state and just records it. The address is saved last, so a run that stops half way repeats this.
    `profile_keys_sent` (this device put keys on the relay) is not reset by a new address: it may be the same relay under
    another spelling, and a flag lost there would leave the keys on it for ever when they are switched off (a different
    relay without keys just clears the flag at its first sync). An install with no address saved yet and the keys switched
    on seeds the flag, so keys already on the relay from before the flag existed are taken off when switched off."""
    origin = origin_of(url)
    saved = notes.get_meta("relay_origin", "")
    if origin == saved:
        return
    if saved:
        notes.set_meta("relay_cursor", 0)
        notes.set_meta("profile_version", 0)
        notes.set_meta("profile_snapshot", "{}")
        notes.mark_all_dirty()
    else:
        keys_on = core.load_config().get("relay_sync_keys")
        if core.config_is_fallback():
            return   # the real setting is unknown: do not record the address, so this runs again next time
        if keys_on:
            notes.set_meta("profile_keys_sent", "1")
    notes.set_meta("relay_origin", origin)


def problem(url, token):
    """Why this relay address or token cannot be used, or ''."""
    err = core.endpoint_error({"base_url": url})
    if err:
        return err
    if not (url or "").strip():
        return "Enter the relay address."
    if not (token or "").strip():
        return "Enter the relay token."
    return ""


def _call(method, url, path, token, device, headers=None, allow=(), **kw):
    """(status, JSON body). Statuses in `allow` are returned; other failures raise SyncError."""
    h = {"Authorization": "Bearer " + token, "X-Vox-Device": _ascii_name(device)}   # a header is latin-1: spelled as Android does
    h.update(headers or {})
    try:
        r = _session.request(method, url + path, headers=h, timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise SyncError("Cannot reach the relay (is Tailscale running?): " + type(e).__name__)
    if r.status_code not in allow:
        if r.status_code == 401:
            raise SyncError("The relay refused the token.", 401)
        if r.status_code == 403:
            raise SyncError("The relay belongs to another Tailscale user.", 403)
        if r.status_code >= 400:
            raise SyncError(f"The relay answered HTTP {r.status_code}.", r.status_code)
    try:
        return r.status_code, r.json()
    except ValueError:
        raise SyncError(NOT_A_RELAY)


def _request(method, url, path, token, device, **kw):
    return _call(method, url, path, token, device, **kw)[1]


def relay_check(status, health, device, failure=""):
    """What the Test connection button reports, from the HTTP status of GET /health (0: no answer) and its parsed JSON
    answer (None: none usable): {"ok", "reachable", "token_ok", "device_name", "relay_version", "notes", "message"}.
    `ok`: a relay answered and took the token. `reachable`: a relay answered at all; a 401 or 403 is a relay's own
    refusal, so it counts, any other failure does not. `token_ok`: the token was accepted; the relay checks it before
    the tailnet owner, so a 403 means the token was right. `relay_version` is kept only as 1 to 20 of letters, digits
    and . + _ - (a relay sends a short string such as "0.2"), `notes` only as a whole number (a fraction is cut, a
    string, boolean or negative number is 0, more than MAX_NOTES is cut to it); both are only read from an answer that is ok. `device_name` is the name
    the asking call sent (it is what the relay lists this device as). `failure` is the caller's words for a failure
    (the message of SyncError). Android twin: RelayCheck.of (golden rows `relaycheck`)."""
    base = {"ok": False, "reachable": False, "token_ok": False, "device_name": device, "relay_version": "", "notes": 0}
    if 200 <= status < 300:
        if not isinstance(health, dict) or health.get("ok") is not True:
            return dict(base, message=NOT_A_RELAY)
        notes_n, version = health.get("notes"), health.get("version")
        notes_n = int(notes_n) if isinstance(notes_n, (int, float)) and not isinstance(notes_n, bool) and math.isfinite(notes_n) else 0
        notes_n = max(0, min(MAX_NOTES, notes_n))
        version = version.strip() if isinstance(version, str) else ""
        return dict(base, ok=True, reachable=True, token_ok=True, notes=notes_n,
                    relay_version=version if VERSION_OK.fullmatch(version) else "",
                    message=f"Connected. The relay holds {notes_n} notes.")
    if status in (401, 403):
        return dict(base, reachable=True, token_ok=status == 403, message=failure)
    return dict(base, message=failure or NOT_A_RELAY)


def test_relay(url, token, device="Vox"):
    """relay_check's answer for this address and token: can they reach a relay? Makes no request when they are unusable."""
    err = problem(url, token)
    if err:
        return relay_check(0, None, device, err)
    try:
        h = _request("GET", url.strip().rstrip("/"), "/health", token.strip(), device)
    except SyncError as e:
        return relay_check(e.status, None, device, str(e))
    return relay_check(200, h, device)


# ------------------------------------------------------------------ the devices list
ACTIVE_SECS = 600        # seen within 10 minutes: "active"
RECENT_SECS = 86400      # seen within a day: "recent", older: "old"
UNKNOWN_DEVICE = "Unknown device"


def fetch_devices(cfg):
    """The devices that have used the relay, newest first, as the relay sends them (GET /devices): a list of
    {"name", "first_seen", "last_seen", "requests", "login"}. Needs only the saved address and token (not the notes
    switch). Raises SyncError. The asking call itself carries this device's name, so a first look lists this device too."""
    url, token = (cfg.get("relay_url") or "").strip().rstrip("/"), (cfg.get("relay_token") or "").strip()
    err = problem(url, token)
    if err:
        raise SyncError(err)
    try:
        body = _request("GET", url, "/devices", token, device_name(cfg))
    except SyncError as e:
        if e.status == 404:
            raise SyncError("This relay is too old to list devices. Update relay.py on it.", 404)
        raise
    devices = body.get("devices") if isinstance(body, dict) else None
    if not isinstance(devices, list) or not all(isinstance(d, dict) for d in devices):
        raise SyncError(NOT_A_RELAY)
    return devices


def _ascii_name(name):
    """A device name as the Android app puts it in the X-Vox-Device header: every UTF-16 unit outside printable ASCII
    becomes "?" (Android twin: RelayClient.headerText), so the relay lists that phone under the "?" spelling."""
    return "".join(c if " " <= c <= "~" else "?" * (2 if ord(c) > 0xFFFF else 1) for c in name)


def ago_text(secs):
    """"just now", "40 s ago", "5 min ago", "3 h ago", "2 days ago" for an age in whole seconds (JS twin: agoText in
    ui-shared/common.js; rounding is half up, in integers, so Python and Java agree to the digit)."""
    if secs < 10:
        return "just now"
    if secs < 90:
        return f"{secs} s ago"
    if secs < 5400:
        return f"{(2 * secs + 60) // 120} min ago"
    if secs < 129600:
        return f"{(2 * secs + 3600) // 7200} h ago"
    return f"{(2 * secs + 86400) // 172800} days ago"


def devices_view(devices, now, my_name):
    """What the Devices card shows, from the relay's list: one row per device, in the relay's order (newest first):
    {"name", "this" (this is the device asking: same name as `my_name`, or the spelling Android sends), "state"
    ("active" seen under 10 minutes ago, "recent" under a day, else "old"), "ago" ("5 min ago", "never" when the relay
    gave no usable time)}. Entries that are not objects are skipped; a missing name shows as "Unknown device"; a time in
    the future counts as now. Android twin: DevicesView.rows (golden rows `devices`)."""
    me = (my_name or "").strip()
    mine = {me, _ascii_name(me)} if me else set()
    rows = []
    for d in devices or []:
        if not isinstance(d, dict):
            continue
        name = d.get("name")
        name = name.strip() if isinstance(name, str) else ""
        seen = d.get("last_seen")
        ok = isinstance(seen, (int, float)) and not isinstance(seen, bool) and math.isfinite(seen) and seen > 0
        age = max(0, int(now - seen)) if ok else None
        rows.append({"name": name or UNKNOWN_DEVICE, "this": bool(name) and name in mine,
                     "state": "old" if age is None or age >= RECENT_SECS else "active" if age < ACTIVE_SECS else "recent",
                     "ago": "never" if age is None else ago_text(age)})
    return rows


def devices_for_ui(cfg, now=None):
    """The bridge's answer for the Devices card, same shape in both apps: {"ok", "error", "devices": rows}. A failure
    gives an empty list and the reason in plain words; nothing is raised."""
    try:
        devices = fetch_devices(cfg)
    except SyncError as e:
        return {"ok": False, "error": str(e), "devices": []}
    except Exception as e:
        log.warning("device list failed: %s", type(e).__name__)
        return {"ok": False, "error": "The device list could not be read.", "devices": []}
    return {"ok": True, "error": "", "devices": devices_view(devices, time.time() if now is None else now, device_name(cfg))}


def wire(n):
    """A local note as the relay stores it."""
    return {k: n[k] for k in ("id", "source", "title", "text", "raw", "created_at", "updated_at", "secs", "device", "tags", "deleted")}


# ------------------------------------------------------------------ the profile
PROFILE_FIELDS = ("user_context", "dictionary", "people", "default_style", "cleanup", "language", "my_cleanup_rules", "snippets")
PROFILE_KEY_FIELDS = ("provider", "base_url", "stt_base_url", "llm_base_url", "stt_model", "llm_model",
                      "llm_reasoning", "api_key", "stt_api_key", "llm_api_key")   # only with relay_sync_keys


def shared_fields(cfg):
    """Settings that follow the user from device to device. Provider settings and API keys only when the user
    switched on `relay_sync_keys`; per-app styles, the hotkey, the microphone and the relay settings never do."""
    return PROFILE_FIELDS + (PROFILE_KEY_FIELDS if cfg.get("relay_sync_keys") else ())


def _blank(v):
    return isinstance(v, (str, list, dict)) and len(v) == 0


def merge3(base, local, remote):
    """Field by field: the side that changed since `base` wins; if both changed differently, the relay's value wins.
    One exception: a field with no base (this device's first sync) whose relay value is blank ("", an empty list or an
    empty map) keeps this device's value when that is not blank, because the blank is only the other device's default."""
    out = {}
    for k in set(base) | set(local) | set(remote):
        b, l, r = base.get(k), local.get(k), remote.get(k)
        if b is None and l is not None and r is not None and _blank(r) and not _blank(l):
            out[k] = l
            continue
        v = l if l == r else r if l == b else l if r == b else r
        if v is not None:
            out[k] = v
    return out


def sync_profile(url, token, device):
    """Two-way sync of the shared settings with the relay's profile document. Returns "", "sent", "received" or
    "both" or PROFILE_SKIPPED (config.json cannot be opened: the defaults are not the user's settings, so nothing is sent or
    written); raises SyncError. The relay refuses a stale write (If-Match), so a race is retried, not lost."""
    received_any = False   # settings written here in any attempt: a retry sees them as local, so remember them
    for _ in range(3):
        cfg = core.load_config()
        if core.config_is_fallback():
            return PROFILE_SKIPPED
        fields = shared_fields(cfg)
        keys_on = bool(cfg.get("relay_sync_keys"))
        _, remote = _call("GET", url, "/profile", token, device)
        version, data = int(remote["version"]), dict(remote["data"])
        base_version = int(notes.get_meta("profile_version", "0") or 0)
        base = json.loads(notes.get_meta("profile_snapshot", "{}") or "{}")
        local = {k: cfg[k] for k in fields if k in cfg}
        remote_shared = {k: v for k, v in data.items() if k in fields}
        merged = local if version in (0, base_version) else merge3(base, local, remote_shared)
        received = {k: v for k, v in merged.items() if cfg.get(k) != v}
        if received:
            def take(live):   # the file as it is now, in one locked step: the window may have saved during the request
                if any(live.get(k) != cfg.get(k) for k in received):
                    return False   # a field we would write was changed here meanwhile: merge again with the new value
                live.update(received)
                return True
            try:
                taken = core.update_config(take)
            except OSError:
                if core.config_is_fallback():
                    return PROFILE_SKIPPED
                raise
            if not taken:
                continue
            received_any = True
        # Keys leave the relay only on this device's own on-to-off switch (it sent keys, now they are off). A device that
        # never sent keys leaves other devices' keys alone, or two devices would undo each other for ever.
        relay_has_keys = any(k in data for k in PROFILE_KEY_FIELDS)
        sent_keys = notes.get_meta("profile_keys_sent", "") == "1"
        stale_keys = not keys_on and sent_keys and relay_has_keys
        if not keys_on and sent_keys and not relay_has_keys:
            notes.set_meta("profile_keys_sent", "")   # someone else took them off already
        if merged == remote_shared and not stale_keys:
            if keys_on:
                notes.set_meta("profile_keys_sent", "1")
            notes.set_meta("profile_version", version)
            notes.set_meta("profile_snapshot", json.dumps(merged))
            return "received" if received_any else ""
        doc = {k: v for k, v in data.items() if k not in PROFILE_KEY_FIELDS or not stale_keys}   # keep fields other devices added, their keys too
        doc.update(merged)
        status, out = _call("PUT", url, "/profile", token, device, headers={"If-Match": str(version)}, allow=(412,), json=doc)
        if status == 412:
            continue   # someone wrote in between: look again
        if keys_on:
            notes.set_meta("profile_keys_sent", "1")
        elif stale_keys:
            notes.set_meta("profile_keys_sent", "")
        notes.set_meta("profile_version", out["version"])
        notes.set_meta("profile_snapshot", json.dumps(merged))
        return "both" if received_any else "sent"
    raise SyncError("The profile keeps changing on the relay; it will be tried again later.")


def sync_once(cfg):
    """Sends local changes, then fetches remote ones. Returns {"pushed", "pulled", "error"}; never raises."""
    s = settings(cfg)
    if not s:
        return {"pushed": 0, "pulled": 0, "error": "Sync is off or not set up."}
    url, token, device = s
    err = problem(url, token)
    if err:
        return {"pushed": 0, "pulled": 0, "error": err}
    pushed = pulled = 0
    refused = []   # what the relay said about each note it refuses for good: those notes are skipped, the rest goes on
    try:
        follow_relay(url)   # inside the try: a database error is a result, never a dead sync thread
        handled = set()   # (id, updated_at) of every version sent or refused in this run, so none is tried twice in a run
        parked = 0        # refused notes: the only handled ones that stay dirty, so the only ones that need room in the batch
        while True:
            batch = [n for n in notes.dirty_notes(PUSH_BATCH + parked) if (n["id"], n["updated_at"]) not in handled][:PUSH_BATCH]
            if not batch:
                break
            for n in batch:
                handled.add((n["id"], n["updated_at"]))
                try:
                    out = _request("PUT", url, "/notes/" + n["id"], token, device, json=wire(n))
                except SyncError as e:
                    if not e.permanent:
                        raise   # the relay, the token or the network is the problem, not this note: stop and try again later
                    refused.append(str(e))
                    parked += 1
                    continue
                stored = out["note"]
                if out["applied"]:
                    notes.mark_synced(n["id"], n["updated_at"], stored["seq"])
                    pushed += 1
                elif notes.apply_remote(stored):   # the relay already has a newer version: take it
                    pulled += 1
                else:                              # the relay has this exact version
                    notes.mark_synced(n["id"], n["updated_at"], stored["seq"])
        cursor = int(notes.get_meta("relay_cursor", "0") or 0)
        while True:
            d = _request("GET", url, f"/changes?since={cursor}&limit=200", token, device)
            for n in d["notes"]:
                if notes.apply_remote(n):
                    pulled += 1
            cursor = d["next"]
            notes.set_meta("relay_cursor", cursor)
            if not d.get("more"):
                break
        profile = sync_profile(url, token, device)
    except SyncError as e:
        return {"pushed": pushed, "pulled": pulled, "error": str(e)}
    except Exception as e:
        log.exception("sync failed")
        return {"pushed": pushed, "pulled": pulled, "error": "Sync failed: " + type(e).__name__}
    error = f"{len(refused)} note{'s' if len(refused) != 1 else ''} could not be sent: {refused[0]}" if refused else ""
    return {"pushed": pushed, "pulled": pulled, "error": error, "profile": profile}


class SyncWorker:
    """Background thread: syncs at start, every INTERVAL seconds, and whenever `trigger()` is called."""

    def __init__(self, get_cfg):
        self.get_cfg = get_cfg
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._state = {"enabled": False, "running": False, "last_run": None, "last_ok": None, "error": "", "pushed": 0, "pulled": 0}

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="vox-sync", daemon=True)
        self._thread.start()
        self._event.set()   # sync right away

    def stop(self):
        self._stop.set()
        self._event.set()

    def trigger(self):
        self._event.set()

    def status(self):
        return dict(self._state, enabled=settings(self.get_cfg()) is not None)

    def _loop(self):
        while not self._stop.is_set():
            self._event.wait(INTERVAL)
            self._event.clear()
            if self._stop.is_set():
                break
            cfg = self.get_cfg()
            if settings(cfg) is None:
                continue
            self._state["running"] = True
            res = sync_once(cfg)
            now = time.time()
            self._state.update(running=False, last_run=now, error=res["error"], pushed=res["pushed"], pulled=res["pulled"])
            if not res["error"]:
                self._state["last_ok"] = now
