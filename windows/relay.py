#!/usr/bin/env python3
"""Vox relay: a small server you run on your own machine so your phone and PC share voice notes and a profile.

It listens on 127.0.0.1 only. To reach it from your other devices, publish it to your own tailnet with
`tailscale serve --bg 8765` (never Funnel). Every request needs the bearer token from relay.json; if `owner`
is set, requests must also carry that Tailscale login in the `Tailscale-User-Login` header (added by
`tailscale serve`). Standard library only, so it runs anywhere Python does.

Protocol and decisions: documentation/14-relay.md and documentation/decisions/0020-relay-design.md.
"""
import argparse
import contextlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_BODY = 1_000_000        # bytes accepted in one request
MAX_TEXT = 100_000          # characters kept per text field
MAX_TAGS = 20
MAX_PROFILE = 64_000        # bytes of profile JSON
ID_RE = re.compile(r"^[0-9a-f]{32}$")
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
"""


class BadRequest(ValueError):
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
    note = {
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
    return note


class RelayStore:
    """SQLite storage for the relay: notes with a server-assigned sequence number, and one profile document."""

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


# ------------------------------------------------------------------- server
def load_config(data_dir, port=None, owner=None):
    """Reads relay.json in `data_dir`, creating it (with a new random token) on first use."""
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "relay.json")
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    changed = False
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(32)
        changed = True
    if port is not None and cfg.get("port") != port:
        cfg["port"], changed = port, True
    if owner is not None and cfg.get("owner") != owner:
        cfg["owner"], changed = owner, True
    cfg.setdefault("port", 8765)
    cfg.setdefault("owner", "")
    if changed or not os.path.exists(path):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    return cfg


class Handler(BaseHTTPRequestHandler):
    server_version = "VoxRelay"

    def log_message(self, *args):   # no access log: paths carry search words and note ids
        pass

    # ------------------------------------------------------------ plumbing
    def _send(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self):
        given = self.headers.get("Authorization", "")
        want = "Bearer " + self.server.token
        if not hmac.compare_digest(given.encode("utf-8"), want.encode("utf-8")):
            self._send(401, {"error": "missing or wrong token"})
            return False
        owner = self.server.owner
        if owner and self.headers.get("Tailscale-User-Login", "") != owner:
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

    def _route(self, method):
        if not self._authorised():
            return
        store = self.server.store
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        try:
            if parts == ["health"] and method == "GET":
                return self._send(200, dict(store.stats(), ok=True))
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

    def do_GET(self):
        self._route("GET")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_POST(self):
        self._route("POST")


class BodyTooLarge(Exception):
    pass


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, store, token, owner=""):
        super().__init__(addr, Handler)
        self.store, self.token, self.owner = store, token, owner


def make_server(data_dir, port=None, owner=None, use_fts=True):
    """A relay bound to 127.0.0.1 only. `port=0` picks a free port (tests)."""
    cfg = load_config(data_dir, port=port if port else None, owner=owner)
    listen = cfg["port"] if port is None else port
    store = RelayStore(os.path.join(data_dir, "relay.db"), use_fts=use_fts)
    return RelayServer(("127.0.0.1", listen), store, cfg["token"], cfg.get("owner", ""))


def default_data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.path.join(base, "VoxRelay")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Vox relay: shares voice notes and a profile between your devices.")
    ap.add_argument("--data-dir", default=default_data_dir(), help="where relay.json and relay.db live")
    ap.add_argument("--port", type=int, default=None, help="port on 127.0.0.1 (default 8765, remembered)")
    ap.add_argument("--owner", default=None, help="only accept this Tailscale login (from `tailscale serve`)")
    ap.add_argument("--show-token", action="store_true", help="print the token clients need, then start")
    args = ap.parse_args(argv)
    server = make_server(args.data_dir, port=args.port, owner=args.owner)
    port = server.server_address[1]
    print(f"Vox relay listening on 127.0.0.1:{port}. Publish it to your tailnet with: tailscale serve --bg {port}")
    if args.show_token:
        print("Token:", server.token)
    else:
        print(f"The token is in {os.path.join(args.data_dir, 'relay.json')} (run with --show-token to print it).")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
