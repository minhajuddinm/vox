"""The relay's proxy mode: POST/GET /proxy/stt/... and /proxy/llm/... forward to the configured upstream servers.

A stub upstream (a real HTTP server on localhost, started in the test) records exactly what it receives, so the tests
can check the security rules from the outside: the upstream address is the configured base plus a fixed suffix, the
client's token never travels on, the upstream key never comes back, bodies and answers are capped, slots are limited."""
import http.client
import json
import logging
import os
import re
import socket
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import relay

STT_KEY = "sk-stt-UPSTREAM-SECRET-7f3a9c"
LLM_KEY = "sk-llm-UPSTREAM-SECRET-51d0be"
STT_PATH, STT_MODELS = "/proxy/stt/audio/transcriptions", "/proxy/stt/models"
LLM_PATH, LLM_MODELS = "/proxy/llm/chat/completions", "/proxy/llm/models"
# (method, relay path, role, the only upstream path it may ever produce)
ROUTES = [
    ("POST", STT_PATH, "stt", "/v1/audio/transcriptions"),
    ("GET", STT_MODELS, "stt", "/v1/models"),
    ("POST", LLM_PATH, "llm", "/v1/chat/completions"),
    ("GET", LLM_MODELS, "llm", "/v1/models"),
]
FIXED_UPSTREAM_PATHS = {r[3] for r in ROUTES}
BOUNDARY = b"----VoxBoundary7MA4YWxkTrZu0gW"


def route_id(r):
    return r[0] + " " + r[1]


# ------------------------------------------------------------------ the stub upstream
class Stub(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self.seen = []        # one dict per request that reached this server
        self.script = None    # callable(handler, record): answers itself instead of the default reply
        self._lock = threading.Lock()

    @property
    def url(self):
        return "http://127.0.0.1:%d/v1" % self.server_address[1]

    def handle_error(self, request, client_address):   # the relay hangs up on purpose in some tests
        pass


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status=200, body=b'{"text": "from the stub"}', headers=None):
        self.send_response(status)
        for k, v in dict({"Content-Type": "application/json"}, **(headers or {})).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        rec = {"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
               "names": [k.lower() for k in self.headers.keys()], "body": body}
        with self.server._lock:
            self.server.seen.append(rec)
        if self.server.script:
            return self.server.script(self, rec)
        self.reply()

    do_GET = do_POST = _handle


def start(srv):
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()   # so shutdown() is quick
    return srv


@pytest.fixture
def stubs():
    a, b = start(Stub()), start(Stub())
    yield a, b
    for s in (a, b):
        s.shutdown()
        s.server_close()


@pytest.fixture
def stt_stub(stubs):
    return stubs[0]


@pytest.fixture
def llm_stub(stubs):
    return stubs[1]


@pytest.fixture(autouse=True)
def quick_drain(monkeypatch):
    monkeypatch.setattr(relay, "DRAIN_IDLE", 0.3)   # how long an oversize upload may go quiet before we answer


@pytest.fixture
def server(tmp_path, stubs):
    """A relay whose two roles point at the two stubs, each with its own key."""
    srv = relay.make_server(str(tmp_path), port=0)
    srv.set_upstream("stt", stubs[0].url, STT_KEY)
    srv.set_upstream("llm", stubs[1].url, LLM_KEY)
    start(srv)
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def bare(tmp_path_factory):
    """A relay with no AI server configured at all (its own data folder, so it can sit next to `server`)."""
    srv = start(relay.make_server(str(tmp_path_factory.mktemp("bare")), port=0))
    yield srv
    srv.shutdown()
    srv.server_close()


# ------------------------------------------------------------------------- client helpers
class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body)

    def header(self, name):
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return None

    @property
    def raw(self):
        head = "%d\n%s\n" % (self.status, "\n".join("%s: %s" % kv for kv in self.headers))
        return head.encode("latin-1", "replace") + self.body


def call(srv, method, path, body=None, headers=None, token=True, timeout=10):
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + (srv.token if token is True else token)
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=timeout)
    try:
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        return Resp(r.status, r.getheaders(), r.read())
    finally:
        c.close()


def raw_call(srv, payload, timeout=10):
    """Sends exact bytes (for requests http.client would not build) and parses the answer."""
    s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=timeout)
    try:
        s.sendall(payload)
        r = http.client.HTTPResponse(s)
        r.begin()
        return Resp(r.status, r.getheaders(), r.read())
    finally:
        s.close()


def auth(srv):
    return ("Authorization: Bearer %s\r\n" % srv.token).encode()


def chat(text="hello"):
    return json.dumps({"model": "m", "messages": [{"role": "user", "content": text}]}).encode()


def multipart(payload=None):
    payload = payload if payload is not None else bytes(range(256)) * 20 + b"\r\n--" + BOUNDARY[:6] + b"\r\n\r\n\x00\xff"
    return (b"--" + BOUNDARY + b'\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper-large-v3-turbo\r\n'
            b"--" + BOUNDARY + b'\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n'
            + payload + b"\r\n--" + BOUNDARY + b"--\r\n")


def send_route(srv, route, **kw):
    method, path = route[0], route[1]
    body = None
    headers = dict(kw.pop("headers", None) or {})
    if method == "POST":
        if route[2] == "stt":
            body = multipart()
            headers.setdefault("Content-Type", "multipart/form-data; boundary=" + BOUNDARY.decode())
        else:
            body = chat()
            headers.setdefault("Content-Type", "application/json")
    return call(srv, method, path, body=body, headers=headers, **kw)


def free_slots(srv):
    """True when all proxy slots are free again (and leaves them free)."""
    got = 0
    while srv.proxy_slots.acquire(blocking=False):
        got += 1
    for _ in range(got):
        srv.proxy_slots.release()
    return got == relay.PROXY_SLOTS


def everything_seen(*stubs):
    return [r for s in stubs for r in s.seen]


# ================================================================= happy paths
@pytest.mark.parametrize("route", ROUTES, ids=route_id)
def test_each_route_forwards_to_its_fixed_upstream_url_and_returns_the_answer(server, stubs, route):
    method, path, role, upstream_path = route
    stub = stubs[0] if role == "stt" else stubs[1]
    key = STT_KEY if role == "stt" else LLM_KEY
    stub.script = lambda h, rec: h.reply(200, b'{"from": "upstream", "n": 42}', {"Content-Type": "application/json; charset=utf-8"})
    resp = send_route(server, route, headers={"Accept": "application/json"})
    assert resp.status == 200 and resp.json() == {"from": "upstream", "n": 42}
    assert resp.header("Content-Type") == "application/json; charset=utf-8"
    assert len(stub.seen) == 1
    rec = stub.seen[0]
    assert rec["method"] == method and rec["path"] == upstream_path       # base /v1 + the route's own suffix
    assert rec["headers"]["authorization"] == "Bearer " + key
    assert rec["headers"]["user-agent"] == "vox-relay" and rec["headers"]["accept"] == "application/json"
    assert rec["headers"]["host"] == "127.0.0.1:%d" % stub.server_address[1]
    if method == "POST":
        assert rec["headers"]["content-length"] == str(len(rec["body"])) and rec["body"]
    else:
        assert "content-length" not in rec["headers"] and rec["body"] == b""
    other = stubs[1] if role == "stt" else stubs[0]
    assert other.seen == []


@pytest.mark.parametrize("route", ROUTES, ids=route_id)
def test_only_a_short_list_of_headers_travels_upstream(server, stubs, route):
    """Everything else the client sent (token, device name, tailnet login, cookies, forwarding headers) stays behind."""
    send_route(server, route, headers={"Accept": "application/json", "X-Vox-Device": "pixel", "Tailscale-User-Login": "me@example.com",
                                       "Cookie": "sid=abc", "X-Forwarded-For": "1.2.3.4", "Proxy-Authorization": "Basic eDp5",
                                       "Accept-Encoding": "gzip", "X-Api-Key": "client-key", "Connection": "keep-alive"})
    rec = everything_seen(*stubs)[0]
    assert set(rec["names"]) <= {"host", "user-agent", "accept", "accept-encoding", "content-type", "content-length", "authorization"}, rec["names"]
    assert rec["headers"]["accept-encoding"] == "identity"
    assert rec["names"].count("authorization") == 1


def test_a_get_does_not_forward_a_content_type(server, llm_stub):
    call(server, "GET", LLM_MODELS, headers={"Content-Type": "application/json", "Accept": "*/*"})
    assert "content-type" not in llm_stub.seen[0]["headers"]


