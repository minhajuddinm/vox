#!/usr/bin/env python3
"""Vox relay: a small server you run on your own machine (PC, Raspberry Pi, any Linux box) so your phone and PC
share voice notes and a profile.

It listens on 127.0.0.1 only. To reach it from your other devices, publish it to your own tailnet with
`tailscale serve --bg 8765` (never Funnel). Every data request needs the bearer token from relay.json; if `owner`
is set, requests must also carry that Tailscale login in the `Tailscale-User-Login` header (added by
`tailscale serve`). Open the address in a browser for the management page (it asks for the token).
Standard library only; needs Python 3.9 or newer.

Protocol and decisions: documentation/14-relay.md and documentation/decisions/0020-relay-design.md and 0021.
"""
import argparse
import collections
import contextlib
import hmac
import json
import math
import os
import platform
import re
import secrets
import signal
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RELAY_VERSION = "0.2"
MAX_BODY = 1_000_000        # bytes accepted in one request
MAX_TEXT = 100_000          # characters kept per text field
MAX_TAGS = 20
MAX_PROFILE = 64_000        # bytes of profile JSON
ID_RE = re.compile(r"^[0-9a-f]{32}$")
ID_IN_PATH = re.compile(r"[0-9a-f]{32}")
SECRET_WORDS = ("key", "token", "secret", "password")
NOTE_FIELDS = ("id", "source", "title", "text", "raw", "created_at", "updated_at", "secs", "device", "tags", "deleted")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL DEFAULT 'voice note',
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    raw TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    secs REAL NOT NULL DEFAULT 0,
    device TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    name TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    login TEXT NOT NULL DEFAULT ''
);
"""


class BadRequest(ValueError):
    pass


class BodyTooLarge(Exception):
    pass


def _text(value, limit=MAX_TEXT):
    return ("" if value is None else str(value))[:limit]


def _number(value, name):
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise BadRequest(f"{name} must be a number")
    if not math.isfinite(f):
        raise BadRequest(f"{name} must be a number")
    return f


def clean_note(raw, note_id=None):
    """A note dict checked and trimmed for storage. Raises BadRequest."""
    if not isinstance(raw, dict):
        raise BadRequest("a note must be a JSON object")
    nid = str(note_id or raw.get("id") or "")
    if not ID_RE.match(nid):
        raise BadRequest("id must be 32 lowercase hex characters")
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        raise BadRequest("tags must be a list")
    tags = [t for t in dict.fromkeys(_text(t, 60).strip() for t in tags[:MAX_TAGS]) if t]
    deleted = bool(raw.get("deleted"))
    return {
        "id": nid,
        "source": _text(raw.get("source") or "voice note", 40),
        "title": "" if deleted else _text(raw.get("title"), 300),
        "text": "" if deleted else _text(raw.get("text")),
        "raw": "" if deleted else _text(raw.get("raw")),
        "created_at": _number(raw.get("created_at", time.time()), "created_at"),
        "updated_at": _number(raw.get("updated_at", time.time()), "updated_at"),
        "secs": max(0.0, _number(raw.get("secs", 0), "secs")),
        "device": _text(raw.get("device"), 60),
        "tags": [] if deleted else tags,
        "deleted": deleted,
    }


def mask_secrets(value, key=""):
    """A copy of a JSON value with anything that looks like a secret (key, token, secret, password) hidden."""
    if isinstance(value, dict):
        return {k: mask_secrets(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(v, key) for v in value]
    if isinstance(value, str) and value and any(w in key.lower() for w in SECRET_WORDS):
        return "•" * 8
    return value


class RelayStore:
    """SQLite storage for the relay: notes with a server-assigned sequence number, one profile, known devices."""

    def __init__(self, path, use_fts=True):
        self.path = path
        self.use_fts = use_fts
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with contextlib.closing(self._connect()):
            pass

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(_SCHEMA)
        try:
            con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(id UNINDEXED, title, text)")
        except sqlite3.OperationalError:
            pass
        return con

    @staticmethod
    def _has_fts(con):
        return con.execute("SELECT 1 FROM sqlite_master WHERE name = 'notes_fts'").fetchone() is not None

    @staticmethod
    def _row(r):
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["deleted"] = bool(d["deleted"])
        return d

    # ------------------------------------------------------------------ notes
    def upsert_note(self, raw, note_id=None):
        """Stores a note unless a newer version is already here. Returns (stored_note, applied)."""
        note = clean_note(raw, note_id)
        with self._lock, contextlib.closing(self._connect()) as con, con:
            cur = con.execute("SELECT * FROM notes WHERE id = ?", (note["id"],)).fetchone()
            if cur is not None:
                old = self._row(cur)
                if old["updated_at"] > note["updated_at"] or all(old[k] == note[k] for k in NOTE_FIELDS):
                    return old, False   # an older or identical write: keep what we have, no new sequence number
            con.execute("INSERT OR REPLACE INTO notes (id, source, title, text, raw, created_at, updated_at, secs, device, tags, deleted) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (note["id"], note["source"], note["title"], note["text"], note["raw"], note["created_at"],
                         note["updated_at"], note["secs"], note["device"], json.dumps(note["tags"]), int(note["deleted"])))
            if self._has_fts(con):
                con.execute("DELETE FROM notes_fts WHERE id = ?", (note["id"],))
                if not note["deleted"]:
                    con.execute("INSERT INTO notes_fts (id, title, text) VALUES (?, ?, ?)", (note["id"], note["title"], note["text"]))
            stored = self._row(con.execute("SELECT * FROM notes WHERE id = ?", (note["id"],)).fetchone())
        return stored, True

    def get_note(self, nid):
        with contextlib.closing(self._connect()) as con:
            r = con.execute("SELECT * FROM notes WHERE id = ? AND deleted = 0", (nid,)).fetchone()
        return self._row(r) if r else None

    def delete_note(self, nid, updated_at=None):
        """Turns a note into a delete marker (content removed). Returns the marker, or None if unknown."""
        with contextlib.closing(self._connect()) as con:
            r = con.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone()
        if r is None:
            return None
        marker = dict(self._row(r), deleted=True, updated_at=max(float(updated_at or 0), time.time()))
        return self.upsert_note(marker)[0]

    def changes(self, since=0, limit=200):
        """Notes and delete markers written after sequence number `since`, oldest first."""
        limit = max(1, min(int(limit), 500))
        with contextlib.closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM notes WHERE seq > ? ORDER BY seq LIMIT ?", (int(since), limit)).fetchall()
        notes = [self._row(r) for r in rows]
        return {"notes": notes, "next": notes[-1]["seq"] if notes else int(since), "more": len(notes) == limit}

    def search(self, query="", tag=None, since=None, until=None, limit=200):
        tokens = re.findall(r"\w+", query or "", re.UNICODE)
        where, args, join = ["n.deleted = 0"], [], ""
        with contextlib.closing(self._connect()) as con:
            if tokens and self.use_fts and self._has_fts(con):
                join = "JOIN notes_fts f ON f.id = n.id"
                where.append("notes_fts MATCH ?")
                args.append(" ".join(f'"{t}"*' for t in tokens))
            else:
                for t in tokens:
                    where.append("(n.title LIKE ? OR n.text LIKE ?)")
                    args += [f"%{t}%", f"%{t}%"]
            if tag:
                where.append("n.tags LIKE ?")
                args.append('%"' + str(tag).replace('"', "") + '"%')
            if since is not None:
                where.append("n.created_at >= ?")
                args.append(since)
            if until is not None:
                where.append("n.created_at < ?")
                args.append(until)
            rows = con.execute(f"SELECT n.* FROM notes n {join} WHERE {' AND '.join(where)} ORDER BY n.created_at DESC LIMIT ?",
                               args + [max(1, min(int(limit), 500))]).fetchall()
        return [self._row(r) for r in rows]

    def stats(self):
        with contextlib.closing(self._connect()) as con:
            n = con.execute("SELECT COUNT(*) FROM notes WHERE deleted = 0").fetchone()[0]
            seq = con.execute("SELECT COALESCE(MAX(seq), 0) FROM notes").fetchone()[0]
        return {"notes": n, "seq": seq}

    # ---------------------------------------------------------------- profile
    def get_profile(self):
        with contextlib.closing(self._connect()) as con:
            r = con.execute("SELECT version, data FROM profile WHERE id = 1").fetchone()
        return {"version": r["version"], "data": json.loads(r["data"])} if r else {"version": 0, "data": {}}

    def put_profile(self, data, if_match):
        """Replaces the profile if `if_match` is the current version ("0" or "*" for a first save).
        Returns (ok, profile): on a mismatch ok is False and profile is the current one."""
        if not isinstance(data, dict):
            raise BadRequest("the profile must be a JSON object")
        blob = json.dumps(data, separators=(",", ":"))
        if len(blob.encode("utf-8")) > MAX_PROFILE:
            raise BadRequest("the profile is too large")
        with self._lock, contextlib.closing(self._connect()) as con, con:
            r = con.execute("SELECT version FROM profile WHERE id = 1").fetchone()
            current = r["version"] if r else 0
            ok = str(if_match).strip() == str(current) or (str(if_match).strip() == "*" and current == 0)
            if not ok:
                return False, self.get_profile()
            con.execute("INSERT OR REPLACE INTO profile (id, version, data) VALUES (1, ?, ?)", (current + 1, blob))
        return True, {"version": current + 1, "data": data}

    # ---------------------------------------------------------------- devices
    def touch_device(self, name, login=""):
        now = time.time()
        with self._lock, contextlib.closing(self._connect()) as con, con:
            con.execute("INSERT INTO devices (name, first_seen, last_seen, requests, login) VALUES (?, ?, ?, 1, ?) "
                        "ON CONFLICT(name) DO UPDATE SET last_seen = excluded.last_seen, requests = requests + 1, "
                        "login = excluded.login", (name, now, now, login[:120]))

    def devices(self):
        with contextlib.closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ maintenance
    def details(self):
        """Numbers for the management page."""
        with contextlib.closing(self._connect()) as con:
            markers = con.execute("SELECT COUNT(*) FROM notes WHERE deleted = 1").fetchone()[0]
            last = con.execute("SELECT MAX(updated_at) FROM notes").fetchone()[0]
        size = sum(os.path.getsize(self.path + s) for s in ("", "-wal") if os.path.exists(self.path + s))
        return {"markers": markers, "last_write": last, "db_bytes": size, "profile_version": self.get_profile()["version"]}

    def export_notes(self):
        with contextlib.closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM notes WHERE deleted = 0 ORDER BY created_at").fetchall()
        return [self._row(r) for r in rows]

    def purge_markers(self, days):
        """Forgets delete markers older than `days`. A device that was offline for longer could bring a deleted note back."""
        cutoff = time.time() - max(0.0, float(days)) * 86400
        with self._lock, contextlib.closing(self._connect()) as con, con:
            return con.execute("DELETE FROM notes WHERE deleted = 1 AND updated_at < ?", (cutoff,)).rowcount

    def vacuum(self):
        with self._lock:
            con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            try:
                con.execute("VACUUM")
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                con.close()

    def backup(self, dest):
        """Writes a consistent copy of the database to `dest`."""
        with contextlib.closing(self._connect()) as src, contextlib.closing(sqlite3.connect(dest)) as dst:
            src.backup(dst)


# ------------------------------------------------------------------- config
def default_data_dir():
    """Where relay.json and relay.db live: %APPDATA%\\VoxRelay on Windows, ~/Library/Application Support/VoxRelay on
    macOS, $XDG_DATA_HOME/vox-relay (default ~/.local/share/vox-relay) elsewhere."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "VoxRelay")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/VoxRelay")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "vox-relay")


