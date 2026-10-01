"""Profile sync (About you, dictionary, people, and with a switch the provider settings and keys) between two
'devices' (two data folders) through a real relay on localhost."""
import threading

import pytest

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
    c = core.load_config()
    c.update(kw)
    core.save_config(c)


def cfg_now():
    return core.load_config()


# ------------------------------------------------------------------ merge3
def test_merge3_takes_the_side_that_changed_and_the_relay_on_a_clash():
    base = {"a": 1, "b": 1, "c": 1, "d": 1}
    assert sync.merge3(base, {"a": 2, "b": 1, "c": 5, "d": 1}, {"a": 1, "b": 3, "c": 6, "d": 1}) == {"a": 2, "b": 3, "c": 6, "d": 1}
    assert sync.merge3({}, {"x": 1}, {"x": 1}) == {"x": 1}
    assert sync.merge3({}, {"x": 1}, {}) == {"x": 1}          # only here
    assert sync.merge3({}, {}, {"y": 2}) == {"y": 2}          # only on the relay
    assert sync.merge3({"z": 1}, {}, {"z": 1}) == {}          # removed here, unchanged there


def test_shared_fields_never_include_device_specific_settings():
    off = set(sync.shared_fields({}))
    on = set(sync.shared_fields({"relay_sync_keys": True}))
    assert {"user_context", "dictionary", "people"} <= off and "api_key" not in off and "base_url" not in off
    assert {"api_key", "stt_api_key", "llm_api_key", "base_url", "stt_model"} <= on
    for never in ("app_styles", "hotkey", "input_device", "relay_token", "relay_url", "keep_clipboard", "your_name"):
        assert never not in on


# ------------------------------------------------------------ between devices
def test_the_profile_travels_and_edits_follow(dev, srv):
    a = dev("A")
    set_cfg(user_context="I lead Atlas.", dictionary=["Atlas", "wrong => right"], people=["Ada"])
    assert sync.sync_once(a)["profile"] == "sent"
    prof = srv.store.get_profile()
    assert prof["version"] == 1 and prof["data"]["user_context"] == "I lead Atlas." and prof["data"]["people"] == ["Ada"]
    assert "api_key" not in prof["data"] and "app_styles" not in prof["data"]
    b = dev("B")
    assert sync.sync_once(b)["profile"] == "received"
    c = cfg_now()
    assert c["user_context"] == "I lead Atlas." and c["dictionary"] == ["Atlas", "wrong => right"] and c["people"] == ["Ada"]
    assert sync.sync_once(b)["profile"] == "" and srv.store.get_profile()["version"] == 1      # nothing changed: quiet
    set_cfg(dictionary=["Atlas", "Northwind"])
    assert sync.sync_once(b)["profile"] == "sent" and srv.store.get_profile()["version"] == 2
    dev("A")
    assert sync.sync_once(a)["profile"] == "received" and cfg_now()["dictionary"] == ["Atlas", "Northwind"]


def test_changes_to_different_fields_merge_and_a_clash_goes_to_the_relay(dev, srv):
    a = dev("A")
    set_cfg(user_context="original", dictionary=["one"])
    sync.sync_once(a)
    b = dev("B")
    sync.sync_once(b)
    dev("A")
    set_cfg(user_context="A wrote this", dictionary=["one", "two"])
    dev("B")
    set_cfg(user_context="B wrote this", people=["Ada"])
    assert sync.sync_once(b)["profile"] == "sent"                      # B reaches the relay first
    dev("A")
    assert sync.sync_once(a)["profile"] == "both"
    merged = srv.store.get_profile()["data"]
    assert merged["user_context"] == "B wrote this"                    # both changed it: the relay's value wins
    assert merged["dictionary"] == ["one", "two"] and merged["people"] == ["Ada"]   # each side's own field survives
    assert cfg_now()["user_context"] == "B wrote this" and cfg_now()["people"] == ["Ada"]


