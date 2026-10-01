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
import http.client
import ipaddress
import json
import math
import os
import platform
import re
import secrets
import select
import signal
import socket
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RELAY_VERSION = "0.2"
MAX_CLOCK_AHEAD = 86400    # seconds a note time may be ahead of the relay clock
MAX_BODY = 1_000_000        # bytes accepted in one request
MAX_TEXT = 100_000          # characters kept per text field
MAX_TAGS = 20
MAX_PROFILE = 64_000        # bytes of profile JSON
MAX_URL = 2048              # characters in an upstream server address
MAX_KEY = 1024              # characters in an upstream API key
UPSTREAM_ROLES = ("stt", "llm")
MAX_DRAIN = 5 * MAX_BODY    # bytes of a refused upload that are read and dropped so the client can still read our answer
DRAIN_IDLE = 1.0            # seconds a refused upload may go quiet while being dropped before we answer anyway
PROXY_SLOTS = 4             # upstream requests in flight at once; the next one is answered 429 at once
# Seconds for a whole upstream exchange, by kind of call. They must cover the longest wait of any client that uses the
# call: stt 180 = the meeting recorder's pieces (windows/vox_core.py transcribe_segments; dictation waits 60 s);
# llm 240 = meeting notes (windows/meeting.py; dictation cleanup waits 60 s); models 15 = the model lists (15 s).
# A client that gives up earlier does not keep its slot for the rest of this time: see CLIENT_POLL.
PROXY_TIMEOUT = {"stt": 180, "llm": 240, "models": 15}
CLIENT_POLL = 0.25          # seconds between looks at the client's connection while an upstream answer is awaited
MAX_PROXY_BODY = {"stt": 25_000_000, "llm": 1_000_000, "models": 0}   # bytes a client may send, by kind of call
MAX_PROXY_REPLY = 8_000_000   # bytes of an upstream answer that are passed on; more is a 502
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


class LengthRequired(BadRequest):
    """Chunked upload: answered 411 (the other framing problems are a plain 400)."""


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


def _time_field(value, name):
    """A note time in seconds. More than a day ahead of the relay is refused (that is a clock in milliseconds or a
    broken one: such a note would win over every later edit and delete)."""
    now = time.time()
    f = _number(now if value is None else value, name)
    if f > now + MAX_CLOCK_AHEAD:
        raise BadRequest(f"{name} is too far in the future (seconds since 1970, not milliseconds)")
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
        "created_at": _time_field(raw.get("created_at"), "created_at"),
        "updated_at": _time_field(raw.get("updated_at"), "updated_at"),
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
            return self._store_note(con, note)

    def _store_note(self, con, note):
        """The body of `upsert_note` on an open connection (the caller holds the lock and the transaction)."""
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
        """Turns a note into a delete marker (content removed). Returns (marker, applied), or None if unknown."""
        with self._lock, contextlib.closing(self._connect()) as con, con:    # read and write in one step: no one slips in between
            r = con.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone()
            if r is None:
                return None
            old = self._row(r)
            # later than the stored time too, or a note stamped ahead of this clock (a fast phone) would "win" over its own delete
            stamp = max(float(updated_at or 0), time.time(), old["updated_at"] + 0.001)
            return self._store_note(con, clean_note(dict(old, deleted=True, updated_at=stamp)))

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


class DataDirError(Exception):
    """The data folder (or relay.json) is not safe to use: the relay refuses to start rather than adopt it."""


def _check_private(path, what):
    """POSIX only (Windows has no such modes; see the known limit in the docs): `path` must belong to the user running
    the relay and must not be writable by anyone else, otherwise someone else could have planted the token in it or
    could swap files under us. A folder that is merely readable by others is closed to 0700 (a file to 0600).
    A symlink is followed on purpose (systemd's DynamicUser makes /var/lib/<name> one): what counts is where it leads."""
    if os.name != "posix":
        return
    st = os.stat(path)
    if st.st_uid != os.getuid():
        raise DataDirError(f"{what} {path} belongs to another user. Use a folder of your own (for example --data-dir \"$(mktemp -d)\").")
    if st.st_mode & 0o022:
        raise DataDirError(f"{what} {path} can be changed by other users. Run: chmod go-w {path}  (or pick another folder).")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)