def _write_config(path, cfg):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # private from the first byte on Linux and macOS
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def load_config(data_dir, port=None, owner=None):
    """Reads relay.json in `data_dir`, creating it (with a new random token) on first use."""
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    path = os.path.join(data_dir, "relay.json")
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    changed = not os.path.exists(path)
    if not cfg.get("token"):
        cfg["token"], changed = secrets.token_urlsafe(32), True
    if port is not None and cfg.get("port") != port:
        cfg["port"], changed = port, True
    if owner is not None and cfg.get("owner") != owner:
        cfg["owner"], changed = owner, True
    cfg.setdefault("port", 8765)
    cfg.setdefault("owner", "")
    if changed:
        _write_config(path, cfg)
    return cfg


def rotate_token(data_dir):
    """Writes a new random token to relay.json and returns it. The old one stops working when the server adopts it."""
    path = os.path.join(data_dir, "relay.json")
    cfg = load_config(data_dir)
    cfg["token"] = secrets.token_urlsafe(32)
    _write_config(path, cfg)
    return cfg["token"]


# ------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    server_version = "VoxRelay"

    def log_message(self, *args):   # no access log: paths carry search words and note ids
        pass

    # ------------------------------------------------------------ plumbing
    def _send(self, status, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self):
        given = self.headers.get("Authorization", "")
        want = "Bearer " + self.server.token
        if not hmac.compare_digest(given.encode("utf-8"), want.encode("utf-8")):
            self.server.auth_failed()
            self._send(401, {"error": "missing or wrong token"})
            return False
        owner = self.server.owner
        if owner and self.headers.get("Tailscale-User-Login", "") != owner:
            self.server.auth_failed()
            self._send(403, {"error": "this relay belongs to another tailnet user"})
            return False
        return True

    def _json_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadRequest("bad Content-Length")
        if n > MAX_BODY:
            raise BodyTooLarge()
        try:
            return json.loads(self.rfile.read(n) or b"null")
        except (ValueError, UnicodeDecodeError):
            raise BadRequest("the body is not valid JSON")

    def _page(self):
        nonce = secrets.token_urlsafe(16)
        html = UI_HTML.replace("__NONCE__", nonce).replace("__VERSION__", RELAY_VERSION).encode("utf-8")
        csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; "
               "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self._send(200, html, "text/html; charset=utf-8",
                   {"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})

    def _route(self, method):
        self._status = 0
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        if method == "GET" and parts in ([], ["ui"]):
            return self._page()   # only the page shell: no data, the token is asked for in the browser
        if method == "GET" and parts == ["favicon.ico"]:
            return self._send(204, b"", "image/x-icon")
        if not self._authorised():
            self.server.record(method, ID_IN_PATH.sub("{id}", u.path), self._status, "")
            return
        device =(self.headers.get("X-Vox-Device") or "").strip()[:60]
        if device:
            try:
                self.server.store.touch_device(device, self.headers.get("Tailscale-User-Login", ""))
            except Exception:
                pass
        try:
            self._dispatch(method, u, parts, device)
        finally:
            self.server.record(method, ID_IN_PATH.sub("{id}", u.path), self._status, device)

    def _dispatch(self, method, u, parts, device):
        store = self.server.store
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if parts and parts[0] == "admin":
                return self._admin(method, parts, q)
            if parts == ["health"] and method == "GET":
                return self._send(200, dict(store.stats(), ok=True, version=RELAY_VERSION))
            if parts == ["changes"] and method == "GET":
                return self._send(200, store.changes(int(q.get("since", 0)), int(q.get("limit", 200))))
            if parts == ["notes"] and method == "GET":
                since = float(q["from"]) if "from" in q else None
                until = float(q["to"]) if "to" in q else None
                return self._send(200, {"notes": store.search(q.get("q", ""), q.get("tag"), since, until, int(q.get("limit", 200)))})
            if len(parts) == 2 and parts[0] == "notes":
                nid = parts[1]
                if not ID_RE.match(nid):
                    return self._send(400, {"error": "bad note id"})
                if method == "GET":
                    note = store.get_note(nid)
                    return self._send(200, note) if note else self._send(404, {"error": "no such note"})
                if method == "PUT":
                    note, applied = store.upsert_note(self._json_body(), nid)
                    return self._send(200, {"note": note, "applied": applied})
                if method == "DELETE":
                    marker = store.delete_note(nid)
                    return self._send(200, {"note": marker}) if marker else self._send(404, {"error": "no such note"})
            if parts == ["profile"]:
                if method == "GET":
                    return self._send(200, store.get_profile())
                if method == "PUT":
                    match = self.headers.get("If-Match")
                    if match is None:
                        return self._send(428, {"error": "send If-Match with the version you last read (0 for the first save)"})
                    ok, profile = store.put_profile(self._json_body(), match.strip('"'))
                    return self._send(200 if ok else 412, profile)
            return self._send(404 if method in ("GET", "PUT", "DELETE") else 405, {"error": "unknown request"})
        except BodyTooLarge:
            return self._send(413, {"error": "request too large"})
        except (BadRequest, ValueError, KeyError) as e:
            return self._send(400, {"error": str(e) or "bad request"})
        except Exception:
            return self._send(500, {"error": "the relay hit an error"})

    def _admin(self, method, parts, q):
        srv, store = self.server, self.server.store
        what = parts[1] if len(parts) == 2 else ""
        if method == "GET" and what == "status":
            return self._send(200, srv.status())
        if method == "GET" and what == "activity":
            return self._send(200, {"events": srv.recent(), "devices": store.devices()})
        if method == "GET" and what == "profile":
            p = store.get_profile()
            return self._send(200, {"version": p["version"], "data": mask_secrets(p["data"])})
        if method == "GET" and what == "export":
            body = json.dumps({"exported_at": time.time(), "notes": store.export_notes()}, indent=1).encode("utf-8")
            return self._send(200, body, "application/json", {"Content-Disposition": 'attachment; filename="vox-notes.json"'})
        if method == "GET" and what == "backup":
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                store.backup(tmp)
                with open(tmp, "rb") as f:
                    data = f.read()
            finally:
                os.unlink(tmp)
            return self._send(200, data, "application/octet-stream", {"Content-Disposition": 'attachment; filename="relay-backup.db"'})
        if method == "POST" and what == "vacuum":
            store.vacuum()
            return self._send(200, {"ok": True, **store.details()})
        if method == "POST" and what == "purge":
            body = self._json_body() or {}
            return self._send(200, {"removed": store.purge_markers(_number(body.get("days", 30), "days"))})
        if method == "POST" and what == "rotate-token":
            if not srv.data_dir:
                return self._send(409, {"error": "this relay was started without a data folder"})
            srv.token = rotate_token(srv.data_dir)
            return self._send(200, {"token": srv.token})
        return self._send(404 if method == "GET" else 405, {"error": "unknown request"})

    def do_GET(self):
        self._route("GET")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_POST(self):
        self._route("POST")


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, store, token, owner="", data_dir=None):
        super().__init__(addr, Handler)
        self.store, self.token, self.owner, self.data_dir = store, token, owner, data_dir
        self.started = time.time()
        self._events = collections.deque(maxlen=100)
        self._counts = {"requests": 0, "errors": 0, "auth_failures": 0, "last_auth_failure": None}
        self._mlock = threading.Lock()

    def record(self, method, route, status, device):
        with self._mlock:
            self._counts["requests"] += 1
            if status >= 400:
                self._counts["errors"] += 1
            self._events.appendleft({"t": time.time(), "method": method, "route": route, "status": status, "device": device})

    def auth_failed(self):
        with self._mlock:
            self._counts["auth_failures"] += 1
            self._counts["last_auth_failure"] = time.time()

    def recent(self):
        with self._mlock:
            return list(self._events)

    def status(self):
        with self._mlock:
            counts = dict(self._counts)
        port = self.server_address[1]
        return {**self.store.stats(), **self.store.details(), "ok": True, "version": RELAY_VERSION, "started": self.started,
                "uptime": time.time() - self.started, "python": platform.python_version(), "platform": platform.platform(),
                "machine": platform.machine(), "port": port, "owner_set": bool(self.owner), "requests": counts,
                "serve_command": f"tailscale serve --bg {port}", "fts": self.store.use_fts}


