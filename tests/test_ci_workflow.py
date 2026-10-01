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


# ------------------------------------------------------------------ medium round M4 (C-B2, C-B3, C-B4)
def job(name):
    """The text of one job of the workflow."""
    m = re.search(r"(?ms)^  " + re.escape(name) + r":\n(.*?)(?=^  \w[\w-]*:\n|\Z)", WORKFLOW)
    assert m, name
    return m.group(1)


def test_the_release_waits_for_every_test_job():
    needs = re.search(r"(?m)^    needs: \[(.*)\]", job("release")).group(1)
    assert {"tests", "relay", "windows", "android"} <= {x.strip() for x in needs.split(",")}


def test_the_windows_job_runs_on_pull_requests_and_runs_pytest():
    windows = job("windows")
    assert "pull_request" not in windows
    assert "python -m pytest" in windows and "pip install" in windows.split("python -m pytest")[0]
    assert windows.index("python -m pytest") < windows.index("pyinstaller @g")      # a failing test stops the build


def test_the_android_job_puts_the_tag_into_the_manifest_before_the_build():
    android = job("android")
    version = step_in(android, "version")
    assert "startsWith(github.ref, 'refs/tags/v')" in version and "AndroidManifest.xml" in version
    assert android.index("name: Version from the tag") < android.index("name: Build APK")


def step_in(text, name_part):
    parts = re.split(r"(?m)^      - ", text)
    found = [p for p in parts if p.startswith("name:") and name_part in p.split("\n", 1)[0].lower()]
    assert len(found) == 1, name_part
    return found[0]


def _version_script():
    run = step_in(job("android"), "version").split("run: |\n", 1)[1]
    return "\n".join(line[10:] for line in run.splitlines())


def _run_version_step(tmp_path, tag):
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if not bash:
        import pytest
        pytest.skip("no bash")
    (tmp_path / "android").mkdir()
    shutil.copy(os.path.join(ROOT, "android", "AndroidManifest.xml"), tmp_path / "android" / "AndroidManifest.xml")
    r = subprocess.run([bash, "-c", _version_script()], cwd=tmp_path, capture_output=True, text=True,
                       env=dict(os.environ, GITHUB_REF_NAME=tag))
    return r, (tmp_path / "android" / "AndroidManifest.xml").read_text(encoding="utf-8")


def test_the_version_step_writes_name_and_code_from_the_tag(tmp_path):
    r, manifest = _run_version_step(tmp_path, "v1.4.2")
    assert r.returncode == 0, r.stderr
    assert 'android:versionName="1.4.2"' in manifest and 'android:versionCode="10402"' in manifest
    assert 'package="com.minhaj.vox"' in manifest and "<uses-sdk" in manifest     # nothing else was touched


def test_the_version_step_accepts_two_part_tags_and_refuses_anything_else(tmp_path):
    r, manifest = _run_version_step(tmp_path, "v2.0")
    assert r.returncode == 0 and 'android:versionName="2.0"' in manifest and 'android:versionCode="20000"' in manifest
    for n, bad in enumerate(("v1", "v1.2.3-rc1", "vabc", "v1.2.3.4", "v1.100.0")):
        (tmp_path / str(n)).mkdir()
        r, manifest = _run_version_step(tmp_path / str(n), bad)
        assert r.returncode != 0 and "::error::" in r.stdout + r.stderr, bad
        assert 'android:versionName="1.3"' in manifest, bad        # a refused tag changes nothing


def test_the_committed_manifest_is_not_changed_by_the_workflow_text():
    assert 'android:versionCode="4"' in read("android", "AndroidManifest.xml")      # the step edits a copy in the runner only
