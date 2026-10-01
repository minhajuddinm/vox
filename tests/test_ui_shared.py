"""The shared UI parts (ui-shared/) are generated into both pages by tools/sync_ui.py.

`--check` must pass on the committed pages, and must fail when a generated block was edited by hand (so nobody fixes
one page and forgets the other). The failure test works on temp copies, never on the real pages.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOL = os.path.join(ROOT, "tools", "sync_ui.py")
PAGES = [os.path.join("windows", "ui", "index.html"), os.path.join("android", "assets", "index.html")]


def run_tool(root, *args):
    return subprocess.run([sys.executable, TOOL, "--root", root, *args], capture_output=True, text=True)


def temp_copy(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(os.path.join(ROOT, "ui-shared"), root / "ui-shared")
    for page in PAGES:
        os.makedirs(os.path.dirname(root / page), exist_ok=True)
        shutil.copy(os.path.join(ROOT, page), root / page)
    return str(root)


def test_check_passes_on_the_committed_pages():
    r = subprocess.run([sys.executable, TOOL, "--check"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_fails_when_a_generated_css_block_is_edited_by_hand(tmp_path):
    root = temp_copy(tmp_path)
    page = os.path.join(root, PAGES[0])
    with open(page, encoding="utf-8", newline="") as f:
        text = f.read()
    assert ".card { background: var(--panel)" in text
    with open(page, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace(".card { background: var(--panel)", ".card { background: red", 1))
    r = run_tool(root, "--check")
    assert r.returncode != 0
    assert "windows" in r.stdout + r.stderr


def test_check_fails_when_a_generated_js_block_is_edited_by_hand(tmp_path):
    root = temp_copy(tmp_path)
    page = os.path.join(root, PAGES[1])
    with open(page, encoding="utf-8", newline="") as f:
        text = f.read()
    assert "const ABOUT_MAX = 8000;" in text
    with open(page, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("const ABOUT_MAX = 8000;", "const ABOUT_MAX = 9000;", 1))
    r = run_tool(root, "--check")
    assert r.returncode != 0
    assert "android" in r.stdout + r.stderr


def test_write_repairs_a_hand_edit_and_then_check_passes(tmp_path):
    root = temp_copy(tmp_path)
    page = os.path.join(root, PAGES[0])
    with open(page, encoding="utf-8", newline="") as f:
        text = f.read()
    with open(page, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace(".card { background: var(--panel)", ".card { background: red", 1))
    assert run_tool(root, "--check").returncode != 0
    assert run_tool(root).returncode == 0
    assert run_tool(root, "--check").returncode == 0


def test_both_dark_mechanisms_come_from_one_token_source():
    with open(os.path.join(ROOT, PAGES[0]), encoding="utf-8") as f:
        win = f.read()
    with open(os.path.join(ROOT, PAGES[1]), encoding="utf-8") as f:
        andr = f.read()
    assert "@media (prefers-color-scheme: dark)" in win
    assert "\n.dark {" in andr and "prefers-color-scheme" not in andr


def test_status_rows_show_a_failed_dictation_even_when_history_has_older_entries(tmp_path):
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not installed")
    script = tmp_path / "t.js"
    js = open(os.path.join(ROOT, "ui-shared", "common.js"), encoding="utf-8").read()
    script.write_text(js + """
const old = {words: 3, t: Date.now() / 1000 - 600, app: "x.exe"};
const row = (st) => statusRows(st, {relay_sync: false}, null, null).find(r => r[0] === "Last dictation");
const a = row({last: old, unsent: true, notes: 0});
const b = row({last: old, unsent: false, notes: 0});
console.log(JSON.stringify([a, b]));
""", encoding="utf-8")
    r = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    import json
    a, b = json.loads(r.stdout.strip().splitlines()[-1])
    assert a[1].startswith("Not sent") and a[2] == "bad"
    assert "3 words" in b[1]
