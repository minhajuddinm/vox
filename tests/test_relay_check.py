"""Test connection: sync.test_relay / relay_check against a real relay on localhost, the bridge's answer, and the edge cases.

The decision from status and answer (ok, reachable, token_ok, version, notes) is also pinned by the `relaycheck` rows in
spec/golden.txt, which the Android RelayCheck runs too; the tests here cover what that file cannot express (the real relay,
the sent device name, the bridge, no request for unusable settings).
"""
import sys
import threading
from unittest.mock import MagicMock

import pytest

import relay
import sync

FIELDS = {"ok", "reachable", "token_ok", "device_name", "relay_version", "notes", "message"}


@pytest.fixture
def srv(tmp_path):
    server = relay.make_server(str(tmp_path / "relay"), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def url_of(srv):
    return f"http://127.0.0.1:{srv.server_address[1]}"


# ------------------------------------------------------------------ against a real relay

def test_a_good_test_reports_the_relay_version_the_token_and_the_device_name(srv):
    r = sync.test_relay(url_of(srv), srv.token, "Laptop")
    assert set(r) == FIELDS
    assert r["ok"] is True and r["reachable"] is True and r["token_ok"] is True
    assert r["relay_version"] == relay.RELAY_VERSION and r["device_name"] == "Laptop" and r["notes"] == 0
    assert r["message"] == "Connected. The relay holds 0 notes."   # the words the button always showed


def test_the_notes_count_is_the_relays(srv):
    srv.store.upsert_note({"id": "a" * 32, "text": "hello", "created_at": 1.0, "updated_at": 1.0}, "a" * 32)
    r = sync.test_relay(url_of(srv), srv.token, "Laptop")
    assert r["notes"] == 1 and r["message"] == "Connected. The relay holds 1 notes."


def test_the_test_makes_this_device_appear_in_the_relays_list(srv):
    sync.test_relay(url_of(srv), srv.token, "Brand new laptop")
    assert "Brand new laptop" in [d["name"] for d in srv.store.devices()]


def test_a_wrong_token_is_reachable_but_not_ok(srv):
    r = sync.test_relay(url_of(srv), "nope", "Laptop")
    assert (r["ok"], r["reachable"], r["token_ok"]) == (False, True, False)
    assert r["message"] == "The relay refused the token." and r["relay_version"] == "" and r["notes"] == 0


def test_a_relay_that_belongs_to_another_tailnet_user_took_the_token(srv):
    srv.owner = "someone@example.com"   # the relay checks the token first, then the Tailscale login header
    r = sync.test_relay(url_of(srv), srv.token, "Laptop")
    assert (r["ok"], r["reachable"], r["token_ok"]) == (False, True, True)
    assert r["message"] == "The relay belongs to another Tailscale user."


def test_nothing_answering_is_not_reachable_and_says_tailscale():
    r = sync.test_relay("http://127.0.0.1:1", "t", "Laptop")
    assert (r["ok"], r["reachable"], r["token_ok"]) == (False, False, False)
    assert "Cannot reach the relay (is Tailscale running?)" in r["message"]


def test_the_token_never_appears_in_any_field(srv):
    for r in (sync.test_relay(url_of(srv), srv.token, "Laptop"), sync.test_relay(url_of(srv), "secret-wrong-token", "Laptop")):
        assert srv.token not in repr(r) and "secret-wrong-token" not in repr(r)


# ------------------------------------------------------------------ no request, odd answers

@pytest.mark.parametrize("url,token,words", [("", "t", "address"), ("http://127.0.0.1:9", "", "token"), ("ftp://x", "t", "http://")])
def test_unusable_settings_make_no_request(monkeypatch, url, token, words):
    monkeypatch.setattr(sync._session, "request", lambda *a, **k: pytest.fail("a request was made"))
    r = sync.test_relay(url, token, "Laptop")
    assert set(r) == FIELDS and r["ok"] is False and r["reachable"] is False and words in r["message"]


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def answer(monkeypatch, status, body):
    monkeypatch.setattr(sync._session, "request", lambda *a, **k: FakeResponse(status, body))


@pytest.mark.parametrize("body", [ValueError("not json"), [], "text", None, {"ok": False}, {"ok": "yes"}, {"version": "0.2"}])
def test_an_answer_that_is_not_a_relays_is_not_reachable(monkeypatch, body):
    answer(monkeypatch, 200, body)
    r = sync.test_relay("http://127.0.0.1:9", "t", "Laptop")
    assert (r["ok"], r["reachable"], r["token_ok"], r["relay_version"]) == (False, False, False, "")
    assert r["message"] == "That address did not answer like a Vox relay."


def test_an_unhealthy_relay_is_not_reachable_and_names_the_status(monkeypatch):
    answer(monkeypatch, 500, {"error": "boom"})
    r = sync.test_relay("http://127.0.0.1:9", "t", "Laptop")
    assert (r["ok"], r["reachable"]) == (False, False) and r["message"] == "The relay answered HTTP 500."


def test_a_version_that_could_carry_markup_is_dropped(monkeypatch):
    answer(monkeypatch, 200, {"ok": True, "version": "<img src=x onerror=a()>", "notes": 2})
    r = sync.test_relay("http://127.0.0.1:9", "t", "Laptop")
    assert r["ok"] is True and r["relay_version"] == "" and r["notes"] == 2


# ------------------------------------------------------------------ the bridge

@pytest.fixture
def ui_app(monkeypatch):
    """windows/ui_app.py imports pyperclip and webview at the top; CI's test job has neither. Stub whichever is missing for
    this test only (monkeypatch undoes it), and never leave a stub-bound ui_app module for other tests."""
    for name in ("pyperclip", "webview"):
        try:
            __import__(name)
        except ImportError:
            monkeypatch.setitem(sys.modules, name, MagicMock())
    had = "ui_app" in sys.modules
    import ui_app as module
    yield module
    if not had:
        sys.modules.pop("ui_app", None)


def test_the_windows_bridge_passes_the_whole_answer_on(srv, monkeypatch, tmp_path, ui_app):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    api = ui_app.Api.__new__(ui_app.Api)   # the bridge method needs no engine
    r = api.sync_test(url_of(srv), srv.token)
    assert set(r) == FIELDS and r["ok"] is True and r["relay_version"] == relay.RELAY_VERSION
    assert r["device_name"] == sync.device_name({})   # no device name saved: the computer name, as on the notes


def test_the_windows_bridge_never_raises(monkeypatch, tmp_path, ui_app):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(sync, "test_relay", lambda *a: (_ for _ in ()).throw(ValueError("x")))
    api = ui_app.Api.__new__(ui_app.Api)
    r = api.sync_test("http://127.0.0.1:9", "t")
    assert set(r) == FIELDS and r["ok"] is False and r["message"] == "The test could not run."
