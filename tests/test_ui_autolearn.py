"""Learn from my corrections in both apps' settings and Dictionary pages, the Android service config, and the Windows bridge
that removes a learned fix. Static checks on the page text plus the bridge on a temporary profile."""
import os
import sys
from unittest.mock import MagicMock

import pytest

import autolearn
import vox_core as core

ROOT = os.path.join(os.path.dirname(__file__), "..")
PAGES = {"windows": os.path.join(ROOT, "windows", "ui", "index.html"),
         "android": os.path.join(ROOT, "android", "assets", "index.html")}
ANDROID = os.path.join(ROOT, "android")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("name", ["windows", "android"])
def test_the_switch_sits_in_privacy_on_by_default_and_saves(name):
    html = read(PAGES[name])
    privacy = html[html.index("<h2>Privacy</h2>"):]
    assert 'id="auto-learn"' in privacy.split("<h2>", 2)[1] and 'id="auto-learn-d"' in privacy
    assert "Learn from my corrections" in privacy
    assert '$("auto-learn").checked = c.auto_learn !== false' in html
    assert "save({ auto_learn: e.target.checked }" in html
    assert '$("auto-learn-d").textContent = AUTO_LEARN_TEXT' in html


@pytest.mark.parametrize("name", ["windows", "android"])
def test_the_switch_text_says_how_long_and_what_is_kept(name):
    html = read(PAGES[name])
    assert "For up to 3 minutes after Vox types, or until you send it" in html
    assert "Only the changed words are kept" in html and "Password fields are never read" in html


@pytest.mark.parametrize("name", ["windows", "android"])
def test_the_dictionary_page_lists_recently_learned_with_remove(name):
    html = read(PAGES[name])
    assert "<h2>Recently learned</h2>" in html and 'id="learned"' in html
    assert '$("learned").innerHTML = learnedHtml(S.config.learned_log)' in html
    assert "data-unlearn" in html


def test_the_pages_call_the_remove_bridges_that_exist():
    assert "api().learned_remove(" in read(PAGES["windows"]) and "def learned_remove(" in read(ROOT, "windows", "ui_app.py")
    main = read(ANDROID, "src", "com", "minhaj", "vox", "MainActivity.java")
    assert "V.learnedRemove(" in read(PAGES["android"]) and "public String learnedRemove(String t)" in main
    assert 'cfg.put("auto_learn", prefs.autoLearn())' in main and 'e.putBoolean("auto_learn"' in main


def test_the_accessibility_service_gets_text_changes_and_says_so():
    # Final fixes (android 3): text changes are asked for only while a watch runs, never in the static config, so typing in
    # other apps does not wake Vox when auto-learn is off or no watch is armed.
    assert "typeViewTextChanged" not in read(ANDROID, "res", "xml", "accessibility_config.xml")
    strings = read(ANDROID, "res", "values", "strings.xml")
    assert "for up to 3 minutes after Vox types, or until you send it" in strings and "password fields are never read" in strings
    svc = read(ANDROID, "src", "com", "minhaj", "vox", "VoxAccessibilityService.java")
    assert "if (typed) armLearning(targetPkg, text);" in svc
    assert "e.isPassword() || src.isPassword()" in svc
    assert 'Log.i("vox", "auto-learn: learned " + added.size() + " corrections")' in svc
    arm = svc[svc.index("private void armLearning("):svc.index("private void endLearning(")]
    end = svc[svc.index("private void endLearning("):svc.index("private void onTextChanged(")]
    changed = svc[svc.index("private void onTextChanged("):svc.index("private void learnTick(")]
    assert "textEvents(true)" in arm and arm.index("autoLearn()") < arm.index("textEvents(true)")
    assert "textEvents(false)" in end
    assert "autoLearn()" in changed       # the setting turned off while a watch runs: nothing more is read


def test_learned_settings_stay_on_the_device():
    import sync
    assert "auto_learn" not in sync.PROFILE_FIELDS and "learned_log" not in sync.PROFILE_FIELDS
    prefs = read(ANDROID, "src", "com", "minhaj", "vox", "Prefs.java")
    profile = prefs[prefs.index("public Map<String, Object> profileStored()"):prefs.index("public void applyProfile")]
    assert "learned_log" not in profile and "auto_learn" not in profile


@pytest.fixture
def api(monkeypatch, tmp_path):
    """ui_app imports pyperclip, webview and (through meeting.py) numpy: stubbed where missing, as in test_ui_app_bridge."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(core, "_config_unread", False)   # an earlier test may leave "unreadable" set
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


def test_remove_takes_the_learned_fix_out_of_the_dictionary(api):
    cfg = dict(core.DEFAULT_CONFIG, dictionary=["LoomXR"])
    parts, _ = autolearn.apply_learned(cfg, [["you vrag", "Yuvraj"]], now=42.0)
    cfg.update(parts)
    core.save_config(cfg)
    out = api.learned_remove(42.0)
    assert out == {"dictionary": ["LoomXR"], "learned_log": []}
    assert core.load_config()["dictionary"] == ["LoomXR"]


def test_remove_with_a_bad_time_changes_nothing(api):
    core.save_config(dict(core.DEFAULT_CONFIG, dictionary=["LoomXR"]))
    assert api.learned_remove("not a time") == {"dictionary": ["LoomXR"], "learned_log": []}
