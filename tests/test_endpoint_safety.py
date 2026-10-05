import pytest

import vox_core as core


# ------------------------------------------------------------ is_private_host

@pytest.mark.parametrize("host", [
    "localhost", "127.0.0.1", "::1", "[::1]", "10.1.2.3", "172.16.0.5", "172.31.255.255",
    "192.168.1.20", "100.64.0.1", "100.101.102.103", "100.127.255.254",
    "laptop", "your-pi", "laptop.your-tailnet.ts.net", "printer.local", "nas.lan", "169.254.1.1",
])
def test_private_hosts(host):
    assert core.is_private_host(host)


@pytest.mark.parametrize("host", [
    "api.groq.com", "example.com", "8.8.8.8", "100.63.255.255", "100.128.0.1", "172.32.0.1",
    "192.169.0.1", "evil.ts.net.example.com", "", None,
])
def test_public_hosts(host):
    assert not core.is_private_host(host)


# ------------------------------------------------------------- endpoint_error

def test_blank_or_default_endpoint_is_fine():
    assert core.endpoint_error({}) == ""
    assert core.endpoint_error({"base_url": ""}) == ""
    assert core.endpoint_error({"base_url": core.BASE}) == ""


def test_https_anywhere_is_fine():
    assert core.endpoint_error({"base_url": "https://whisper.example.com/v1"}) == ""


def test_http_allowed_for_private_hosts():
    for url in ("http://laptop:8000/v1", "http://100.90.1.2:8000/v1", "http://192.168.1.5:11434/v1",
                "http://localhost:8000/v1"):
        assert core.endpoint_error({"base_url": url}) == "", url


def test_http_refused_for_public_hosts_because_the_key_would_travel_in_clear():
    err = core.endpoint_error({"base_url": "http://whisper.example.com/v1"})
    assert "https" in err


def test_garbage_addresses_are_refused():
    for url in ("ftp://laptop/v1", "laptop:8000/v1", "http://", "not a url"):
        assert core.endpoint_error({"base_url": url}), url


# ---------------------------------------------------------------- key_missing

def test_groq_needs_a_key():
    assert core.key_missing({"api_key": ""})
    assert core.key_missing({"api_key": "   ", "base_url": core.BASE})
    assert not core.key_missing({"api_key": "gsk_x"})


def test_self_hosted_server_can_run_without_a_key():
    assert not core.key_missing({"api_key": "", "base_url": "http://laptop:8000/v1"})


# ---------------------------------------------------------- error type rename

def test_api_error_carries_status_code():
    e = core.ApiError(429, "slow down")
    assert e.code == 429 and str(e) == "slow down"


def test_check_response_raises_api_error_with_server_message():
    class R:
        status_code = 401
        text = ""

        def json(self):
            return {"error": {"message": "bad key"}}

    with pytest.raises(core.ApiError) as ei:
        core.check_response(R())
    assert ei.value.code == 401 and "bad key" in str(ei.value)


# ------------------------------------------------------------- bf-e: SEC-3, where a plain http name really leads
@pytest.mark.parametrize("address, ok", [("127.0.0.1", True), ("::1", True), ("fe80::1%3", True), ("100.100.1.1", True),
                                         ("10.0.0.2", True), ("8.8.8.8", False), ("::ffff:8.8.8.8", False),
                                         ("::ffff:192.168.1.2", True), ("2606:4700::1111", False), ("", False)])
def test_private_peer_judges_the_address_a_connection_reached(address, ok):
    assert core.private_peer(address) is ok


@pytest.mark.parametrize("host", ["134744072", "0x08080808", "127.1", "2130706433"])
def test_a_numeric_host_needs_https(host):
    assert core.endpoint_error({"base_url": f"http://{host}:8000/v1"})


def _local_server():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def test_plain_http_to_a_name_that_leads_to_a_private_address_works():
    srv, seen = _local_server()
    try:
        r = core.requests.get(f"http://localhost:{srv.server_address[1]}/x", headers={"Authorization": "Bearer k"}, timeout=5)
        assert r.status_code == 200 and seen == ["Bearer k"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_plain_http_that_reaches_a_public_address_sends_nothing(monkeypatch):
    srv, seen = _local_server()
    monkeypatch.setattr(core, "private_peer", lambda address: False)    # as if the name had resolved to the internet
    try:
        for send in (lambda url, **kw: core.requests.get(url, **kw), lambda url, **kw: core._session.get(url, **kw)):
            with pytest.raises(core.requests.ConnectionError):
                send(f"http://localhost:{srv.server_address[1]}/x", headers={"Authorization": "Bearer k"}, timeout=5)
        assert seen == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_relay_test_says_why_when_its_name_led_elsewhere(monkeypatch):
    import sync
    srv, seen = _local_server()
    monkeypatch.setattr(core, "private_peer", lambda address: False)
    try:
        out = sync.test_relay(f"http://localhost:{srv.server_address[1]}", "tok")
        assert not out["ok"] and out["message"] == core.PLAIN_HTTP_ELSEWHERE and seen == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_https_connections_are_not_affected():
    import urllib3.connectionpool
    assert urllib3.connectionpool.HTTPSConnectionPool.ConnectionCls is not core.PrivatePeerConnection
    assert urllib3.connectionpool.HTTPConnectionPool.ConnectionCls is core.PrivatePeerConnection