def _write_config(path, cfg):
    tmp = path + ".tmp"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)      # whatever is there (a leftover, or a symlink someone planted) is not written through
    # O_EXCL and O_NOFOLLOW: never open an existing file or follow a link; private from the first byte on Linux and macOS
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def load_config(data_dir, port=None, owner=None):
    """Reads relay.json in `data_dir`, creating it (with a new random token) on first use.
    Raises DataDirError when the folder or the file is not the running user's own (POSIX)."""
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    _check_private(data_dir, "The data folder")
    path = os.path.join(data_dir, "relay.json")
    if os.path.exists(path):
        _check_private(path, "The file")
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


# --------------------------------------------------------- upstream servers
# Proxy mode: for each role ("stt" speech to text, "llm" text cleanup) the relay keeps the address of an
# OpenAI-compatible server and the key for it, so the apps never need the key. The key is write-only: it lives in
# relay.json, only the relay uses it, and no endpoint returns it.
_KEY_RE = re.compile(r"[\x21-\x7e]*")      # printable ASCII without spaces: safe in an Authorization header
_HOST_RE = re.compile(r"[a-z0-9._-]+")


def is_private_host(host):
    """True for hosts where plain http is acceptable: this machine, the home or office LAN and Tailscale.
    The same rule as windows/vox_core.py (the relay cannot import app code)."""
    host = (host or "").strip("[]").lower().rstrip(".")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A name: single-label names, .local/.lan and Tailscale MagicDNS names never leave the private network.
        return "." not in host or host.endswith((".local", ".lan", ".ts.net"))
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10")


def upstream_problem(url):
    """Why `url` cannot be an upstream server address, or None when it is fine.

    Only http and https, a host, no user name or password, no query and no fragment (the relay adds fixed paths to the
    address), and plain http only for private hosts because the key and the audio travel to it. The messages never
    repeat the address, which might hold a secret."""
    if not isinstance(url, str) or not url:
        return "The server address is empty."
    if len(url) > MAX_URL or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
        return "The server address is too long or has spaces or unusual characters."
    try:
        u = urlparse(url)
        host = u.hostname
        u.port   # raises ValueError when the port is not a number in range
    except ValueError:
        return "The server address is not valid."
    if u.scheme not in ("http", "https") or not host:
        return "The server address must start with http:// or https:// and name a host."
    if "@" in u.netloc:
        return "The server address must not contain a user name or password. The key goes in the key field."
    if "#" in url or "?" in url:
        return "The server address must not contain a query (?) or a fragment (#)."
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOST_RE.fullmatch(host):
            return "The server address has an invalid host name."
    if u.scheme == "http" and not is_private_host(host):
        return "Plain http is only allowed for this PC, your local network or Tailscale. Use https:// for other servers."
    return None


def _clean_url(text):
    return text.strip().rstrip("/")


def upstream_settings(block):
    """The `upstream` value of relay.json as {"stt": {"base_url", "api_key"}, "llm": {...}}: both roles always there,
    every value a string (anything else, for example from a hand edit, reads as not set)."""
    block = block if isinstance(block, dict) else {}
    out = {}
    for role in UPSTREAM_ROLES:
        entry = block.get(role) if isinstance(block.get(role), dict) else {}
        out[role] = {k: entry[k] if isinstance(entry.get(k), str) else "" for k in ("base_url", "api_key")}
    return out


# The four routes that forward to an upstream server: (method, exact path) -> (role, suffix added to the role's address, kind).
# The kind picks the timeout and the body limit. Nothing from a request reaches the upstream address except this choice.
PROXY_ROUTES = {
    ("POST", "/proxy/stt/audio/transcriptions"): ("stt", "/audio/transcriptions", "stt"),
    ("GET", "/proxy/stt/models"): ("stt", "/models", "models"),
    ("POST", "/proxy/llm/chat/completions"): ("llm", "/chat/completions", "llm"),
    ("GET", "/proxy/llm/models"): ("llm", "/models", "models"),
}
ROLE_NAMES = {"stt": "speech to text", "llm": "text cleanup"}


class ClientGone(Exception):
    """The client that asked hung up while its upstream exchange was running; the exchange was abandoned."""


def client_gone(sock):
    """True when the peer of `sock` has closed its end (or reset the connection). Call it only after the whole request
    has been read: from then on a well-behaved client sends nothing, so a readable socket is an end of file. (A client
    that half-closes its sending side right after the request counts as gone too; http clients such as requests,
    HttpURLConnection and browsers keep the connection fully open while they wait for the answer.)"""
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        return sock.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


