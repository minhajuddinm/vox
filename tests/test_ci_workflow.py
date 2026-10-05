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
    assert "::notice::" in signing and "throw-away" in signing     # (bf-e CI1: the normal case for a non-tag build now)
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

def test_batch_files_are_checked_out_with_crlf_on_every_os():
    """C-B6: build_app.bat is run by cmd.exe, which can mis-find a label in an LF file; the checkout (a Linux runner too) must give CRLF."""
    assert re.search(r"(?m)^\*\.bat\s+text\s+eol=crlf\s*$", read(".gitattributes"))
    with open(os.path.join(ROOT, "windows", "build_app.bat"), "rb") as f:
        data = f.read()
    assert data.count(b"\r\n") == data.count(b"\n") > 0


# ------------------------------------------------------------------ bf-e: CI1, CI2, CI10, CI11, SEC-5/CI5
def test_only_a_tag_build_gets_the_release_key_and_only_after_the_tests():
    android = job("android")
    signing = step_in(android, "signing key")
    for secret in ("ANDROID_KEYSTORE_B64", "ANDROID_KEYSTORE_PASS"):
        uses = re.findall(r"\$\{\{[^}]*secrets\." + secret + r"[^}]*\}\}", android)
        assert uses and all("startsWith(github.ref, 'refs/tags/v')" in u for u in uses), secret   # never materialised for a PR
    assert android.index("name: Signing key") > android.index("name: Unit tests")
    assert android.index("name: Signing key") > android.index("name: Sync client against the real relay")
    assert android.index("name: Signing key") < android.index("name: Build APK")
    assert 'if [[ "$GITHUB_REF" == refs/tags/v* ]]' in signing


NL = "\n"


def test_build_sh_warns_without_the_password_and_keeps_it_off_the_command_line():
    """Controller ruling: a tag build without ANDROID_KEYSTORE_PASS warns and uses the old default password; it does not
    fail (the repository has no such secret yet, and a failing android job would publish no release)."""
    sh = read("android", "build.sh")
    assert '--ks-pass env:KS_PASS' in sh and '--key-pass env:KS_PASS' in sh and 'pass:$PASS' not in sh
    assert 'PASS="${KS_PASS:-voxvox}"' in sh
    existing = sh.split('if [ -f "$KS" ]; then', 1)[1].split("else", 1)[0]
    assert "exit 1" not in existing and "Warning" in existing and "KS_PASS" in existing
    missing = sh.split('if [ -f "$KS" ]; then', 1)[1].split("else", 1)[1].split("fi" + NL, 1)[0]
    assert "refs/tags/v" in missing and "exit 1" in missing   # never a throw-away key on a tag


def test_a_tag_build_fails_only_without_the_keystore_and_warns_without_its_password():
    signing = step_in(job("android"), "signing key")
    tag = signing.split('if [[ "$GITHUB_REF" == refs/tags/v* ]]; then', 1)[1].split(NL + "          else", 1)[0]
    blocks = tag.split("fi" + NL)
    keystore = next(b for b in blocks if "exit 1" in b)
    assert '-z "$ANDROID_KEYSTORE_B64"' in keystore and "KS_PASS" not in keystore
    assert sum("exit 1" in b for b in blocks) == 1
    password = next(b for b in blocks if '-z "$KS_PASS"' in b)
    assert "::warning::" in password and "ANDROID_KEYSTORE_PASS" in password and "exit" not in password


def test_a_release_is_published_only_from_a_commit_on_main():
    release = job("release")
    assert "fetch-depth: 0" in release
    gate = step_in(release, "on main")
    assert "git merge-base --is-ancestor" in gate and "origin/main" in gate and "exit 1" in gate
    assert release.index("on main") < release.index("action-gh-release")


def test_the_release_checksums_cover_every_file():
    sums = step_in(job("release"), "sha256sums")
    for f in ("VoxSetup.exe", "Vox.apk", "vox-relay-windows-x64.exe", "vox-relay-linux-x64", "vox-relay-linux-arm64"):
        assert f in sums, f
    assert job("release").index("SHA256SUMS.txt for every") < job("release").index("action-gh-release")


def test_every_job_has_a_time_limit():
    for name in re.findall(r"(?m)^  (\w[\w-]*):\n", WORKFLOW.split("\njobs:\n", 1)[1]):
        assert re.search(r"(?m)^    timeout-minutes: \d+$", job(name)), name


def _locked(path):
    out = {}
    for line in read(*path.split("/")).splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, rest = line.split("==", 1)
            out[name.strip().lower()] = rest.split(";", 1)[0].strip()
    return out


def test_release_builds_install_exactly_the_locked_packages():
    app, tools_lock = _locked("windows/requirements.lock"), _locked("tools/build-requirements.lock")
    direct = {l.split("==")[0].strip().lower(): l.split("==")[1].strip()
              for l in read("windows", "requirements.txt").splitlines() if "==" in l}
    assert all(app.get(k) == v for k, v in direct.items()), "requirements.lock must agree with requirements.txt"
    assert "pythonnet" in app and "clr-loader" in app and "pyinstaller-hooks-contrib" in tools_lock
    assert "pytest" not in app and "pytest" not in tools_lock            # nothing of the test run goes into the exe
    windows = job("windows")
    build = step_in(windows, "build vox.exe")
    assert "requirements.lock" in build and "build-requirements.lock" in build and "check_lock.py" in build
    assert "python -m venv" in build and "pip install -r requirements.txt" not in build
    for leg in job("relay-exe").split("- name:"):
        if "pyinstaller" in leg.lower() and "Build" in leg:
            assert "build-requirements.lock" in leg and "check_lock.py" in leg and "pip install pyinstaller" not in leg
    bat = read("windows", "build_app.bat")
    assert "requirements.lock" in bat and "build-requirements.lock" in bat and "check_lock.py" in bat
