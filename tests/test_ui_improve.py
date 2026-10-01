"""The "Improve my cleanup" card of the Windows Settings page (task F2): its ids and place, that nothing is sent before the
confirm step, and the two renderers (run with node when it is installed). Android has no card: the learned rules reach the
phone through the profile sync. The bridge itself is tested in tests/test_improve_card.py."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WIN = os.path.join(ROOT, "windows", "ui", "index.html")
AND = os.path.join(ROOT, "android", "assets", "index.html")
COMMON = os.path.join(ROOT, "ui-shared", "common.js")
NODE = shutil.which("node")

IDS = ("improve-card", "improve-days", "improve-model", "improve-provider", "improve-counts", "improve-run", "improve-confirm-box",
       "improve-confirm-text", "improve-confirm", "improve-cancel", "improve-status", "improve-proposal", "improve-apply",
       "improve-all", "improve-versions", "improve-remind")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def settings(html):
    return re.search(r'<section[^>]*id="settings".*?</section>', html, re.S).group(0)


def function_text(html, name):
    m = re.search(r"^function %s\(.*?^}\n" % name, html, re.S | re.M)
    assert m, name + " not found in the page"
    return m.group(0)


def render(name, arg):
    kinds = re.search(r"^const IMPROVE_KINDS = .*$", read(WIN), re.M).group(0)
    code = read(COMMON) + "\n" + kinds + "\n" + function_text(read(WIN), name) + "\n;process.stdout.write(%s(%s));" % (name, json.dumps(arg))
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_card_has_every_id_once_and_sits_between_voice_and_privacy():
    section = settings(read(WIN))
    for needed in IDS:
        assert section.count('id="%s"' % needed) == 1, needed
    assert section.index("<h2>Voice &amp; audio</h2>") < section.index('id="improve-card"') < section.index("<h2>Privacy</h2>")


def test_android_has_no_card_for_it():
    assert "improve-" not in read(AND) and "improve_" not in read(AND)


def test_the_card_says_nothing_is_sent_until_the_person_confirms():
    card = re.search(r'<div class="card list" id="improve-card".*?\n    </div>\n', read(WIN), re.S).group(0)
    assert "Improve my cleanup" in card and "Nothing is sent until you press Run once and confirm" in card
    assert 'list="llm-models"' in card and "openai/gpt-oss-120b" in card         # the model picker is the cleanup model list


def test_the_date_range_offers_the_ranges_the_bridge_accepts():
    sel = re.search(r'<select id="improve-days">(.*?)</select>', read(WIN), re.S).group(1)
    assert re.findall(r'value="(\d+)"', sel) == ["7", "14", "30", "90", "0"]


def test_only_the_confirm_button_runs_it():
    html = read(WIN)
    assert len(re.findall(r"\.improve_run\(", html)) == 1
    run_handler = re.search(r'\$\("improve-run"\)\.onclick = async \(\) => \{(.*?)\n\};', html, re.S).group(1)
    confirm_handler = re.search(r'\$\("improve-confirm"\)\.onclick = async \(\) => \{(.*?)\n\};', html, re.S).group(1)
    assert "improve_run" not in run_handler and "improve-confirm-box" in run_handler
    assert "api().improve_run(" in confirm_handler


def test_the_run_asks_for_the_numbers_that_were_shown_and_the_sentence_comes_from_the_bridge():
    html = read(WIN)
    assert re.search(r"improve_run\([^)]*\.count[^)]*\.chars\)", html)
    assert re.search(r'improve-confirm-text"\)\.textContent = \w+\.confirm', html)


def test_the_apply_button_sends_the_ticked_ids_only():
    html = read(WIN)
    assert re.search(r"improve_apply\(\[\.\.\.\$\(\"improve-proposal\"\)\.querySelectorAll\('input\[data-id\]:checked'\)\]\.map\(", html)


def test_the_weekly_reminder_resets_its_clock_when_it_is_switched():
    assert re.search(r"save\(\{ improve_remind: \w+(\.target)?(\.checked)?, improve_remind_last: 0 \}", read(WIN))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestRenderers:
    PROPOSAL = {"ok": True, "error": "", "findings": [{"id": "t3", "note": "dropped a <b>sentence</b>"}],
                "items": [{"id": "dictionary:0", "kind": "dictionary", "text": "Kubernetes"},
                          {"id": "replacement:0", "kind": "replacement", "text": "cube => Kubernetes"},
                          {"id": "rule:0", "kind": "rule", "text": 'Write "Atlas".'},
                          {"id": "about:0", "kind": "about", "text": "+ I lead <i>Atlas</i>."}]}

    def test_each_item_but_the_about_suggestions_has_a_checkbox_with_its_id(self):
        html = render("improveItemsHtml", self.PROPOSAL)
        assert re.findall(r'<input type="checkbox" data-id="([^"]+)"', html) == ["dictionary:0", "replacement:0", "rule:0"]
        assert "Kubernetes" in html and "cube =&gt; Kubernetes" in html
        assert "Vox never changes your About you text" in html and "about:0" not in html

    def test_text_from_the_model_is_escaped(self):
        html = render("improveItemsHtml", self.PROPOSAL)
        assert "<b>" not in html and "<i>" not in html and "&lt;b&gt;sentence&lt;/b&gt;" in html and "&lt;i&gt;Atlas&lt;/i&gt;" in html
        assert "&quot;Atlas&quot;" in html

    def test_an_id_cannot_break_out_of_its_attribute(self):
        html = render("improveItemsHtml", {"items": [{"id": 'x" onclick="a()', "kind": "rule", "text": "t"}], "findings": []})
        assert 'onclick="a()' not in html

    def test_nothing_proposed_and_an_error_are_told_apart(self):
        assert "found nothing" in render("improveItemsHtml", {"ok": True, "error": "", "items": [], "findings": []})
        bad = render("improveItemsHtml", {"ok": True, "error": "The answer was not usable JSON", "items": [], "findings": []})
        assert 'class="status bad"' in bad and "not usable JSON" in bad

    def test_versions_have_a_revert_button_with_their_index_newest_first(self):
        html = render("improveVersionsHtml", [{"index": 1, "t": 200, "text": "2 rules"}, {"index": 0, "t": 100, "text": "1 dictionary line, 1 rule"}])
        assert re.findall(r'data-rev="(\d+)"', html) == ["1", "0"]
        assert "2 rules" in html and "1 dictionary line, 1 rule" in html

    def test_no_versions_says_so(self):
        assert "No changes applied yet" in render("improveVersionsHtml", [])
