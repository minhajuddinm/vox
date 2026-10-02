"""The relay: auth, note sync (sequence cursor, last writer wins, delete markers), search, profile versions, limits."""
import http.client
import json
import threading
import time
import uuid

import pytest

import relay


def nid():
    return uuid.uuid4().hex


def note(text="hello world", **kw):
    now = time.time()
    return dict({"id": nid(), "text": text, "title": text[:20], "created_at": now, "updated_at": now, "device": "pc"}, **kw)


@pytest.fixture
def server(tmp_path):
    srv = relay.make_server(str(tmp_path), port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


class Client:
    def __init__(self, srv, token=True):
        self.port, self.token = srv.server_address[1], srv.token if token is True else token

    def call(self, method, path, body=None, headers=None, raw=None):
        h = dict(headers or {})
        if self.token:
            h.setdefault("Authorization", "Bearer " + self.token)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = json.loads(r.read() or b"null")
        c.close()
        return r.status, out


@pytest.fixture
def cl(server):
    return Client(server)


# ------------------------------------------------------------------ auth
def test_every_request_needs_the_token(server):
    assert Client(server, token=None).call("GET", "/health")[0] == 401
    assert Client(server, token="wrong").call("GET", "/changes")[0] == 401
    assert Client(server).call("GET", "/health")[0] == 200


def test_owner_header_is_checked_when_set(tmp_path):
    srv = relay.make_server(str(tmp_path), port=0, owner="me@example.com")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Client(srv)
        assert c.call("GET", "/health")[0] == 403
        assert c.call("GET", "/health", headers={"Tailscale-User-Login": "other@example.com"})[0] == 403
        assert c.call("GET", "/health", headers={"Tailscale-User-Login": "me@example.com"})[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_token_is_created_once_and_reused(tmp_path):
    a = relay.load_config(str(tmp_path))
    b = relay.load_config(str(tmp_path))
    assert a["token"] == b["token"] and len(a["token"]) >= 32 and a["port"] == 8765
    assert relay.load_config(str(tmp_path), port=9000)["port"] == 9000
    assert relay.load_config(str(tmp_path))["token"] == a["token"]


def test_server_listens_on_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


# ----------------------------------------------------------------- notes
def test_put_stores_a_note_and_changes_lists_it_with_a_cursor(cl):
    n = note("first note")
    st, out = cl.call("PUT", "/notes/" + n["id"], n)
    assert st == 200 and out["applied"] and out["note"]["seq"] == 1
    st, ch = cl.call("GET", "/changes?since=0")
    assert [x["id"] for x in ch["notes"]] == [n["id"]] and ch["next"] == 1 and ch["more"] is False
    assert cl.call("GET", "/changes?since=1")[1] == {"notes": [], "next": 1, "more": False}
    assert cl.call("GET", "/notes/" + n["id"])[1]["text"] == "first note"


def test_changes_pages_with_limit(cl):
    ids = [note(f"note {i}") for i in range(5)]
    for n in ids:
        cl.call("PUT", "/notes/" + n["id"], n)
    _, page = cl.call("GET", "/changes?since=0&limit=2")
    assert len(page["notes"]) == 2 and page["more"] is True and page["next"] == 2
    _, rest = cl.call("GET", f"/changes?since={page['next']}&limit=10")
    assert [x["text"] for x in rest["notes"]] == ["note 2", "note 3", "note 4"]


def test_last_writer_wins_by_updated_at_and_repeats_are_harmless(cl):
    n = note("v1")
    cl.call("PUT", "/notes/" + n["id"], n)
    assert cl.call("PUT", "/notes/" + n["id"], n)[1]["applied"] is False       # identical repeat: no new sequence number
    newer = dict(n, text="v2", updated_at=n["updated_at"] + 10)
    st, out = cl.call("PUT", "/notes/" + n["id"], newer)
    assert out["applied"] and out["note"]["seq"] == 2 and out["note"]["text"] == "v2"
    older = dict(n, text="stale", updated_at=n["updated_at"] - 5)
    st, out = cl.call("PUT", "/notes/" + n["id"], older)
    assert out["applied"] is False and out["note"]["text"] == "v2"
    assert cl.call("GET", "/changes?since=0")[1]["next"] == 2


def test_delete_leaves_a_marker_without_content_that_syncs(cl):
    n = note("private thought", tags=["x"])
    cl.call("PUT", "/notes/" + n["id"], n)
    st, out = cl.call("DELETE", "/notes/" + n["id"])
    marker = out["note"]
    assert st == 200 and marker["deleted"] and marker["text"] == "" and marker["title"] == "" and marker["tags"] == []
    assert cl.call("GET", "/notes/" + n["id"])[0] == 404
    assert cl.call("GET", "/notes?q=private")[1]["notes"] == []
    _, ch = cl.call("GET", "/changes?since=1")
    assert ch["notes"][0]["deleted"] is True and ch["notes"][0]["seq"] == 2
    assert cl.call("DELETE", "/notes/" + nid())[0] == 404


def test_a_tombstone_from_a_client_beats_an_older_edit(cl):
    n = note("draft")
    cl.call("PUT", "/notes/" + n["id"], n)
    cl.call("PUT", "/notes/" + n["id"], dict(n, deleted=True, updated_at=n["updated_at"] + 1))
    late_edit = dict(n, text="edited on the phone offline", updated_at=n["updated_at"] + 0.5)
    assert cl.call("PUT", "/notes/" + n["id"], late_edit)[1]["applied"] is False
    assert cl.call("GET", "/notes")[1]["notes"] == []


def test_search_and_filters(cl):
    now = time.time()
    a = note("Meeting with the design team", created_at=now - 40 * 86400, updated_at=now, tags=["work"])
    b = note("Grocery list eggs and coffee", tags=["home"])
    for n in (a, b):
        cl.call("PUT", "/notes/" + n["id"], n)
    ids = lambda path: [x["id"] for x in cl.call("GET", path)[1]["notes"]]  # noqa: E731
    assert ids("/notes?q=desig") == [a["id"]]
    assert ids("/notes?q=design+coffee") == []
    assert ids("/notes?tag=home") == [b["id"]]
    assert ids(f"/notes?from={now - 7 * 86400}") == [b["id"]]
    assert ids(f"/notes?to={now - 7 * 86400}") == [a["id"]]
    assert ids("/notes") == [b["id"], a["id"]]


def test_search_works_without_fts5(tmp_path):
    store = relay.RelayStore(str(tmp_path / "x.db"), use_fts=False)
    n = note("plain text search")
    store.upsert_note(n)
    assert [x["id"] for x in store.search("plain")] == [n["id"]]


def test_bad_input_is_rejected(cl):
    assert cl.call("PUT", "/notes/not-an-id", note())[0] == 400
    assert cl.call("PUT", "/notes/" + nid(), raw=b"{not json")[0] == 400
    assert cl.call("PUT", "/notes/" + nid(), ["a list"])[0] == 400
    assert cl.call("PUT", "/notes/" + nid(), note(created_at="soon"))[0] == 400
    assert cl.call("PUT", "/notes/" + nid(), note(tags="work"))[0] == 400
    assert cl.call("GET", "/changes?since=abc")[0] == 400
    assert cl.call("GET", "/nothing")[0] == 404
    assert cl.call("POST", "/notes", {})[0] == 405


def test_the_id_in_the_path_wins_and_text_is_capped(cl):
    n = note("x" * (relay.MAX_TEXT + 50))
    other = nid()
    st, out = cl.call("PUT", "/notes/" + other, n)
    assert out["note"]["id"] == other and len(out["note"]["text"]) == relay.MAX_TEXT


def test_oversized_bodies_are_refused(cl):
    st, out = cl.call("PUT", "/notes/" + nid(), raw=b"x" * (relay.MAX_BODY + 10))
    assert st == 413


# --------------------------------------------------------------- profile
def test_profile_versions_guard_against_lost_updates(cl):
    assert cl.call("GET", "/profile")[1] == {"version": 0, "data": {}}
    assert cl.call("PUT", "/profile", {"a": 1})[0] == 428                                  # If-Match is required
    st, out = cl.call("PUT", "/profile", {"about": "hello"}, headers={"If-Match": "0"})
    assert st == 200 and out["version"] == 1
    st, out = cl.call("PUT", "/profile", {"about": "stale"}, headers={"If-Match": "0"})
    assert st == 412 and out["version"] == 1 and out["data"] == {"about": "hello"}
    st, out = cl.call("PUT", "/profile", {"about": "second"}, headers={"If-Match": '"1"'})
    assert st == 200 and out["version"] == 2
    assert cl.call("GET", "/profile")[1] == {"version": 2, "data": {"about": "second"}}
    assert cl.call("PUT", "/profile", ["not", "an", "object"], headers={"If-Match": "2"})[0] == 400
    assert cl.call("PUT", "/profile", {"big": "x" * relay.MAX_PROFILE}, headers={"If-Match": "2"})[0] == 400


def test_star_creates_only_when_there_is_no_profile(cl):
    assert cl.call("PUT", "/profile", {"a": 1}, headers={"If-Match": "*"})[0] == 200
    assert cl.call("PUT", "/profile", {"a": 2}, headers={"If-Match": "*"})[0] == 412


def test_data_survives_a_restart(tmp_path):
    a = relay.make_server(str(tmp_path), port=0)
    n = note("kept")
    a.store.upsert_note(n)
    a.store.put_profile({"k": "v"}, "0")
    a.server_close()
    b = relay.make_server(str(tmp_path), port=0)
    try:
        assert b.token == a.token
        assert b.store.get_note(n["id"])["text"] == "kept" and b.store.get_profile()["data"] == {"k": "v"}
        assert b.store.stats() == {"notes": 1, "seq": 1}
    finally:
        b.server_close()


# ------------------------------------------------------------------ medium round M4 (C-R1, C-R2)
def test_deleting_a_note_stamped_in_the_future_really_deletes_it(cl):
    n = note("ahead of the relay clock", updated_at=time.time() + 60)    # a phone whose clock runs a minute fast
    stored = cl.call("PUT", "/notes/" + n["id"], n)[1]["note"]      # cut back to the relay clock plus a few seconds
    st, out = cl.call("DELETE", "/notes/" + n["id"])
    assert st == 200 and out["note"]["deleted"] is True and out["applied"] is True
    assert out["note"]["updated_at"] > stored["updated_at"]
    assert cl.call("GET", "/notes/" + n["id"])[0] == 404


def test_a_timestamp_in_milliseconds_is_refused(cl):
    n = note("wrong unit", updated_at=time.time() * 1000)
    assert cl.call("PUT", "/notes/" + n["id"], n)[0] == 400
    assert cl.call("PUT", "/notes/" + nid(), note("x", created_at=time.time() * 1000))[0] == 400
    assert cl.call("PUT", "/notes/" + nid(), note("a day or less ahead is fine", updated_at=time.time() + 3600))[0] == 200


def _raw_request(srv, head, body=b"", wait=2.0):
    """Sends a request by hand and returns the status line of the answer. The socket stays open on our side."""
    import socket
    s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=wait)
    try:
        s.sendall(head.encode("latin-1") + body)
        data = b""
        while b"\r\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.split(b"\r\n", 1)[0].decode("latin-1")
    finally:
        s.close()


@pytest.mark.parametrize("length", ["-1", "1, 1", "+5", "0x10", " "])
def test_a_malformed_content_length_is_a_400_at_once_and_reads_nothing_more(server, length):
    head = (f"PUT /profile HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {server.token}\r\nIf-Match: 0\r\n"
            f"Content-Length: {length}\r\n\r\n")
    status = _raw_request(server, head, b"x" * 65536)     # the client keeps its side open: the answer must not wait for EOF
    assert " 400 " in status


# ------------------------------------------------------------------ final review (Relay-CI 1, Relay-CI 2): a wrong clock must not pin a note
def _legacy_ahead(srv, n, ahead):
    """Writes a note straight into the database with a time `ahead` seconds in the future: what an older relay (with no bound
    on the stored time) could have kept."""
    import sqlite3
    st, out = Client(srv).call("PUT", "/notes/" + n["id"], dict(n, updated_at=time.time()))
    assert st == 200
    con = sqlite3.connect(srv.store.path)
    with con:
        con.execute("UPDATE notes SET updated_at = ?, created_at = ?, order_at = NULL WHERE id = ?",
                    (time.time() + ahead, time.time() + ahead, n["id"]))
    con.close()


def test_a_fast_phone_time_is_stored_as_sent_so_the_phone_agrees_with_the_relay(cl):
    """Final fixes (relay-docs 1): the phone keeps its own time for the note; a smaller stored time would make it ignore
    every later change from the other devices. The time only decides the order cut back (see the tests below)."""
    n = note("fast clock", updated_at=time.time() + 3600)
    st, out = cl.call("PUT", "/notes/" + n["id"], n)
    assert st == 200 and out["applied"] is True
    assert out["note"]["updated_at"] == n["updated_at"] and "order_at" not in out["note"]
    assert "order_at" not in cl.call("GET", "/changes?since=0")[1]["notes"][0]


def test_a_write_that_wins_over_a_fast_phone_is_raised_above_its_time(cl, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(relay, "time", clock)
    n = note("from the fast phone", updated_at=time.time() + 3600)
    cl.call("PUT", "/notes/" + n["id"], n)
    clock.offset = 30
    st, out = cl.call("PUT", "/notes/" + n["id"], dict(n, text="from the laptop", updated_at=time.time() + 30))
    assert out["applied"] is True and out["note"]["updated_at"] > n["updated_at"]     # the phone will take it
    stale = dict(n, text="an older edit from the laptop", updated_at=time.time() - 10)
    st, out = cl.call("PUT", "/notes/" + n["id"], stale)
    assert out["applied"] is False and out["note"]["text"] == "from the laptop"


class _Clock:
    """`relay.time` with the clock moved on by `offset` seconds (everything else is the real module)."""
    offset = 0.0

    def __getattr__(self, name):
        return getattr(time, name)

    def time(self):
        return time.time() + self.offset


def test_a_device_delete_or_edit_beats_a_note_from_a_fast_phone(cl, monkeypatch):
    """Sync path: devices delete with PUT and a marker of their own time. The phone's clock is 5 minutes fast."""
    clock = _Clock()
    monkeypatch.setattr(relay, "time", clock)
    keep, gone = note("kept", updated_at=time.time() + 300), note("deleted", updated_at=time.time() + 300)
    first = [cl.call("PUT", "/notes/" + n["id"], n)[1] for n in (keep, gone)]
    assert all(o["applied"] for o in first)
    clock.offset = 30        # half a minute later, by the right clock of the laptop
    st, out = cl.call("PUT", "/notes/" + gone["id"], dict(gone, text="", title="", raw="", tags=[], deleted=True, updated_at=time.time() + 30))
    assert st == 200 and out["applied"] is True and out["note"]["deleted"] is True
    assert cl.call("GET", "/notes/" + gone["id"])[0] == 404
    st, out = cl.call("PUT", "/notes/" + keep["id"], dict(keep, text="edited on the laptop", updated_at=time.time() + 30))
    assert st == 200 and out["applied"] is True and cl.call("GET", "/notes/" + keep["id"])[1]["text"] == "edited on the laptop"


def test_an_old_offline_delete_still_loses_to_a_newer_edit(cl):
    """The relay clock only wins for what is recent: an old marker is not a reason to throw away a later edit."""
    n = note("edited later", updated_at=time.time() - 10)
    cl.call("PUT", "/notes/" + n["id"], n)
    out = cl.call("PUT", "/notes/" + n["id"], dict(n, text="", deleted=True, updated_at=time.time() - 5000))[1]
    assert out["applied"] is False and out["note"]["deleted"] is False


def test_a_note_stored_a_week_ahead_can_still_be_deleted_and_overwritten(server):
    c = Client(server)
    a, b = note("stuck a"), note("stuck b")
    _legacy_ahead(server, a, 7 * 86400)
    _legacy_ahead(server, b, 7 * 86400)
    st, out = c.call("DELETE", "/notes/" + a["id"])            # the management page
    assert st == 200 and out["applied"] is True and out["note"]["deleted"] is True
    assert c.call("GET", "/notes/" + a["id"])[0] == 404
    st, out = c.call("PUT", "/notes/" + b["id"], dict(b, text="fixed", updated_at=time.time()))      # a device edit
    assert st == 200 and out["applied"] is True and out["note"]["text"] == "fixed"
    assert out["note"]["updated_at"] > time.time() + 6 * 86400    # raised above the old time: every device takes it


def test_a_database_of_an_older_relay_gets_the_order_column_and_keeps_its_notes(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    with con:
        con.execute("CREATE TABLE notes (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, "
                    "source TEXT NOT NULL DEFAULT 'voice note', title TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '', "
                    "raw TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL, "
                    "secs REAL NOT NULL DEFAULT 0, device TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]', "
                    "deleted INTEGER NOT NULL DEFAULT 0)")
        n = note("kept from before")
        con.execute("INSERT INTO notes (id, title, text, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (n["id"], n["title"], n["text"], n["created_at"], n["updated_at"]))
    con.close()
    store = relay.RelayStore(path)
    assert store.get_note(n["id"])["text"] == "kept from before"
    stored, applied = store.upsert_note(dict(n, text="edited", updated_at=n["updated_at"] + 1))
    assert applied and stored["text"] == "edited" and "order_at" not in stored