def test_the_multipart_body_and_boundary_arrive_byte_for_byte(server, stt_stub):
    body = multipart()
    ctype = "multipart/form-data; boundary=" + BOUNDARY.decode()
    resp = call(server, "POST", STT_PATH, body=body, headers={"Content-Type": ctype})
    assert resp.status == 200
    rec = stt_stub.seen[0]
    assert rec["body"] == body and rec["headers"]["content-type"] == ctype and rec["headers"]["content-length"] == str(len(body))


def test_a_json_body_arrives_byte_for_byte(server, llm_stub):
    body = chat("héllo ☃ wörld")
    call(server, "POST", LLM_PATH, body=body, headers={"Content-Type": "application/json"})
    assert llm_stub.seen[0]["body"] == body


def test_the_client_token_never_reaches_any_upstream(server, stubs):
    for route in ROUTES:
        send_route(server, route)
    for rec in everything_seen(*stubs):
        assert server.token not in json.dumps(rec["headers"]) and server.token.encode() not in rec["body"]


def test_each_role_key_goes_only_to_its_own_address(server, stubs):
    for route in ROUTES:
        send_route(server, route)
    stt_seen, llm_seen = stubs[0].seen, stubs[1].seen
    assert len(stt_seen) == 2 and len(llm_seen) == 2
    assert all(r["headers"]["authorization"] == "Bearer " + STT_KEY for r in stt_seen)
    assert all(r["headers"]["authorization"] == "Bearer " + LLM_KEY for r in llm_seen)
    assert LLM_KEY not in json.dumps([r["headers"] for r in stt_seen]) and STT_KEY not in json.dumps([r["headers"] for r in llm_seen])


def test_without_a_stored_key_no_authorization_header_is_sent_and_the_clients_is_not_used(server, llm_stub):
    server.set_upstream("llm", llm_stub.url, "")
    resp = send_route(server, ROUTES[2])
    assert resp.status == 200
    assert "authorization" not in llm_stub.seen[0]["headers"]


def test_the_upstream_address_keeps_its_path_prefix_and_gets_exactly_one_slash(server, llm_stub):
    server.set_upstream("llm", llm_stub.url.replace("/v1", "/openai/deep/v1"), LLM_KEY)
    send_route(server, ROUTES[2])
    send_route(server, ROUTES[3])
    assert [r["path"] for r in llm_stub.seen] == ["/openai/deep/v1/chat/completions", "/openai/deep/v1/models"]
    # a trailing slash from a hand-edited relay.json does not double up
    server.upstream = relay.upstream_settings({"llm": {"base_url": llm_stub.url + "/", "api_key": LLM_KEY}})
    send_route(server, ROUTES[3])
    assert llm_stub.seen[-1]["path"] == "/v1/models"
    # an address that is just a host has no prefix at all
    host_only = llm_stub.url[: -len("/v1")]
    server.upstream = relay.upstream_settings({"llm": {"base_url": host_only, "api_key": ""}})
    send_route(server, ROUTES[3])
    assert llm_stub.seen[-1]["path"] == "/models"


# ================================================================= the answer that comes back
def test_upstream_429_with_retry_after_is_passed_through_not_mistaken_for_busy(server, llm_stub):
    body = b'{"error": {"message": "rate limited, slow down"}}'
    llm_stub.script = lambda h, rec: h.reply(429, body, {"Retry-After": "7"})
    resp = send_route(server, ROUTES[2])
    assert resp.status == 429 and resp.body == body and resp.header("Retry-After") == "7"


def test_upstream_500_and_its_body_are_passed_through(server, stt_stub):
    stt_stub.script = lambda h, rec: h.reply(500, b"boom: the model fell over\n", {"Content-Type": "text/plain"})
    resp = send_route(server, ROUTES[0])
    assert resp.status == 500 and resp.body == b"boom: the model fell over\n" and resp.header("Content-Type") == "text/plain"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 503])
def test_other_upstream_statuses_are_passed_through(server, llm_stub, status):
    llm_stub.script = lambda h, rec: h.reply(status, json.dumps({"error": {"message": "nope %d" % status}}).encode())
    resp = send_route(server, ROUTES[2])
    assert resp.status == status and resp.json()["error"]["message"] == "nope %d" % status


def test_only_content_type_and_retry_after_come_back_from_the_upstream_headers(server, llm_stub):
    llm_stub.script = lambda h, rec: h.reply(200, b"{}", {"Set-Cookie": "sid=1", "X-Upstream-Debug": "hunter2", "Location": "http://evil.example/",
                                                          "WWW-Authenticate": "Bearer realm=x", "Retry-After": "3", "Content-Encoding": "identity"})
    resp = send_route(server, ROUTES[2])
    names = {k.lower() for k, _ in resp.headers}
    assert not names & {"set-cookie", "x-upstream-debug", "location", "www-authenticate", "content-encoding"}
    assert resp.header("Retry-After") == "3" and resp.header("Content-Type") == "application/json"


def test_proxied_answers_cannot_be_used_as_a_page_on_the_relays_origin(server, llm_stub):
    llm_stub.script = lambda h, rec: h.reply(200, b"<script>alert(1)</script>", {"Content-Type": "text/html"})
    resp = send_route(server, ROUTES[2])
    assert "sandbox" in resp.header("Content-Security-Policy") and resp.header("X-Content-Type-Options") == "nosniff"
    assert resp.header("Cache-Control") == "no-store"


def test_a_folded_upstream_header_value_is_not_copied(server, llm_stub):
    def script(h, rec):
        h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n X-Injected: 1\r\nRetry-After: 5\r\nContent-Length: 2\r\n\r\nhi")
    llm_stub.script = script
    resp = send_route(server, ROUTES[2])
    assert resp.status == 200 and resp.body == b"hi"
    assert resp.header("Content-Type") == "application/json" and resp.header("X-Injected") is None
    assert "\n" not in (resp.header("Content-Type") or "") and resp.header("Retry-After") == "5"


def test_the_key_is_hidden_when_an_upstream_error_echoes_it(server, stt_stub):
    stt_stub.script = lambda h, rec: h.reply(401, json.dumps({"error": {"message": "bad credentials: " + rec["headers"]["authorization"]}}).encode())
    resp = send_route(server, ROUTES[0])
    assert resp.status == 401 and STT_KEY.encode() not in resp.raw and b"bad credentials" in resp.body
    # a key with characters JSON escapes is hidden in its escaped form too
    odd = 'ab"cd\\efgh12345'
    server.set_upstream("stt", stt_stub.url, odd)
    stt_stub.script = lambda h, rec: h.reply(401, json.dumps({"echo": odd}).encode())
    resp = send_route(server, ROUTES[0])
    assert resp.status == 401 and odd.encode() not in resp.raw and json.dumps(odd)[1:-1].encode() not in resp.raw


def test_scrub_replaces_the_key_but_leaves_short_keys_alone():
    assert relay._scrub(b"a sk-abcdef123 b sk-abcdef123", "sk-abcdef123") == b"a *** b ***"
    assert relay._scrub(b"text with ollama in it", "ollama") == b"text with ollama in it"   # too short to tell from ordinary words
    assert relay._scrub(b"no key here", "") == b"no key here"


def test_an_upstream_redirect_is_not_followed_and_is_a_502(server, stubs):
    stubs[1].script = lambda h, rec: h.reply(302, b"", {"Location": stubs[0].url + "/stolen"})
    resp = send_route(server, ROUTES[2])
    assert resp.status == 502 and resp.json() == {"error": {"message": "upstream answered with a redirect"}}
    assert stubs[0].seen == []
    assert resp.header("Location") is None


# ================================================================= upstream trouble -> 502
def dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d/v1" % port


def hang_up(stub):
    """Makes a stub accept the request and close the connection without answering."""
    stub.script = lambda h, rec: setattr(h, "close_connection", True)


def test_an_upstream_with_nothing_listening_is_an_openai_shaped_502(server):
    server.set_upstream("stt", dead_url(), STT_KEY)       # (a refused connection takes about 2 s on Windows)
    resp = send_route(server, ROUTES[0])
    assert resp.status == 502 and resp.json() == {"error": {"message": "upstream unreachable"}}
    assert STT_KEY.encode() not in resp.raw and "127.0.0.1" not in resp.raw.decode("latin-1")   # not even the address
    assert free_slots(server)


