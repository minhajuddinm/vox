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
        self.mode, self.seen, self.sent = mode, [], 0

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
            if self.server.mode == "huge":   # a proof answer that does not end: 64 MB
                self.send_response(200)
                self.send_header("Content-Length", str(64 << 20))
                self.end_headers()
                try:
                    for _ in range(64):
                        self.wfile.write(b"x" * (1 << 20))
                        self.server.sent += 1 << 20
                except OSError:
                    pass   # the client stopped reading
                return
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


# ------------------------------------------------------------------ final review RC-I1: the relay stops, a squatter takes the port
def _age_proofs(seconds):
    for k, (t, result) in list(sync._proofs.items()):
        sync._proofs[k] = (t - seconds, result)


class Gateway(Squatter):
    """tailscale serve while the relay behind it is stopped: 502 for everything, and it records what it gets."""
    def __init__(self, port):
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", port), _GatewayHandler)
        self.mode, self.seen = "gateway", []


class _GatewayHandler(_SquatHandler):
    def _any(self):
        self.server.seen.append((self.command, self.path.split("?")[0], self.headers.get("Authorization")))
        self._answer(502, {"error": "upstream down"})

    do_GET = do_PUT = do_POST = _any


def test_a_proof_is_trusted_for_seconds_not_minutes():
    assert sync.PROOF_TTL <= 10


def test_a_squatter_on_the_stopped_relays_port_gets_no_token_once_the_short_proof_ran_out(tmp_path):
    real = start(relay.make_server(str(tmp_path / "relay"), port=0))
    port = real.server_address[1]
    url = "http://127.0.0.1:%d" % port
    assert sync.prove_relay(url, real.token) == "proven"
    real.shutdown()
    real.server_close()
    sq = Squatter("wrong")
    ThreadingHTTPServer.__init__(sq, ("127.0.0.1", port), _SquatHandler)   # takes the port at once: no failed connection
    start(sq)
    try:
        _age_proofs(sync.PROOF_TTL + 1)
        assert "token was not sent" in sync.sync_once(sync_cfg(url, real.token))["error"]
        assert tokens(sq) == []
    finally:
        sq.shutdown()
        sq.server_close()


def test_a_502_from_tailscale_serve_forgets_the_proof(tmp_path):
    real = start(relay.make_server(str(tmp_path / "relay"), port=0))
    port = real.server_address[1]
    url = "http://127.0.0.1:%d" % port
    sync.prove_relay(url, real.token)
    real.shutdown()
    real.server_close()
    gw = start(Gateway(port))
    try:
        with pytest.raises(sync.SyncError):
            sync._request("GET", url, "/changes?since=0", real.token, "pc")
        assert not [k for k in sync._proofs if k[0] == sync.origin_of(url)]   # the next request proves again
    finally:
        gw.shutdown()
        gw.server_close()


@pytest.mark.real_session
def test_dictation_through_the_relay_proves_again_before_it_sends_again(tmp_path, monkeypatch):
    """A retry after a 502 or a dropped connection proves again first, so a squatter that took the port meanwhile gets no
    token and no audio."""
    real = start(relay.make_server(str(tmp_path / "relay"), port=0))
    port = real.server_address[1]
    url = "http://127.0.0.1:%d" % port
    sync.prove_relay(url, real.token)
    real.shutdown()
    real.server_close()
    gw = start(Gateway(port))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    try:
        with pytest.raises(core.ApiError):
            core.post_with_retry(url + "/proxy/stt/audio/transcriptions", via_relay=True, data=b"audio",
                                 headers={"Authorization": "Bearer " + real.token})
        assert [a for _, path, a in gw.seen if not path.startswith("/proof")] == ["Bearer " + real.token]   # the 1st try only
        assert [path for _, path, _ in gw.seen][1:] and all(path == "/proof" for _, path, _ in gw.seen[1:])
    finally:
        gw.shutdown()
        gw.server_close()


def test_a_proof_request_that_got_no_answer_is_tried_again(monkeypatch):
    """Final review W-M1: a Tailscale blip on the /proof GET does not cost the dictation at once."""
    calls = []

    def prove(url, token):
        calls.append(url)
        if len(calls) == 1:
            raise sync.RelayUnreachable("Cannot reach the relay (is Tailscale running?): ConnectionError")
        return "proven"
    monkeypatch.setattr(sync, "prove_relay", prove)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    monkeypatch.setattr(core, "_post", lambda url, **kw: type("R", (), {"status_code": 200})())
    assert core.post_with_retry("http://127.0.0.1:9/proxy/stt/x", via_relay=True,
                                headers={"Authorization": "Bearer t"}).status_code == 200
    assert len(calls) == 2


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
    # fix wave (review 5): the warning about a possible squatter never says how to lift the pin (clearing the token)
    assert "update it" in sync.NO_LONGER and not any(w in sync.NO_LONGER.lower() for w in ("clear", "enter it", "settings"))
    # leftovers: an older relay put back at the address works again once the user changes its address or token
    sync.unpin(squatter.url + "/")
    assert sync.origin_of(squatter.url) not in json.loads(notes.get_meta("relay_proven", "[]"))
    out = sync.test_relay(squatter.url, "REAL-TOKEN-A")
    assert out["ok"] and sync.OLD_RELAY in out["message"]
    sync.unpin("")   # no address: nothing to do


@pytest.mark.parametrize("squatter", ["huge"], indirect=True)
def test_a_proof_answer_that_does_not_end_is_cut_off_and_gets_no_token(squatter):
    # review 11: the /proof answer was read whole, whatever its size
    import time
    out = sync.test_relay(squatter.url, "REAL-TOKEN-A")
    assert not out["ok"] and out["message"] == sync.NOT_PROVEN and tokens(squatter) == []
    time.sleep(0.5)   # the squatter's last writes fail once the connection is dropped
    assert squatter.sent < 32 << 20, squatter.sent   # far from the 64 MB it offered


@pytest.mark.parametrize("squatter", ["silent"], indirect=True)
def test_a_404_on_proof_is_not_taken_for_an_old_relay(squatter):
    """Final review RC-M3: an old relay checks the token first (401); a 404 is a wrong address or path, and gets nothing."""
    out = sync.test_relay(squatter.url, "REAL-TOKEN-A")
    assert not out["ok"] and "404" in out["message"] and tokens(squatter) == []


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