def test_keys_travel_only_when_switched_on_and_leave_the_relay_when_switched_off(dev, srv):
    a = dev("A")
    set_cfg(api_key="gsk_secret", base_url="https://api.groq.com/openai/v1", stt_model="whisper-large-v3-turbo", user_context="hello")
    sync.sync_once(a)
    assert "api_key" not in srv.store.get_profile()["data"] and "stt_model" not in srv.store.get_profile()["data"]
    set_cfg(relay_sync_keys=True)
    assert sync.sync_once(a)["profile"] == "sent"
    data = srv.store.get_profile()["data"]
    assert data["api_key"] == "gsk_secret" and data["stt_model"] == "whisper-large-v3-turbo"
    b = dev("B")
    set_cfg(relay_sync_keys=True)
    assert sync.sync_once(b)["profile"] == "received"
    assert cfg_now()["api_key"] == "gsk_secret" and cfg_now()["user_context"] == "hello"
    c = dev("C")                                                         # a device that did not opt in
    sync.sync_once(c)
    assert cfg_now()["api_key"] == "" and cfg_now()["user_context"] == "hello"
    dev("A")
    set_cfg(relay_sync_keys=False)
    sync.sync_once(a)
    assert "api_key" not in srv.store.get_profile()["data"]              # switching off takes the keys off the relay
    assert cfg_now()["api_key"] == "gsk_secret"                          # and keeps them on this device


def test_fields_added_by_other_devices_are_kept(dev, srv):
    a = dev("A")
    set_cfg(user_context="first")
    sync.sync_once(a)
    v = srv.store.get_profile()
    srv.store.put_profile(dict(v["data"], app_styles_android={"com.whatsapp": "casual"}), str(v["version"]))
    set_cfg(user_context="second")
    sync.sync_once(a)
    data = srv.store.get_profile()["data"]
    assert data["user_context"] == "second" and data["app_styles_android"] == {"com.whatsapp": "casual"}


def test_a_stale_write_is_retried_not_lost(dev, srv, monkeypatch):
    a = dev("A")
    set_cfg(user_context="mine")
    sync.sync_once(a)
    set_cfg(dictionary=["mine-too"])
    real = sync._call
    state = {"raced": False}

    def racing(method, url, path, token, device, **kw):
        if method == "PUT" and path == "/profile" and not state["raced"]:
            state["raced"] = True
            v = srv.store.get_profile()
            srv.store.put_profile(dict(v["data"], people=["from another device"]), str(v["version"]))
        return real(method, url, path, token, device, **kw)

    monkeypatch.setattr(sync, "_call", racing)
    res = sync.sync_once(a)
    assert res["error"] == "" and state["raced"]
    data = srv.store.get_profile()["data"]
    assert data["dictionary"] == ["mine-too"] and data["people"] == ["from another device"]
    assert cfg_now()["people"] == ["from another device"]


def test_a_relay_that_never_settles_is_reported(dev, srv, monkeypatch):
    a = dev("A")
    set_cfg(user_context="x")
    real = sync._call

    def always_stale(method, url, path, token, device, **kw):
        if method == "PUT" and path == "/profile":
            return 412, {"version": 99, "data": {}}
        return real(method, url, path, token, device, **kw)

    monkeypatch.setattr(sync, "_call", always_stale)
    assert "keeps changing" in sync.sync_once(a)["error"]


def test_settings_received_before_a_stale_write_still_count_as_received(dev, srv, monkeypatch):
    a = dev("A")
    set_cfg(user_context="mine")
    sync.sync_once(a)
    set_cfg(dictionary=["mine-too"])
    v = srv.store.get_profile()
    srv.store.put_profile(dict(v["data"], people=["Ada"]), str(v["version"]))   # another device: A will receive this
    real = sync._call
    state = {"raced": False}

    def racing(method, url, path, token, device, **kw):
        if method == "PUT" and path == "/profile" and not state["raced"]:
            state["raced"] = True
            v = srv.store.get_profile()
            srv.store.put_profile(dict(v["data"]), str(v["version"]))          # a save that changes nothing we merge
        return real(method, url, path, token, device, **kw)

    monkeypatch.setattr(sync, "_call", racing)
    res = sync.sync_once(a)
    assert state["raced"] and res["error"] == ""
    assert res["profile"] == "both"                    # the retry sees "Ada" as local, but it was received on the first try
    assert cfg_now()["people"] == ["Ada"]
