"""A note id from the relay goes into the page as an attribute and into URLs: only 32 lowercase hex characters are accepted."""
import pytest

import notes
import sync

BAD = 'x" onmouseover="1'


def remote(nid, **kw):
    return dict({"id": nid, "source": "voice note", "title": "t", "text": "hello", "raw": "", "created_at": 1.0,
                 "updated_at": 2.0, "secs": 0, "device": "d", "tags": [], "deleted": False, "seq": 1}, **kw)


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


@pytest.mark.parametrize("nid", [BAD, "", "A" * 32, "a" * 31, "a" * 33, "<script>", None, 5])
def test_apply_remote_ignores_a_note_with_a_bad_id(nid):
    assert notes.apply_remote(remote(nid)) is False
    assert notes.count() == 0 and notes.search("") == []


def test_apply_remote_still_takes_a_good_id():
    assert notes.apply_remote(remote("a1" * 16)) is True
    assert notes.count() == 1


def test_a_sync_against_a_relay_that_sends_a_bad_id_stores_nothing_and_does_not_raise(monkeypatch):
    def fake_request(method, url, path, token, device, json=None):
        assert method == "GET" and path.startswith("/changes")
        return {"notes": [remote(BAD), remote("b2" * 16)], "next": 2, "more": False}

    monkeypatch.setattr(sync, "_request", fake_request)
    monkeypatch.setattr(sync, "sync_profile", lambda *a: {})
    monkeypatch.setattr(sync, "follow_relay", lambda url: None)
    res = sync.sync_once({"relay_sync": True, "relay_url": "http://127.0.0.1:1", "relay_token": "t", "device_name": "d"})
    assert res["error"] == "" and res["pulled"] == 1
    assert [n["id"] for n in notes.search("")] == ["b2" * 16]