@pytest.mark.parametrize("route", ROUTES, ids=route_id)
def test_an_upstream_that_hangs_up_is_the_same_502_on_every_route(server, stubs, route):
    hang_up(stubs[0] if route[2] == "stt" else stubs[1])
    resp = send_route(server, route)
    assert resp.status == 502 and resp.json() == {"error": {"message": "upstream unreachable"}}
    assert STT_KEY.encode() not in resp.raw and LLM_KEY.encode() not in resp.raw and free_slots(server)


def test_an_upstream_that_never_answers_times_out_as_a_502(server, stt_stub, monkeypatch):
    monkeypatch.setitem(relay.PROXY_TIMEOUT, "models", 0.4)
    stt_stub.script = lambda h, rec: time.sleep(2)
    t0 = time.monotonic()
    resp = call(server, "GET", STT_MODELS)
    assert resp.status == 502 and resp.json() == {"error": {"message": "upstream unreachable"}}
    assert time.monotonic() - t0 < 1.8 and free_slots(server)


def test_the_timeouts_are_180_240_and_15_seconds():
    assert relay.PROXY_TIMEOUT == {"stt": 180, "llm": 240, "models": 15}


def test_a_trickling_upstream_hits_the_total_deadline_not_just_the_per_read_one(server, llm_stub, monkeypatch):
    monkeypatch.setitem(relay.PROXY_TIMEOUT, "models", 0.6)

    def script(h, rec):
        h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n")
        h.wfile.flush()
        try:
            for _ in range(100):
                h.wfile.write(b"x")
                h.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass
    llm_stub.script = script
    t0 = time.monotonic()
    resp = call(server, "GET", LLM_MODELS)
    assert resp.status == 502 and time.monotonic() - t0 < 3
    assert free_slots(server)


# An upstream that keeps the exchange alive with a steady trickle of bytes that http.client takes for progress. Each
# script below misbehaves for 4 s; the relay must give up after the total deadline (0.5 s here) whatever it does.
def _for_four_seconds():
    end = time.monotonic() + 4
    while time.monotonic() < end:
        yield


def endless_continue(h, rec):
    """'100 Continue' over and over: http.client loops over them inside one getresponse() call."""
    try:
        for _ in _for_four_seconds():
            h.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
            time.sleep(0.05)
    except OSError:
        pass


def endless_trailers(h, rec):
    """A complete chunked body, then trailer lines without end: one read1() loops over them."""
    try:
        h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n")
        for _ in _for_four_seconds():
            h.wfile.write(b"X-Trailer: t\r\n")
            time.sleep(0.05)
        h.wfile.write(b"\r\n")
    except OSError:
        pass


def trickled_status_line(h, rec):
    """The status line, one byte every 100 ms: every single read is quick, the line as a whole is not."""
    try:
        for byte in b"HTTP/1.1 200 " + b"O" * 40 + b"\r\nContent-Length: 0\r\n\r\n":
            h.wfile.write(bytes([byte]))
            time.sleep(0.1)
    except OSError:
        pass


def timers_running():
    return [t for t in threading.enumerate() if isinstance(t, threading.Timer)]


def wait_for_no_timers(seconds=2):
    end = time.monotonic() + seconds
    while timers_running() and time.monotonic() < end:
        time.sleep(0.02)
    return timers_running()


@pytest.mark.parametrize("misbehaviour", [endless_continue, endless_trailers, trickled_status_line],
                         ids=["endless-100-continue", "endless-trailer-lines", "trickled-status-line"])
@pytest.mark.parametrize("route", [ROUTES[1], ROUTES[3]], ids=route_id)
def test_an_upstream_that_keeps_the_exchange_moving_still_hits_the_total_deadline(server, stubs, monkeypatch, route, misbehaviour):
    monkeypatch.setitem(relay.PROXY_TIMEOUT, "models", 0.5)
    stub = stubs[0] if route[2] == "stt" else stubs[1]
    stub.script = misbehaviour
    t0 = time.monotonic()
    resp = call(server, "GET", route[1])
    took = time.monotonic() - t0
    assert resp.status == 502 and resp.json() == {"error": {"message": "upstream unreachable"}}
    assert took < 2.0
    assert free_slots(server)
    stub.script = None       # and the relay is fine afterwards
    assert call(server, "GET", route[1]).status == 200


def test_four_stuck_upstream_calls_do_not_use_up_the_slots_for_good(server, stubs, monkeypatch):
    monkeypatch.setitem(relay.PROXY_TIMEOUT, "models", 0.5)
    stubs[0].script = endless_continue
    stubs[1].script = endless_trailers
    results = []
    threads = [threading.Thread(target=lambda r=r: results.append(call(server, "GET", r[1]).status)) for r in (ROUTES[1], ROUTES[3]) * 2]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results == [502] * 4 and time.monotonic() - t0 < 3
    assert free_slots(server)
    stubs[0].script = stubs[1].script = None
    assert call(server, "GET", STT_MODELS).status == 200 and call(server, "GET", LLM_MODELS).status == 200


def crippled_sockets(monkeypatch, no_shutdown=False, no_clamp=False):
    """The relay's sockets with one of its two safeguards switched off, to show that each one works on its own:
    the watchdog's shutdown (on Windows a shutdown does not wake a read that is waiting with a timeout; on Linux a
    read after the shutdown gives end-of-file, which http.client can take for a normal end) and the per-read timeout
    that shrinks with the time left."""
    class Sock(socket.socket):
        def shutdown(self, how):
            if not no_shutdown:
                super().shutdown(how)

        def settimeout(self, value):
            super().settimeout(max(value, 10) if no_clamp else value)
    fake_sockets(monkeypatch, socket.getaddrinfo, Sock)


def test_an_end_that_looks_clean_after_the_cut_off_is_still_a_502(server, llm_stub, monkeypatch):
    """With no shutdown and no clamp, the upstream finishes its chunked reply (trailers and all) only after the
    deadline, so http.client sees a normal end: only the watchdog's flag can turn that 200 into a 502."""
    monkeypatch.setitem(relay.PROXY_TIMEOUT, "models", 0.5)
    crippled_sockets(monkeypatch, no_shutdown=True, no_clamp=True)

    def late_end(h, rec):
        h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n")
        time.sleep(0.9)
        h.wfile.write(b"\r\n")
    llm_stub.script = late_end
    resp = call(server, "GET", LLM_MODELS)
    assert resp.status == 502 and resp.json() == {"error": {"message": "upstream unreachable"}}
    assert free_slots(server)

    def in_time(h, rec):       # the same reply without the delay is an ordinary 200
        h.wfile.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n")
    monkeypatch.setattr(relay, "socket", socket)
    llm_stub.script = in_time
    resp = call(server, "GET", LLM_MODELS)
    assert resp.status == 200 and resp.body == b"hello"


def test_a_read_that_goes_quiet_is_cut_at_the_deadline_even_where_a_shutdown_cannot_wake_it(server, llm_stub, monkeypatch):
    monkeypatch.setitem(relay.PROXY_TIMEOUT, "models", 1.0)
    crippled_sockets(monkeypatch, no_shutdown=True)

    def goes_quiet(h, rec):
        h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + b"x" * 10)
        time.sleep(0.8)
        h.wfile.write(b"x" * 10)    # a read starts right after this one, 0.2 s before the deadline, then nothing
        time.sleep(4)
    llm_stub.script = goes_quiet
    t0 = time.monotonic()
    resp = call(server, "GET", LLM_MODELS)
    assert resp.status == 502 and time.monotonic() - t0 < 1.4      # (a whole new 1 s for the last read would end at 1.8 s)
    assert free_slots(server)


def test_the_watchdog_timer_is_gone_after_every_kind_of_exchange(server, stubs):
    assert call(server, "GET", LLM_MODELS).status == 200
    assert wait_for_no_timers() == []
    hang_up(stubs[1])
    assert call(server, "GET", LLM_MODELS).status == 502
    assert wait_for_no_timers() == []
    stubs[1].script = lambda h, rec: h.reply(302, b"", {"Location": "/x"})
    assert call(server, "GET", LLM_MODELS).status == 502
    assert wait_for_no_timers() == []


# The connect phase is on the clock too: one deadline for the name lookup and all the connection attempts together.
def fake_sockets(monkeypatch, lookup, sock_class):
    """Replaces the `socket` module as the relay sees it (nothing else in the process changes)."""
    ns = types.SimpleNamespace(**vars(socket))
    ns.getaddrinfo, ns.socket = lookup, sock_class
    monkeypatch.setattr(relay, "socket", ns)


