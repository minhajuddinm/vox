"""Lists which documentation pages must be updated for the code you changed.

    python documentation/tools/docs_todo.py            # changes since origin/main (or main), plus uncommitted work
    python documentation/tools/docs_todo.py <git-ref>  # changes since another ref

It reads only `git diff --name-status` and prints a checklist. It does not edit anything; you still read the
changed code and write the documentation. `check_docs.py` is the safety net that fails CI if the checklist was
ignored for files, settings or links.
"""
import fnmatch
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# (path pattern, page, why). First column is a glob against the changed path.
RULES = [
    ("windows/engine.py", "documentation/04-windows-app.md", "hotkey, recording, tray, paste, retry, control server"),
    ("windows/overlay.py", "documentation/04-windows-app.md", "the recording pill"),
    ("windows/ui_app.py", "documentation/04-windows-app.md", "the window's Api methods"),
    ("windows/ui/*", "documentation/04-windows-app.md", "window pages and what they show"),
    ("windows/meeting.py", "documentation/04-windows-app.md", "meeting notes behaviour"),
    ("windows/gcal.py", "documentation/04-windows-app.md", "calendar"),
    ("windows/vcalendar.py", "documentation/04-windows-app.md", "calendar"),
    ("windows/vox_core.py", "documentation/06-pipeline.md", "pipeline, prompts, server calls, guards"),
    ("windows/vox_core.py", "documentation/07-config-and-data.md", "settings keys and defaults, data formats"),
    ("windows/secret.py", "documentation/09-security-privacy.md", "how secrets are stored"),
    ("windows/audio_devices.py", "documentation/04-windows-app.md", "microphone selection"),
    ("windows/requirements.txt", "documentation/10-build-test-release.md", "dependencies"),
    ("windows/build_app.bat", "documentation/10-build-test-release.md", "local build"),
    ("windows/installer.iss", "documentation/10-build-test-release.md", "installer"),
    ("android/src/*", "documentation/05-android-app.md", "components, states, helpers"),
    ("android/src/*/GroqClient.java", "documentation/06-pipeline.md", "pipeline and prompts (Java twin)"),
    ("android/src/*/Prefs.java", "documentation/07-config-and-data.md", "preference keys and defaults"),
    ("android/AndroidManifest.xml", "documentation/05-android-app.md", "permissions, components, version"),
    ("android/assets/index.html", "documentation/05-android-app.md", "screens"),
    ("android/res/*", "documentation/05-android-app.md", "resources and configs"),
    ("android/build.sh", "documentation/10-build-test-release.md", "Android build"),
    (".github/workflows/*", "documentation/10-build-test-release.md", "CI jobs and secrets"),
    ("spec/golden.txt", "documentation/06-pipeline.md", "shared golden cases"),
    ("tests/*", "documentation/10-build-test-release.md", "test counts and what is covered"),
    ("android/test/*", "documentation/10-build-test-release.md", "Java test programs"),
    ("docs/privacy.html", "documentation/09-security-privacy.md", "the public privacy promise"),
]
ALWAYS = [
    ("CHANGELOG.md", "add a line under Unreleased for every user-visible change"),
    ("documentation/devlog.md", "add a dated entry: what changed, what was verified, what surprised you"),
    ("documentation/08-features.md", "if a feature was added, changed or removed"),
    ("documentation/12-known-issues-and-roadmap.md", "close what you fixed, add what you found"),
    ("documentation/decisions/", "a new record if you made a decision someone will question later"),
    ("documentation/README.md", "update the 'Verified against' commit and the test counts"),
]


def changed_paths(base):
    """(status, path) for commits since `base`, plus staged, unstaged and new files."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    out = git("diff", "--name-status", f"{base}...HEAD") if base else ""
    out += git("diff", "--name-status", "HEAD")
    for p in git("ls-files", "--others", "--exclude-standard").splitlines():
        out += f"A\t{p}\n"
    rows, seen = [], set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[-1] not in seen:
            seen.add(parts[-1])
            rows.append((parts[0][0], parts[-1]))
    return rows


def pages_for(rows):
    """Map changed files to (page -> list of (path, reason)); documentation files themselves are skipped."""
    pages = {}
    for _status, path in rows:
        if path.startswith("documentation/") or path in ("CHANGELOG.md", "AGENTS.md"):
            continue
        for pattern, page, why in RULES:
            if fnmatch.fnmatch(path, pattern):
                pages.setdefault(page, []).append((path, why))
    return pages


def main(argv):
    base = argv[1] if len(argv) > 1 else next((r for r in ("origin/main", "main") if subprocess.run(
        ["git", "rev-parse", "--verify", "-q", r], cwd=ROOT, capture_output=True).returncode == 0), "")
    rows = changed_paths(base)
    print(f"Changes considered: {len(rows)} file(s) since {base or 'HEAD'} (plus uncommitted work)\n")
    if not rows:
        print("Nothing changed, nothing to sync.")
        return 0
    added = [p for s, p in rows if s == "A"]
    removed = [p for s, p in rows if s == "D"]
    renamed = [p for s, p in rows if s == "R"]
    if added or removed or renamed:
        print("Tree page: documentation/03-repo-tree.md must list new files and drop removed or renamed ones:")
        for p in added:
            print(f"  + add     {p}")
        for p in removed + renamed:
            print(f"  - remove/rename {p}")
        print()
    pages = pages_for(rows)
    if pages:
        print("Pages that describe the code you touched (read the code, then update):")
        for page in sorted(pages):
            print(f"  [ ] {page}")
            for path, why in pages[page]:
                print(f"        {path}  ({why})")
        print()
    print("Always consider:")
    for page, why in ALWAYS:
        print(f"  [ ] {page}: {why}")
    print("\nThen run: python documentation/tools/check_docs.py  and  python -m pytest -q")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
