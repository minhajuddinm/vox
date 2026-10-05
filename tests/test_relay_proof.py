"""bf-e SEC-2: the apps send the relay token only to a relay that has proved it holds it (GET /proof, HMAC of a nonce).

A program squatting on the relay's port while the relay is down (tailscale serve forwards to whatever listens there)
must receive no token: not from the background sync, the Test connection button, the device list, nor from dictation
through the relay as the AI server. An older relay without /proof is still used, with a warning, until the address
has once proved itself; after that a missing proof is refused."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("requests")
import relay
import sync
import vox_core as core

pytestmark = pytest.mark.real_relay_proof


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(sync, "_proofs", {})


class Squatter(ThreadingHTTPServer):
    """Answers like a relay, records every Authorization header it gets. mode: "wrong" (a proof that does not match),
    "old" (no /proof: 401 like a relay from before it), "silent" (proof route answers 404)."""
    daemon_threads = True

    def __init__(self, mode):
        super().__init__(("127.0.0.1", 0), _SquatHandler)
        self.mode, self.seen = mode, []

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server_address[1]


class _SquatHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _answer(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _any(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self.server.seen.append((self.command, self.path.split("?")[0], self.headers.get("Authorization")))
        if self.path.startswith("/proof"):
            if self.server.mode == "wrong":
                return self._answer(200, {"proof": "0" * 64})
            if self.server.mode == "old":
                return self._answer(401, {"error": "missing or wrong token"})
            return self._answer(404, {"error": "unknown request"})
        return self._answer(200, {"ok": True, "notes": 0, "seq": 0, "version": 0, "data": {}, "devices": [],
                                  "next": 0, "more": False, "text": "squatted", "data_list": []})

    do_GET = do_PUT = do_POST = _any


def start(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def squatter(request):
    srv = start(Squatter(getattr(request, "param", "wrong")))
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def real(tmp_path):
    srv = start(relay.make_server(str(tmp_path / "relay"), port=0))
    yield srv
    srv.shutdown()
    srv.server_close()


def tokens(srv):
    return [a for _, path, a in srv.seen if a]


def sync_cfg(url, token="REAL-TOKEN-A"):
    return {"relay_sync": True, "relay_url": url, "relay_token": token, "device_name": "pc"}


# ------------------------------------------------------------------ the real relay proves itself
def test_a_real_relay_proves_itself_and_the_sync_runs(real):
    url = "http://127.0.0.1:%d" % real.server_address[1]
    assert sync.prove_relay(url, real.token) == "proven"
    assert sync.test_relay(url, real.token)["message"] == "Connected. The relay holds 0 notes."
    assert sync.sync_once(sync_cfg(url, real.token))["error"] == ""


def test_a_wrong_token_against_the_real_relay_is_not_sent_either(real):
    url = "http://127.0.0.1:%d" % real.server_address[1]
    out = sync.test_relay(url, "not-the-token")
    assert not out["ok"] and out["message"] == sync.NOT_PROVEN
    assert real.status()["requests"]["auth_failures"] == 0      # the wrong token never reached the relay


def test_a_proof_is_kept_for_a_while_and_asked_again_after_a_connection_failure(real, monkeypatch):
    url = "http://127.0.0.1:%d" % real.server_address[1]
    asked = []
    real_get = sync._session.get
    monkeypatch.setattr(sync._session, "get", lambda u, **kw: asked.append(u) or real_get(u, **kw))
    for _ in range(3):
        sync.test_relay(url, real.token)
    assert len(asked) == 1
    sync.forget_proof(url)
    sync.test_relay(url, real.token)
    assert len(asked) == 2


# ------------------------------------------------------------------ a squatter gets nothing
def test_a_squatter_with_a_wrong_proof_gets_no_token_from_any_relay_call(squatter):
    out = sync.test_relay(squatter.url, "REAL-TOKEN-A")
    assert not out["ok"] and out["message"] == sync.NOT_PROVEN
    assert "token was not sent" in sync.sync_once(sync_cfg(squatter.url))["error"]
    assert sync.devices_for_ui(sync_cfg(squatter.url))["ok"] is False
    assert tokens(squatter) == [] and all(path == "/proof" for _, path, _ in squatter.seen)


@pytest.mark.parametrize("squatter", ["old"], indirect=True)
def test_an_old_relay_is_still_used_with_a_warning_until_its_address_has_proved_itself(squatter):
    out = sync.test_relay(squatter.url, "REAL-TOKEN-A")
    assert out["ok"] and sync.OLD_RELAY in out["message"]
    assert tokens(squatter)                                     # the fallback: an old relay cannot prove anything
    import notes
    notes.set_meta("relay_proven", json.dumps([sync.origin_of(squatter.url)]))   # this address proved itself before
    squatter.seen.clear()
    sync.forget_proof(squatter.url)
    out = sync.test_relay(squatter.url, "REAL-TOKEN-A")
    assert not out["ok"] and out["message"] == sync.NO_LONGER and tokens(squatter) == []


def test_proving_an_address_pins_it(real):
    import notes
    url = "http://127.0.0.1:%d" % real.server_address[1]
    sync.prove_relay(url, real.token)
    assert sync.origin_of(url) in json.loads(notes.get_meta("relay_proven", "[]"))


# ------------------------------------------------------------------ the relay as the AI server
def proxy_cfg(url):
    return dict(core.DEFAULT_CONFIG, relay_proxy=True, relay_url=url, relay_token="REAL-TOKEN-A")


@pytest.mark.real_session
def test_dictation_through_a_squatted_relay_sends_no_token(squatter):
    cfg = proxy_cfg(squatter.url)
    with pytest.raises(core.ApiError) as e:
        core.transcribe(cfg, b"RIFF" + b"\0" * 100)
    assert sync.NOT_PROVEN in str(e.value)
    with pytest.raises(core.ApiError):
        core.cleanup(cfg, "some raw words to tidy", "neutral", "")
    core.warm(cfg).join(5)
    assert providers_errors(cfg)
    assert tokens(squatter) == []


def providers_errors(cfg):
    import providers
    listed = providers.list_models(cfg, "llm")
    tested = providers.test(cfg, "llm")
    return listed["error"] == sync.NOT_PROVEN and not tested["ok"] and tested["message"] == sync.NOT_PROVEN


@pytest.mark.real_session
def test_dictation_through_the_real_relay_still_works(real, tmp_path):
    from test_relay_proxy import Stub
    stub = start(Stub())
    try:
        real.set_upstream("stt", stub.url, "")
        cfg = proxy_cfg("http://127.0.0.1:%d" % real.server_address[1])
        cfg["relay_token"] = real.token
        assert core.transcribe(cfg, b"RIFF" + bytes(range(256)) * 4) == "from the stub"
    finally:
        stub.shutdown()
        stub.server_close()


def test_without_the_marker_tests_skip_the_proof(request):
    assert request.node.get_closest_marker("real_relay_proof")   # this file opts in; conftest stubs it for the others
