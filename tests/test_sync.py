"""Windows sync client against a real relay on localhost: two 'devices' are two data folders."""
import contextlib
import sqlite3
import threading
import time

import pytest

import notes
import relay
import sync


@pytest.fixture
def srv(tmp_path):
    server = relay.make_server(str(tmp_path / "relay"), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def dev(tmp_path, monkeypatch, srv):
    """dev("A") switches this process to device A's data folder and returns its sync settings."""
    def switch(name):
        monkeypatch.setenv("APPDATA", str(tmp_path / name))
        return {"relay_sync": True, "relay_url": f"http://127.0.0.1:{srv.server_address[1]}",
                "relay_token": srv.token, "device_name": name}
    return switch


def brief(res):
    return {k: res[k] for k in ("pushed", "pulled", "error")}


def texts():
    return sorted(n["text"] for n in notes.search(""))


def test_a_note_travels_from_one_device_to_another(dev, srv):
    a = dev("A")
    n = notes.add("call the dentist", device="A")
    assert brief(sync.sync_once(a)) == {"pushed": 1, "pulled": 0, "error": ""}      # our own send coming back is not "pulled"
    assert srv.store.get_note(n["id"])["text"] == "call the dentist"
    b = dev("B")
    assert brief(sync.sync_once(b)) == {"pushed": 0, "pulled": 1, "error": ""}
    got = notes.get(n["id"])
    assert got["text"] == "call the dentist" and got["device"] == "A" and got["dirty"] is False
    assert brief(sync.sync_once(b)) == {"pushed": 0, "pulled": 0, "error": ""}      # nothing new: quiet
    assert int(notes.get_meta("relay_cursor")) == srv.store.stats()["seq"]


def test_edits_and_deletes_follow(dev, srv):
    a = dev("A")
    n = notes.add("first version")
    sync.sync_once(a)
    b = dev("B")
    sync.sync_once(b)
    time.sleep(0.01)
    notes.update(n["id"], text="edited on B")
    assert sync.sync_once(b)["pushed"] == 1
    dev("A")
    assert sync.sync_once(a)["pulled"] == 1 and notes.get(n["id"])["text"] == "edited on B"
    time.sleep(0.01)
    notes.delete(n["id"])
    assert sync.sync_once(a)["pushed"] == 1
    dev("B")
    sync.sync_once(b)
    assert notes.get(n["id"]) is None and notes.search("edited") == []
    marker = srv.store.changes(0)["notes"][-1]
    assert marker["deleted"] and marker["text"] == ""            # the relay keeps no text for a deleted note


def test_the_newer_edit_wins_a_conflict(dev, srv):
    a = dev("A")
    n = notes.add("original")
    sync.sync_once(a)
    b = dev("B")
    sync.sync_once(b)
    dev("A")
    time.sleep(0.01)
    notes.update(n["id"], text="A edit (older)")
    dev("B")
    time.sleep(0.01)
    notes.update(n["id"], text="B edit (newer)")
    assert sync.sync_once(b)["pushed"] == 1                     # B reaches the relay first
    dev("A")
    res = sync.sync_once(a)                                     # A's older edit is refused, A takes B's
    assert res["error"] == "" and notes.get(n["id"])["text"] == "B edit (newer)"
    assert srv.store.get_note(n["id"])["text"] == "B edit (newer)"
    assert notes.dirty_notes() == []


def test_a_note_changed_again_while_being_sent_stays_dirty(dev):
    dev("A")
    n = notes.add("v1")
    sent = notes.get(n["id"])["updated_at"]
    time.sleep(0.01)
    notes.update(n["id"], text="v2")
    notes.mark_synced(n["id"], sent, 7)
    assert [x["id"] for x in notes.dirty_notes()] == [n["id"]]


def test_failures_are_messages_and_lose_nothing(dev, srv):
    a = dev("A")
    n = notes.add("keep me")
    assert "refused the token" in sync.sync_once(dict(a, relay_token="wrong"))["error"]
    assert "Cannot reach" in sync.sync_once(dict(a, relay_url="http://127.0.0.1:1"))["error"]
    assert "Plain http" in sync.sync_once(dict(a, relay_url="http://relay.example.com"))["error"]
    assert "off or not set up" in sync.sync_once(dict(a, relay_sync=False))["error"]
    assert "off or not set up" in sync.sync_once(dict(a, relay_token=""))["error"]
    assert notes.get(n["id"])["dirty"] is True and srv.store.stats()["notes"] == 0
    assert sync.sync_once(a)["error"] == "" and srv.store.stats()["notes"] == 1


def add_unsendable(text="the relay will refuse this", updated_at=1.0):
    """A note row the relay answers 400 for ("bad note id"), the oldest one, so it is the first to be sent."""
    notes.search("")   # makes sure the database exists
    con = sqlite3.connect(notes.db_path())
    con.execute("INSERT INTO notes (id, title, text, created_at, updated_at) VALUES ('NOT-A-VALID-ID', 'bad', ?, 1, ?)", (text, updated_at))
    con.commit()
    con.close()
    return "NOT-A-VALID-ID"


def test_a_note_the_relay_refuses_for_good_does_not_block_the_others(dev, srv):
    a = dev("A")
    bad = add_unsendable()
    good = [notes.add("good one"), notes.add("good two")]
    elsewhere = {"id": "c" * 32, "source": "voice note", "title": "", "text": "from another device", "raw": "", "created_at": 5,
                 "updated_at": 5, "secs": 0, "device": "B", "tags": [], "deleted": False}
    srv.store.upsert_note(elsewhere)
    res = sync.sync_once(a)
    assert (res["pushed"], res["pulled"]) == (2, 1)                       # the other notes and the pull still complete
    assert res["error"].startswith("1 note could not be sent") and "HTTP 400" in res["error"]
    assert srv.store.get_note(good[0]["id"]) and srv.store.get_note(good[1]["id"])
    assert [x["id"] for x in notes.dirty_notes()] == [bad]               # the refused note stays here, nothing is lost
    assert notes.get(elsewhere["id"])["text"] == "from another device"
    again = sync.sync_once(a)                                             # tried once more, never in a loop
    assert (again["pushed"], again["pulled"]) == (0, 0) and "1 note could not be sent" in again["error"]


def test_several_refused_notes_are_counted_and_the_profile_still_syncs(dev, srv):
    a = dev("A")
    add_unsendable("one", 1.0)
    notes.add("fine")
    con = sqlite3.connect(notes.db_path())
    con.execute("INSERT INTO notes (id, title, text, created_at, updated_at) VALUES ('ALSO-BAD', 'bad', 'two', 1, 2)")
    con.commit()
    con.close()
    res = sync.sync_once(a)
    assert res["pushed"] == 1 and res["error"].startswith("2 notes could not be sent")
    assert res["profile"] == "sent" and srv.store.get_profile()["version"] == 1


def test_a_failure_that_is_not_about_one_note_still_stops_the_run(dev):
    a = dev("A")
    add_unsendable()
    notes.add("waits for the next run")
    res = sync.sync_once(dict(a, relay_token="wrong"))
    assert "refused the token" in res["error"] and res["pushed"] == 0      # 401 is not "this note is bad": stop, keep everything
    assert len(notes.dirty_notes()) == 2


def test_permanent_errors_are_the_4xx_the_same_request_will_always_get():
    perm = [s for s in range(0, 600) if sync.SyncError("x", s).permanent]
    assert perm == [s for s in range(400, 500) if s not in (401, 403, 429)]


def test_an_owner_mismatch_is_reported(dev, tmp_path):
    server = relay.make_server(str(tmp_path / "owned"), port=0, owner="someone@example.com")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cfg = dict(dev("A"), relay_url=f"http://127.0.0.1:{server.server_address[1]}", relay_token=server.token)
        assert "another Tailscale user" in sync.sync_once(cfg)["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_test_relay_reports_what_is_wrong(srv):
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    ok = sync.test_relay(url, srv.token)
    assert ok["ok"] and "0 notes" in ok["message"]
    assert not sync.test_relay(url, "nope")["ok"] and "refused" in sync.test_relay(url, "nope")["message"]
    assert "http://" in sync.test_relay("ftp://x", "t")["message"]
    assert "address" in sync.test_relay("", "t")["message"] and "token" in sync.test_relay(url, "")["message"]


def test_old_databases_are_upgraded_and_their_notes_get_sent(dev, srv):
    a = dev("A")
    import os
    os.makedirs(os.path.join(os.environ["APPDATA"], "Vox"), exist_ok=True)
    con = sqlite3.connect(notes.db_path())
    con.executescript("""CREATE TABLE notes (id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'voice note', title TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL DEFAULT '', raw TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL,
        secs REAL NOT NULL DEFAULT 0, device TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]', deleted INTEGER NOT NULL DEFAULT 0);""")
    con.execute("INSERT INTO notes (id, title, text, created_at, updated_at) VALUES (?, 'old', 'from before sync existed', 1, 1)", ("a" * 32,))
    con.commit()
    con.close()
    assert notes.search("")[0]["dirty"] is True
    assert sync.sync_once(a)["pushed"] == 1 and srv.store.get_note("a" * 32)["text"] == "from before sync existed"


def test_apply_remote_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))   # never the real profile's notes.db (the conftest guard also stops that)
    n = notes.add("local")
    base = {"id": n["id"], "source": "voice note", "title": "t", "text": "remote", "raw": "", "created_at": n["created_at"], "secs": 0, "device": "x", "tags": ["a"], "seq": 5}
    assert notes.apply_remote(dict(base, updated_at=n["updated_at"] - 10, deleted=False)) is False   # older: ignored
    assert notes.get(n["id"])["text"] == "local"
    assert notes.apply_remote(dict(base, updated_at=n["updated_at"] + 10, deleted=False)) is True
    got = notes.get(n["id"])
    assert got["text"] == "remote" and got["seq"] == 5 and got["dirty"] is False and got["tags"] == ["a"]
    assert [x["id"] for x in notes.search("remote")] == [n["id"]] and notes.search("local") == []
    assert notes.apply_remote(dict(base, id="b" * 32, updated_at=1, deleted=True)) is False          # delete of an unknown note
    assert notes.apply_remote(dict(base, updated_at=n["updated_at"] + 20, deleted=True)) is True
    assert notes.get(n["id"]) is None and notes.search("remote") == []


