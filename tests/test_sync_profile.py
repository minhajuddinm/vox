"""Profile sync (About you, dictionary, people, and with a switch the provider settings and keys) between two
'devices' (two data folders) through a real relay on localhost."""
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


def test_merge3_a_blank_default_on_the_other_side_never_wipes_a_value_when_there_is_no_base():
    # the first sync of a device: no snapshot yet, the other device only holds blank defaults
    mine = {"user_context": "me", "dictionary": ["Vox"], "people": ["Ada"]}
    blank = {"user_context": "", "dictionary": [], "people": []}
    assert sync.merge3({}, mine, blank) == mine
    assert sync.merge3({}, blank, mine) == mine                          # the mirror case: the other side's value
    assert sync.merge3({}, blank, blank) == blank
    # with a snapshot, clearing a field on purpose is still a change like any other
    assert sync.merge3({"user_context": "me"}, {"user_context": "me"}, {"user_context": ""}) == {"user_context": ""}


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


def test_a_device_with_keys_off_does_not_undo_the_keys_another_device_put_on_the_relay(dev, srv):
    a = dev("A")
    set_cfg(api_key="gsk_pc", user_context="hello", relay_sync_keys=True)
    sync.sync_once(a)
    assert srv.store.get_profile()["data"]["api_key"] == "gsk_pc"
    b = dev("B")                                                         # keys off, like the phone's default
    for _ in range(4):
        dev("B")
        sync.sync_once(b)
        assert srv.store.get_profile()["data"]["api_key"] == "gsk_pc"
        assert cfg_now()["api_key"] == ""                                # and B never took it
        dev("A")
        assert sync.sync_once(a)["profile"] == ""                        # A is not asked to change anything
        assert cfg_now()["api_key"] == "gsk_pc"
    version = srv.store.get_profile()["version"]
    dev("B")
    sync.sync_once(b)
    assert srv.store.get_profile()["version"] == version                 # settled: no more writes
    set_cfg(user_context="from B")                                       # B's own change keeps the key on the relay
    sync.sync_once(b)
    data = srv.store.get_profile()["data"]
    assert data["user_context"] == "from B" and data["api_key"] == "gsk_pc"


def test_the_real_on_to_off_switch_takes_the_keys_off_once(dev, srv):
    a = dev("A")
    set_cfg(api_key="gsk_secret", relay_sync_keys=True)
    sync.sync_once(a)
    set_cfg(relay_sync_keys=False)
    sync.sync_once(a)
    assert "api_key" not in srv.store.get_profile()["data"]
    version = srv.store.get_profile()["version"]
    sync.sync_once(a)
    assert srv.store.get_profile()["version"] == version
    v = srv.store.get_profile()                                          # another device puts keys back: this one leaves them
    srv.store.put_profile(dict(v["data"], api_key="gsk_again"), str(v["version"]))
    sync.sync_once(a)
    assert srv.store.get_profile()["data"]["api_key"] == "gsk_again"


def _keys_on_the_relay_then_forget_where_they_came_from(srv, a):
    """Keys on the relay from this device, as an install from before the flag leaves them: no flag, no saved address."""
    set_cfg(api_key="gsk_secret", relay_sync_keys=True)
    sync.sync_once(a)
    assert srv.store.get_profile()["data"]["api_key"] == "gsk_secret"
    notes.set_meta("profile_keys_sent", "")
    notes.set_meta("relay_origin", "")


def test_an_upgraded_install_that_never_synced_since_treats_the_keys_on_the_relay_as_its_own(dev, srv):
    a = dev("A")
    _keys_on_the_relay_then_forget_where_they_came_from(srv, a)
    sync.follow_relay(a["relay_url"])                                    # the first run after the upgrade, which then stops early
    assert notes.get_meta("profile_keys_sent") == "1"
    set_cfg(relay_sync_keys=False)
    sync.sync_once(a)
    assert "api_key" not in srv.store.get_profile()["data"]              # switching off still takes them off the relay


def test_a_fresh_install_with_keys_off_does_not_claim_the_keys_on_the_relay(dev, srv):
    a = dev("A")
    set_cfg(api_key="gsk_pc", relay_sync_keys=True)
    sync.sync_once(a)
    b = dev("B")                                                         # keys off, no saved address
    sync.follow_relay(b["relay_url"])
    assert notes.get_meta("profile_keys_sent", "") == ""
    sync.sync_once(b)
    assert srv.store.get_profile()["data"]["api_key"] == "gsk_pc"


def test_keys_switched_off_in_the_same_save_as_a_rewritten_relay_address_still_leave_the_relay(dev, srv):
    a = dev("A")
    set_cfg(api_key="gsk_secret", relay_sync_keys=True)
    sync.sync_once(a)
    assert notes.get_meta("profile_keys_sent") == "1"
    notes.set_meta("relay_origin", "http://old-spelling.example:8765")   # the same relay under another address
    set_cfg(relay_sync_keys=False)
    sync.sync_once(a)
    assert "api_key" not in srv.store.get_profile()["data"]
    assert notes.get_meta("profile_keys_sent", "") == ""                 # and once they are gone the flag goes too


