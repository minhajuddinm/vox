"""Sync voice notes with a relay (relay/relay.py): send what changed here, fetch what changed elsewhere.

Offline-first: the notes always work locally; syncing is best effort and every failure just becomes a message.
Protocol: documentation/14-relay.md. Rules: the newer `updated_at` wins, deletes travel as markers, the relay's
sequence number is the cursor for "what is new".
"""
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
    pass


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


def _request(method, url, path, token, device, **kw):
    try:
        r = _session.request(method, url + path, headers={"Authorization": "Bearer " + token, "X-Vox-Device": device},
                             timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise SyncError("Cannot reach the relay (is Tailscale running?): " + type(e).__name__)
    if r.status_code == 401:
        raise SyncError("The relay refused the token.")
    if r.status_code == 403:
        raise SyncError("The relay belongs to another Tailscale user.")
    if r.status_code >= 400:
        raise SyncError(f"The relay answered HTTP {r.status_code}.")
    try:
        return r.json()
    except ValueError:
        raise SyncError("That address did not answer like a Vox relay.")


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
    try:
        while True:
            batch = notes.dirty_notes(PUSH_BATCH)
            if not batch:
                break
            for n in batch:
                out = _request("PUT", url, "/notes/" + n["id"], token, device, json=wire(n))
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
    except SyncError as e:
        return {"pushed": pushed, "pulled": pulled, "error": str(e)}
    except Exception as e:
        log.exception("sync failed")
        return {"pushed": pushed, "pulled": pulled, "error": "Sync failed: " + type(e).__name__}
    return {"pushed": pushed, "pulled": pulled, "error": ""}


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