def test_worker_syncs_on_trigger_and_reports_status(dev, srv):
    cfg = dev("A")
    n = notes.add("sent by the worker")
    worker = sync.SyncWorker(lambda: cfg)
    assert worker.status()["enabled"] is True and worker.status()["last_ok"] is None
    worker.start()
    try:
        for _ in range(100):
            if worker.status()["last_ok"]:
                break
            time.sleep(0.05)
        st = worker.status()
        assert st["last_ok"] and st["error"] == "" and st["pushed"] == 1 and srv.store.get_note(n["id"])
        cfg["relay_sync"] = False
        assert worker.status()["enabled"] is False
    finally:
        worker.stop()


def test_a_first_sync_of_many_notes_reads_each_dirty_row_about_once(dev, srv, monkeypatch):
    a = dev("A")
    total = sync.PUSH_BATCH * 8
    for i in range(total):
        notes.add(f"note {i}")
    rows = {"n": 0}
    real = notes.dirty_notes

    def counting(limit=100):
        out = real(limit)
        rows["n"] += len(out)
        return out

    monkeypatch.setattr(notes, "dirty_notes", counting)
    res = sync.sync_once(a)
    assert res["pushed"] == total and res["error"] == ""
    assert rows["n"] <= total + 2 * sync.PUSH_BATCH     # not the sum of a growing limit for every batch