def test_a_relay_change_keeps_the_keys_flag_and_resets_the_rest(dev, srv):
    a = dev("A")
    set_cfg(api_key="gsk_secret", relay_sync_keys=True)
    sync.sync_once(a)
    notes.set_meta("relay_origin", "http://old-spelling.example:8765")
    sync.follow_relay(a["relay_url"])
    assert notes.get_meta("profile_keys_sent") == "1"
    assert notes.get_meta("profile_version") == "0"                      # the rest of the state is still reset


def test_the_learned_cleanup_rules_travel_but_their_versions_stay_on_the_device(dev, srv):
    a = dev("A")
    set_cfg(my_cleanup_rules="Write Atlas, not atlas.", my_cleanup_rules_versions=[{"t": 1, "rules": "", "added": []}])
    assert sync.sync_once(a)["profile"] == "sent"
    data = srv.store.get_profile()["data"]
    assert data["my_cleanup_rules"] == "Write Atlas, not atlas." and "my_cleanup_rules_versions" not in data
    b = dev("B")
    assert sync.sync_once(b)["profile"] == "received"
    c = cfg_now()
    assert c["my_cleanup_rules"] == "Write Atlas, not atlas." and c["my_cleanup_rules_versions"] == []


def test_a_first_sync_against_blank_phone_defaults_keeps_the_pcs_settings(dev, srv):
    # the phone synced first: the relay holds only its blank defaults (version 1). The PC has never synced.
    srv.store.put_profile({"user_context": "", "dictionary": [], "people": []}, "0")
    a = dev("PC")
    set_cfg(user_context="I am Y", dictionary=["Vox"])
    assert sync.sync_once(a)["profile"] == "sent"
    c = cfg_now()
    assert c["user_context"] == "I am Y" and c["dictionary"] == ["Vox"]
    data = srv.store.get_profile()["data"]
    assert data["user_context"] == "I am Y" and data["dictionary"] == ["Vox"]


# ------------------------------------------------------------------ an unreadable config.json is not the user's settings
def _unreadable(monkeypatch):
    """Every open of config.json fails (another process holds it); returns the undo for it."""
    import os
    path = os.path.abspath(core.config_path())
    real_open = open

    def fake(file, *a, **kw):
        if os.path.abspath(str(file)) == path:
            raise PermissionError(13, "sharing violation")
        return real_open(file, *a, **kw)

    ctx = monkeypatch.context()
    m = ctx.__enter__()
    m.setattr("builtins.open", fake)
    m.setattr(core, "_OPEN_PAUSE", 0)
    return lambda: ctx.__exit__(None, None, None), path


def test_config_is_fallback_only_after_a_load_that_fell_back(dev, monkeypatch):
    dev("A")
    set_cfg(user_context="mine")
    assert core.config_is_fallback() is False
    undo, _ = _unreadable(monkeypatch)
    core.load_config()
    assert core.config_is_fallback() is True
    undo()
    assert core.load_config()["user_context"] == "mine"
    assert core.config_is_fallback() is False


def test_a_profile_sync_with_a_fallback_config_pushes_and_writes_nothing(dev, srv, monkeypatch):
    a = dev("A")
    set_cfg(user_context="I lead Atlas.", dictionary=["Atlas"], people=["Ada"])
    assert sync.sync_once(a)["profile"] == "sent"
    b = dev("B")
    sync.sync_once(b)                                            # B takes A's settings, then edits one
    set_cfg(dictionary=["Atlas", "Northwind"])
    sync.sync_once(b)                                            # the relay is now at version 2
    a = dev("A")
    meta_before = (notes.get_meta("profile_version", ""), notes.get_meta("profile_snapshot", ""))
    undo, path = _unreadable(monkeypatch)
    out = sync.sync_once(a)
    undo()
    assert out["profile"] == sync.PROFILE_SKIPPED and "unreadable" in sync.PROFILE_SKIPPED
    prof = srv.store.get_profile()
    assert prof["version"] == 2 and prof["data"]["user_context"] == "I lead Atlas."   # nothing pushed, nothing wiped
    assert prof["data"]["dictionary"] == ["Atlas", "Northwind"]
    assert (notes.get_meta("profile_version", ""), notes.get_meta("profile_snapshot", "")) == meta_before
    import glob
    assert not glob.glob(path + ".bad-*")
    c = cfg_now()                                                # the file was never touched: still A's own settings
    assert c["user_context"] == "I lead Atlas." and c["dictionary"] == ["Atlas"]


def test_the_profile_sync_works_again_once_the_config_has_been_read(dev, srv, monkeypatch):
    a = dev("A")
    set_cfg(user_context="I lead Atlas.")
    undo, _ = _unreadable(monkeypatch)
    assert sync.sync_once(a)["profile"] == sync.PROFILE_SKIPPED
    undo()
    assert srv.store.get_profile()["version"] == 0
    assert sync.sync_once(a)["profile"] == "sent"
    assert srv.store.get_profile()["data"]["user_context"] == "I lead Atlas."


def test_a_new_relay_address_is_not_recorded_while_the_config_is_unreadable(dev, srv, monkeypatch):
    a = dev("A")
    set_cfg(relay_sync_keys=True)
    undo, _ = _unreadable(monkeypatch)
    sync.sync_once(a)
    undo()
    assert notes.get_meta("relay_origin", "") == ""              # follow_relay would have lost the keys flag; tried again later
    sync.sync_once(a)
    assert notes.get_meta("relay_origin", "") != "" and notes.get_meta("profile_keys_sent", "") == "1"
