"""Dictionary and People edits change one item in the file's current list, not a whole list from a stale page."""
import sys
from unittest.mock import MagicMock

import pytest

import vox_core as core


@pytest.fixture
def api(monkeypatch, tmp_path):
    """windows/ui_app.py imports pyperclip and webview, and through meeting.py numpy; CI's test job has only requests and
    pytest. Stub whichever is missing for this test only and drop every module imported while stubs were active."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    before = set(sys.modules)
    stubbed = False
    for name in ("pyperclip", "webview", "numpy"):
        try:
            __import__(name)
        except ImportError:
            monkeypatch.setitem(sys.modules, name, MagicMock())
            stubbed = True
    import ui_app
    yield ui_app.Api.__new__(ui_app.Api)
    if stubbed:
        for name in set(sys.modules) - before:
            sys.modules.pop(name, None)


def on_disk(key, value):
    """What the sync thread does: it writes a list received from another device into config.json."""
    cfg = core.load_config()
    cfg[key] = value
    core.save_config(cfg)


def test_adding_a_word_keeps_a_word_synced_from_another_device(api):
    on_disk("dictionary", ["a"])
    on_disk("dictionary", ["a", "phone"])   # the page still shows ["a"]
    assert api.dict_add_term("b") == ["a", "phone", "b"]
    assert core.load_config()["dictionary"] == ["a", "phone", "b"]


def test_removing_a_word_removes_only_that_word(api):
    on_disk("dictionary", ["a", "phone", "b => c"])
    assert api.dict_remove_term("a") == ["phone", "b => c"]
    assert core.load_config()["dictionary"] == ["phone", "b => c"]


def test_replacements_are_added_and_removed_by_value(api):
    on_disk("dictionary", ["phone", "x => y"])
    assert api.dict_add_repl("wrong", "right") == ["phone", "x => y", "wrong => right"]
    assert api.dict_add_repl("wrong", "right") == ["phone", "x => y", "wrong => right"]   # no duplicate line
    assert api.dict_remove_repl("x", "y") == ["phone", "wrong => right"]
    assert core.load_config()["dictionary"] == ["phone", "wrong => right"]


def test_people_add_and_remove_keep_names_from_another_device(api):
    on_disk("people", ["Ann"])
    on_disk("people", ["Ann", "Bob"])   # synced after the page was drawn
    assert api.people_add("Cy") == ["Ann", "Bob", "Cy"]
    assert api.people_add("Cy") == ["Ann", "Bob", "Cy"]
    assert api.people_remove("Ann") == ["Bob", "Cy"]
    assert core.load_config()["people"] == ["Bob", "Cy"]


def test_empty_input_changes_nothing(api):
    on_disk("dictionary", ["a"])
    assert api.dict_add_term("  ") == ["a"]
    assert api.dict_add_repl("", "x") == ["a"]
    assert api.people_add("") == core.load_config()["people"]


# ---- the meeting bridge refuses ids that are not meeting ids (R3-H1 / C-U7) ---------------------------------------------

def test_the_meeting_bridge_refuses_a_bad_id_and_leaves_the_data_folder_alone(api, tmp_path):
    import os
    import meeting
    data_dir = os.path.dirname(meeting.meetings_dir())
    sentinel = os.path.join(data_dir, "config.json")
    with open(sentinel, "w", encoding="utf-8") as f:
        f.write("{}")
    for bad in ("..", "", None):
        assert api.meeting_delete(bad) is False
        assert api.meeting_save_notes(bad, "x") is False
        assert api.meeting_set_done(bad, 0, True) is False
        assert api.meeting_rename(bad, "x") == ""
    assert os.path.exists(sentinel) and not os.path.exists(os.path.join(data_dir, "my_notes.md"))


def test_the_meeting_bridge_still_deletes_a_real_meeting(api):
    import os
    import meeting
    d = os.path.join(meeting.meetings_dir(), "20261001-120000")
    os.makedirs(d)
    assert api.meeting_delete("20261001-120000") is True
    assert not os.path.exists(d)


def test_meeting_open_does_not_open_a_path_built_from_a_bad_id(api, monkeypatch):
    import ui_app
    opened = []
    monkeypatch.setattr(ui_app.os, "startfile", lambda p: opened.append(p), raising=False)
    api.meeting_open("..")
    assert opened == []


# ---- a save that fails is reported to the page (DAT-1) ---------------------------------------------------------------

def test_a_failed_save_answers_false_and_keeps_the_file(api, monkeypatch):
    on_disk("language", "de")

    def busy(change):
        raise OSError("another save of config.json is still running")

    monkeypatch.setattr(core, "update_config", busy)
    assert api.save_config({"language": "fr"}) is False


def test_a_page_save_keeps_a_setting_saved_meanwhile_by_the_engine(api):
    on_disk("dictionary", ["phone"])          # the sync thread, after the page was drawn
    assert api.save_config({"language": "de"}) is True
    cfg = core.load_config()
    assert cfg["language"] == "de" and cfg["dictionary"] == ["phone"]


def test_the_page_says_when_a_save_failed():
    import os
    import re
    page = open(os.path.join(os.path.dirname(__file__), "..", "windows", "ui", "index.html"), encoding="utf-8").read()
    save = re.search(r"async function save\(part, msg = \"Saved\"\) \{(.*?)\n\}", page, re.S).group(1)
    assert "catch" in save and "ok === false" in save and "refresh()" in save
    assert "Not saved" in save


# ---- Copy buttons use Vox's clipboard markers (PRV-4) ----------------------------------------------------------------

def test_copy_marks_the_text_like_a_dictation(api, monkeypatch):
    import types
    calls = []
    fake = types.ModuleType("paste")
    fake.SystemDeps = lambda: types.SimpleNamespace(clip_set=lambda text, history=False: calls.append((text, history)))
    monkeypatch.setitem(sys.modules, "paste", fake)
    on_disk("clipboard_history", False)
    assert api.copy("a dictation") is True
    on_disk("clipboard_history", True)
    assert api.copy("another") is True
    assert calls == [("a dictation", False), ("another", True)]   # never the cloud clipboard; Win+V as the switch says


def test_copy_that_fails_answers_false(api, monkeypatch):
    import types
    fake = types.ModuleType("paste")

    def busy(text, history=False):
        raise OSError("clipboard busy")

    fake.SystemDeps = lambda: types.SimpleNamespace(clip_set=busy)
    monkeypatch.setitem(sys.modules, "paste", fake)
    assert api.copy("x") is False


# ---- snippets: one item at a time, like the dictionary (DAT-9) -------------------------------------------------------

def test_adding_a_snippet_keeps_one_synced_from_another_device(api):
    on_disk("snippets", {"my address": "Flat 4"})
    on_disk("snippets", {"my address": "Flat 4", "sig": "Best, Ann"})   # the page still shows only "my address"
    assert api.snippet_set("my phone", "0123") == {"my address": "Flat 4", "sig": "Best, Ann", "my phone": "0123"}
    assert api.snippet_set("MY ADDRESS", "Flat 5") == {"sig": "Best, Ann", "my phone": "0123", "MY ADDRESS": "Flat 5"}
    assert core.load_config()["snippets"] == {"sig": "Best, Ann", "my phone": "0123", "MY ADDRESS": "Flat 5"}


def test_removing_a_snippet_removes_only_that_one(api):
    on_disk("snippets", {"a b": "x", "sig": "Best, Ann"})
    assert api.snippet_set("a b", None) == {"sig": "Best, Ann"}
    assert core.load_config()["snippets"] == {"sig": "Best, Ann"}


def test_the_page_edits_snippets_through_the_one_item_bridge_call():
    import os
    import re
    page = open(os.path.join(os.path.dirname(__file__), "..", "windows", "ui", "index.html"), encoding="utf-8").read()
    assert not re.search(r"save\(\s*\{\s*snippets\s*:", page)
    assert page.count("api().snippet_set(") == 2
