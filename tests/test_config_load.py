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


# ---- a failure to OPEN the file is not a damaged file (final review, Windows high 1) ----------------------------------

def _opener_failing(real_open, path, times):
    calls = {"n": 0}

    def fake(file, *a, **kw):
        if os.path.abspath(str(file)) == os.path.abspath(path) and calls["n"] < times:
            calls["n"] += 1
            raise PermissionError(13, "The process cannot access the file (sharing violation)")
        return real_open(file, *a, **kw)

    return fake, calls


def test_a_blip_while_opening_is_retried_and_the_file_is_read(appdata, monkeypatch):
    path = _write(appdata, json.dumps({"language": "de"}).encode())
    fake, calls = _opener_failing(open, path, 2)
    monkeypatch.setattr("builtins.open", fake)
    assert core.load_config()["language"] == "de"
    assert calls["n"] == 2
    assert not glob.glob(path + ".bad-*")


def test_a_file_that_stays_unopenable_is_not_moved_aside_nor_overwritten(appdata, monkeypatch):
    original = json.dumps({"language": "de", "api_key": "k-keep"}).encode()
    path = _write(appdata, original)
    fake, _ = _opener_failing(open, path, 10 ** 6)
    with monkeypatch.context() as m:
        m.setattr("builtins.open", fake)
        cfg = core.load_config()
    assert cfg["language"] == core.DEFAULT_CONFIG["language"]       # defaults for this run only
    assert not glob.glob(path + ".bad-*")
    with open(path, "rb") as f:
        assert f.read() == original
    # nothing in this run may save the defaults over the good file
    with pytest.raises(OSError):
        core.save_config(cfg)
    with open(path, "rb") as f:
        assert f.read() == original


def test_saving_works_again_once_the_file_has_been_read(appdata, monkeypatch):
    path = _write(appdata, json.dumps({"language": "de"}).encode())
    fake, _ = _opener_failing(open, path, 10 ** 6)
    with monkeypatch.context() as m:
        m.setattr("builtins.open", fake)
        core.load_config()
    cfg = core.load_config()            # the file is readable now
    assert cfg["language"] == "de"
    cfg["language"] = "fr"
    core.save_config(cfg)
    assert core.load_config()["language"] == "fr"


def test_invalid_utf8_is_still_moved_aside(appdata):
    path = _write(appdata, b'{"language": "caf\xff"}')
    assert core.load_config()["language"] == core.DEFAULT_CONFIG["language"]
    assert glob.glob(path + ".bad-*")


def test_a_stat_error_is_not_taken_for_a_missing_file(appdata, monkeypatch):
    original = json.dumps({"language": "de"}).encode()
    path = _write(appdata, original)
    real_stat, real_open = os.stat, open
    fake_open, _ = _opener_failing(real_open, path, 10 ** 6)

    def fake_stat(p, *a, **kw):
        if os.path.abspath(str(p)) == os.path.abspath(path):
            raise PermissionError(13, "delete pending")
        return real_stat(p, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(os, "stat", fake_stat)
        m.setattr("builtins.open", fake_open)
        core.load_config()
    with open(path, "rb") as f:
        assert f.read() == original


def test_a_list_or_number_where_text_or_a_switch_belongs_falls_back_to_the_default(appdata):
    # DAT-7 of the v2 review: a non-text default_style broke every dictation (codemode lower())
    _write(appdata, json.dumps({"default_style": ["formal"], "user_context": 5, "language": {"x": 1},
                                "cleanup": "no", "keep_history": 0}).encode())
    cfg = core.load_config()
    assert cfg["default_style"] == "neutral" and cfg["user_context"] == "" and cfg["language"] == ""
    assert cfg["cleanup"] is True and cfg["keep_history"] is True
    out = core.process_text(dict(cfg, cleanup=False), "hello there friend", "x.exe", "")
    assert out[0]


def test_the_old_default_cleanup_min_words_3_becomes_4_once(appdata):
    # cqf (final review I2, controller ruling): an older config.json holds the old default 3; it becomes 4 like Android,
    # once (cleanup_min_words_v marks it), so a 3 set after the upgrade is kept
    _write(appdata, json.dumps({"cleanup_min_words": 3, "language": "de"}).encode())
    cfg = core.load_config()
    assert cfg["cleanup_min_words"] == 4 and cfg["language"] == "de"
    core.update_config(lambda c: c.__setitem__("cleanup_min_words", 3))   # the user sets 3 again in Settings
    assert core.load_config()["cleanup_min_words"] == 3
    with open(core.config_path(), encoding="utf-8") as f:
        assert json.load(f)["cleanup_min_words_v"] == core.DEFAULT_CONFIG["cleanup_min_words_v"]


def test_the_cleanup_min_words_migration_leaves_other_values_and_new_installs_alone(appdata):
    _write(appdata, json.dumps({"cleanup_min_words": 6}).encode())
    assert core.load_config()["cleanup_min_words"] == 6
    os.remove(core.config_path())
    assert core.load_config()["cleanup_min_words"] == 4   # a new install: the defaults, with the marker
    with open(core.config_path(), encoding="utf-8") as f:
        assert "cleanup_min_words_v" in json.load(f)
