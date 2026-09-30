import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "windows"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "relay"))

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "real_session: use vox_core's real shared HTTP session (no requests.post routing)")


@pytest.fixture(autouse=True)
def route_posts_through_requests(request, monkeypatch):
    """Tests replace requests.post; vox_core posts through a shared session, so route it back unless a test wants the session."""
    if request.node.get_closest_marker("real_session"):
        return
    try:
        import vox_core
    except Exception:   # relay-only runs (CI) have no Windows packages
        return
    monkeypatch.setattr(vox_core, "_post", lambda url, **kw: vox_core.requests.post(url, **kw))
