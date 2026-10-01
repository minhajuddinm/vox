r"""The test-suite isolation in tests/conftest.py: every test gets its own profile folders, and a test that reaches for
the real Vox profile (the real %APPDATA%\Vox, ~/.config/Vox, ...) is stopped with an error instead of writing there."""
import os
import sqlite3

import pytest

import conftest   # tests/conftest.py (pytest puts tests/ on sys.path)


def under(path, root):
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root + os.sep)


def test_every_test_runs_with_its_own_profile_folders(tmp_path_factory):
    base = str(tmp_path_factory.getbasetemp())
    for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        assert under(os.environ[name], base), name
        assert os.path.isdir(os.environ[name]), name
    assert under(os.path.expanduser("~"), base)


def test_the_real_vox_profile_is_refused(tmp_path):
    assert conftest.GUARDED, "no guarded paths were recorded"
    for root in conftest.GUARDED:
        probe = os.path.join(root, "__guard_probe__")
        with pytest.raises(RuntimeError, match="real profile"):
            open(probe, "w")
        with pytest.raises(RuntimeError, match="real profile"):
            os.mkdir(os.path.join(root, "__guard_probe_dir__"))
        with pytest.raises(RuntimeError, match="real profile"):
            sqlite3.connect(os.path.join(root, "__guard_probe__.db"))
        assert not os.path.exists(probe)
        assert not os.path.exists(os.path.join(root, "__guard_probe_dir__"))


def test_a_test_may_set_its_own_appdata(tmp_path, monkeypatch):
    import vox_core
    monkeypatch.setenv("APPDATA", str(tmp_path / "mine"))
    folder = vox_core.data_dir()
    assert under(folder, tmp_path / "mine") and os.path.isdir(folder)
    with open(os.path.join(folder, "x.txt"), "w") as f:
        f.write("ok")


def test_other_folders_are_not_guarded(tmp_path):
    with open(tmp_path / "plain.txt", "w") as f:
        f.write("ok")
