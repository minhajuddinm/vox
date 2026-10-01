"""The note hotkey (branch E, task E5): the pure parse and duplicate rule. No keyboard, no audio, no network."""
import pytest

import session


def parse(text, dictation=("ctrl_l", "cmd")):
    return session.parse_note_hotkey(text, dictation)


def test_the_default_is_ctrl_alt_n_and_is_fine_next_to_the_default_dictation_shortcut():
    hk, problem = parse(session.NOTE_HOTKEY_DEFAULT)
    assert problem == "" and hk.text == "ctrl+alt+n" and hk.mods == ("ctrl", "alt") and hk.vk == ord("N")
    assert hk.label == "Ctrl + Alt + N"


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_means_off_and_is_not_a_problem(text):
    assert parse(text) == (None, "")


@pytest.mark.parametrize("text,canonical,vk", [
    ("Ctrl+Alt+N", "ctrl+alt+n", 0x4E),
    (" ctrl + alt + n ", "ctrl+alt+n", 0x4E),
    ("alt+ctrl+n", "ctrl+alt+n", 0x4E),               # modifiers always in the same order
    ("control+shift+7", "ctrl+shift+7", 0x37),
    ("win+alt+m", "alt+cmd+m", 0x4D),
    ("windows+f9", "cmd+f9", 0x78),
    ("option+f12", "alt+f12", 0x7B),
    ("ctrl+f1", "ctrl+f1", 0x70),
])
def test_valid_combos_are_normalised(text, canonical, vk):
    hk, problem = parse(text)
    assert problem == "" and hk.text == canonical and hk.vk == vk


@pytest.mark.parametrize("text", [
    "n",                 # no modifier: it would fire while typing
    "shift+n",           # shift alone is not enough either
    "ctrl+alt",          # no main key
    "ctrl+alt+n+m",      # two main keys
    "ctrl+ctrl+n",       # a key twice
    "ctrl+alt+nn",       # not one key
    "ctrl+alt+space",    # space is the dictation shortcut's key
    "ctrl+alt+f13",
    "ctrl+alt+f0",
    "ctrl+alt+;",
    "ctrl++n",
    "ctrl+alt+",
    "+",
    "hello",
    42,
])
def test_invalid_combos_are_refused_with_a_hint(text):
    hk, problem = parse(text)
    assert hk is None and "ctrl+alt+n" in problem


@pytest.mark.parametrize("dictation", [("ctrl", "alt"), ["ctrl_l", "alt"], ("alt_r", "ctrl_r")])
def test_a_combo_that_contains_the_dictation_shortcut_is_refused(dictation):
    # pressing Ctrl and Alt would already start a dictation before the N went down
    hk, problem = parse("ctrl+alt+n", dictation)
    assert hk is None and "dictation" in problem


def test_ctrl_win_n_is_refused_with_the_default_dictation_shortcut():
    assert parse("ctrl+cmd+n")[0] is None
    assert parse("win+ctrl+n")[0] is None


def test_a_dictation_shortcut_that_is_only_a_part_of_the_combo_is_a_conflict_a_different_one_is_not():
    assert parse("ctrl+alt+n", ("ctrl_r",))[0] is None          # right Ctrl is a Ctrl
    assert parse("ctrl+alt+n", ("alt_r",))[0] is None
    assert parse("ctrl+alt+n", ("ctrl", "shift"))[0].text == "ctrl+alt+n"
    assert parse("ctrl+alt+n", ("ctrl_l", "cmd"))[0].text == "ctrl+alt+n"


def test_a_missing_or_unusable_dictation_setting_counts_as_the_default_ctrl_win():
    assert parse("ctrl+cmd+n", None)[0] is None
    assert parse("ctrl+cmd+n", [])[0] is None
    assert parse("ctrl+cmd+n", ["bogus"])[0] is None
    assert parse("ctrl+cmd+n", "ctrl")[0] is None                 # not a list: the default
    assert parse("ctrl+alt+n", None)[0].text == "ctrl+alt+n"


def test_note_hotkey_reads_both_settings_from_the_config():
    assert session.note_hotkey({"note_hotkey": "ctrl+alt+n", "hotkey": ["ctrl_l", "cmd"]})[0].text == "ctrl+alt+n"
    assert session.note_hotkey({"note_hotkey": "ctrl+alt+n", "hotkey": ["ctrl", "alt"]})[0] is None
    assert session.note_hotkey({"note_hotkey": ""}) == (None, "")
    assert session.note_hotkey({}) == (None, "")


def test_the_default_setting_is_ctrl_alt_n():
    import vox_core as core
    assert core.DEFAULT_CONFIG["note_hotkey"] == session.NOTE_HOTKEY_DEFAULT == "ctrl+alt+n"


def test_the_window_bridge_saves_a_good_shortcut_normalised_and_refuses_a_bad_one(tmp_path, monkeypatch):
    ui_app = pytest.importorskip("ui_app")
    import vox_core as core
    monkeypatch.setenv("APPDATA", str(tmp_path))
    api = object.__new__(ui_app.Api)
    assert api.set_note_hotkey(" Win + Alt + M ") == {"value": "alt+cmd+m", "label": "Alt + Win + M", "problem": ""}
    assert core.load_config()["note_hotkey"] == "alt+cmd+m"
    bad = api.set_note_hotkey("ctrl+cmd+n")                      # holds the dictation shortcut
    assert bad["problem"] and core.load_config()["note_hotkey"] == "alt+cmd+m"   # not saved
    assert api.set_note_hotkey("") == {"value": "", "label": "", "problem": ""}
    assert core.load_config()["note_hotkey"] == ""


def test_the_window_bridge_says_when_the_saved_shortcut_clashes_with_a_new_dictation_shortcut(tmp_path, monkeypatch):
    ui_app = pytest.importorskip("ui_app")
    import vox_core as core
    monkeypatch.setenv("APPDATA", str(tmp_path))
    api = object.__new__(ui_app.Api)
    assert api.note_hotkey_problem() == ""                        # the defaults agree
    assert api.set_hotkey("ctrl+alt") == "Ctrl + Alt"             # the dictation shortcut becomes Ctrl + Alt
    assert "dictation" in api.note_hotkey_problem()               # the saved Ctrl + Alt + N is now ignored by the engine
    assert api.note_hotkey_problem("ctrl+shift+n") == ""          # a text that is being typed is checked, not the saved one
