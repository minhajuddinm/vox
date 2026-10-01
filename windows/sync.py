"""Sync voice notes with a relay (relay/relay.py): send what changed here, fetch what changed elsewhere.

Offline-first: the notes always work locally; syncing is best effort and every failure just becomes a message.
Protocol: documentation/14-relay.md. Rules: the newer `updated_at` wins, deletes travel as markers, the relay's
sequence number is the cursor for "what is new".
"""
import json
import logging
import platform
import threading
import time

import requests

import notes
import vox_core as core

log = logging.getLogger("vox.sync")
TIMEOUT = 15          # seconds per request
INTERVAL = 90         # seconds between background syncs
PUSH_BATCH = 100
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


def follow_relay(url):
    """The sync state (cursor, profile version and snapshot, which notes the relay has) describes one relay. When the
    address changes, start from zero and send everything again, notes and delete markers. An install with no address
    saved yet keeps its state and just records it. The address is saved last, so a run that stops half way repeats this."""
    origin = origin_of(url)
    saved = notes.get_meta("relay_origin", "")
    if origin == saved:
        return
    if saved:
        notes.set_meta("relay_cursor", 0)
        notes.set_meta("profile_version", 0)
        notes.set_meta("profile_snapshot", "{}")
        notes.set_meta("profile_keys_sent", "")
        notes.mark_all_dirty()
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
    h = {"Authorization": "Bearer " + token, "X-Vox-Device": device}
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
        raise SyncError("That address did not answer like a Vox relay.")


def _request(method, url, path, token, device, **kw):
    return _call(method, url, path, token, device, **kw)[1]


def test_relay(url, token, device="Vox"):
    """{"ok", "message"}: can this address and token reach a relay?"""
    err = problem(url, token)
    if err:
        return {"ok": False, "message": err}
    try:
        h = _request("GET", url.strip().rstrip("/"), "/health", token.strip(), device)
    except SyncError as e:
        return {"ok": False, "message": str(e)}
    if not isinstance(h, dict) or not h.get("ok"):
        return {"ok": False, "message": "That address did not answer like a Vox relay."}
    return {"ok": True, "message": f"Connected. The relay holds {h.get('notes', 0)} notes."}


def wire(n):
    """A local note as the relay stores it."""
    return {k: n[k] for k in ("id", "source", "title", "text", "raw", "created_at", "updated_at", "secs", "device", "tags", "deleted")}


# ------------------------------------------------------------------ the profile
PROFILE_FIELDS = ("user_context", "dictionary", "people", "default_style", "cleanup", "language")
PROFILE_KEY_FIELDS = ("provider", "base_url", "stt_base_url", "llm_base_url", "stt_model", "llm_model",
                      "llm_reasoning", "api_key", "stt_api_key", "llm_api_key")   # only with relay_sync_keys


def shared_fields(cfg):
    """Settings that follow the user from device to device. Provider settings and API keys only when the user
    switched on `relay_sync_keys`; per-app styles, the hotkey, the microphone and the relay settings never do."""
    return PROFILE_FIELDS + (PROFILE_KEY_FIELDS if cfg.get("relay_sync_keys") else ())


def merge3(base, local, remote):
    """Field by field: the side that changed since `base` wins; if both changed differently, the relay's value wins."""
    out = {}
    for k in set(base) | set(local) | set(remote):
        b, l, r = base.get(k), local.get(k), remote.get(k)
        v = l if l == r else r if l == b else l if r == b else r
        if v is not None:
            out[k] = v
    return out


def sync_profile(url, token, device):
    """Two-way sync of the shared settings with the relay's profile document. Returns "", "sent", "received" or
    "both"; raises SyncError. The relay refuses a stale write (If-Match), so a race is retried, not lost."""
    received_any = False   # settings written here in any attempt: a retry sees them as local, so remember them
    for _ in range(3):
        cfg = core.load_config()
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
            live = core.load_config()
            live.update(received)
            core.save_config(live)
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
    follow_relay(url)
    refused = []   # what the relay said about each note it refuses for good: those notes are skipped, the rest goes on
    try:
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
