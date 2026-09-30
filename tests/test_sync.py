"""Windows sync client against a real relay on localhost: two 'devices' are two data folders."""
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


def test_apply_remote_rules():
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
