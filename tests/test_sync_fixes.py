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
