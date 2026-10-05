"""CI3: `Vox.exe --selfcheck` imports every module of the app (and the relay, libsndfile and pythonnet), so a build that
leaves one out fails in CI instead of on a user's PC. The CI windows job runs it on the built exe; here it runs from
source in a child process (nothing is started: no tray, window, hotkey or microphone)."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
WINDOWS = os.path.join(ROOT, "windows")

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil", "webview"):
    pytest.importorskip(_mod)

OPTIONAL = ("soundfile", "clr")   # in requirements.lock (CI, the exe); a development venv may lack them


def test_the_list_holds_every_module_of_the_app():
    import vox_app
    here = {f[:-3] for f in os.listdir(WINDOWS) if f.endswith(".py")} - {"vox", "vox_app"}   # the two entry points
    assert set(vox_app.SELFCHECK_MODULES) == here | {"relay"} | set(vox_app.SELFCHECK_LIBS) | set(OPTIONAL)


def _imported_later():
    """Third-party modules that windows/*.py imports inside a function, a class, a try or an if (not at load time)."""
    import ast
    here = {f[:-3] for f in os.listdir(WINDOWS) if f.endswith(".py")}
    out = set()
    for f in os.listdir(WINDOWS):
        if not f.endswith(".py"):
            continue
        with open(os.path.join(WINDOWS, f), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Try, ast.If)):
                continue
            for n in ast.walk(node):
                names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else []
                for m in names:
                    top = m.split(".")[0]
                    if top not in sys.stdlib_module_names and top not in here and top != "System":   # System: clr's
                        out.add(m)
    return out


def test_the_list_holds_every_library_the_app_imports_later():
    # review 4: soundcard (meetings), icalendar and recurring_ical_events (calendar) are imported only inside functions,
    # and the window's backend only at webview.start(): a build that leaves them out passed the check before
    import vox_app
    later = _imported_later()
    assert {"soundcard", "icalendar", "recurring_ical_events"} <= later   # the finder itself works
    assert later <= set(vox_app.SELFCHECK_MODULES), later - set(vox_app.SELFCHECK_MODULES)
    assert {"webview.platforms.winforms", "webview.platforms.edgechromium", "tzdata"} <= set(vox_app.SELFCHECK_LIBS)


def test_a_missing_time_zone_database_fails_the_check(tmp_path, monkeypatch):
    import zoneinfo

    import vox_app

    def no_zones(key):
        raise zoneinfo.ZoneInfoNotFoundError("No time zone found with key " + key)

    monkeypatch.setattr(zoneinfo, "ZoneInfo", no_zones)
    report = tmp_path / "r.txt"
    assert vox_app.selfcheck(str(report), ("tzdata",)) == 1
    assert "FAIL zoneinfo: ZoneInfoNotFoundError" in report.read_text(encoding="utf-8")


def test_selfcheck_from_source_imports_every_app_module(tmp_path):
    import importlib.util
    import vox_app
    report = tmp_path / "selfcheck.txt"
    p = subprocess.run([sys.executable, os.path.join(WINDOWS, "vox_app.py"), "--selfcheck", str(report)],
                       cwd=WINDOWS, capture_output=True, timeout=120)
    lines = report.read_text(encoding="utf-8").splitlines()
    app = [m for m in vox_app.SELFCHECK_MODULES if m not in OPTIONAL]
    assert ["ok " + m for m in app] == lines[:len(app)], lines
    missing = [m for m in OPTIONAL if importlib.util.find_spec(m) is None]
    assert (p.returncode == 0) == (not missing) and lines[-1] == ("selfcheck: ok" if not missing else "selfcheck: FAILED")
    for m in OPTIONAL:
        assert ("FAIL " + m if m in missing else "ok " + m) in "\n".join(lines)


def test_a_module_that_does_not_import_fails_the_check(tmp_path, monkeypatch):
    import vox_app
    report = tmp_path / "r.txt"
    assert vox_app.selfcheck(str(report), ("vox_core", "no_such_module_vox")) == 1
    lines = report.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "ok vox_core" and lines[1].startswith("FAIL no_such_module_vox: ModuleNotFoundError")
    assert lines[-1] == "selfcheck: FAILED"


def test_missing_flac_fails_the_check(tmp_path, monkeypatch):
    import vox_app
    monkeypatch.setattr(vox_app.core, "flac_available", lambda: False)
    report = tmp_path / "r.txt"
    assert vox_app.selfcheck(str(report), ("soundfile",)) == 1
    assert "FAIL flac" in report.read_text(encoding="utf-8")