def make_server(data_dir, port=None, owner=None, use_fts=True):
    """A relay bound to 127.0.0.1 only. `port=0` picks a free port (tests)."""
    cfg = load_config(data_dir, port=port if port else None, owner=owner)
    listen = cfg["port"] if port is None else port
    store = RelayStore(os.path.join(data_dir, "relay.db"), use_fts=use_fts)
    return RelayServer(("127.0.0.1", listen), store, cfg["token"], cfg.get("owner", ""), data_dir=data_dir)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Vox relay: shares voice notes and a profile between your devices.")
    ap.add_argument("--data-dir", default=default_data_dir(), help="where relay.json and relay.db live")
    ap.add_argument("--port", type=int, default=None, help="port on 127.0.0.1 (default 8765, remembered)")
    ap.add_argument("--owner", default=None, help="only accept this Tailscale login (from `tailscale serve`)")
    ap.add_argument("--show-token", action="store_true", help="print the token clients need, then start")
    args = ap.parse_args(argv)
    server = make_server(args.data_dir, port=args.port, owner=args.owner)
    port = server.server_address[1]
    print(f"Vox relay {RELAY_VERSION} listening on 127.0.0.1:{port}. Publish it to your tailnet with: tailscale serve --bg {port}")
    print(f"Management page: open that address in a browser. Data folder: {args.data_dir}", flush=True)
    if args.show_token:
        print("Token:", server.token, flush=True)
    else:
        print(f"The token is in {os.path.join(args.data_dir, 'relay.json')} (run with --show-token to print it).", flush=True)

    def stop(*_):   # systemd sends SIGTERM
        threading.Thread(target=server.shutdown, daemon=True).start()

    with contextlib.suppress(ValueError, AttributeError):
        signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    return 0