def test_another_relay_address_means_everything_is_sent_again(dev, srv, tmp_path):
    a = dev("A")
    keep = notes.add("keep me", device="A")
    gone = notes.add("delete me", device="A")
    sync.sync_once(a)
    notes.delete(gone["id"])
    sync.sync_once(a)
    assert notes.get_meta("relay_origin") == a["relay_url"]
    # the same relay spelled differently is not another relay
    spelled = dict(a, relay_url="HTTP://" + a["relay_url"][len("http://"):] + "/")
    assert sync.origin_of(spelled["relay_url"]) == sync.origin_of(a["relay_url"])
    assert brief(sync.sync_once(spelled)) == {"pushed": 0, "pulled": 0, "error": ""}
    assert int(notes.get_meta("relay_cursor")) == srv.store.stats()["seq"]
    # a new, empty relay (another device already put a note there)
    other = relay.make_server(str(tmp_path / "relay2"), port=0)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        b = dict(a, relay_url=f"http://127.0.0.1:{other.server_address[1]}", relay_token=other.token)
        other.store.upsert_note({"id": "f" * 32, "source": "note", "title": "t", "text": "from elsewhere", "raw": "", "created_at": 1.0, "updated_at": 1.0,
                              "secs": 0.0, "device": "pc", "tags": [], "deleted": False})
        res = sync.sync_once(b)
        assert res["error"] == "" and res["pushed"] == 2
        assert other.store.get_note(keep["id"])["text"] == "keep me"
        marker = [n for n in other.store.changes(0)["notes"] if n["id"] == gone["id"]][0]
        assert marker["deleted"] is True                                 # the delete marker travels too
        assert notes.get(("f" * 32))["text"] == "from elsewhere"         # the cursor was reset, so this is received
        assert notes.get_meta("relay_origin") == b["relay_url"]
        assert int(notes.get_meta("relay_cursor")) == other.store.stats()["seq"]
        assert brief(sync.sync_once(b)) == {"pushed": 0, "pulled": 0, "error": ""}
    finally:
        other.shutdown()
        other.server_close()


