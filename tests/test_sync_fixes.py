"""Windows sync fixes from the v2 review (ARC-04, DAT-3, DAT-4, DAT-5, DAT-7, DAT-13), between 'devices' (data folders)
through a real relay on localhost, like test_sync.py and test_sync_profile.py."""
import json
import threading

import pytest

import notes
import relay
import sync
import vox_core as core


@pytest.fixture
def srv(tmp_path):
    server = relay.make_server(str(tmp_path / "relay"), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def dev(tmp_path, monkeypatch, srv):
    def switch(name):
        monkeypatch.setenv("APPDATA", str(tmp_path / name))
        return {"relay_sync": True, "relay_url": f"http://127.0.0.1:{srv.server_address[1]}",
                "relay_token": srv.token, "device_name": name}
    return switch


def set_cfg(**kw):
    core.update_config(lambda c: c.update(kw))


# ------------------------------------------------------- ARC-04: a save made during the profile request is kept
def _union_lists(real):
    """merge3 with a per-item union for lists (what a per-item dictionary merge does): shows that the client merges
    against the settings as they are when it writes, not as they were before the request."""
    def merge(base, local, remote):
        out = real(base, local, remote)
        for k, v in out.items():
            if isinstance(v, list) and isinstance(local.get(k), list) and isinstance(remote.get(k), list):
                out[k] = list(dict.fromkeys(local[k] + remote[k]))
        return out
    return merge


def test_a_word_added_in_the_window_during_the_profile_request_is_not_written_over(dev, srv, monkeypatch):
    a = dev("A")
    set_cfg(dictionary=["Alpha", "Beta"])
    assert sync.sync_once(a)["profile"] == "sent"
    b = dev("B")
    sync.sync_once(b)
    set_cfg(dictionary=["Alpha", "Beta", "FromPhone"])
    assert sync.sync_once(b)["profile"] == "sent"
    dev("A")
    monkeypatch.setattr(sync, "merge3", _union_lists(sync.merge3))
    real_call, done = sync._call, []

    def window_saves_during_the_get(method, url, path, *args, **kw):
        out = real_call(method, url, path, *args, **kw)
        if method == "GET" and path == "/profile" and not done:
            done.append(1)
            core.update_config(lambda c: c["dictionary"].append("AddedNow"))   # what ui_app.dict_add_term does
        return out

    monkeypatch.setattr(sync, "_call", window_saves_during_the_get)
    sync.sync_once(a)
    assert core.load_config()["dictionary"] == ["Alpha", "Beta", "AddedNow", "FromPhone"]
    assert srv.store.get_profile()["data"]["dictionary"] == ["Alpha", "Beta", "AddedNow", "FromPhone"]


# ------------------------------------------------------- DAT-3: a PC clock that is behind keeps its own edits
class _SlowClock:
    """Stands in for the `time` module inside notes.py only (patching time.time would move the relay's clock too)."""
    def __init__(self, behind):
        import time as real
        self._real, self._behind = real, behind

    def time(self):
        return self._real.time() - self._behind

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_an_edit_and_a_delete_made_with_a_slow_clock_are_kept(dev, srv, monkeypatch):
    phone = dev("Phone")
    keep = notes.add("phone note to edit")
    gone = notes.add("phone note to delete")
    sync.sync_once(phone)
    pc = dev("PC")
    assert sync.sync_once(pc)["pulled"] == 2
    monkeypatch.setattr(notes, "time", _SlowClock(600))
    notes.update(keep["id"], text="edited on the pc")
    assert notes.delete(gone["id"])
    out = sync.sync_once(pc)
    assert out["error"] == "" and out["pushed"] == 2
    assert notes.get(keep["id"])["text"] == "edited on the pc" and notes.get(gone["id"]) is None
    on_relay = {n["id"]: n for n in srv.store.changes(0, 50)["notes"]}
    assert on_relay[keep["id"]]["text"] == "edited on the pc" and on_relay[gone["id"]]["deleted"]


def test_a_local_change_is_always_stamped_later_than_the_version_it_changes(dev, monkeypatch):
    dev("PC")
    n = notes.add("x")
    monkeypatch.setattr(notes, "time", _SlowClock(3600))
    assert notes.update(n["id"], text="y")["updated_at"] > n["updated_at"]


# ------------------------------------------------------- DAT-4: a relay that lost its data at the same address
def test_a_wiped_relay_at_the_same_address_gets_everything_again(dev, srv, tmp_path):
    pc = dev("PC")
    for i in range(3):
        notes.add(f"pc note {i}")
    assert sync.sync_once(pc)["pushed"] == 3
    srv.store = relay.RelayStore(str(tmp_path / "fresh" / "relay.db"))   # reinstalled: an empty database, same address
    phone = dev("Phone")
    notes.add("phone note")
    assert sync.sync_once(phone)["pushed"] == 1
    dev("PC")
    out = sync.sync_once(pc)
    assert out["error"] == "" and out["pushed"] == 3 and out["pulled"] == 1
    assert sorted(n["text"] for n in notes.search()) == ["pc note 0", "pc note 1", "pc note 2", "phone note"]
    assert srv.store.stats()["notes"] == 4


# ------------------------------------------------------- DAT-13: the pull loop does not trust `next` and `more`
@pytest.mark.parametrize("answer", [{"notes": [], "next": 0, "more": True}, {"notes": [], "next": "7", "more": False},
                                    {"notes": [], "next": True, "more": True}])
def test_a_relay_that_does_not_move_its_cursor_does_not_keep_sync_busy(dev, srv, monkeypatch, answer):
    pc = dev("PC")
    real, calls = sync._call, []

    def odd(method, url, path, *a, **kw):
        if path.startswith("/changes"):
            calls.append(path)
            if len(calls) > 5:
                raise AssertionError("asked again and again")
            return 200, dict(answer)
        return real(method, url, path, *a, **kw)

    monkeypatch.setattr(sync, "_call", odd)
    out = sync.sync_once(pc)
    assert len(calls) == 1
    assert out["error"] == ""
    assert notes.get_meta("relay_cursor", "0") == "0"
    assert sync.sync_once(pc)["error"] == ""   # a later sync still works (no "7" or True saved as the cursor)


# ------------------------------------------------------- DAT-5: no API key in plain text in notes.db
def test_the_profile_snapshot_keeps_no_api_key_and_key_changes_still_merge(dev, srv):
    a = dev("A")
    set_cfg(api_key="gsk_SECRETKEY_123", llm_api_key="sk-LLMSECRET-456", relay_sync_keys=True)
    assert sync.sync_once(a)["profile"] == "sent"
    snap = notes.get_meta("profile_snapshot", "{}")
    assert "gsk_SECRETKEY_123" not in snap and "sk-LLMSECRET-456" not in snap
    with open(notes.db_path(), "rb") as f:
        db = f.read()
    import os
    wal = notes.db_path() + "-wal"
    if os.path.exists(wal):
        with open(wal, "rb") as f:
            db += f.read()
    assert b"gsk_SECRETKEY_123" not in db
    # the three-way merge still sees which side changed the key
    assert sync.sync_once(a)["profile"] == ""
    b = dev("B")
    set_cfg(relay_sync_keys=True)
    assert sync.sync_once(b)["profile"] == "received"
    assert core.load_config()["api_key"] == "gsk_SECRETKEY_123"
    set_cfg(api_key="gsk_NEWKEY_789")
    assert sync.sync_once(b)["profile"] == "sent"
    dev("A")
    assert sync.sync_once(a)["profile"] == "received"
    assert core.load_config()["api_key"] == "gsk_NEWKEY_789"
    set_cfg(api_key="gsk_MINE_000")   # changed here only: this device's key wins
    assert sync.sync_once(a)["profile"] == "sent"
    assert srv.store.get_profile()["data"]["api_key"] == "gsk_MINE_000"


def test_an_old_snapshot_with_plain_keys_is_replaced_and_its_pages_are_wiped(dev, srv):
    a = dev("A")
    set_cfg(api_key="gsk_OLDPLAIN_42", relay_sync_keys=True)
    assert sync.sync_once(a)["profile"] == "sent"
    notes.set_meta("profile_snapshot", json.dumps(dict(json.loads(notes.get_meta("profile_snapshot")),
                                                        api_key="gsk_OLDPLAIN_42")))   # as an older Vox stored it
    assert sync.sync_once(a)["error"] == ""
    import os
    data = b""
    for p in (notes.db_path(), notes.db_path() + "-wal"):
        if os.path.exists(p):
            with open(p, "rb") as f:
                data += f.read()
    assert b"gsk_OLDPLAIN_42" not in data


# ------------------------------------------------------- DAT-7: a value of the wrong type from the relay
def test_a_wrong_type_from_the_relay_is_not_written_here_and_is_repaired_on_the_relay(dev, srv):
    a = dev("A")
    set_cfg(default_style="formal", user_context="me", cleanup=True)
    assert sync.sync_once(a)["profile"] == "sent"
    prof = srv.store.get_profile()
    srv.store.put_profile(dict(prof["data"], default_style=["formal"], user_context=5, cleanup="no"), str(prof["version"]))
    assert sync.sync_once(a)["error"] == ""
    cfg = core.load_config()
    assert (cfg["default_style"], cfg["user_context"], cfg["cleanup"]) == ("formal", "me", True)
    data = srv.store.get_profile()["data"]
    assert (data["default_style"], data["user_context"], data["cleanup"]) == ("formal", "me", True)


# ------------------------------------------------------- DAT-6 (Windows part): "Recently learned" follows the dictionary
def test_a_learned_word_that_a_received_dictionary_no_longer_has_leaves_recently_learned(dev, srv):
    import autolearn
    a = dev("A")
    set_cfg(dictionary=["Vox"])
    assert sync.sync_once(a)["profile"] == "sent"
    b = dev("B")
    sync.sync_once(b)
    dev("A")
    core.update_config(lambda c: c.update(autolearn.apply_learned(c, [("fubar", "Foobar")], now=100.0)[0]))
    assert [e["right"] for e in autolearn.learned_log(core.load_config())] == ["Foobar"]
    dev("B")
    set_cfg(dictionary=["Vox", "Bar"])
    assert sync.sync_once(b)["profile"] == "sent"
    dev("A")
    sync.sync_once(a)          # both changed the dictionary: the relay's list wins (the merge rule itself is not changed here)
    cfg = core.load_config()
    assert cfg["dictionary"] == ["Vox", "Bar"]
    assert autolearn.learned_log(cfg) == []   # no row for a word that is not in the dictionary any more
