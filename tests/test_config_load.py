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


# ---- values of the wrong type (R1-5) ---------------------------------------------------------------------------------

def test_wrong_typed_values_fall_back_to_the_defaults(appdata):
    _write(appdata, json.dumps({"dictionary": None, "people": None, "app_styles": [], "hotkey": None,
                                "language": None}).encode())
    cfg = core.load_config()
    assert cfg["dictionary"] == [] and cfg["people"] == []
    assert cfg["app_styles"] == core.DEFAULT_CONFIG["app_styles"]
    assert cfg["hotkey"] == core.DEFAULT_CONFIG["hotkey"]
    assert cfg["language"] == ""
    out = core.process_text(dict(cfg, cleanup=False), "hello there friend", "x.exe", "")
    assert out[0]


def test_list_settings_keep_only_text_items(appdata):
    _write(appdata, json.dumps({"dictionary": ["a", 3, None, "b=>c"], "people": [1, "Ann"], "hotkey": ["ctrl_l", 7]}).encode())
    cfg = core.load_config()
    assert cfg["dictionary"] == ["a", "b=>c"]
    assert cfg["people"] == ["Ann"]
    assert cfg["hotkey"] == ["ctrl_l"]


def test_numbers_and_bools_are_left_alone(appdata):
    _write(appdata, json.dumps({"cleanup_min_words": "5", "cleanup": False}).encode())
    cfg = core.load_config()
    assert cfg["cleanup_min_words"] == "5" and cfg["cleanup"] is False


# ---- history with a damaged tail (R1-6) ------------------------------------------------------------------------------

def test_history_survives_an_invalid_byte_and_a_cut_off_last_line(appdata):
    with open(core.history_path(), "wb") as f:
        f.write(b'{"t": 1, "text": "ok"}\n{"t": 2, "text": "caf\xc3')
    core.add_history({"t": 3})
    assert [e["t"] for e in core.read_history()] == [1, 3]


def test_history_with_a_bad_byte_inside_a_line_is_still_readable(appdata):
    with open(core.history_path(), "wb") as f:
        f.write(b'{"t": 1, "text": "caf\xff"}\n{"t": 2}\n[1, 2]\n')
    assert [e["t"] for e in core.read_history()] == [1, 2]
