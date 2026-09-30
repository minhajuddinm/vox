"""Connection warm-up at hotkey-down: one GET per distinct server, with the right key, on the shared session."""
import pytest

import vox_core as core


class Resp:
    status_code = 200


def capture_gets(monkeypatch):
    calls = []
    monkeypatch.setattr(core._session, "get", lambda url, **kw: calls.append((url, kw.get("headers"))) or Resp())
    return calls


def test_warm_opens_one_connection_for_a_shared_server(monkeypatch):
    calls = capture_gets(monkeypatch)
    core.warm({"api_key": "k"}).join(5)
    assert calls == [(core.BASE + "/models", {"Authorization": "Bearer k"})]


def test_warm_opens_both_servers_and_keeps_keys_apart(monkeypatch):
    calls = capture_gets(monkeypatch)
    cfg = {"api_key": "main", "llm_base_url": "http://localhost:11434/v1"}
    core.warm(cfg).join(5)
    assert sorted(calls) == sorted([(core.BASE + "/models", {"Authorization": "Bearer main"}),
                                    ("http://localhost:11434/v1/models", {})])


def test_warm_ignores_failures(monkeypatch):
    def boom(url, **kw):
        raise core.requests.ConnectionError("down")

    monkeypatch.setattr(core._session, "get", boom)
    core.warm({"api_key": "k"}).join(5)   # must not raise


@pytest.mark.real_session
def test_posts_go_through_the_shared_session(monkeypatch):
    seen = []

    class R:
        status_code = 200

    monkeypatch.setattr(core._session, "post", lambda url, **kw: seen.append(url) or R())
    core.post_with_retry("http://localhost/x", data={})
    assert seen == ["http://localhost/x"]


def test_other_tests_still_see_requests_post(monkeypatch):
    seen = []

    class R:
        status_code = 200

    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.append(url) or R())
    core.post_with_retry("http://localhost/y", data={})
    assert seen == ["http://localhost/y"]
