"""The Devices card on both pages: shared markup builder (ui-shared/common.js devicesHtml), ids, bridge calls.

The builder is run with node when it is installed (it is pure: rows in, HTML text out); the rest is static text checks like
tests/test_ui_static.py. The bridges themselves are tested in tests/test_sync_devices.py (Python) and RelayClientTest (Java).
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
COMMON = os.path.join(ROOT, "ui-shared", "common.js")
CSS = os.path.join(ROOT, "ui-shared", "components.css")
PAGES = {
    "windows": os.path.join(ROOT, "windows", "ui", "index.html"),
    "android": os.path.join(ROOT, "android", "assets", "index.html"),
}
NODE = shutil.which("node")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def render(res):
    """devicesHtml(res) run by node, with only the shared script loaded (no page, no bridge)."""
    code = read(COMMON) + "\n;process.stdout.write(devicesHtml(" + json.dumps(res) + "));"
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


ROWS = [{"name": "Pixel 7", "this": True, "state": "active", "ago": "just now"},
        {"name": "Laptop", "this": False, "state": "recent", "ago": "3 h ago"},
        {"name": "Old PC", "this": False, "state": "old", "ago": "2 days ago"}]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestDevicesHtml:
    def test_one_row_per_device_with_state_name_and_age(self):
        html = render({"ok": True, "error": "", "devices": ROWS})
        assert html.count('class="drow') == 3
        assert 'class="drow active"' in html and 'class="drow recent"' in html and 'class="drow old"' in html
        for text in ("Pixel 7", "Laptop", "Old PC", "just now", "3 h ago", "2 days ago"):
            assert text in html

    def test_only_the_asking_device_gets_the_badge(self):
        html = render({"ok": True, "error": "", "devices": ROWS})
        assert html.count('class="badge"') == 1
        assert re.search(r'Pixel 7<span class="badge">this device</span>', html)

    def test_an_empty_list_says_no_device_has_synced_yet(self):
        html = render({"ok": True, "error": "", "devices": []})
        assert "No device has synced yet" in html and "drow" not in html

    def test_a_failure_shows_the_reason_in_the_error_style(self):
        html = render({"ok": False, "error": "Cannot reach the relay (is Tailscale running?): ConnectionError", "devices": []})
        assert 'class="status bad"' in html and "Cannot reach the relay (is Tailscale running?)" in html
        assert "No device has synced yet" not in html

    @pytest.mark.parametrize("res", [None, {}, {"ok": False}, {"ok": False, "error": ""}])
    def test_a_failure_without_a_reason_still_says_something(self, res):
        assert "The device list could not be read." in render(res)

    def test_names_are_escaped(self):
        html = render({"ok": True, "error": "", "devices": [{"name": '<img src=x onerror="a()">&', "this": False, "state": "old", "ago": "never"}]})
        assert "<img" not in html and "&lt;img" in html and "&amp;" in html and "&quot;" in html

    def test_an_unknown_state_is_drawn_as_old_and_never_lands_in_the_class_text(self):
        html = render({"ok": True, "error": "", "devices": [{"name": "d", "this": False, "state": 'x" onclick="a()', "ago": "never"}]})
        assert 'class="drow old"' in html and "onclick" not in html

    def test_nothing_but_the_four_fields_is_drawn(self):
        html = render({"ok": True, "error": "", "devices": [{"name": "d", "this": False, "state": "old", "ago": "never", "login": "me@example.com", "requests": 9}]})
        assert "me@example.com" not in html and "9" not in html


def test_the_shared_css_styles_every_class_the_builder_uses():
    css = read(CSS)
    for cls in (".drow", ".dot", ".dn", ".da", ".badge", ".dempty"):
        assert cls in css, cls
    for state in ("active", "recent", "old"):
        assert f".drow.{state}" in css


@pytest.mark.parametrize("page", list(PAGES))
def test_the_card_comes_after_the_relay_settings_in_settings(page):
    html = read(PAGES[page])
    section = re.search(r'<section[^>]*id="settings".*?</section>', html, re.S).group(0)
    for needed in ('id="devices-card"', 'id="devices-list"', 'id="devices-refresh"'):
        assert section.count(needed) == 1, needed
    assert section.index('id="relay-token"') < section.index('id="devices-card"') < section.index("<h2>System</h2>")


@pytest.mark.parametrize("page", list(PAGES))
def test_the_card_names_its_purpose_and_the_empty_state_comes_from_the_shared_builder(page):
    html = read(PAGES[page])
    assert "Devices on my relay" in html
    assert "devicesHtml(" in html
    assert "No device has synced yet" in read(COMMON)


def test_each_page_calls_its_own_bridge():
    assert re.search(r"api\(\)\.get_devices\(", read(PAGES["windows"]))
    assert re.search(r"(?<![\w$])V\.getDevices\(", read(PAGES["android"]))


def test_the_android_preview_has_a_getDevices_stand_in():
    """Without it the page opened in a desktop browser (the design preview) would throw at the first Settings visit."""
    assert re.search(r"getDevices:\s*\(", read(PAGES["android"]))


@pytest.mark.parametrize("page", list(PAGES))
def test_the_card_is_not_filled_before_the_relay_has_an_address_and_a_token(page):
    """No request for a card that cannot work: the page says what is missing instead."""
    html = read(PAGES[page])
    assert "Fill in the relay address and token above to see your devices." in html
