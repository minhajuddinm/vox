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