class Unreachable:
    """A socket whose connect fails after 0.3 s, or when its timeout is over if that comes first."""
    made = []

    def __init__(self, *args):
        self.timeout = None
        Unreachable.made.append(self)

    def settimeout(self, t):
        self.timeout = t

    def connect(self, address):
        time.sleep(min(self.timeout, 0.3))
        raise OSError("timed out")

    def close(self):
        pass


def test_the_connection_attempts_share_the_deadline_instead_of_each_getting_the_whole_timeout(monkeypatch):
    Unreachable.made = []
    looked_up = []

    def lookup(host, port, **kw):
        looked_up.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.%d" % i, port)) for i in range(1, 8)]
    fake_sockets(monkeypatch, lookup, Unreachable)
    t0 = time.monotonic()
    with pytest.raises(relay.UpstreamError) as err:
        relay.forward_upstream("http://many.example.test:8080/v1", "", "/models", "GET", b"", None, None, 0.5)
    assert err.value.message == "upstream unreachable"
    assert looked_up == [("many.example.test", 8080)]          # the fake was used, not the real resolver
    assert time.monotonic() - t0 < 1.2                         # (seven attempts with a whole timeout each would be 2.1 s)
    tries = [s.timeout for s in Unreachable.made]
    assert 1 <= len(tries) <= 3 and tries[0] <= 0.5 and all(b < a for a, b in zip(tries, tries[1:]))   # each gets what is left


def test_a_name_lookup_that_never_ends_is_cut_off_by_the_deadline(monkeypatch):
    looked_up = []

    def slow_lookup(host, port, **kw):
        looked_up.append(host)
        time.sleep(2)
        return []
    fake_sockets(monkeypatch, slow_lookup, Unreachable)
    t0 = time.monotonic()
    with pytest.raises(relay.UpstreamError) as err:
        relay.forward_upstream("http://slow-dns.example.test/v1", "", "/models", "GET", b"", None, None, 0.4)
    assert looked_up == ["slow-dns.example.test"]          # the fake was used, not the real resolver
    assert err.value.message == "upstream unreachable" and time.monotonic() - t0 < 1.2


@pytest.mark.parametrize("failure", [socket.gaierror(11001, "getaddrinfo failed"), UnicodeError("label empty or too long")],
                         ids=["lookup-fails", "name-the-idna-codec-refuses"])
def test_a_failed_name_lookup_is_the_same_502_text_at_once_and_does_not_show_the_name(monkeypatch, failure):
    looked_up = []

    def failing_lookup(host, port, **kw):
        looked_up.append(host)
        raise failure
    fake_sockets(monkeypatch, failing_lookup, Unreachable)
    crashed = []
    monkeypatch.setattr(threading, "excepthook", crashed.append)     # the lookup runs in a helper thread: no traceback from it
    t0 = time.monotonic()
    with pytest.raises(relay.UpstreamError) as err:
        relay.forward_upstream("http://secret-name.example.test/v1", "", "/models", "GET", b"", None, None, 5)
    assert looked_up == ["secret-name.example.test"]
    assert err.value.message == "upstream unreachable" and "secret-name" not in str(err.value)
    assert time.monotonic() - t0 < 1 and crashed == []        # at once, not after the whole timeout


def test_a_reply_cut_short_by_the_upstream_is_a_502_not_a_short_200(server, llm_stub):
    def script(h, rec):
        h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n" + b"x" * 10)
        h.close_connection = True
    llm_stub.script = script
    resp = send_route(server, ROUTES[2])
    assert resp.status == 502 and resp.json() == {"error": {"message": "upstream unreachable"}}


def test_a_reply_over_the_size_cap_is_a_502_and_one_at_the_cap_is_fine(server, llm_stub, monkeypatch):
    assert relay.MAX_PROXY_REPLY == 8_000_000
    monkeypatch.setattr(relay, "MAX_PROXY_REPLY", 1000)
    llm_stub.script = lambda h, rec: h.reply(200, b"x" * 1000)
    assert send_route(server, ROUTES[2]).body == b"x" * 1000
    llm_stub.script = lambda h, rec: h.reply(200, b"x" * 1001)
    resp = send_route(server, ROUTES[2])
    assert resp.status == 502 and resp.json() == {"error": {"message": "upstream reply too large"}}
    # chunked replies are counted too (no Content-Length to check up front)

    def chunked(h, rec):
        h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
        for _ in range(4):
            h.wfile.write(b"%x\r\n" % 400 + b"y" * 400 + b"\r\n")
        h.wfile.write(b"0\r\n\r\n")
    llm_stub.script = chunked
    assert send_route(server, ROUTES[2]).status == 502
    assert free_slots(server)


def test_a_chunked_upstream_reply_under_the_cap_is_passed_on_whole(server, llm_stub):
    def chunked(h, rec):
        h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
        h.wfile.write(b"5\r\n{\"a\":\r\n4\r\n 1} \r\n0\r\n\r\n")
    llm_stub.script = chunked
    resp = send_route(server, ROUTES[2])
    assert resp.status == 200 and resp.body == b'{"a": 1} ' and resp.header("Transfer-Encoding") is None


def test_https_addresses_use_a_certificate_checking_connection(server, monkeypatch):
    made = []

    class Fake:
        def __init__(self, host, port=None, timeout=None, **kw):
            made.append({"host": host, "port": port, "timeout": timeout, "kw": kw})

        def connect(self):
            raise OSError("no network in tests")

        def request(self, *a, **k):
            raise OSError("no network in tests")

        def close(self):
            pass
    monkeypatch.setattr(http.client, "HTTPSConnection", Fake)
    server.set_upstream("llm", "https://llm.example.com/v1", LLM_KEY)
    resp = send_route(server, ROUTES[2])
    assert resp.status == 502
    assert made == [{"host": "llm.example.com", "port": 443, "timeout": relay.PROXY_TIMEOUT["llm"], "kw": {}}]   # no custom SSL context: the default verifies
    server.set_upstream("llm", "https://[2001:db8::1]:8443/v1", LLM_KEY)
    send_route(server, ROUTES[3])
    assert made[-1] == {"host": "2001:db8::1", "port": 8443, "timeout": 15, "kw": {}}


def test_the_relay_never_turns_certificate_checks_off():
    src = open(relay.__file__, encoding="utf-8").read().lower()
    for word in ("_create_unverified_context", "cert_none", "check_hostname"):
        assert word not in src


# ================================================================= not configured -> 503
@pytest.mark.parametrize("route", ROUTES, ids=route_id)
def test_a_role_with_no_server_answers_503_not_configured_and_sends_nothing(bare, route):
    resp = send_route(bare, route)
    assert resp.status == 503
    assert "is not configured on the relay" in resp.json()["error"] and ("speech to text" in resp.json()["error"]) == (route[2] == "stt")
    assert free_slots(bare)


def test_one_configured_role_does_not_make_the_other_work(server, llm_stub):
    server.set_upstream("stt", "", "")
    assert send_route(server, ROUTES[0]).status == 503 and send_route(server, ROUTES[1]).status == 503
    assert send_route(server, ROUTES[2]).status == 200 and send_route(server, ROUTES[3]).status == 200
    assert {r["path"] for r in llm_stub.seen} == {"/v1/chat/completions", "/v1/models"}


def test_a_big_body_to_an_unconfigured_role_still_gets_the_503(bare):
    resp = call(bare, "POST", LLM_PATH, body=b"x" * 900_000, headers={"Content-Type": "application/json"})
    assert resp.status == 503 and "not configured" in resp.json()["error"]


@pytest.mark.parametrize("settings", [
    {"base_url": "ftp://example.com/v1", "api_key": ""},
    {"base_url": "http://example.com/v1", "api_key": ""},                     # plain http to a public host
    {"base_url": "https://user:pw@example.com/v1", "api_key": ""},
    {"base_url": "https://example.com/v1?x=1", "api_key": ""},
    {"base_url": "https://example.com/v1", "api_key": "bad key with spaces"},
    {"base_url": "https://example.com/v1", "api_key": "line\nbreak"},
    {"base_url": "https://example.com/v1", "api_key": "k" * (relay.MAX_KEY + 1)},
    {"base_url": "", "api_key": "sk-but-no-address-1234"},
])
def test_a_hand_edited_bad_setting_is_treated_as_not_configured(server, stubs, settings):
    server.upstream = relay.upstream_settings({"llm": settings})
    resp = send_route(server, ROUTES[2])
    assert resp.status == 503 and "not configured" in resp.json()["error"]
    assert ("is not configured on the relay" in resp.json()["error"]) == (not settings["base_url"])   # empty: nothing saved; else: saved but unusable
    assert everything_seen(*stubs) == []


