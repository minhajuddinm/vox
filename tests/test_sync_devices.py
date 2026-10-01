"""The Devices card: sync.fetch_devices / devices_for_ui against a real relay on localhost, plus the view's edge cases.

The view's rows (state, "this device", "5 min ago") are also pinned by the `devices` rows in spec/golden.txt, which the
Android DevicesView runs too; the tests here cover what that file cannot express (types, the bridge, the old relay).
"""
import threading
import time

import pytest

import relay
import sync


@pytest.fixture
def srv(tmp_path):
    server = relay.make_server(str(tmp_path / "relay"), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def cfg_for(srv, name="Laptop", **extra):
    return dict({"relay_url": f"http://127.0.0.1:{srv.server_address[1]}", "relay_token": srv.token, "device_name": name}, **extra)


# ------------------------------------------------------------------ fetch_devices

def test_fetch_devices_lists_this_device_and_the_others(srv):
    srv.store.touch_device("Pixel 7", "me@example.com")
    got = sync.fetch_devices(cfg_for(srv, "Laptop"))
    assert sorted(d["name"] for d in got) == ["Laptop", "Pixel 7"]
    assert all(set(d) == {"name", "first_seen", "last_seen", "requests", "login"} for d in got)


@pytest.mark.parametrize("name", ["Yuvraj’s PC", "युवराज", "Desk 😀"])
def test_a_device_name_outside_latin_1_does_not_break_the_relay_calls(srv, name):
    """http.client sends a header as latin-1: the header carries the Android spelling (? for every other character)."""
    got = sync.fetch_devices(cfg_for(srv, name))
    assert [d["name"] for d in got] == [sync._ascii_name(name)]
    assert sync.test_relay(f"http://127.0.0.1:{srv.server_address[1]}", srv.token, name)["ok"]
    assert sync.devices_for_ui(cfg_for(srv, name))["devices"][0]["this"]   # the row is still marked as this device


def test_fetch_devices_works_with_sync_switched_off(srv):
    """The card needs only the saved address and token, not the notes switch."""
    assert sync.fetch_devices(cfg_for(srv, relay_sync=False))[0]["name"] == "Laptop"


def test_fetch_devices_needs_an_address_and_a_token(srv):
    for bad in ({"relay_url": "", "relay_token": "t"}, {"relay_url": "http://127.0.0.1:9", "relay_token": " "}):
        with pytest.raises(sync.SyncError):
            sync.fetch_devices(bad)


def test_fetch_devices_names_a_wrong_token(srv):
    with pytest.raises(sync.SyncError) as e:
        sync.fetch_devices(cfg_for(srv, relay_token="wrong"))
    assert e.value.status == 401 and "token" in str(e.value)


def test_fetch_devices_says_tailscale_when_the_relay_cannot_be_reached():
    with pytest.raises(sync.SyncError) as e:
        sync.fetch_devices({"relay_url": "http://127.0.0.1:1", "relay_token": "t", "device_name": "x"})
    assert "Cannot reach the relay (is Tailscale running?)" in str(e.value)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def answer(monkeypatch, status, body):
    monkeypatch.setattr(sync._session, "request", lambda *a, **k: FakeResponse(status, body))


CFG = {"relay_url": "http://127.0.0.1:9", "relay_token": "t", "device_name": "x"}


def test_fetch_devices_explains_a_relay_that_is_too_old(monkeypatch):
    answer(monkeypatch, 404, {"error": "not found"})
    with pytest.raises(sync.SyncError) as e:
        sync.fetch_devices(CFG)
    assert "too old" in str(e.value) and e.value.status == 404


@pytest.mark.parametrize("body", [[], {}, {"devices": "x"}, {"devices": [1]}, {"devices": None}])
def test_fetch_devices_refuses_an_answer_that_is_not_a_device_list(monkeypatch, body):
    answer(monkeypatch, 200, body)
    with pytest.raises(sync.SyncError) as e:
        sync.fetch_devices(CFG)
    assert "did not answer like a Vox relay" in str(e.value)


# ------------------------------------------------------------------ the bridge's answer

def test_devices_for_ui_gives_rows_and_marks_this_device(srv):
    srv.store.touch_device("Pixel 7", "")
    res = sync.devices_for_ui(cfg_for(srv, "Laptop"))
    assert res["ok"] is True and res["error"] == ""
    by_name = {r["name"]: r for r in res["devices"]}
    assert by_name["Laptop"]["this"] is True and by_name["Pixel 7"]["this"] is False
    assert by_name["Laptop"]["state"] == "active" and by_name["Laptop"]["ago"] == "just now"
    assert all(set(r) == {"name", "this", "state", "ago"} for r in res["devices"])   # nothing else leaves the view (no login, no counts)


def test_devices_for_ui_returns_an_empty_list_and_the_reason_on_failure():
    res = sync.devices_for_ui({"relay_url": "http://127.0.0.1:1", "relay_token": "t", "device_name": "x"})
    assert res["ok"] is False and res["devices"] == []
    assert "Cannot reach the relay (is Tailscale running?)" in res["error"]


def test_devices_for_ui_never_raises(monkeypatch):
    def boom(cfg):
        raise ValueError("unexpected")
    monkeypatch.setattr(sync, "fetch_devices", boom)
    res = sync.devices_for_ui(CFG)
    assert res == {"ok": False, "error": "The device list could not be read.", "devices": []}


# ------------------------------------------------------------------ the view

NOW = 1_000_000.0


def view(devices, me="me", now=NOW):
    return sync.devices_view(devices, now, me)


def test_view_keeps_the_relays_order_and_reads_only_name_and_last_seen():
    rows = view([{"name": "b", "last_seen": NOW - 5, "login": "x@y", "requests": 3}, {"name": "a", "last_seen": NOW - 4000}])
    assert [r["name"] for r in rows] == ["b", "a"]
    assert [r["state"] for r in rows] == ["active", "recent"]
    assert all(set(r) == {"name", "this", "state", "ago"} for r in rows)


@pytest.mark.parametrize("seen", [None, "yesterday", float("nan"), float("inf"), True, [], {}])
def test_view_treats_a_last_seen_that_is_not_a_usable_number_as_never(seen):
    assert view([{"name": "d", "last_seen": seen}]) == [{"name": "d", "this": False, "state": "old", "ago": "never"}]


def test_view_survives_rows_without_a_name_or_that_are_not_objects():
    rows = view([{"last_seen": NOW}, {"name": None, "last_seen": NOW}, "junk", 5, None])
    assert [r["name"] for r in rows] == ["Unknown device"] * 2   # junk entries are skipped


def test_view_with_no_devices_is_empty():
    assert view([]) == [] and view(None) == []


def test_view_uses_the_clock_it_is_given_not_the_current_one():
    assert view([{"name": "d", "last_seen": 100}], now=100)[0]["ago"] == "just now"
    assert view([{"name": "d", "last_seen": time.time()}], now=time.time() + 4000)[0]["state"] == "recent"
