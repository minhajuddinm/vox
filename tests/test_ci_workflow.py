"""Text checks on .github/workflows/build.yml and android/build.sh: a tag release must never ship an APK signed with a
throw-away key. (Nothing here runs GitHub Actions; the workflow text is the only thing that can be checked locally.)"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


WORKFLOW = read(".github", "workflows", "build.yml")


def step(name_part):
    """The text of the workflow step whose `- name:` contains name_part (up to the next step)."""
    parts = re.split(r"(?m)^      - ", WORKFLOW)
    found = [p for p in parts if p.startswith("name:") and name_part in p.split("\n", 1)[0]]
    assert len(found) == 1, name_part
    return found[0]


def test_a_tag_build_without_the_keystore_secret_fails():
    signing = step("Signing key")
    assert "ANDROID_KEYSTORE_B64" in signing
    assert "refs/tags/v" in signing, "the signing step has no condition on a tag"
    tag_branch = signing.split("refs/tags/v", 1)[1].split("else", 1)[0]
    assert "::error::" in tag_branch and "exit 1" in tag_branch


def test_a_build_without_the_secret_says_so_and_names_the_artifact_after_it():
    signing = step("Signing key")
    assert "::warning::" in signing and "throw-away" in signing
    assert "Vox-android-debug-key" in signing and "GITHUB_ENV" in signing
    upload = WORKFLOW.split("path: android/build/Vox.apk", 1)[0].rsplit("- uses:", 1)[1]
    assert "${{ env.APK_ARTIFACT }}" in upload


def test_the_keystore_password_default_is_untouched():
    assert 'PASS="${KS_PASS:-voxvox}"' in read("android", "build.sh")   # the owner's decision, not a CI check


def test_build_sh_says_which_key_it_signs_with_and_refuses_a_throwaway_key_on_a_tag():
    sh = read("android", "build.sh")
    assert "refs/tags/v" in sh and "exit 1" in sh.split("refs/tags/v", 1)[1].split("fi", 1)[0]
    gen = sh.split("keytool -genkeypair", 1)[1].split("\n", 3)[:3]
    assert ">/dev/null" not in "".join(gen), "keytool errors must be visible"
    assert "throw-away" in sh
