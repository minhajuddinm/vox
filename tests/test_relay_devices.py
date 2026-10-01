"""GET /devices: the devices that have used the relay, for the Devices card in both apps."""
import http.client
import json
import threading
import time

import pytest

import relay


@pytest.fixture
def server(tmp_path):
    srv = relay.make_server(str(tmp_path), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, token=True, headers=None, body=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + (srv.token if token is True else token)
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data else None)


def test_devices_needs_the_token(server):
    server.store.touch_device("pixel", "me@example.com")
    for tok in (False, "wrong"):
        status, body = call(server, "GET", "/devices", token=tok)
        assert status == 401 and "devices" not in body


def test_a_device_appears_after_a_request_with_its_name(server):
    call(server, "GET", "/health", headers={"X-Vox-Device": "pixel"})
    status, body = call(server, "GET", "/devices")
    assert status == 200 and list(body) == ["devices"]
    assert [d["name"] for d in body["devices"]] == ["pixel"]   # the asking call itself carried no name


def test_the_asking_device_is_listed_with_its_own_counts(server):
    h = {"X-Vox-Device": "pixel", "Tailscale-User-Login": "me@example.com"}
    status, body = call(server, "GET", "/devices", headers=h)
    assert status == 200
    d = body["devices"][0]
    assert set(d) == {"name", "first_seen", "last_seen", "requests", "login"}
    assert d["name"] == "pixel" and d["requests"] == 1 and d["login"] == "me@example.com"
    assert abs(d["last_seen"] - time.time()) < 30 and d["first_seen"] <= d["last_seen"]


def test_devices_are_newest_first_and_carry_no_events(server):
    server.store.touch_device("old", "")
    time.sleep(0.02)
    server.store.touch_device("mid", "")
    time.sleep(0.02)
    status, body = call(server, "GET", "/devices", headers={"X-Vox-Device": "new"})
    assert [d["name"] for d in body["devices"]] == ["new", "mid", "old"]
    text = json.dumps(body)
    assert "route" not in text and "events" not in text and "/devices" not in text


def test_no_other_field_than_the_management_page_shows(server):
    server.store.touch_device("pc", "me@example.com")
    _, mine = call(server, "GET", "/devices")
    _, admin = call(server, "GET", "/admin/activity")
    assert mine["devices"] == admin["devices"]


def test_names_are_returned_as_stored_and_never_run_as_markup(server):
    call(server, "GET", "/health", headers={"X-Vox-Device": "  <b>Yuvi's phone</b> "})
    _, body = call(server, "GET", "/devices")
    assert body["devices"][0]["name"] == "<b>Yuvi's phone</b>"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_other_methods_are_refused(server, method):
    status, _ = call(server, method, "/devices", body=b"{}" if method != "DELETE" else None)
    assert status in (404, 405)
    if method == "POST":
        assert status == 405


def test_the_owner_check_applies(tmp_path):
    srv = relay.make_server(str(tmp_path), port=0, owner="me@example.com")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert call(srv, "GET", "/devices")[0] == 403
        assert call(srv, "GET", "/devices", headers={"Tailscale-User-Login": "me@example.com"})[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()