class UpstreamError(Exception):
    """An upstream server could not be used. `message` is fixed text that is safe to show to the client."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _plain_header(value, limit=256):
    """A header value that is safe to copy to another request or response: printable ASCII only (no control
    characters, so no line breaks or folding), not empty, not long. None when it is not."""
    if not isinstance(value, str) or len(value) > limit or not value.strip() or any(ord(c) < 32 or ord(c) > 126 for c in value):
        return None
    return value.strip()


def _scrub(data, key):
    """`data` (bytes) with the upstream key hidden, for a server that repeats the credentials it was sent in an error
    message. A key shorter than 8 characters is left alone: it cannot be told from ordinary words."""
    if len(key) < 8:
        return data
    for form in (key, json.dumps(key)[1:-1]):
        data = data.replace(form.encode("utf-8"), b"***")
    return data


def _open_socket(host, port, deadline):
    """A connected TCP socket to host:port, like socket.create_connection, except that the name lookup and all the
    connection attempts share one deadline (a time.monotonic() value) instead of each getting a whole timeout. The
    socket comes back with what is left as its timeout, which also bounds an https handshake. Raises OSError."""
    found = []

    def look_up():
        try:
            found.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except (OSError, ValueError) as e:     # (a host name the idna codec refuses is a UnicodeError, a ValueError)
            found.append(e)
    # getaddrinfo cannot be given a timeout: a helper thread lets us stop waiting for it (it ends when the resolver does)
    t = threading.Thread(target=look_up, daemon=True)
    t.start()
    t.join(max(deadline - time.monotonic(), 0))
    if not found:
        raise OSError("name lookup timed out")
    if isinstance(found[0], Exception):
        raise found[0]
    err = OSError("no address to connect to")
    for family, kind, proto, _name, address in found[0]:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        s = socket.socket(family, kind, proto)
        try:
            s.settimeout(left)
            s.connect(address)
            s.settimeout(max(deadline - time.monotonic(), 0.001))
            return s
        except OSError as e:
            err = e
            s.close()
    raise err


def forward_upstream(base_url, api_key, suffix, method, body, content_type, accept, timeout, gone=None, on_abandon=None):
    """One request to an upstream server. Returns (status, content type, retry-after or None, body bytes).

    The address is `base_url` plus `suffix`, nothing else. The only headers sent are the ones built here (the client's
    Authorization is never among them); the key is sent as a bearer token when there is one. `timeout` is the total for
    the whole exchange, not per read: name lookup and connecting share it (`_open_socket`), and once connected a timer
    shuts the socket down when the time is up, because http.client has loops of its own (any number of "100 Continue"
    answers, header bytes that trickle in, trailer lines without end) in which every single read is quick. The answer
    is capped at MAX_PROXY_REPLY bytes, a redirect is not followed, and the key is hidden if the server echoes it.
    `gone` is an optional function that says whether the client has hung up; it is asked every CLIENT_POLL seconds and,
    when it says yes, the exchange is shut down and ClientGone is raised, so an abandoned request does not hold on to
    its slot (and the upstream's attention) for the rest of the timeout. `on_abandon` is called (from the watching
    thread) at that moment, because on some systems (Windows) shutting a socket down does not wake a read that is
    blocked on it in another thread: the caller can give up what the exchange holds (the proxy slot) without waiting
    for that read to end. On Linux the shutdown does wake it, and the exchange ends at once.
    Raises UpstreamError for anything that goes wrong; its text never holds the address or the key."""
    u = urlparse(base_url)
    https = u.scheme == "https"
    headers = {"User-Agent": "vox-relay"}
    if content_type:
        headers["Content-Type"] = content_type
    if accept:
        headers["Accept"] = accept
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    if method == "POST":
        headers["Content-Length"] = str(len(body))
    deadline = time.monotonic() + timeout
    conn = (http.client.HTTPSConnection if https else http.client.HTTPConnection)(u.hostname, u.port or (443 if https else 80), timeout=timeout)
    # http.client calls this (an instance attribute, set here) to open the TCP connection; https still wraps it its own way
    conn._create_connection = lambda address, *_ignored: _open_socket(address[0], address[1], deadline)
    expired = threading.Event()
    abandoned = threading.Event()
    watchdog = None
    watcher_stop = threading.Event()
    box = []     # the upstream socket, once there is one (the watcher may need it from another thread)

    def watch():
        while not watcher_stop.wait(CLIENT_POLL):
            if gone():
                abandoned.set()
                if box:
                    try:
                        socket.socket.shutdown(box[0], socket.SHUT_RDWR)
                    except (OSError, ValueError):
                        pass
                if on_abandon is not None:
                    on_abandon()
                return
    if gone is not None:
        threading.Thread(target=watch, daemon=True).start()
    try:
        conn.connect()
        sock = conn.sock     # kept now: http.client lets go of it when the server says it will close the connection
        box.append(sock)
        if abandoned.is_set():
            raise ClientGone()

        def time_up():
            expired.set()
            try:     # shutdown wakes a blocked read or write at once (close would not, on Linux); called on the base class
                socket.socket.shutdown(sock, socket.SHUT_RDWR)    # so that an ssl socket is not touched from this thread
            except (OSError, ValueError):
                pass
        watchdog = threading.Timer(max(deadline - time.monotonic(), 0), time_up)
        watchdog.daemon = True
        watchdog.start()

        def budget():
            left = deadline - time.monotonic()
            if left <= 0:
                raise UpstreamError("upstream unreachable")
            if sock is not None:
                sock.settimeout(left)

        conn.request(method, u.path.rstrip("/") + suffix, body=body if method == "POST" else None, headers=headers)
        budget()
        resp = conn.getresponse()
        status = resp.status
        if 300 <= status < 400:
            raise UpstreamError("upstream answered with a redirect")
        if status < 200:
            raise UpstreamError("upstream unreachable")
        declared = resp.getheader("Content-Length") or ""
        if re.fullmatch(r"[0-9]{1,12}", declared.strip()) and int(declared) > MAX_PROXY_REPLY:
            raise UpstreamError("upstream reply too large")
        chunks, total = [], 0
        while True:
            budget()
            chunk = resp.read1(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_PROXY_REPLY:
                raise UpstreamError("upstream reply too large")
            chunks.append(chunk)
        if resp.length or expired.is_set():     # it promised more bytes than it sent, or the time ran out and the
            raise UpstreamError("upstream unreachable")     # cut-off looked like a normal end (as it does after trailers)
        return (status, _plain_header(resp.getheader("Content-Type")) or "application/json",
                _plain_header(resp.getheader("Retry-After"), 64), _scrub(b"".join(chunks), api_key))
    except UpstreamError:
        if abandoned.is_set():
            raise ClientGone() from None
        raise
    except ClientGone:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        if abandoned.is_set():
            raise ClientGone() from None
        raise UpstreamError("upstream unreachable") from None
    finally:
        watcher_stop.set()
        if watchdog is not None:
            watchdog.cancel()
        conn.close()


# ------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    server_version = "VoxRelay"
    timeout = 30   # seconds a client may stall before its connection is dropped

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
        n, problem = self._body_length()     # strict: "-1", "+5" or a repeated Content-Length must not reach read()
        if problem:
            self.close_connection = True     # the end of the body is unknown, so this connection cannot be reused
            raise (LengthRequired if problem[0] == 411 else BadRequest)(problem[1])
        n = n or 0
        if n > MAX_BODY:
            self._drain(n)
            raise BodyTooLarge()
        try:
            return json.loads(self.rfile.read(n) or b"null")
        except (ValueError, UnicodeDecodeError):
            raise BadRequest("the body is not valid JSON")

    def _drain(self, n):
        """Reads and drops what a client sends of a body we are not going to use (up to MAX_DRAIN bytes, and only while
        it keeps coming), so it can still read our answer instead of hitting a broken pipe. Makes this the last request
        on the connection, because the rest of the body cannot be skipped."""
        self.close_connection = True
        remaining = min(n, MAX_DRAIN)
        try:
            self.connection.settimeout(DRAIN_IDLE)
            while remaining > 0:
                chunk = self.rfile.read1(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:     # it went quiet or hung up: answer anyway
            pass
        finally:
            with contextlib.suppress(OSError):
                self.connection.settimeout(self.timeout)

    def _page(self):
        nonce = secrets.token_urlsafe(16)
        html = UI_HTML.replace("__NONCE__", nonce).replace("__VERSION__", RELAY_VERSION).encode("utf-8")
        csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; "
               "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self._send(200, html, "text/html; charset=utf-8",
                   {"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})

    def _route(self, method):
        self._status = 0
        try:
            u = urlparse(self.path)
        except ValueError:      # for example an absolute-form target with a broken host
            return self._send(400, {"error": "bad request target"})
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
        except (ConnectionError, socket.timeout):   # the client went away while we were reading from it or answering it
            pass
        finally:
            self.server.record(method, ID_IN_PATH.sub("{id}", u.path), self._status, device)

    def _dispatch(self, method, u, parts, device):
        store = self.server.store
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if parts and parts[0] == "proxy":
                return self._proxy(method)
            if parts and parts[0] == "admin":
                return self._admin(method, parts, q)
            if parts == ["health"] and method == "GET":
                return self._send(200, dict(store.stats(), ok=True, version=RELAY_VERSION))
            if parts == ["devices"] and method == "GET":
                return self._send(200, {"devices": [{k: d[k] for k in ("name", "first_seen", "last_seen", "requests", "login")}
                                                    for d in store.devices()]})   # newest first; no events, no notes
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
                    if not marker:
                        return self._send(404, {"error": "no such note"})
                    return self._send(200, {"note": marker[0], "applied": marker[1]})
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
        except LengthRequired as e:
            return self._send(411, {"error": str(e)})
        except (BadRequest, ValueError, KeyError) as e:
            return self._send(400, {"error": str(e) or "bad request"})
        except Exception:
            return self._send(500, {"error": "the relay hit an error"})

    # ------------------------------------------------------------------ proxy
    def _body_length(self):
        """What the client declared about its body, as (size, problem). `size` is None when there is no Content-Length;
        `problem` is (status, message) when the framing cannot be trusted (chunked, repeated or malformed Content-Length)."""
        if self.headers.get("Transfer-Encoding") is not None:
            return None, (411, "send a Content-Length (chunked uploads are not supported)")
        values = self.headers.get_all("Content-Length") or []
        if not values:
            return None, None
        if len(values) > 1 or not re.fullmatch(r"[0-9]{1,12}", values[0].strip()):
            return None, (400, "bad Content-Length")
        return int(values[0]), None

    def _refuse(self, status, payload, n=0):
        """Answers without using the request body: what the client already sent of it is dropped first."""
        if n:
            self._drain(n)
        self._send(status, payload)

    def _proxy(self, method):
        """The four proxy routes: checks, then one request to the role's upstream server, then its answer.
        The order is: route (404), framing (411 or 400), role configured (503), size (413), free slot (429)."""
        srv = self.server
        route = PROXY_ROUTES.get((method, self.path.split("?", 1)[0]))    # exact text of the path: no decoding, no ".." handling
        n, problem = self._body_length()
        if route is None:
            return self._refuse(404, {"error": "unknown request"}, n or 0)
        if problem:
            # The body of a chunked or badly framed request cannot be skipped (its end is unknown), so what comes in is read
            # and dropped for a moment before the answer, as `_drain` does, and the connection ends: otherwise the client
            # gets a reset instead of the 411 that says what to change.
            self._drain(MAX_DRAIN if self.headers.get("Transfer-Encoding") is not None else 0)
            self.close_connection = True
            return self._send(problem[0], {"error": problem[1]})
        if method == "POST" and n is None:
            return self._send(411, {"error": "Content-Length is required"})
        n = n or 0
        role, suffix, kind = route
        if n and not MAX_PROXY_BODY[kind]:      # a body on a GET means nothing
            self._drain(n)
            n = 0
        entry = srv.upstream[role]      # looked up once: a save replaces the whole entry, so address and key always belong together
        base, key = entry["base_url"], entry["api_key"]
        if not base:
            return self._refuse(503, {"error": f"The {ROLE_NAMES[role]} server is not configured on the relay. "
                                               "Set its address on the relay's management page (AI server tab)."}, n)
        if upstream_problem(base) or len(key) > MAX_KEY or not _KEY_RE.fullmatch(key):    # relay.json edited by hand
            return self._refuse(503, {"error": f"The {ROLE_NAMES[role]} server is not configured correctly on the relay. "
                                               "Check its address and key on the management page."}, n)
        if n > MAX_PROXY_BODY[kind]:
            return self._refuse(413, {"error": "request too large"}, n)
        if not srv.proxy_running.acquire(blocking=False):     # exchanges still alive (an abandoned one lives on until its read ends)
            return self._refuse(429, {"error": "busy"}, n)
        if not srv.proxy_slots.acquire(blocking=False):
            srv.proxy_running.release()
            return self._refuse(429, {"error": "busy"}, n)
        once = threading.Lock()     # the slot is given back by whoever comes first: the exchange, or the watcher of a
                                    # client that left (its upstream read may take a while to notice, see forward_upstream)

        def release():
            if once.acquire(False):
                srv.proxy_slots.release()
        try:
            reply = self._exchange(n, base, key, suffix, kind, method, release)
        finally:
            release()
            srv.proxy_running.release()     # only here, when the exchange has really ended (never from the watcher)
        if reply is None:       # the client left: nobody to answer
            self._status = 499
            self.close_connection = True
            return
        self._send(*reply)

    def _exchange(self, n, base, key, suffix, kind, method, on_abandon=None):
        """Reads the request body and makes the upstream call. Returns the arguments for `_send`, or None when the client
        hung up while it waited (the upstream exchange is then dropped at once). Holds a proxy slot."""
        body = b""
        if n:
            try:
                body = self.rfile.read(n)
            except OSError:
                pass
            if len(body) != n:
                return 400, {"error": "the request body was cut short"}
        content_type = _plain_header(self.headers.get("Content-Type")) if method == "POST" else None
        try:
            status, ctype, retry_after, data = forward_upstream(base, key, suffix, method, body, content_type,
                                                                _plain_header(self.headers.get("Accept")), PROXY_TIMEOUT[kind],
                                                                gone=lambda: client_gone(self.connection), on_abandon=on_abandon)
        except ClientGone:
            return None
        except UpstreamError as e:
            return 502, {"error": {"message": e.message}}
        headers = {"Content-Security-Policy": "default-src 'none'; sandbox"}    # an answer is data, never a page on the relay's origin
        if retry_after:
            headers["Retry-After"] = retry_after
        return status, data, ctype, headers

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
            with srv.config_lock:
                srv.token = rotate_token(srv.data_dir)
            return self._send(200, {"token": srv.token})
        if method == "GET" and what == "upstream":
            return self._send(200, srv.upstream_view())   # addresses and "key set" flags, never a key
        if method == "PUT" and what == "upstream":
            if not srv.data_dir:
                return self._send(409, {"error": "this relay was started without a data folder"})
            body = self._json_body()
            if not isinstance(body, dict):
                raise BadRequest("the body must be a JSON object")
            if "base_url" not in body:
                raise BadRequest('base_url is required (use "" to clear the address)')
            return self._send(200, srv.set_upstream(body.get("role"), body["base_url"], body.get("api_key")))
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

    def __init__(self, addr, store, token, owner="", data_dir=None, upstream=None):
        super().__init__(addr, Handler)
        self.store, self.token, self.owner, self.data_dir = store, token, owner, data_dir
        self.upstream = upstream_settings(upstream)   # replaced as a whole, never changed in place
        self.config_lock = threading.Lock()           # one writer at a time for relay.json
        self.proxy_running = threading.BoundedSemaphore(2 * PROXY_SLOTS)   # exchanges alive at all, abandoned ones included
        self.proxy_slots = threading.BoundedSemaphore(PROXY_SLOTS)   # upstream exchanges in flight (and their bodies in memory)
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

    def upstream_view(self):
        """Each role's address and whether a key is stored: what the management page and GET /admin/upstream show."""
        return {role: {"base_url": s["base_url"], "key_set": bool(s["api_key"])} for role, s in self.upstream.items()}

    def set_upstream(self, role, base_url, api_key=None):
        """Saves one role's server address and key in relay.json and returns the new view.

        `api_key` None keeps the stored key, but only while the address is unchanged: a key belongs to the address it was
        saved for, so moving a role to another server drops it unless a new key comes with the move. "" clears it.
        `base_url` "" clears the address. Raises BadRequest; its message never repeats the address or the key."""
        if not isinstance(role, str) or role not in UPSTREAM_ROLES:
            raise BadRequest('role must be "stt" or "llm"')
        if not isinstance(base_url, str):
            raise BadRequest("base_url must be text")
        base_url = _clean_url(base_url)
        if base_url:
            problem = upstream_problem(base_url)
            if problem:
                raise BadRequest(problem)
        if api_key is not None:
            if not isinstance(api_key, str):
                raise BadRequest("api_key must be text")
            api_key = api_key.strip()
            if len(api_key) > MAX_KEY or not _KEY_RE.fullmatch(api_key):
                raise BadRequest("The key is too long or has spaces or unusual characters.")
        with self.config_lock:
            cfg = load_config(self.data_dir)   # the file is the truth: keeps the token and every key this code does not know
            block = dict(cfg["upstream"]) if isinstance(cfg.get("upstream"), dict) else {}
            entry = dict(block[role]) if isinstance(block.get(role), dict) else {}
            old = upstream_settings(block)[role]
            if api_key is None:
                api_key = old["api_key"] if _clean_url(old["base_url"]) == base_url else ""
            entry.update(base_url=base_url, api_key=api_key)
            block[role] = entry
            cfg["upstream"] = block
            _write_config(os.path.join(self.data_dir, "relay.json"), cfg)
            self.upstream = upstream_settings(block)
        return self.upstream_view()

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
    return RelayServer(("127.0.0.1", listen), store, cfg["token"], cfg.get("owner", ""), data_dir=data_dir, upstream=cfg.get("upstream"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Vox relay: shares voice notes and a profile between your devices.")
    ap.add_argument("--data-dir", default=default_data_dir(), help="where relay.json and relay.db live")
    ap.add_argument("--port", type=int, default=None, help="port on 127.0.0.1 (default 8765, remembered)")
    ap.add_argument("--owner", default=None, help="only accept this Tailscale login (from `tailscale serve`)")
    ap.add_argument("--show-token", action="store_true", help="print the token clients need, then start")
    args = ap.parse_args(argv)
    try:
        server = make_server(args.data_dir, port=args.port, owner=args.owner)
    except DataDirError as e:
        print("Not starting:", e, file=sys.stderr, flush=True)
        return 1
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
const TABS = [["overview", "Overview"], ["notes", "Notes"], ["activity", "Devices and activity"], ["profile", "Profile"], ["upstream", "AI server (proxy)"], ["tools", "Maintenance"]];

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
  const draw = { overview, notes, activity, profile, upstream, tools }[name];
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

async function upstream() {
  const d = await getJson("/admin/upstream");
  const role = (id, title, what) => {
    const url = h("input", { class: "grow", placeholder: "https://api.example.com/v1", autocomplete: "off", spellcheck: "false", "aria-label": title + ": server address" });
    const key = h("input", { type: "password", class: "grow", autocomplete: "new-password", "aria-label": title + ": API key (write-only)" });
    const state = h("p", {}), msg = h("p", { class: "muted" });
    const paint = (v) => {   // the key field is never filled in: the relay does not send keys back
      url.value = v.base_url; key.value = "";
      key.placeholder = v.key_set ? "A key is saved. Type a new one to replace it." : "API key (leave empty if the server needs none)";
      state.replaceChildren("key set: ", h("strong", { class: v.key_set ? "ok" : "muted" }, v.key_set ? "yes" : "no"));
    };
    const send = async (body, done) => {
      try {
        const r = await api("/admin/upstream", { method: "PUT", body: JSON.stringify(Object.assign({ role: id }, body)) });
        const out = await r.json();
        if (!r.ok) { msg.className = "bad"; msg.textContent = out.error || "Could not save."; return; }
        paint(out[id]); msg.className = "ok"; msg.textContent = done;
      } catch (e) { msg.className = "bad"; msg.textContent = e.message; }
    };
    paint(d[id]);
    return h("div", { class: "box" }, h("h2", {}, title), h("p", { class: "muted" }, what),
      h("div", { class: "row" }, h("label", { class: "muted" }, "Address"), url), state,
      h("div", { class: "row" }, h("label", { class: "muted" }, "Key"), key),
      h("div", { class: "row" },
        h("button", { class: "b", onclick: () => send(key.value ? { base_url: url.value.trim(), api_key: key.value } : { base_url: url.value.trim() }, "Saved.") }, "Save"),
        h("button", { class: "b g", onclick: () => { if (confirm("Remove the address and the key for " + title + "?")) send({ base_url: "", api_key: "" }, "Cleared."); } }, "Clear")),
      msg);
  };
  $("#view").replaceChildren(
    h("div", { class: "box" }, h("h2", {}, "AI server (proxy)"),
      h("p", { class: "muted" }, "The server the relay sends each kind of request to, and the key it uses there. A key is write-only: it is stored on this machine and used by the relay, and it is never shown again, not even here. Changing the address removes the saved key unless you type a new one. Plain http is only accepted for this machine, your local network and Tailscale.")),
    role("stt", "Speech to text", "Turns a recording into text."),
    role("llm", "Text cleanup", "Tidies the text after it has been transcribed."));
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