# ================================================================= auth first
@pytest.mark.parametrize("route", ROUTES, ids=route_id)
def test_no_token_and_a_wrong_token_are_401_before_anything_else(server, bare, stubs, route):
    for srv in (server, bare):      # 401 even where the role is not configured: auth comes before everything
        assert send_route(srv, route, token=False).status == 401
        assert send_route(srv, route, token="wrong").status == 401
        assert send_route(srv, route, token=server.token[:-1]).status == 401
    assert everything_seen(*stubs) == []


def test_the_owner_check_applies_to_the_proxy_routes(tmp_path, stubs):
    srv = relay.make_server(str(tmp_path), port=0, owner="me@example.com")
    srv.set_upstream("llm", stubs[1].url, LLM_KEY)
    start(srv)
    try:
        assert send_route(srv, ROUTES[2]).status == 403
        assert send_route(srv, ROUTES[2], headers={"Tailscale-User-Login": "other@example.com"}).status == 403
        assert stubs[1].seen == []
        ok = send_route(srv, ROUTES[2], headers={"Tailscale-User-Login": "me@example.com"})
        assert ok.status == 200 and "tailscale-user-login" not in stubs[1].seen[0]["headers"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_proxy_calls_count_the_device_and_show_up_in_activity_without_any_content(server, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    secret_words = "PRIVATE-DICTATION-WORDS-do-not-log"
    call(server, "POST", LLM_PATH, body=chat(secret_words), headers={"Content-Type": "application/json", "X-Vox-Device": "pixel"})
    call(server, "GET", LLM_MODELS, headers={"X-Vox-Device": "pixel"})
    act = call(server, "GET", "/admin/activity").json()
    devs = {d["name"]: d for d in act["devices"]}
    assert devs["pixel"]["requests"] == 2
    routes = [(e["method"], e["route"], e["status"], e["device"]) for e in act["events"]]
    assert ("POST", LLM_PATH, 200, "pixel") in routes and ("GET", LLM_MODELS, 200, "pixel") in routes
    out = capsys.readouterr()
    for text in (json.dumps(act), out.out, out.err, caplog.text):
        assert secret_words not in text


# ================================================================= unknown routes and path tricks
def variants(path):
    """Ways of spelling (or bending) one proxy path that must not reach the upstream."""
    head, tail = path.rsplit("/", 1)
    return [path + "/", path + "/..", path + "/../../../admin/status", path + ";x=1", path + "#frag", path + "%00", path + "%20", path + "%2f",
            path + ".", head + "%2F" + tail, head + "/" + tail.upper(), path.replace("/proxy/", "/proxy//"), path.replace("/", "//")[1:],
            path.replace("/proxy/", "/proxy/./"), path.replace("/proxy/", "/proxy/x/../"), path.replace("/proxy", "/%70roxy"),
            path.replace("/proxy", "/Proxy"), "http://evil.example" + path, "http://evil.example:9" + path, "http://[::1]" + path,
            "/x/.." + path, path + "/%2e%2e/%2e%2e/admin/status", head + "/%2e%2e/" + tail, path.replace("/proxy/", "/proxy/%2e%2e/proxy/")]


OTHER_TARGETS = ["/proxy", "/proxy/", "/proxy/llm", "/proxy/llm/", "/proxy/other/models", "/proxy/llm/files", "/proxy/llm/../admin/status",
                 "/proxy/llm/chat/completions/../../../admin/status", "/proxy/stt/audio/transcriptions/../../llm/chat/completions",
                 "/proxy/stt/models/x", "/proxy/llm/v1/chat/completions", "/proxy/v1/models", "/proxy/stt/audio/translations", "/proxy/llm/completions",
                 "/proxy/llm/embeddings", "/proxy/stt/files", "/proxy/llm/models/../chat/completions", "/proxy/llm/%2e%2e/admin/status"]


@pytest.mark.parametrize("route", ROUTES, ids=route_id)
def test_bent_spellings_of_a_route_are_404_and_reach_nothing(server, stubs, route):
    method = route[0]
    for target in variants(route[1]) + OTHER_TARGETS:
        body = chat() if method == "POST" else None
        resp = call(server, method, target, body=body, headers={"Content-Type": "application/json"} if body else None)
        assert resp.status == 404 or (resp.status == 405 and not target.startswith("/proxy/")), (target, resp.status, resp.body[:200])
        assert b"uptime" not in resp.body and b"db_bytes" not in resp.body, target      # never the admin data
    assert everything_seen(*stubs) == []


@pytest.mark.parametrize("target", ["//proxy/llm/chat/completions", "///proxy/llm/chat/completions"])
def test_leading_slashes_are_404_or_exactly_the_whitelisted_route(server, stubs, target):
    """Newer Pythons (and security releases of older ones) fold leading slashes before routing; either outcome is safe."""
    resp = call(server, "POST", target, body=chat(), headers={"Content-Type": "application/json"})
    assert resp.status in (200, 404)
    seen = everything_seen(*stubs)
    if resp.status == 200:
        assert [r["path"] for r in seen] == ["/v1/chat/completions"]
    else:
        assert seen == []


@pytest.mark.parametrize("target", ["/proxy/llm/chat/completions?x=http://evil.example", "/proxy/llm/chat/completions?",
                                    "/proxy/llm/chat/completions?/../../admin/status", "/proxy/llm/chat/completions?a=1&a=2&key=" + LLM_KEY[:8]])
def test_a_query_string_on_a_whitelisted_route_is_ignored_and_never_forwarded(server, llm_stub, target):
    resp = call(server, "POST", target, body=chat(), headers={"Content-Type": "application/json"})
    assert resp.status == 200 and [r["path"] for r in llm_stub.seen] == ["/v1/chat/completions"]
    assert b"uptime" not in resp.body


@pytest.mark.parametrize("method,path", [("GET", LLM_PATH), ("PUT", LLM_PATH), ("DELETE", LLM_PATH), ("POST", LLM_MODELS), ("PUT", STT_MODELS),
                                         ("GET", STT_PATH), ("DELETE", STT_MODELS), ("POST", "/proxy/stt/audio/transcriptions/x")])
def test_the_wrong_method_on_a_proxy_path_is_404(server, stubs, method, path):
    resp = call(server, method, path, body=b"{}" if method in ("POST", "PUT") else None)
    assert resp.status == 404 and everything_seen(*stubs) == []


def test_a_malformed_request_target_is_a_400_not_a_dropped_connection(server):
    resp = raw_call(server, b"GET http://[ HTTP/1.1\r\nHost: x\r\n\r\n")
    assert resp.status == 400
    assert call(server, "GET", "/health").status == 200     # the relay is fine


# ================================================================= body framing
def post_bytes(srv, path, head_lines, body=b""):
    lines = ["POST %s HTTP/1.1" % path, "Host: relay"] + head_lines
    return raw_call(srv, ("\r\n".join(lines) + "\r\n").encode() + auth(srv) + b"\r\n" + body)


def test_a_post_without_content_length_is_411(server, stubs):
    for path in (STT_PATH, LLM_PATH):
        resp = post_bytes(server, path, ["Content-Type: application/json"], b"")
        assert resp.status == 411 and "Content-Length" in resp.json()["error"], path
    assert everything_seen(*stubs) == []
    assert free_slots(server)


def test_chunked_uploads_are_411(server, stubs):
    for path in (STT_PATH, LLM_PATH):
        resp = post_bytes(server, path, ["Transfer-Encoding: chunked", "Content-Type: application/json"], b"5\r\nhello\r\n0\r\n\r\n")
        assert resp.status == 411, path
        resp = post_bytes(server, path, ["Transfer-Encoding: chunked", "Content-Length: 5"], b"hello")   # both: never guess
        assert resp.status == 411, path
    assert everything_seen(*stubs) == []


@pytest.mark.parametrize("value", ["abc", "-1", "1_0", "+5", "5 5", "0x10", "", "9999999999999999999999", "1.5"])
def test_a_bad_content_length_is_400(server, stubs, value):
    resp = post_bytes(server, LLM_PATH, ["Content-Length: " + value], b"hello")
    assert resp.status == 400 and everything_seen(*stubs) == []


def test_two_content_length_headers_are_refused(server, stubs):
    resp = post_bytes(server, LLM_PATH, ["Content-Length: 5", "Content-Length: 5", "Content-Type: application/json"], b"hello")
    assert resp.status == 400 and everything_seen(*stubs) == []


def test_a_short_body_is_a_400_and_nothing_is_forwarded(server, stubs):
    s = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=10)
    try:
        s.sendall(b"POST " + LLM_PATH.encode() + b" HTTP/1.1\r\nHost: x\r\n" + auth(server) + b"Content-Length: 100\r\n\r\nonly ten b")
        s.shutdown(socket.SHUT_WR)     # the client gives up before sending what it promised
        r = http.client.HTTPResponse(s)
        r.begin()
        assert r.status == 400
    finally:
        s.close()
    assert everything_seen(*stubs) == [] and free_slots(server)


def test_a_body_on_a_get_is_read_and_dropped_not_forwarded(server, llm_stub):
    resp = call(server, "GET", LLM_MODELS, body=b"hello" * 40_000, headers={"Content-Type": "application/json"})
    assert resp.status == 200 and llm_stub.seen[0]["body"] == b"" and "content-length" not in llm_stub.seen[0]["headers"]


def test_an_empty_post_body_is_forwarded_as_it_is(server, llm_stub):
    resp = call(server, "POST", LLM_PATH, body=b"", headers={"Content-Type": "application/json"})
    assert resp.status == 200 and llm_stub.seen[0]["body"] == b"" and llm_stub.seen[0]["headers"]["content-length"] == "0"


# ================================================================= a refused upload gets exactly one answer
def answers_to(srv, method, path, extra=(), token=True, length=True):
    """Sends a request whose body is itself a complete, valid request (with the real token) and returns the status of
    every answer that comes back. Today the relay closes every connection after one answer (it speaks HTTP/1.0), so this
    is one answer by construction; the tests keep it that way if keep-alive is ever switched on without dropping the body."""
    body = b"GET /health HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + srv.token.encode() + b"\r\n\r\n"
    lines = ["%s %s HTTP/1.1" % (method, path), "Host: relay"] + list(extra)
    if length:
        lines.append("Content-Length: %d" % len(body))
    head = "\r\n".join(lines).encode() + b"\r\n" + (auth(srv) if token else b"") + b"\r\n"
    s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=2)
    data = b""
    try:
        s.sendall(head + body)
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
    finally:
        s.close()
    return [int(c) for c in re.findall(rb"HTTP/1\.[01] (\d{3})", data)]


