"""Files that hold secrets or personal data must be ignored by git wherever they are in the tree."""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")

SECRET_FILES = [
    "config.json", "windows/config.json", "android/config.json", "relay/config.json",
    "google_client.json", "windows/google_client.json", "client_secret_123.json",
    "relay.json", "relay/relay.json", "relay.json.tmp", "relay.db", "relay/relay.db", "relay.db-wal", "relay.db-shm",
    ".env", ".env.local", "windows/.env", "x.jks", "android/x.jks", "x.p12", "x.pem", "keys/server.pem",
    "vox.keystore", "android/vox.keystore",
]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize("path", SECRET_FILES)
def test_secret_files_are_ignored(path):
    # --no-index: judge by the rules alone, even if such a file were already tracked
    r = subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT, capture_output=True)
    assert r.returncode == 0, f"{path} is not ignored by .gitignore"
