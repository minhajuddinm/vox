"""G2 install safety: what the code can do about Play Protect and "Restricted setting" on a sideloaded APK.

Checks the manifest (permissions the code really uses, targetSdk in step with the platform the sources compile against, no
isAccessibilityTool) and the Android page's install-help card (steps, the App info button, the adb alternative).
Sources and limits: documentation/specs/p9g-note-bubble.md (G2 section) and J:/Projects/.notes research (not in the repo).
"""
import glob
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
ANDROID = os.path.join(ROOT, "android")


def read(*parts):
    with open(os.path.join(ANDROID, *parts), encoding="utf-8") as f:
        return f.read()


MANIFEST = read("AndroidManifest.xml")
SOURCES = "\n".join(read(os.path.relpath(p, ANDROID)) for p in glob.glob(os.path.join(ANDROID, "src", "**", "*.java"), recursive=True))
PAGE = read("assets", "index.html")


def declared():
    return set(re.findall(r'<uses-permission android:name="android\.permission\.(\w+)"', MANIFEST))


def test_every_declared_permission_is_used_by_the_code():
    # What in the sources proves each permission is needed.
    proof = {
        "RECORD_AUDIO": "AudioRecord",
        "INTERNET": "HttpURLConnection",
        "FOREGROUND_SERVICE": "startForeground(",
        "FOREGROUND_SERVICE_MICROPHONE": "FOREGROUND_SERVICE_TYPE_MICROPHONE",
        "POST_NOTIFICATIONS": "Manifest.permission.POST_NOTIFICATIONS",
    }
    assert declared() == set(proof)
    for perm, marker in proof.items():
        assert marker in SOURCES, f"{perm}: nothing in the sources uses {marker}"


def test_vibrate_is_not_declared_because_only_view_haptics_are_used():
    assert "VIBRATE" not in declared()
    assert "Vibrator" not in SOURCES and "VibrationEffect" not in SOURCES
    assert "performHapticFeedback" in SOURCES   # needs no permission


def test_target_sdk_matches_the_build_script_and_the_installed_platform():
    target = re.search(r'android:targetSdkVersion="(\d+)"', MANIFEST).group(1)
    assert f"--target-sdk-version {target}" in read("build.sh")
    # The sources are compiled against platform android-34 (build.sh, run-tests.sh, CI): do not target above it.
    assert f"platforms/android-{target}/android.jar" in read("build.sh").replace("$SDK/", "")


def test_the_service_is_not_declared_an_accessibility_tool():
    for path in glob.glob(os.path.join(ANDROID, "res", "xml", "*.xml")) + [os.path.join(ANDROID, "AndroidManifest.xml")]:
        with open(path, encoding="utf-8") as f:
            assert "isAccessibilityTool" not in f.read(), path


def test_the_page_has_an_install_help_card_with_the_steps():
    card = re.search(r'<details class="rsteps" id="install-help">.*?</details>', PAGE, re.S).group(0)
    for text in ("Allow restricted settings", "Install anyway", "adb install -r", "ACCESS_RESTRICTED_SETTINGS", "Optional", "Google Play"):
        assert text in card, text
    assert 'id="install-help-appinfo"' in card
    assert 'class="btn ghost rs-copy"' in card   # the adb commands can be copied
    assert 'V.openAppInfo()' in PAGE and 'install-help-appinfo' in PAGE.split("<script>", 1)[1]
    assert re.search(r'ACCESS_RESTRICTED_SETTINGS[^<]*', card) and "com.minhaj.vox" in card


def test_the_setup_step_links_to_the_install_help():
    steps = PAGE[PAGE.index("function setupSteps"):PAGE.index("// The Home status card")]
    assert 'id="installhelp"' in steps
    assert '$("install-help").open = true' in PAGE
