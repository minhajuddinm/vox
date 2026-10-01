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