UI_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vox relay</title>
<style nonce="__NONCE__">
:root { --bg:#f6f6f4; --panel:#fff; --text:#1c1c1e; --muted:#6b6b70; --line:#e2e2df; --accent:#2f5bea; --bad:#d33a2c; --ok:#1f8f4e; }
@media (prefers-color-scheme: dark) { :root { --bg:#141416; --panel:#1e1e21; --text:#f2f2f4; --muted:#9a9aa1; --line:#2e2e33; --accent:#6d8dff; --bad:#ff6a5c; --ok:#4cc37f; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { display:flex; align-items:center; gap:12px; padding:12px 16px; border-bottom:1px solid var(--line); background:var(--panel); position:sticky; top:0; }
header h1 { font-size:17px; margin:0; flex:1; }
header .pill { font-size:12px; color:var(--muted); }
nav { display:flex; gap:4px; padding:8px 12px; overflow-x:auto; border-bottom:1px solid var(--line); }
nav button { all:unset; cursor:pointer; padding:7px 12px; border-radius:8px; color:var(--muted); font-weight:600; white-space:nowrap; }
nav button.on { background:var(--panel); color:var(--text); box-shadow:0 0 0 1px var(--line); }
main { max-width:960px; margin:0 auto; padding:16px; }
.cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; margin-bottom:16px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
.card .n { font-size:22px; font-weight:700; }
.card .l { color:var(--muted); font-size:12px; }
.box { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:14px; }
h2 { font-size:15px; margin:0 0 8px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
input, select { font:inherit; color:var(--text); background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:8px 10px; max-width:100%; }
button.b { font:inherit; font-weight:600; cursor:pointer; border:0; border-radius:8px; padding:8px 14px; background:var(--accent); color:#fff; }
button.b.g { background:transparent; color:var(--text); border:1px solid var(--line); }
button.b.d { background:var(--bad); }
.row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
.grow { flex:1; min-width:140px; }
.muted { color:var(--muted); }
.ok { color:var(--ok); } .bad { color:var(--bad); }
pre { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px; overflow:auto; font-size:12px; margin:0; }
code { background:var(--bg); border:1px solid var(--line); border-radius:6px; padding:1px 6px; }
.note { border-bottom:1px solid var(--line); padding:10px 0; }
.note:last-child { border-bottom:0; }
.note .t { font-weight:650; }
.note .m { color:var(--muted); font-size:12px; margin:2px 0 4px; }
.note .x { white-space:pre-wrap; max-height:7.5em; overflow:hidden; }
.login { max-width:420px; margin:12vh auto; }
[hidden] { display:none !important; }
</style></head><body>
<div id="login" class="box login" hidden>
  <h2>Vox relay</h2>
  <p class="muted">Enter the relay token (it is in <code>relay.json</code> in the relay's data folder, or run the relay with <code>--show-token</code>).</p>
  <div class="row"><input id="tok" type="password" class="grow" placeholder="Token" autocomplete="off"><button class="b" id="go">Sign in</button></div>
  <label class="muted"><input type="checkbox" id="remember"> Remember on this device</label>
  <p id="lerr" class="bad"></p>
</div>
<div id="app" hidden>
  <header><h1>Vox relay <span class="pill">v__VERSION__</span></h1><span class="pill" id="live"></span><button class="b g" id="out">Sign out</button></header>
  <nav id="tabs"></nav>
  <main id="view"></main>
</div>
<script nonce="__NONCE__">
"use strict";
const $ = (s) => document.querySelector(s);
const h = (tag, attrs, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else if (k === "style") e.style.cssText = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) e.append(c instanceof Node ? c : String(c ?? ""));
  return e;
};
let token = sessionStorage.getItem("vrt") || localStorage.getItem("vrt") || "";
let tab = "overview", timer = null;
const TABS = [["overview", "Overview"], ["notes", "Notes"], ["activity", "Devices and activity"], ["profile", "Profile"], ["tools", "Maintenance"]];

async function api(path, opts) {
  const o = opts || {};
  const r = await fetch(path, { method: o.method || "GET", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" }, body: o.body });
  if (r.status === 401 || r.status === 403) { signOut(); throw new Error("Not allowed"); }
  return r;
}
const getJson = async (p) => (await api(p)).json();
const bytes = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : n > 1024 ? Math.round(n / 1024) + " KB" : n + " B";
const dur = (s) => { s = Math.round(s); const d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + "d " : "") + (hh ? hh + "h " : "") + m + "m"; };
const ago = (t) => { if (!t) return "never"; const s = Math.round(Date.now() / 1000 - t); return s < 5 ? "just now" : s < 90 ? s + " s ago" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 172800 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago"; };
const when = (t) => new Date(t * 1000).toLocaleString();
const card = (n, l) => h("div", { class: "card" }, h("div", { class: "n" }, n), h("div", { class: "l" }, l));

function signOut() { sessionStorage.removeItem("vrt"); localStorage.removeItem("vrt"); token = ""; clearInterval(timer); $("#app").hidden = true; $("#login").hidden = false; }
async function signIn(t, remember) {
  token = t;
  const r = await fetch("/health", { headers: { Authorization: "Bearer " + t } });
  if (!r.ok) { $("#lerr").textContent = r.status === 403 ? "This relay belongs to another Tailscale user." : "That token is not right."; token = ""; return false; }
  (remember ? localStorage : sessionStorage).setItem("vrt", t);
  $("#lerr").textContent = ""; $("#login").hidden = true; $("#app").hidden = false;
  show(tab); return true;
}

function show(name) {
  tab = name; clearInterval(timer);
  $("#tabs").replaceChildren(...TABS.map(([id, label]) => h("button", { class: id === name ? "on" : "", onclick: () => show(id) }, label)));
  const draw = { overview, notes, activity, profile, tools }[name];
  const run = () => draw().catch((e) => { $("#view").replaceChildren(h("div", { class: "box bad" }, "Could not load: " + e.message)); });
  run();
  if (name === "overview" || name === "activity") { timer = setInterval(run, 10000); $("#live").textContent = "refreshes every 10 s"; } else $("#live").textContent = "";
}

async function overview() {
  const s = await getJson("/admin/status");
  const r = s.requests;
  $("#view").replaceChildren(
    h("div", { class: "cards" }, card(s.notes, "notes"), card(s.markers, "delete markers"), card(bytes(s.db_bytes), "database size"), card(s.seq, "sync position"),
      card(dur(s.uptime), "uptime"), card(r.requests, "requests since start"), card(r.errors, "errors"), card(r.auth_failures, "refused (bad token)"), card("v" + s.profile_version, "profile version")),
    h("div", { class: "box" }, h("h2", {}, "Status"),
      h("p", {}, h("span", { class: "ok" }, "Running"), " on ", s.platform, " (", s.machine, "), Python ", s.python, ". Last note written ", ago(s.last_write), "."),
      h("p", { class: "muted" }, "Search: ", s.fts ? "full-text" : "simple matching", ". Owner check: ", s.owner_set ? "on (only your Tailscale login)" : "off (token only)", ".", r.last_auth_failure ? " Last refused request " + ago(r.last_auth_failure) + "." : "")),
    h("div", { class: "box" }, h("h2", {}, "Reach it from your devices"),
      h("p", {}, "The relay listens on this machine only (port ", s.port, "). Publish it to your own tailnet with:"), h("pre", {}, s.serve_command),
      h("p", { class: "muted" }, "Then use the https address shown by ", h("code", {}, "tailscale serve status"), " in the apps. Never use Funnel: it would put the relay on the public internet.")));
}

async function notes() {
  const box = h("div", {}), q = h("input", { class: "grow", placeholder: "Search notes" });
  const load = async () => {
    const d = await getJson("/notes?limit=100&q=" + encodeURIComponent(q.value.trim()));
    box.replaceChildren(...(d.notes.length ? d.notes.map((n) => h("div", { class: "note" },
      h("div", { class: "t" }, n.title || "Untitled"),
      h("div", { class: "m" }, when(n.created_at), " · ", n.device || "unknown device", " · ", n.source, n.tags.length ? " · " + n.tags.join(", ") : ""),
      h("div", { class: "x" }, n.text),
      h("div", { class: "row", style: "margin:6px 0 0" }, h("button", { class: "b g", onclick: async () => { if (confirm("Delete this note on the relay? Devices will remove it when they sync.")) { await api("/notes/" + n.id, { method: "DELETE" }); load(); } } }, "Delete")))) : [h("p", { class: "muted" }, q.value.trim() ? "No notes match." : "No notes yet.")]));
  };
  q.addEventListener("input", () => { clearTimeout(q._t); q._t = setTimeout(load, 250); });
  $("#view").replaceChildren(h("div", { class: "row" }, q, h("button", { class: "b g", onclick: () => download("/admin/export", "vox-notes.json") }, "Export all (JSON)")), h("div", { class: "box" }, box));
  await load();
}

async function activity() {
  const d = await getJson("/admin/activity");
  const tbl = (head, rows) => h("table", {}, h("tr", {}, head.map((x) => h("th", {}, x))), rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))));
  $("#view").replaceChildren(
    h("div", { class: "box" }, h("h2", {}, "Devices"), d.devices.length ? tbl(["Device", "Last seen", "Requests", "Tailscale user"], d.devices.map((x) => [x.name, ago(x.last_seen), x.requests, x.login || "–"]))
      : h("p", { class: "muted" }, "No device has introduced itself yet. Apps send their name in the X-Vox-Device header.")),
    h("div", { class: "box" }, h("h2", {}, "Recent requests (since the relay started)"), d.events.length ? tbl(["Time", "Request", "Result", "Device"], d.events.map((e) => [ago(e.t), e.method + " " + e.route, h("span", { class: e.status >= 400 ? "bad" : "ok" }, e.status), e.device || "–"]))
      : h("p", { class: "muted" }, "Nothing yet.")));
}

async function profile() {
  const p = await getJson("/admin/profile");
  $("#view").replaceChildren(h("div", { class: "box" }, h("h2", {}, "Profile, version " + p.version),
    h("p", { class: "muted" }, "Keys, tokens and passwords are hidden here. The devices get the real values."), h("pre", {}, JSON.stringify(p.data, null, 2))));
}

async function download(path, name) {
  const r = await api(path); const b = await r.blob(); const a = h("a", { href: URL.createObjectURL(b), download: name });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

async function tools() {
  const msg = h("p", { class: "muted" }), days = h("input", { type: "number", value: "30", min: "0", style: "width:90px" });
  const say = (t, bad) => { msg.className = bad ? "bad" : "ok"; msg.textContent = t; };
  const act = (fn) => async () => { try { await fn(); } catch (e) { say(e.message, true); } };
  $("#view").replaceChildren(
    h("div", { class: "box" }, h("h2", {}, "Backup"), h("p", { class: "muted" }, "A consistent copy of the database. On a Raspberry Pi with an SD card, keep one somewhere else."),
      h("button", { class: "b", onclick: act(() => download("/admin/backup", "relay-backup.db")) }, "Download backup")),
    h("div", { class: "box" }, h("h2", {}, "Clean up"),
      h("div", { class: "row" }, h("button", { class: "b g", onclick: act(async () => { const r = await (await api("/admin/vacuum", { method: "POST", body: "{}" })).json(); say("Compacted. Database is now " + bytes(r.db_bytes) + "."); }) }, "Compact database")),
      h("div", { class: "row" }, "Forget delete markers older than ", days, " days ", h("button", { class: "b g", onclick: act(async () => { const r = await (await api("/admin/purge", { method: "POST", body: JSON.stringify({ days: +days.value }) })).json(); say(r.removed + " markers removed."); }) }, "Purge")),
      h("p", { class: "muted" }, "A phone that was offline for longer than that could bring a deleted note back.")),
    h("div", { class: "box" }, h("h2", {}, "Token"), h("p", { class: "muted" }, "Makes a new token and stops the old one working at once. Every device then needs the new token."),
      h("button", { class: "b d", onclick: act(async () => { if (!confirm("Make a new token? All devices will be signed out until you enter it.")) return; const r = await (await api("/admin/rotate-token", { method: "POST", body: "{}" })).json(); token = r.token; (localStorage.getItem("vrt") ? localStorage : sessionStorage).setItem("vrt", token); out.textContent = token; out.hidden = false; say("New token (copy it now, it is not shown again):"); }) }, "Make a new token"),
      h("pre", { id: "newtok", hidden: "" })), msg);
  var out = $("#newtok");
}

$("#go").addEventListener("click", () => signIn($("#tok").value.trim(), $("#remember").checked));
$("#tok").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#go").click(); });
$("#out").addEventListener("click", signOut);
if (token) signIn(token, !!localStorage.getItem("vrt")).then((ok) => { if (!ok) $("#login").hidden = false; }); else $("#login").hidden = false;
</script></body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
