import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "windows"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "relay"))

import pytest


# ---- test isolation: no test may read or write the real Vox profile --------------------------------------------------
# The real profile folders are recorded here, at import, before anything is patched. Every test then runs with its own
# empty APPDATA, LOCALAPPDATA, HOME, USERPROFILE and XDG_* folders (fixture below), and an audit hook stops any file,
# folder or sqlite call on one of the recorded Vox folders while a test runs. Only the Vox folders are guarded, not
# the whole profile: Windows' TEMP and pytest's own folders live under LOCALAPPDATA.
def _real_vox_folders():
    env = os.environ
    homes = {h for h in (os.path.expanduser("~"), env.get("USERPROFILE"), env.get("HOME")) if h}
    roots = []
    if env.get("APPDATA"):
        roots += [os.path.join(env["APPDATA"], "Vox"), os.path.join(env["APPDATA"], "VoxRelay")]
    if env.get("LOCALAPPDATA"):
        roots.append(os.path.join(env["LOCALAPPDATA"], "Vox"))
    if env.get("XDG_DATA_HOME"):
        roots.append(os.path.join(env["XDG_DATA_HOME"], "vox-relay"))
    if env.get("XDG_CONFIG_HOME"):
        roots.append(os.path.join(env["XDG_CONFIG_HOME"], "Vox"))
    for h in homes:
        roots += [os.path.join(h, ".config", "Vox"), os.path.join(h, ".local", "share", "vox-relay"),
                  os.path.join(h, "Library", "Application Support", "VoxRelay"), os.path.join(h, "Documents", "Vox Notes")]
    out = []
    for r in roots:
        r = os.path.normcase(os.path.abspath(r))
        if r not in out:
            out.append(r)
    return out


GUARDED = _real_vox_folders()
_GUARD_ON = False
# audit event -> positions of the arguments that are paths
_PATH_EVENTS = {"open": (0,), "os.mkdir": (0,), "os.listdir": (0,), "os.scandir": (0,), "os.remove": (0,),
                "os.rename": (0, 1), "shutil.rmtree": (0,), "sqlite3.connect": (0,)}


def _audit(event, args):
    if not _GUARD_ON or event not in _PATH_EVENTS:
        return
    for i in _PATH_EVENTS[event]:
        if i >= len(args):
            continue
        p = args[i]
        if isinstance(p, os.PathLike):
            p = os.fspath(p)
        if isinstance(p, bytes):
            p = os.fsdecode(p)
        if not isinstance(p, str) or not p:
            continue
        p = os.path.normcase(os.path.abspath(p))
        for root in GUARDED:
            if p == root or p.startswith(root + os.sep):
                raise RuntimeError(f"test touched the real profile: {p}")


sys.addaudithook(_audit)


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path_factory, monkeypatch):
    """Each test gets its own empty profile. A test that needs a particular APPDATA sets it itself afterwards
    (monkeypatch.setenv) and wins."""
    global _GUARD_ON
    p = tmp_path_factory.mktemp("profile")
    home = p / "home"
    folders = {"APPDATA": p / "Roaming", "LOCALAPPDATA": p / "Local", "USERPROFILE": home, "HOME": home,
               "XDG_DATA_HOME": home / ".local" / "share", "XDG_CONFIG_HOME": home / ".config"}
    for name, folder in folders.items():
        folder.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(folder))
    _GUARD_ON = True
    try:
        yield p
    finally:
        _GUARD_ON = False


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
