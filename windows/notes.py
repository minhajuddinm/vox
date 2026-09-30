"""Voice notes: a small SQLite store (notes.db in the Vox data folder) with search and filters.

One row per note. Deleting keeps a marker row (deleted = 1, text emptied) so a later sync can tell the other
devices. Search uses SQLite's FTS5 when this build has it, otherwise plain LIKE matching.
Design: documentation/specs/p5-voice-notes-windows.md.
"""
import contextlib
import json
import os
import re
import sqlite3
import time
import uuid

import vox_core as core

SOURCE_NOTE = "voice note"
USE_FTS = True   # tests switch it off to exercise the LIKE fallback

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
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
CREATE INDEX IF NOT EXISTS notes_created ON notes(created_at);
"""


def db_path():
    return os.path.join(core.data_dir(), "notes.db")


def _connect():
    con = sqlite3.connect(db_path(), timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(id UNINDEXED, title, text)")
    except sqlite3.OperationalError:
        pass   # this SQLite has no FTS5: search falls back to LIKE
    return con


def _has_fts(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = 'notes_fts'").fetchone() is not None


def _tags(tags):
    if isinstance(tags, str):
        tags = tags.split(",")
    out = []
    for t in tags or []:
        t = str(t).replace('"', "").strip()
        if t and t not in out:
            out.append(t)
    return out


def _auto_title(text):
    words = (text or "").split()
    return " ".join(words[:7]) + ("..." if len(words) > 7 else "")


def _row(r):
    d = dict(r)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["deleted"] = bool(d["deleted"])
    return d


def _index(con, nid, title, text):
    if _has_fts(con):
        con.execute("DELETE FROM notes_fts WHERE id = ?", (nid,))
        con.execute("INSERT INTO notes_fts (id, title, text) VALUES (?, ?, ?)", (nid, title, text))


def add(text, raw="", secs=0.0, source=SOURCE_NOTE, device="", tags=None, title="", created=None):
    """Saves a note and returns it as a dict."""
    text = (text or "").strip()
    now = time.time()
    note = {"id": uuid.uuid4().hex, "source": source, "title": (title or "").strip() or _auto_title(text),
            "text": text, "raw": raw or "", "created_at": created or now, "updated_at": now,
            "secs": round(float(secs or 0), 1), "device": device, "tags": _tags(tags), "deleted": False}
    with contextlib.closing(_connect()) as con, con:
        con.execute("INSERT INTO notes (id, source, title, text, raw, created_at, updated_at, secs, device, tags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (note["id"], note["source"], note["title"], note["text"], note["raw"], note["created_at"],
                     note["updated_at"], note["secs"], note["device"], json.dumps(note["tags"])))
        _index(con, note["id"], note["title"], note["text"])
    return note


def get(nid):
    with contextlib.closing(_connect()) as con:
        r = con.execute("SELECT * FROM notes WHERE id = ? AND deleted = 0", (nid,)).fetchone()
    return _row(r) if r else None


def update(nid, title=None, text=None, tags=None):
    """Changes a note; returns it, or None when it does not exist."""
    with contextlib.closing(_connect()) as con, con:
        r = con.execute("SELECT * FROM notes WHERE id = ? AND deleted = 0", (nid,)).fetchone()
        if not r:
            return None
        new_title = r["title"] if title is None else title.strip()
        new_text = r["text"] if text is None else text.strip()
        new_tags = r["tags"] if tags is None else json.dumps(_tags(tags))
        con.execute("UPDATE notes SET title = ?, text = ?, tags = ?, updated_at = ? WHERE id = ?",
                    (new_title, new_text, new_tags, time.time(), nid))
        _index(con, nid, new_title, new_text)
        r = con.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone()
    return _row(r)


def delete(nid):
    """Removes a note's content and keeps a marker row. True when a note was deleted."""
    with contextlib.closing(_connect()) as con, con:
        cur = con.execute("UPDATE notes SET deleted = 1, title = '', text = '', raw = '', tags = '[]', updated_at = ? "
                          "WHERE id = ? AND deleted = 0", (time.time(), nid))
        if _has_fts(con):
            con.execute("DELETE FROM notes_fts WHERE id = ?", (nid,))
        return cur.rowcount > 0


def search(query="", source=None, since=None, until=None, tag=None, limit=200):
    """Newest first. `query` words must all appear (as word starts); `since`/`until` are epoch seconds."""
    tokens = re.findall(r"\w+", query or "", re.UNICODE)
    where, args, join = ["n.deleted = 0"], [], ""
    with contextlib.closing(_connect()) as con:
        if tokens and USE_FTS and _has_fts(con):
            join = "JOIN notes_fts f ON f.id = n.id"
            where.append("notes_fts MATCH ?")
            args.append(" ".join(f'"{t}"*' for t in tokens))
        else:
            for t in tokens:
                where.append("(n.title LIKE ? OR n.text LIKE ?)")
                args += [f"%{t}%", f"%{t}%"]
        if source:
            where.append("n.source = ?")
            args.append(source)
        if since is not None:
            where.append("n.created_at >= ?")
            args.append(since)
        if until is not None:
            where.append("n.created_at < ?")
            args.append(until)
        if tag:
            where.append("n.tags LIKE ?")
            args.append('%"' + str(tag).replace('"', "") + '"%')
        rows = con.execute(f"SELECT n.* FROM notes n {join} WHERE {' AND '.join(where)} "
                           "ORDER BY n.created_at DESC LIMIT ?", args + [int(limit)]).fetchall()
    return [_row(r) for r in rows]


def count():
    with contextlib.closing(_connect()) as con:
        return con.execute("SELECT COUNT(*) FROM notes WHERE deleted = 0").fetchone()[0]