def test_a_refused_upload_gets_one_answer_and_its_body_is_never_taken_for_a_request(server, bare, monkeypatch):
    assert answers_to(bare, "POST", LLM_PATH) == [503]                                   # role not configured
    assert answers_to(server, "POST", "/proxy/llm/files") == [404]                       # unknown route
    monkeypatch.setitem(relay.MAX_PROXY_BODY, "llm", 10)
    assert answers_to(server, "POST", LLM_PATH) == [413]                                 # too large
    monkeypatch.setitem(relay.MAX_PROXY_BODY, "llm", 1_000_000)
    for _ in range(4):
        server.proxy_slots.acquire()
    try:
        assert answers_to(server, "POST", LLM_PATH) == [429]                             # busy
    finally:
        for _ in range(4):
            server.proxy_slots.release()
    assert answers_to(server, "POST", LLM_PATH, ["Content-Length: abc"], length=False) == [400]   # framing we cannot trust
    assert answers_to(server, "POST", LLM_PATH, ["Transfer-Encoding: chunked"], length=False) == [411]
    assert answers_to(server, "GET", LLM_MODELS) == [200]                                # a body on a GET is dropped, not parsed


def answer_to_a_short_upload(srv, method, path, claimed, sent=1000):
    """Claims a body of `claimed` bytes but sends only `sent`, then waits for the answer. Returns (status, seconds)."""
    head = ("%s %s HTTP/1.1\r\nHost: relay\r\nAuthorization: Bearer %s\r\nContent-Length: %d\r\n\r\n" % (method, path, srv.token, claimed)).encode()
    s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=10)
    try:
        t0 = time.monotonic()
        s.sendall(head + b"x" * sent)
        data = b""
        while b"\r\n" not in data:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        return int(data.split(b" ", 2)[1]), time.monotonic() - t0
    finally:
        s.close()


def test_a_refused_upload_is_read_before_the_answer_goes_out(server, bare, monkeypatch):
    """The relay reads what the client is sending (and only waits while nothing more comes) before it refuses, so the
    client is not cut off in the middle of its upload and can read the answer. No reading would answer at once."""
    monkeypatch.setattr(relay, "DRAIN_IDLE", 0.5)
    cases = [(bare, "POST", LLM_PATH, 503), (server, "POST", LLM_PATH, 413), (server, "POST", "/proxy/llm/files", 404), (server, "GET", LLM_MODELS, 200)]
    for srv, method, path, want in cases:
        claimed = 2_000_000 if want == 413 else 100_000      # 413 needs a body over the 1 MB limit; the others just need more than was sent
        status, seconds = answer_to_a_short_upload(srv, method, path, claimed)
        assert status == want and seconds >= 0.4, (method, path, status, seconds)
    for _ in range(4):
        server.proxy_slots.acquire()
    try:
        status, seconds = answer_to_a_short_upload(server, "POST", LLM_PATH, 100_000)      # busy
        assert status == 429 and seconds >= 0.4
    finally:
        for _ in range(4):
            server.proxy_slots.release()


def test_a_refused_token_with_an_upload_gets_one_401(server, stubs):
    assert answers_to(server, "POST", LLM_PATH, token=False) == [401]
    assert answers_to(server, "POST", STT_PATH, token=False) == [401]
    assert everything_seen(*stubs) == []


def test_a_wrong_tailnet_user_with_an_upload_gets_one_403(tmp_path):
    srv = start(relay.make_server(str(tmp_path), port=0, owner="me@example.com"))
    try:
        assert answers_to(srv, "POST", LLM_PATH) == [403]
    finally:
        srv.shutdown()
        srv.server_close()


# ================================================================= settings are read once per request
class Flipping:
    """Stands in for `server.upstream`: the first lookup sees one set of settings and every later lookup another,
    like a save that lands in the middle of a request."""

    def __init__(self, first, second):
        self.views, self.lookups = [first, second], 0

    def __getitem__(self, role):
        view = self.views[min(self.lookups, 1)]
        self.lookups += 1
        return view[role]


def test_the_address_and_key_of_a_role_are_read_together_so_a_save_cannot_mix_them(server, stubs):
    old = relay.upstream_settings({"llm": {"base_url": stubs[1].url, "api_key": LLM_KEY}})
    new = relay.upstream_settings({"llm": {"base_url": stubs[0].url, "api_key": "sk-NEW-SERVER-KEY-0123456789"}})
    server.upstream = Flipping(old, new)
    assert send_route(server, ROUTES[2]).status == 200
    assert [r["headers"]["authorization"] for r in stubs[1].seen] == ["Bearer " + LLM_KEY]     # the old address got the old key
    assert stubs[0].seen == []                                                                  # and the new key went nowhere


# ================================================================= size limits
def test_the_limits_are_25_mb_for_audio_1_mb_for_chat_and_none_for_models():
    assert relay.MAX_PROXY_BODY == {"stt": 25_000_000, "llm": 1_000_000, "models": 0}
    assert relay.PROXY_SLOTS == 4


def test_a_2_mb_chat_body_is_413_and_the_client_still_gets_to_read_it(server, stubs):
    resp = call(server, "POST", LLM_PATH, body=b"x" * 2_000_000, headers={"Content-Type": "application/json"})
    assert resp.status == 413 and resp.json() == {"error": "request too large"}
    assert everything_seen(*stubs) == [] and free_slots(server)


def test_a_chat_body_at_the_limit_is_forwarded_and_one_byte_more_is_413(server, llm_stub):
    ok = call(server, "POST", LLM_PATH, body=b"y" * 1_000_000, headers={"Content-Type": "application/json"})
    assert ok.status == 200 and len(llm_stub.seen[0]["body"]) == 1_000_000
    over = call(server, "POST", LLM_PATH, body=b"y" * 1_000_001, headers={"Content-Type": "application/json"})
    assert over.status == 413 and len(llm_stub.seen) == 1


