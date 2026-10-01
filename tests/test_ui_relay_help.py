"""The "How to set up the relay" card and the Test connection details on both pages.

The card's text lives once, in ui-shared/relay-steps.txt; tools/sync_ui.py turns it into a generated block in each page, and
relay/README.md must show the same commands. The Test connection rows are built by the shared relayCheckRows (ui-shared/
common.js), run here with node when it is installed. Bridges: tests/test_relay_check.py (Python) and RelayClientTest (Java).
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STEPS = os.path.join(ROOT, "ui-shared", "relay-steps.txt")
COMMON = os.path.join(ROOT, "ui-shared", "common.js")
CSS = os.path.join(ROOT, "ui-shared", "components.css")
README = os.path.join(ROOT, "relay", "README.md")
TOOL = os.path.join(ROOT, "tools", "sync_ui.py")
PAGES = {
    "windows": os.path.join(ROOT, "windows", "ui", "index.html"),
    "android": os.path.join(ROOT, "android", "assets", "index.html"),
}
NODE = shutil.which("node")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def load_tool():
    spec = importlib.util.spec_from_file_location("sync_ui", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TOOLMOD = load_tool()
PARSED = TOOLMOD.parse_steps(read(STEPS))
COMMANDS = [c for s in PARSED["steps"] for c in s["cmds"]]


# ------------------------------------------------------------------ the one source

def test_the_source_has_an_intro_numbered_steps_and_commands():
    assert PARSED["intro"] and len(PARSED["steps"]) >= 6
    assert all(s["text"] for s in PARSED["steps"])
    assert len(COMMANDS) >= 8


def test_the_plan_commands_are_in_the_steps():
    assert "sudo tailscale serve --bg 8765" in COMMANDS
    assert "python3 /opt/vox-relay/relay.py --data-dir /tmp/vox-relay-test --show-token" in COMMANDS


def test_the_last_step_says_to_press_test_connection():
    assert "Test connection" in PARSED["steps"][-1]["text"] and PARSED["steps"][-1]["cmds"] == []


def test_the_parser_reads_steps_commands_and_notes():
    got = TOOLMOD.parse_steps("# comment\n\nintro: Hello\nstep: One\ncmd: a b\ncmd: c\nstep: Two\nnote: Bye\n")
    assert got == {"intro": "Hello", "steps": [{"text": "One", "cmds": ["a b", "c"]}, {"text": "Two", "cmds": []}], "notes": ["Bye"]}


@pytest.mark.parametrize("bad,why", [
    ("cmd: x\nstep: a\n", "before"),
    ("step: a\nwhat: x\n", "unknown"),
    ("intro: only an intro\n", "step"),
    ("step: a\ncmd:\n", "empty"),
    ("step:\n", "empty"),
])
def test_the_parser_refuses_a_malformed_source_with_a_reason(bad, why):
    with pytest.raises(SystemExit) as e:
        TOOLMOD.parse_steps(bad)
    assert why in str(e.value)


def test_the_html_is_escaped_and_the_copy_text_keeps_its_line_breaks():
    html = TOOLMOD.steps_html("intro: a <b> & \"c\"\nstep: use <this> & 'that'\ncmd: echo \"<x>\" && ls\ncmd: second line\n")
    assert "<b>" not in html and "&lt;b&gt;" in html and "&amp;" in html
    assert "<this>" not in html and "&lt;this&gt;" in html
    assert 'data-copy="echo &quot;&lt;x&gt;&quot; &amp;&amp; ls&#10;second line"' in html
    assert "echo &quot;&lt;x&gt;&quot; &amp;&amp; ls\nsecond line</pre>" in html


def test_a_step_without_commands_has_no_copy_button():
    html = TOOLMOD.steps_html("step: just words\nstep: with\ncmd: ls\n")
    assert html.count("rs-copy") == 1 and html.count("<li>") == 2


# ------------------------------------------------------------------ both pages and the README agree

@pytest.mark.parametrize("page", list(PAGES))
def test_the_page_has_the_generated_block_once_and_it_matches_the_source(page):
    html = read(PAGES[page])
    assert html.count("<!-- ui-shared:steps begin") == 1 and html.count("<!-- ui-shared:steps end -->") == 1
    block = re.search(r"<!-- ui-shared:steps begin.*?<!-- ui-shared:steps end -->", html, re.S).group(0)
    assert TOOLMOD.steps_html(read(STEPS)) in block


@pytest.mark.parametrize("page", list(PAGES))
def test_the_page_shows_the_commands_from_the_plan(page):
    html = read(PAGES[page])
    assert "sudo tailscale serve --bg 8765" in html and "python3 /opt/vox-relay/relay.py" in html
    assert "How to set up the relay" in html


@pytest.mark.parametrize("page", list(PAGES))
def test_every_step_with_commands_has_a_copy_button_holding_exactly_those_commands(page):
    html = read(PAGES[page])
    block = re.search(r"<!-- ui-shared:steps begin.*?<!-- ui-shared:steps end -->", html, re.S).group(0)
    copies = re.findall(r'data-copy="([^"]*)"', block)
    wanted = ["&#10;".join(TOOLMOD.esc(c) for c in s["cmds"]) for s in PARSED["steps"] if s["cmds"]]
    assert copies == wanted


def test_the_readme_shows_every_command_of_the_card():
    readme = read(README)
    for c in COMMANDS:
        assert c in readme, c


def test_the_windows_notification_command_is_the_cards_command():
    sys.path.insert(0, os.path.join(ROOT, "windows"))
    try:
        import relay_host
    finally:
        sys.path.pop(0)
    hint = relay_host.serve_hint(8765)   # on Windows the command has no sudo; on the Pi the card's command is the same with it
    assert "sudo " + hint in COMMANDS and hint in read(README)


@pytest.mark.parametrize("page", list(PAGES))
def test_the_card_sits_between_the_relay_settings_and_the_devices_card(page):
    section = re.search(r'<section[^>]*id="settings".*?</section>', read(PAGES[page]), re.S).group(0)
    assert section.count('id="relay-steps"') == 1
    assert section.index('id="relay-token"') < section.index('id="relay-steps"') < section.index('id="devices-card"')


@pytest.mark.parametrize("page,call", [("windows", r"api\(\)\.copy\("), ("android", r"(?<![\w$])V\.copy\(")])
def test_the_copy_buttons_are_wired_to_the_pages_own_clipboard_bridge(page, call):
    html = read(PAGES[page])
    handler = re.search(r'closest\("\.rs-copy"\)[^\n]*', html)
    assert handler and re.search(call, handler.group(0)), "the click handler for .rs-copy must call the page's copy bridge"


def test_the_shared_css_styles_the_card():
    css = read(CSS)
    for cls in (".rsteps", ".rs-list", ".rs-cmd", ".rs-copy", ".rs-intro"):
        assert cls in css, cls


# ------------------------------------------------------------------ sync_ui keeps the block honest

def temp_copy(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(os.path.join(ROOT, "ui-shared"), root / "ui-shared")
    for page in (os.path.join("windows", "ui", "index.html"), os.path.join("android", "assets", "index.html")):
        os.makedirs(os.path.dirname(root / page), exist_ok=True)
        shutil.copy(os.path.join(ROOT, page), root / page)
    return str(root)


def run_tool(root, *args):
    return subprocess.run([sys.executable, TOOL, "--root", root, *args], capture_output=True, text=True)


def test_check_fails_when_the_source_changed_and_the_pages_were_not_regenerated(tmp_path):
    root = temp_copy(tmp_path)
    with open(os.path.join(root, "ui-shared", "relay-steps.txt"), "a", encoding="utf-8") as f:
        f.write("note: one more line\n")
    r = run_tool(root, "--check")
    assert r.returncode == 1 and "out of date" in r.stderr
    assert run_tool(root).returncode == 0                # regenerate
    assert run_tool(root, "--check").returncode == 0     # and now it agrees
    for page in (os.path.join("windows", "ui", "index.html"), os.path.join("android", "assets", "index.html")):
        assert "one more line" in read(os.path.join(root, page))


def test_check_fails_when_a_command_in_a_page_is_edited_by_hand(tmp_path):
    root = temp_copy(tmp_path)
    page = os.path.join(root, "android", "assets", "index.html")
    with open(page, encoding="utf-8", newline="") as f:
        text = f.read()
    assert "sudo tailscale serve --bg 8765" in text
    with open(page, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("sudo tailscale serve --bg 8765", "sudo tailscale funnel 8765", 1))
    r = run_tool(root, "--check")
    assert r.returncode == 1 and "android" in r.stderr


def test_a_page_without_the_block_is_refused(tmp_path):
    root = temp_copy(tmp_path)
    page = os.path.join(root, "windows", "ui", "index.html")
    with open(page, encoding="utf-8", newline="") as f:
        text = f.read()
    with open(page, "w", encoding="utf-8", newline="") as f:
        f.write(re.sub(r"<!-- ui-shared:steps begin.*?<!-- ui-shared:steps end -->", "", text, flags=re.S))
    r = run_tool(root, "--check")
    assert r.returncode != 0 and "ui-shared:steps" in r.stdout + r.stderr


# ------------------------------------------------------------------ the Test connection rows

def rows(res):
    code = read(COMMON) + "\n;process.stdout.write(JSON.stringify(relayCheckRows(" + json.dumps(res) + ")));"
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


GOOD = {"ok": True, "reachable": True, "token_ok": True, "device_name": "Pixel 7", "relay_version": "0.2", "notes": 3, "message": "Connected. The relay holds 3 notes."}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestCheckRows:
    def test_a_good_test_shows_the_relay_version_the_token_and_this_devices_name(self):
        assert rows(GOOD) == [["Relay", "Reachable, version 0.2", "ok"], ["Token", "Accepted", "ok"], ["This device", "Pixel 7", ""]]

    def test_a_good_test_without_a_version_still_reads_well(self):
        assert rows(dict(GOOD, relay_version=""))[0] == ["Relay", "Reachable", "ok"]

    def test_a_refused_token_is_reachable_but_refused_and_names_no_device(self):
        got = rows(dict(GOOD, ok=False, token_ok=False, relay_version="", message="The relay refused the token."))
        assert got == [["Relay", "Reachable", "ok"], ["Token", "Refused", "bad"]]

    def test_another_tailnet_user_means_the_token_was_right(self):
        got = rows(dict(GOOD, ok=False, token_ok=True, relay_version=""))
        assert got == [["Relay", "Reachable", "ok"], ["Token", "Accepted", "ok"]]

    def test_an_unreachable_relay_says_the_token_was_not_checked(self):
        got = rows(dict(GOOD, ok=False, reachable=False, token_ok=False, relay_version=""))
        assert got == [["Relay", "Not reachable", "bad"], ["Token", "Not checked", "dim"]]

    @pytest.mark.parametrize("res", [None, {}, {"ok": True, "message": "Connected."}, {"message": "x"}])
    def test_an_answer_without_the_new_fields_adds_no_rows(self, res):
        assert rows(res) == []

    def test_a_device_name_is_text_not_markup(self):
        html = subprocess.run([NODE, "-e", read(COMMON) + "\n;process.stdout.write(statusHtml(relayCheckRows(" + json.dumps(dict(GOOD, device_name="<img src=x onerror=a()>")) + ")));"],
                              capture_output=True, text=True, encoding="utf-8", timeout=30).stdout
        assert "<img" not in html and "&lt;img" in html


@pytest.mark.parametrize("page", list(PAGES))
def test_each_page_has_the_details_box_and_the_test_connection_button(page):
    html = read(PAGES[page])
    assert 'id="relay-check"' in html and "relayCheckRows(" in html
    assert re.search(r'id="relay-test"[^>]*>Test connection<', html)


def test_the_windows_page_clears_the_old_details_when_the_address_or_token_changes():
    html = read(PAGES["windows"])
    assert html.count('$("relay-check").innerHTML = ""') >= 2


def test_the_android_page_clears_the_old_details_when_the_address_or_token_changes():
    html = read(PAGES["android"])
    assert html.count('$("relay-check").innerHTML = ""') >= 2
