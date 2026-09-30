"""Checks that the documentation still matches the code. Run from anywhere:

    python documentation/tools/check_docs.py

Fails (exit code 1) when:
  1. a file in the repository is not listed in documentation/03-repo-tree.md, or a file listed there is gone;
  2. a Windows setting key (DEFAULT_CONFIG or any cfg.get("...") in windows/*.py) or an Android preference key
     is not named in documentation/07-config-and-data.md;
  3. a relative Markdown link points at a file that does not exist;
  4. documentation/decisions/README.md and the decision files disagree.

It checks structure, not meaning: a wrong description still passes. Only the standard library and (for the
DEFAULT_CONFIG keys) windows/vox_core.py are used.
"""
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOCS = os.path.join(ROOT, "documentation")
problems = []


def fail(msg):
    problems.append(msg)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def repo_files():
    """Tracked files plus new files that are not ignored, as POSIX paths."""
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
        return sorted(set(p for p in out.splitlines() if p and os.path.exists(os.path.join(ROOT, p))))
    except (OSError, subprocess.CalledProcessError):
        files = []
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in {".git", ".venv", "__pycache__", "build"}]
            files += [os.path.relpath(os.path.join(base, n), ROOT).replace(os.sep, "/") for n in names]
        return sorted(files)


# ------------------------------------------------------------------ 1. tree
def check_tree():
    tree = read(os.path.join(DOCS, "03-repo-tree.md"))
    listed = set(re.findall(r"^\| `([^`]+)` \|", tree, re.M))
    files = repo_files()
    for p in files:
        if p not in listed:
            fail(f"03-repo-tree.md: file not listed: {p}")
    for p in sorted(listed):
        if not os.path.exists(os.path.join(ROOT, p)):
            fail(f"03-repo-tree.md: listed file does not exist: {p}")


# ---------------------------------------------------------------- 2. config
def windows_keys():
    keys = set()
    sys.path.insert(0, os.path.join(ROOT, "windows"))
    try:
        import vox_core
        keys |= set(vox_core.DEFAULT_CONFIG)
    except Exception as e:   # the checker must still run without the app's dependencies
        fail(f"could not import windows/vox_core.py to read DEFAULT_CONFIG: {e}")
    for path in glob.glob(os.path.join(ROOT, "windows", "*.py")):
        keys |= set(re.findall(r'cfg\.get\("([a-z_]+)"', read(path)))
    return keys


def android_keys():
    keys = set()
    for path in glob.glob(os.path.join(ROOT, "android", "src", "com", "minhaj", "vox", "*.java")):
        keys |= set(re.findall(r'\bsp\.get(?:String|Boolean|Int)\("([a-z_]+)"', read(path)))
    return keys


def check_config():
    doc = read(os.path.join(DOCS, "07-config-and-data.md"))
    for label, keys in (("Windows setting", windows_keys()), ("Android preference", android_keys())):
        if not keys:
            fail(f"no {label} keys found; the checker's patterns are out of date")
        for k in sorted(keys):
            if f"`{k}`" not in doc:
                fail(f"07-config-and-data.md: {label} `{k}` is not documented")


# ----------------------------------------------------------------- 3. links
def markdown_files():
    out = glob.glob(os.path.join(DOCS, "**", "*.md"), recursive=True)
    for name in ("CHANGELOG.md", "AGENTS.md", "README.md"):
        if os.path.exists(os.path.join(ROOT, name)):
            out.append(os.path.join(ROOT, name))
    return sorted(out)


def check_links():
    for path in markdown_files():
        text = re.sub(r"```.*?```", "", read(path), flags=re.S)   # ignore code blocks
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if re.match(r"^(https?:|mailto:|#)", target) or target.startswith("../../"):
                continue   # web links, anchors, and GitHub-relative links such as ../../releases/latest
            target = target.split("#", 1)[0]
            if not target:
                continue
            full = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if not os.path.exists(full):
                fail(f"{os.path.relpath(path, ROOT)}: broken link -> {target}")


# ------------------------------------------------------------------- 4. ADRs
def check_adrs():
    folder = os.path.join(DOCS, "decisions")
    index = read(os.path.join(folder, "README.md"))
    on_disk = sorted(os.path.basename(p) for p in glob.glob(os.path.join(folder, "[0-9][0-9][0-9][0-9]-*.md")))
    linked = sorted(set(re.findall(r"\]\((\d{4}-[^)]+\.md)\)", index)))
    for name in on_disk:
        if name not in linked:
            fail(f"decisions/README.md: decision not in the index: {name}")
    for name in linked:
        if name not in on_disk:
            fail(f"decisions/README.md: index links a missing decision: {name}")
    for name in on_disk:
        first = read(os.path.join(folder, name)).splitlines()[0]
        if not first.startswith("# " + name[:4] + "."):
            fail(f"decisions/{name}: first line must start with '# {name[:4]}.'")


def main():
    check_tree()
    check_config()
    check_links()
    check_adrs()
    if problems:
        print(f"{len(problems)} documentation problem(s):")
        for p in problems:
            print("  -", p)
        return 1
    print("documentation OK: tree, config keys, links and decision index match the code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
