"""The extra shortcuts (hotkeys.py): the pure parse, the conflict rules and the tap-or-hold rule. No keyboard."""
import pytest

import hotkeys


def parse(text, mods_only=False):
    return hotkeys.parse(text, mods_only)


@pytest.mark.parametrize("text,canonical,vk,label", [
    ("shift+alt+z", "alt+shift+z", 0x5A, "Alt + Shift + Z"),
    ("Ctrl+Win+Space", "ctrl+cmd+space", 0x20, "Ctrl + Win + Space"),
    (" win + ctrl + space ", "ctrl+cmd+space", 0x20, "Ctrl + Win + Space"),
    ("alt+f12", "alt+f12", 0x7B, "Alt + F12"),
    ("control+7", "ctrl+7", 0x37, "Ctrl + 7"),
])
def test_keyed_shortcuts_are_normalised(text, canonical, vk, label):
    chord, problem = parse(text)
    assert problem == "" and chord.text == canonical and chord.vk == vk and chord.label == label


@pytest.mark.parametrize("text", ["", "  ", None])
def test_empty_is_off(text):
    assert parse(text) == (None, "") and parse(text, True) == (None, "")


@pytest.mark.parametrize("text", ["z", "shift+z", "ctrl+alt", "ctrl+z+x", "ctrl+ctrl+z", "ctrl+f13", "ctrl+f01", "ctrl+;",
                                  "ctrl++z", "hello", 42])
def test_bad_keyed_shortcuts_are_refused_with_a_hint(text):
    chord, problem = parse(text)
    assert chord is None and "shift+alt+z" in problem


def test_a_modifiers_only_shortcut_needs_two_modifiers_and_not_only_shift():
    assert parse("ctrl+cmd+alt", True)[0].text == "ctrl+alt+cmd"
    assert parse("ctrl+cmd+alt", True)[0].vk is None
    assert parse("Win + Alt", True)[0].label == "Alt + Win"
    for bad in ("ctrl", "shift", "ctrl+alt+z", "shift+shift"):
        chord, problem = parse(bad, True)
        assert chord is None and "ctrl+cmd+alt" in problem


def test_the_dictation_shortcut_as_modifier_names():
    assert hotkeys.dictation_mods(["ctrl_l", "cmd"]) == {"ctrl", "cmd"}
    assert hotkeys.dictation_mods(["ctrl_r"]) == {"ctrl"}
    assert hotkeys.dictation_mods(None) == {"ctrl", "cmd"}
    assert hotkeys.dictation_mods(["bogus"]) == {"ctrl", "cmd"}


def defaults(**cfg):
    base = {"hotkey": ["ctrl_l", "cmd"], "note_hotkey": "ctrl+alt+n"}
    base.update(cfg)
    return base


def test_the_defaults_agree_with_each_other():
    out = hotkeys.check(defaults())
    # final fixes (windows 8): Ctrl + Win + Space is Windows' own "switch keyboard language", so hands-free is off by default
    assert out["hands_free_hotkey"] == (None, "")
    assert out["paste_last_hotkey"][0].text == "alt+shift+z"
    assert out["copy_last_hotkey"] == (None, "") and out["command_hotkey"] == (None, "")
    assert all(problem == "" for _, problem in out.values())
    assert hotkeys.check(defaults(hands_free_hotkey="ctrl+cmd+h"))["hands_free_hotkey"][0].text == "ctrl+cmd+h"   # extends Ctrl + Win


def test_the_defaults_match_the_config_defaults():
    import vox_core as core
    for name, (_, default, _) in hotkeys.SHORTCUTS.items():
        assert core.DEFAULT_CONFIG[name] == default
    assert core.DEFAULT_CONFIG["hotkey_style"] == "classic"


def test_a_shortcut_that_holds_the_dictation_shortcut_is_refused_unless_it_extends_it():
    out = hotkeys.check(defaults(paste_last_hotkey="ctrl+cmd+v", command_hotkey="ctrl+cmd+alt"))
    assert out["paste_last_hotkey"][0] is None and "dictation" in out["paste_last_hotkey"][1]
    assert out["command_hotkey"][0].text == "ctrl+alt+cmd"