def test_a_claimed_26_mb_audio_body_is_413_even_though_only_a_little_was_sent(server, stubs):
    t0 = time.monotonic()
    resp = call(server, "POST", STT_PATH, body=b"z" * 1000, headers={"Content-Type": "multipart/form-data; boundary=x", "Content-Length": str(26 * 1024 * 1024)})
    assert resp.status == 413 and time.monotonic() - t0 < 5
    assert everything_seen(*stubs) == [] and free_slots(server)


def test_a_claimed_size_of_terabytes_is_413_without_reading_it(server, stubs):
    resp = post_bytes(server, STT_PATH, ["Content-Length: 999999999999"], b"tiny")
    assert resp.status == 413 and everything_seen(*stubs) == []


def test_the_audio_limit_is_checked_against_the_declared_length_to_the_byte(server, stt_stub, monkeypatch):
    monkeypatch.setitem(relay.MAX_PROXY_BODY, "stt", 5000)
    hdr = {"Content-Type": "multipart/form-data; boundary=x"}
    assert call(server, "POST", STT_PATH, body=b"a" * 5000, headers=hdr).status == 200
    assert call(server, "POST", STT_PATH, body=b"a" * 5001, headers=hdr).status == 413
    assert len(stt_stub.seen) == 1 and len(stt_stub.seen[0]["body"]) == 5000


# ================================================================= slots
def test_with_four_slots_held_every_proxy_route_is_429_busy_at_once(server, stubs):
    for _ in range(4):
        assert server.proxy_slots.acquire(blocking=False)
    try:
        for route in ROUTES:
            t0 = time.monotonic()
            resp = send_route(server, route)
            assert resp.status == 429 and resp.json() == {"error": "busy"} and time.monotonic() - t0 < 2, route_id(route)
        assert everything_seen(*stubs) == []
        assert call(server, "GET", "/health").status == 200     # the rest of the relay is not affected
    finally:
        for _ in range(4):
            server.proxy_slots.release()
    assert send_route(server, ROUTES[2]).status == 200


