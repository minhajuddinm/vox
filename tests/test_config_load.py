"""load_config must survive a damaged config.json (BOM, truncated, wrong top-level type, read-only)."""
import glob
import json
import os
import stat

import pytest

import vox_core as core


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def _write(appdata, data):
    path = core.config_path()
    with open(path, "wb") as f:
        f.write(data)
    return path


def test_config_with_a_bom_loads(appdata):
    _write(appdata, b"\xef\xbb\xbf" + json.dumps({"language": "de"}).encode())
    assert core.load_config()["language"] == "de"


def test_truncated_config_falls_back_to_defaults_and_is_kept_aside(appdata):
    path = _write(appdata, b'{"api_')
    cfg = core.load_config()
    assert cfg["language"] == core.DEFAULT_CONFIG["language"]
    assert glob.glob(path + ".bad-*")


def test_list_at_the_top_level_falls_back_to_defaults(appdata):
    _write(appdata, b'["x", "y"]')
    assert core.load_config()["hotkey"] == core.DEFAULT_CONFIG["hotkey"]


def test_read_only_config_with_a_plain_key_does_not_raise(appdata, monkeypatch):
    path = _write(appdata, json.dumps({"api_key": "k"}).encode())

    def refuse(cfg):
        raise PermissionError("read-only")

    monkeypatch.setattr(core.secret, "available", lambda: True)
    monkeypatch.setattr(core, "save_config", refuse)
    os.chmod(path, stat.S_IREAD)
    try:
        assert core.load_config()["api_key"] == "k"
    finally:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