def test_edit_by_voice_cannot_be_the_dictation_shortcut_or_a_part_of_it():
    assert "dictation shortcut" in hotkeys.check(defaults(command_hotkey="ctrl+cmd"))["command_hotkey"][1]
    out = hotkeys.check(defaults(hotkey=["ctrl", "alt", "shift"], note_hotkey="", command_hotkey="ctrl+alt"))
    assert out["command_hotkey"][0] is None and "part of your dictation" in out["command_hotkey"][1]


def test_duplicates_are_refused_and_named():
    out = hotkeys.check(defaults(paste_last_hotkey="ctrl+alt+n"))
    assert out["paste_last_hotkey"] == (None, "Already used by the note shortcut.")
    out = hotkeys.check(defaults(copy_last_hotkey="Alt+Shift+Z"))
    assert out["copy_last_hotkey"] == (None, "Already used by the paste-last shortcut.")
    assert out["paste_last_hotkey"][1] == ""                          # the first one keeps working


def test_modifiers_only_keys_that_are_part_of_another_shortcut_are_refused():
    out = hotkeys.check(defaults(command_hotkey="ctrl+alt"))             # Ctrl + Alt + N would start an edit
    assert out["command_hotkey"][0] is None and "note shortcut" in out["command_hotkey"][1]


def test_a_bad_text_is_reported_and_the_rest_still_work():
    out = hotkeys.check(defaults(hands_free_hotkey="space"))
    assert out["hands_free_hotkey"][0] is None and out["hands_free_hotkey"][1]
    assert out["paste_last_hotkey"][0] is not None


def test_turning_a_default_off_is_kept_off():
    assert hotkeys.check(defaults(hands_free_hotkey="", paste_last_hotkey=""))["paste_last_hotkey"] == (None, "")


def test_the_style_setting():
    assert hotkeys.style({}) == "classic"
    assert hotkeys.style({"hotkey_style": "hold_or_tap"}) == "hold_or_tap"
    assert hotkeys.style({"hotkey_style": "toggle"}) == "classic"


@pytest.mark.parametrize("style,held,command,action", [
    ("classic", 0.1, False, "cancel"),
    ("classic", 0.5, False, "stop"),
    ("hold_or_tap", 0.1, False, "latch"),
    ("hold_or_tap", 0.3, False, "stop"),
    ("hold_or_tap", 0.1, True, "cancel"),
])
def test_tap_or_hold(style, held, command, action):
    assert hotkeys.tap_action(style, held, command) == action


def test_space_cannot_be_used_when_it_is_part_of_a_custom_dictation_shortcut():
    out = hotkeys.check(defaults(hotkey=["ctrl", "space"], note_hotkey="", hands_free_hotkey="ctrl+cmd+space"))
    assert out["hands_free_hotkey"][0] is None and "Space" in out["hands_free_hotkey"][1]


def test_the_window_bridge_saves_a_good_shortcut_normalised_and_refuses_a_clash(tmp_path, monkeypatch):
    ui_app = pytest.importorskip("ui_app")
    import vox_core as core
    monkeypatch.setenv("APPDATA", str(tmp_path))
    api = object.__new__(ui_app.Api)
    assert api.set_shortcut("copy_last_hotkey", " Shift + Alt + X ") == {"value": "alt+shift+x", "label": "Alt + Shift + X", "problem": ""}
    assert core.load_config()["copy_last_hotkey"] == "alt+shift+x"
    bad = api.set_shortcut("command_hotkey", "ctrl+cmd")              # the dictation shortcut itself
    assert bad["problem"] and core.load_config()["command_hotkey"] == ""
    assert api.set_shortcut("paste_last_hotkey", "") == {"value": "", "label": "", "problem": ""}
    assert core.load_config()["paste_last_hotkey"] == ""
    assert api.set_shortcut("hotkey", "ctrl+alt")["problem"]          # only the extra shortcuts go through here
    assert api.shortcut_problems() == {n: "" for n in hotkeys.SHORTCUTS}
    api.set_note_hotkey("alt+shift+x")                                    # the note shortcut wins over the copy-last one
    assert "note shortcut" in api.shortcut_problems()["copy_last_hotkey"]