def test_an_install_without_a_saved_relay_address_keeps_its_state(dev, srv):
    a = dev("A")
    n = notes.add("old", device="A")
    sync.sync_once(a)
    with contextlib.closing(notes._connect()) as con, con:
        con.execute("DELETE FROM sync_meta WHERE key = 'relay_origin'")
    assert brief(sync.sync_once(a)) == {"pushed": 0, "pulled": 0, "error": ""}
    assert notes.get(n["id"])["dirty"] is False and notes.get_meta("relay_origin") == a["relay_url"]


def test_a_database_error_while_following_the_relay_is_a_message_not_a_crash(dev, monkeypatch):
    a = dev("A")

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(notes, "get_meta", locked)
    res = sync.sync_once(a)
    assert res["pushed"] == 0 and res["pulled"] == 0 and res["error"] == "Sync failed: OperationalError"


# ------------------------------------------------------------------ final fixes (relay-docs 1): a fast phone clock must not split devices
class _FastClock:
    """`notes.time` running `offset` seconds ahead (a phone whose clock is wrong)."""
    def __init__(self, offset):
        self.offset = offset

    def __getattr__(self, name):
        return getattr(time, name)

    def time(self):
        return time.time() + self.offset


@pytest.mark.parametrize("act", ["edit", "delete"])
def test_a_note_from_a_fast_clock_device_still_takes_edits_and_deletes_from_the_others(dev, srv, monkeypatch, act):
    """Device A's clock is an hour fast. B edits or deletes A's note half a minute later: every device ends with B's change."""
    a = dev("A")
    monkeypatch.setattr(notes, "time", _FastClock(3600))
    n = notes.add("written on the fast phone", device="A")
    assert sync.sync_once(a)["error"] == ""
    monkeypatch.setattr(notes, "time", time)
    b = dev("B")
    sync.sync_once(b)
    assert notes.get(n["id"])["text"] == "written on the fast phone"
    later = _FastClock(30)                                             # half a minute later, for everyone
    monkeypatch.setattr(notes, "time", later)
    monkeypatch.setattr(relay, "time", later)
    if act == "edit":
        notes.update(n["id"], text="edited on the laptop")
    else:
        notes.delete(n["id"])
    assert sync.sync_once(b)["pushed"] == 1
    want = "edited on the laptop" if act == "edit" else None
    assert (notes.get(n["id"]) or {}).get("text") == want              # B keeps its own change
    dev("A")
    sync.sync_once(a)
    assert (notes.get(n["id"]) or {}).get("text") == want              # and the fast phone takes it
    stored = srv.store.changes(0)["notes"][-1]
    assert stored["deleted"] is (act == "delete")