def test_a_fifth_request_is_busy_while_four_are_really_in_flight_and_the_slots_come_back(server, llm_stub):
    gate, arrived = threading.Event(), threading.Semaphore(0)

    def script(h, rec):
        arrived.release()
        gate.wait(10)
        h.reply(200, b'{"n": 1}')
    llm_stub.script = script
    results = []
    threads = [threading.Thread(target=lambda: results.append(send_route(server, ROUTES[2]))) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        for _ in range(4):
            assert arrived.acquire(timeout=10)
        fifth = send_route(server, ROUTES[3])
        assert fifth.status == 429 and fifth.json() == {"error": "busy"}
    finally:
        gate.set()
        for t in threads:
            t.join(10)
    assert [r.status for r in results] == [200] * 4
    assert len(llm_stub.seen) == 4 and free_slots(server)
    llm_stub.script = None
    assert send_route(server, ROUTES[3]).status == 200


def test_slots_are_returned_after_every_kind_of_failure(server, stubs):
    send_route(server, ROUTES[2], token="wrong")
    call(server, "POST", LLM_PATH, body=b"x" * 1_500_000, headers={"Content-Type": "application/json"})
    stubs[1].script = lambda h, rec: h.reply(500, b"boom")
    send_route(server, ROUTES[2])
    hang_up(stubs[0])
    send_route(server, ROUTES[0])
    server.set_upstream("stt", "", "")
    send_route(server, ROUTES[0])
    post_bytes(server, LLM_PATH, ["Content-Length: abc"])
    assert free_slots(server)
    assert relay.PROXY_SLOTS == 4 and isinstance(server.proxy_slots, threading.BoundedSemaphore)


# ================================================================= the key stays on the relay
def test_the_upstream_key_is_in_no_response_no_output_and_no_download(server, stubs, capsys, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    transcript = []

    def go(*a, **k):
        r = call(*a, **k)
        transcript.append(r.raw)
        return r

    stubs[0].script = lambda h, rec: h.reply(401, json.dumps({"error": {"message": "wrong key " + rec["headers"]["authorization"]}}).encode())
    go(server, "POST", STT_PATH, body=multipart(), headers={"Content-Type": "multipart/form-data; boundary=" + BOUNDARY.decode()})
    stubs[0].script = None
    for route in ROUTES:
        transcript.append(send_route(server, route).raw)
    go(server, "POST", LLM_PATH, body=b"x" * 2_000_000)                                   # 413
    go(server, "GET", "/proxy/llm/files")                                                  # 404
    go(server, "POST", LLM_PATH, headers={"Content-Type": "application/json"}, token="nope")   # 401
    hang_up(stubs[1])
    go(server, "POST", LLM_PATH, body=chat(), headers={"Content-Type": "application/json"})   # 502
    server.set_upstream("stt", "", "")
    go(server, "POST", STT_PATH, body=multipart())                                         # 503
    for _ in range(4):
        server.proxy_slots.acquire()
    go(server, "GET", LLM_MODELS)                                                          # 429
    for _ in range(4):
        server.proxy_slots.release()
    for path in ("/admin/upstream", "/admin/status", "/admin/activity", "/admin/profile", "/admin/export", "/admin/backup", "/profile", "/health", "/changes", "/"):
        go(server, "GET", path)
    assert len(transcript) > 15
    out = capsys.readouterr()
    everything = b"\n".join(transcript) + (out.out + out.err + caplog.text).encode()
    for key in (STT_KEY, LLM_KEY):
        assert key.encode() not in everything
    # the same scan must be able to find a key where one is: it is stored in relay.json, and only there
    assert LLM_KEY in open(os.path.join(str(tmp_path), "relay.json"), encoding="utf-8").read()


# ================================================================= the Windows app's speech upload through the proxy
def _relay_cfg(srv):
    import vox_core
    cfg = dict(vox_core.DEFAULT_CONFIG)
    cfg.update(relay_proxy=True, relay_url="http://127.0.0.1:%d" % srv.server_address[1], relay_token=srv.token)
    return cfg


def test_vox_core_transcribe_goes_through_the_real_proxy_with_a_content_length(server, stt_stub):
    """requests sends Content-Length for an in-memory files= upload; the relay answers 411 to anything chunked."""
    pytest.importorskip("requests")   # the relay CI job is stdlib-only; the tests job runs this
    import vox_core
    wav = b"RIFF" + bytes(range(256)) * 40
    assert vox_core.transcribe(_relay_cfg(server), wav) == "from the stub"
    rec = stt_stub.seen[-1]
    assert rec["path"] == "/v1/audio/transcriptions"
    assert "transfer-encoding" not in rec["headers"] and rec["headers"]["content-length"] == str(len(rec["body"]))
    assert wav in rec["body"]
    assert vox_core.transcribe_segments(_relay_cfg(server), wav)[0]["text"] == "from the stub"


def test_a_streamed_chunked_upload_is_411_through_the_real_proxy_and_never_reaches_the_upstream(server, stt_stub):
    """The framing the Android app used to send (Transfer-Encoding: chunked): the reason an upload needs a length."""
    requests = pytest.importorskip("requests")   # the relay CI job is stdlib-only; the tests job runs this
    body = multipart()
    r = requests.post("http://127.0.0.1:%d%s" % (server.server_address[1], STT_PATH),
                      headers={"Authorization": "Bearer " + server.token, "Content-Type": "multipart/form-data; boundary=" + BOUNDARY.decode()},
                      data=(body[i:i + 100] for i in range(0, len(body), 100)), timeout=10)
    assert r.status_code == 411 and "Content-Length" in r.json()["error"]
    assert stt_stub.seen == [] and free_slots(server)


# ================================================================= clients that give up
def _send_and_hang_up(srv, route, stub, arrived, linger=0.0):
    """Sends a real proxy request, waits until the stub has it, then closes the client's side without reading."""
    method, path = route[0], route[1]
    body = multipart() if route[2] == "stt" else chat()
    ctype = "multipart/form-data; boundary=" + BOUNDARY.decode() if route[2] == "stt" else "application/json"
    head = ("POST %s HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n\r\n"
            % (path, srv.token, ctype, len(body))).encode()
    s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=10)
    s.sendall(head + body)
    assert arrived.acquire(timeout=10)
    time.sleep(linger)
    s.close()


def _slots_free_within(srv, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if free_slots(srv):
            return True
        time.sleep(0.05)
    return free_slots(srv)


def _blocking_stub(stub):
    gate, arrived, closed = threading.Event(), threading.Semaphore(0), threading.Semaphore(0)

    def script(h, rec):
        arrived.release()
        gate.wait(20)
        try:     # the relay has closed its side when it gave up: the stub's reply goes nowhere
            h.reply(200, b'{"text": "late"}')
        except OSError:
            pass
        closed.release()
    stub.script = script
    return gate, arrived, closed


@pytest.mark.parametrize("route", [ROUTES[0], ROUTES[2]], ids=route_id)
def test_a_client_that_hangs_up_mid_wait_frees_its_slot_within_about_a_second(server, stubs, route):
    stub = stubs[0] if route[2] == "stt" else stubs[1]
    gate, arrived, _closed = _blocking_stub(stub)
    try:
        _send_and_hang_up(server, route, stub, arrived)
        t0 = time.monotonic()
        assert _slots_free_within(server, 3), "the slot is still held after the client left"
        assert time.monotonic() - t0 < 1.5
    finally:
        gate.set()


def test_the_upstream_exchange_is_closed_when_the_client_leaves(server, stt_stub):
    """The relay's connection to the stub is shut, so the stub sees a closed peer while it is still waiting."""
    seen_closed = threading.Event()

    def script(h, rec):
        h.connection.settimeout(0.1)
        end = time.monotonic() + 8
        while time.monotonic() < end:
            try:
                if h.connection.recv(1) == b"":
                    seen_closed.set()
                    return
            except socket.timeout:
                continue
            except OSError:
                seen_closed.set()
                return
    arrived = threading.Semaphore(0)
    stt_stub.script = lambda h, rec: (arrived.release(), script(h, rec))
    _send_and_hang_up(server, ROUTES[0], stt_stub, arrived)
    assert seen_closed.wait(5), "the upstream connection was left open"


def test_a_slow_upstream_does_not_fill_the_slots_through_repeated_abandoned_requests(server, stubs):
    gate, arrived, _closed = _blocking_stub(stubs[0])
    try:
        for _ in range(relay.PROXY_SLOTS + 3):      # more abandoned requests than there are slots, one after another
            _send_and_hang_up(server, ROUTES[0], stubs[0], arrived)
            assert _slots_free_within(server, 3)
        stubs[0].script = None
        assert send_route(server, ROUTES[0]).status == 200     # nobody else got a 429 "busy"
    finally:
        gate.set()


def test_four_abandoned_requests_at_once_still_leave_room_for_a_fifth(server, stubs):
    gate, arrived, _closed = _blocking_stub(stubs[1])
    try:
        socks = []
        for _ in range(relay.PROXY_SLOTS):
            body = chat()
            head = ("POST %s HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                    % (LLM_PATH, server.token, len(body))).encode()
            s = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=10)
            s.sendall(head + body)
            socks.append(s)
        for _ in socks:
            assert arrived.acquire(timeout=10)
        assert not free_slots(server)
        for s in socks:
            s.close()
        assert _slots_free_within(server, 3)
        stubs[1].script = None
        assert send_route(server, ROUTES[2]).status == 200
    finally:
        gate.set()


def test_a_client_that_waits_still_gets_its_answer_from_a_slow_upstream(server, stt_stub):
    def script(h, rec):
        time.sleep(1.2)       # longer than the relay's polling interval, a few times over
        h.reply(200, b'{"text": "slow but fine"}')
    stt_stub.script = script
    resp = send_route(server, ROUTES[0])
    assert resp.status == 200 and resp.json() == {"text": "slow but fine"} and free_slots(server)


# ------------------------------------------------------------------ medium round M4 (C-R3)
def test_abandoned_calls_cannot_pile_up_more_than_twice_the_slots_where_a_shutdown_does_not_wake_the_read(server, llm_stub, monkeypatch):
    """On Windows a client that leaves frees its slot at once while the read of its exchange runs on until the timeout.
    Imitated here on every OS. The exchanges (each holds its request body) alive at one time stay at 2 * PROXY_SLOTS."""
    monkeypatch.setitem(relay.PROXY_TIMEOUT, "llm", 3.0)
    crippled_sockets(monkeypatch, no_shutdown=True)
    llm_stub.script = lambda h, rec: time.sleep(3.5)         # reads the request, never answers
    real, state = relay.forward_upstream, {"alive": 0, "peak": 0}
    lock = threading.Lock()

    def counting(*args, **kw):
        with lock:
            state["alive"] += 1
            state["peak"] = max(state["peak"], state["alive"])
        try:
            return real(*args, **kw)
        finally:
            with lock:
                state["alive"] -= 1
    monkeypatch.setattr(relay, "forward_upstream", counting)
    statuses = []

    def one_call():
        c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        try:
            c.request("POST", LLM_PATH, body=chat(), headers={"Authorization": "Bearer " + server.token, "Content-Type": "application/json"})
            c.sock.settimeout(0.5)
            try:
                statuses.append(c.getresponse().status)       # a 429 comes at once; otherwise nothing comes and we leave
            except OSError:
                pass
        finally:
            c.close()

    for _ in range(3):      # three waves of PROXY_SLOTS calls, each wave leaves and frees its slots before the next
        wave = [threading.Thread(target=one_call) for _ in range(relay.PROXY_SLOTS)]
        for t in wave:
            t.start()
        for t in wave:
            t.join(10)
        deadline = time.monotonic() + 3
        while not free_slots(server) and time.monotonic() < deadline:
            time.sleep(0.05)
    assert state["peak"] <= 2 * relay.PROXY_SLOTS
    assert statuses.count(429) == relay.PROXY_SLOTS         # the third wave found the exchanges still alive and was told "busy"
    deadline = time.monotonic() + 8
    while state["alive"] and time.monotonic() < deadline:     # they all end at the timeout and give everything back
        time.sleep(0.05)
    assert state["alive"] == 0
    llm_stub.script = None
    monkeypatch.setattr(relay, "socket", socket)
    assert call(server, "GET", LLM_MODELS).status == 200      # all slots and all exchange places are back

# ------------------------------------------------------------------ final pass (C-R3, the request body is let go once it is sent)
def test_forward_upstream_lets_go_of_the_request_body_once_it_is_sent(llm_stub):
    import sys
    sent, release = threading.Semaphore(0), threading.Event()

    def reads_and_never_answers(h, rec):
        sent.release()
        release.wait(10)
        h.reply()
    llm_stub.script = reads_and_never_answers
    body = b"x" * 300_000
    holder = [body]
    out = []
    t = threading.Thread(target=lambda: out.append(_quiet(relay.forward_upstream, llm_stub.url, "", "/chat/completions", "POST",
                                                          holder, "application/json", None, 3.0)))
    t.start()
    try:
        assert sent.acquire(timeout=10)        # the upstream has the whole request, and is now holding the exchange open
        end = time.monotonic() + 3
        while sys.getrefcount(body) != 2 and time.monotonic() < end:     # (this name and the call itself); the relay's thread
            time.sleep(0.02)                                              # may be a moment behind the stub that has the request
        assert holder == [] and sys.getrefcount(body) == 2, "the exchange is still holding its request body"
        assert llm_stub.seen[0]["body"] == body      # (and the upstream did get all of it)
    finally:
        release.set()
        t.join(10)


def _quiet(fn, *args):
    try:
        return fn(*args)
    except relay.UpstreamError as e:
        return e


def test_the_proxy_hands_the_body_to_the_exchange_and_keeps_none_while_it_waits(server, llm_stub, monkeypatch):
    sent, release = threading.Semaphore(0), threading.Event()
    def reads_then_answers(h, rec):
        sent.release()
        release.wait(10)
        h.reply()
    llm_stub.script = reads_then_answers
    real, handed = relay.forward_upstream, []

    def spy(base, key, suffix, method, body, *rest, **kw):
        handed.append(body)
        return real(base, key, suffix, method, body, *rest, **kw)
    monkeypatch.setattr(relay, "forward_upstream", spy)
    t = threading.Thread(target=lambda: call(server, "POST", LLM_PATH, body=chat(), timeout=10))
    t.start()
    try:
        assert sent.acquire(timeout=10)
        assert len(handed) == 1 and handed[0] == [], "the handler (or the exchange) still holds the request body"
    finally:
        release.set()
        t.join(10)

