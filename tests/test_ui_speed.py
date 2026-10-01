"""The Speed card of the two pages: the shared renderer (ui-shared/common.js, run with node), the card's ids and the
bridge calls on both pages. Where node is missing the renderer tests are skipped (CI has node)."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COMMON = os.path.join(ROOT, "ui-shared", "common.js")
WIN = os.path.join(ROOT, "windows", "ui", "index.html")
AND = os.path.join(ROOT, "android", "assets", "index.html")

VIEW = {
    "count": 12, "biggest": "stt",
    "stages": {"start": {"median": 40, "p90": 90}, "rec": {"median": 2000, "p90": 3000}, "stt": {"median": 1450, "p90": 2100},
               "llm": {"median": 600, "p90": 900}, "insert": {"median": 30, "p90": 50}, "total": {"median": 2100, "p90": 3000}},
    "models": [{"stt_model": "whisper-large-v3-turbo", "llm_model": "openai/gpt-oss-20b", "count": 12, "stt": 1450, "llm": 600, "total": 2100}],
    "last": [{"t": 1, "app": "notepad.exe", "words": 4, "stt_model": "w", "llm_model": "<b>l</b>", "relay": False,
              "stages": {"start": 40, "rec": 2000, "stt": 600, "llm": 0, "insert": 30, "total": 630}}],
}


def run_js(tmp_path, body):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = tmp_path / "t.js"
    with open(COMMON, encoding="utf-8") as f:
        script.write_text(f.read() + "\n" + body, encoding="utf-8")
    r = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


def render(tmp_path, view, label="undefined"):
    return run_js(tmp_path, "console.log(JSON.stringify(speedHtml(%s%s)));" % (json.dumps(view), "" if label == "undefined" else ", " + label))


def test_fmt_ms_matches_the_python_and_java_format(tmp_path):
    out = run_js(tmp_path, "console.log(JSON.stringify([0, 850, 999, 1000, 1049, 1050, 1449, 1450, 12340, 59960, -5].map(fmtMs)));")
    assert json.loads(out) == ["0 ms", "850 ms", "999 ms", "1.0 s", "1.0 s", "1.1 s", "1.4 s", "1.5 s", "12.3 s", "60.0 s", "0 ms"]


def test_the_biggest_stage_is_marked_and_numbers_are_shown(tmp_path):
    html = json.loads(render(tmp_path, VIEW))
    assert html.count('class="srow big"') == 1
    big = re.search(r'<div class="srow big">(.*?)</div>', html).group(1)
    assert "Speech to text" in big and "1.5 s" in big and "2.1 s" in big       # median and the slow one in ten
    assert "Cleanup" in html and "600 ms" in html
    assert "whisper-large-v3-turbo" in html and "openai/gpt-oss-20b" in html
    assert "12 dictations" in html


def test_stage_that_never_ran_shows_a_dash(tmp_path):
    view = json.loads(json.dumps(VIEW))
    view["stages"]["llm"] = {"median": 0, "p90": 0}
    view["biggest"] = ""
    html = json.loads(render(tmp_path, view))
    assert 'class="srow big"' not in html
    assert re.search(r"Cleanup</span><span class=\"sv[^\"]*\">–", html)


def test_model_names_and_apps_are_escaped(tmp_path):
    view = json.loads(json.dumps(VIEW))
    view["models"][0]["llm_model"] = "<b>l</b>"
    html = json.loads(render(tmp_path, view))
    assert "<b>l</b>" not in html and "&lt;b&gt;l&lt;/b&gt;" in html


def test_empty_view_says_how_to_get_numbers(tmp_path):
    html = json.loads(render(tmp_path, {"count": 0, "biggest": "", "stages": {}, "models": [], "last": []}))
    assert "No timed dictations yet" in html and "big" not in html and "By model" not in html
    assert "No timed dictations yet" in json.loads(render(tmp_path, None))


def test_last_dictations_use_the_page_app_label_function(tmp_path):
    html = json.loads(render(tmp_path, VIEW, "a => 'APP:' + a"))
    assert "APP:notepad.exe" in html
    assert "notepad" in json.loads(render(tmp_path, VIEW))                       # default: the exe name without .exe


@pytest.mark.parametrize("path,bridge", [(WIN, "api().get_speed()"), (AND, "V.getSpeed()")])
def test_both_pages_have_the_card_and_ask_their_bridge(path, bridge):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert 'id="speed-card"' in html and 'id="speed-rows"' in html
    assert bridge in html and "speedHtml(" in html
