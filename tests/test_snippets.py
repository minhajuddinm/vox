"""Snippets (windows/snippets.py): a trigger phrase you say becomes the text you saved, in every app. The same rules run on
the phone (Snippets.java); spec/golden.txt (kind snippets) ties the two together. These tests add the caps, the pipeline
order (after the cleanup, before lists), that the saved text never goes to the AI, the sync field and the pages."""
import os
import re

import pytest

import improve
import snippets
import sync
import vox_core as core

ROOT = os.path.join(os.path.dirname(__file__), "..")
SIG = "Best regards,\nYuvi"
S = {"my email": "me@example.com", "my signature": SIG}


@pytest.mark.parametrize("text,expected", [
    ("Send it to my email please.", "Send it to me@example.com please."),
    ("My email.", "me@example.com."),
    ("MY   EMAIL", "me@example.com"),
    ("Thanks. My signature", "Thanks. " + SIG),
    ("my emails are full", "my emails are full"),                 # whole phrase only
    ("tommy email", "tommy email"),
    ("my, email", "my, email"),
    ("", ""),
])
def test_apply(text, expected):
    assert snippets.apply_snippets(text, S) == expected


def test_the_longest_trigger_wins_and_the_expansion_is_not_scanned_again():
    s = {"addr": "my email", "my email": "me@example.com", "my email work": "work@example.com"}
    assert snippets.apply_snippets("my email work and addr", s) == "work@example.com and my email"


def test_no_snippets_change_nothing():
    for empty in (None, {}, [], "x"):
        assert snippets.apply_snippets("my email", empty) == "my email"


def test_clean_keeps_text_pairs_and_applies_the_caps():
    raw = {" my  email ": "a@b.c", "": "x", "blank": "", "num": 5, "!!": "x", "My Email": "dup", "sig": "s" * 3000}
    clean = snippets.clean_snippets(raw)
    assert clean == {"my email": "a@b.c", "sig": "s" * snippets.MAX_EXPANSION}
    many = {"t%d" % i: "x" for i in range(80)}
    assert len(snippets.clean_snippets(many)) == snippets.MAX_SNIPPETS == 50
    big = {"t%d" % i: "y" * 2000 for i in range(20)}
    assert sum(len(v) for v in snippets.clean_snippets(big).values()) <= snippets.MAX_TOTAL
    assert snippets.clean_snippets({"t" * 101: "x"}) == {}
    assert snippets.clean_snippets(None) == {} and snippets.clean_snippets(["a"]) == {}
    assert snippets.clean_snippets({"a": "x\r\ny"}) == {"a": "x\ny"}


def test_load_config_cleans_snippets(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    core.save_config(dict(core.DEFAULT_CONFIG, snippets={"a": 1, "my email": "me@example.com"}))
    assert core.load_config()["snippets"] == {"my email": "me@example.com"}
    core.save_config(dict(core.DEFAULT_CONFIG, snippets=["wrong type"]))
    assert core.load_config()["snippets"] == {}


# ------------------------------------------------------------------ final fixes (android 4): the size the relay counts
def test_the_wire_size_is_what_the_relay_counts():
    import json
    for s in ("abc", 'a"b\\c', "line\nbreak\t", "\x01\x7f", "é", "पहला", "😀", ""):
        assert snippets.wire_size(s) == len(json.dumps(s)) - 2
    assert snippets.wire_size("é") == 6 and snippets.wire_size("😀") == 12 and snippets.wire_size("a\n") == 3


def test_the_total_cap_counts_escaped_bytes_of_triggers_and_texts():
    big = {"t%d" % i: "क" * 1000 for i in range(5)}      # 6,000 bytes each once escaped: only three fit in 20,000
    clean = snippets.clean_snippets(big)
    assert list(clean) == ["t0", "t1", "t2"]
    assert sum(snippets.wire_size(t) + snippets.wire_size(x) for t, x in clean.items()) <= snippets.MAX_TOTAL


# ------------------------------------------------------------------ the pipeline

def _cfg(**kw):
    return dict(core.DEFAULT_CONFIG, api_key="k", snippets=S, **kw)


def test_snippets_come_after_the_cleanup_and_never_go_to_it(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "cleanup", lambda cfg, raw, style, label: sent.append(raw) or "Send it to my email, thanks.")
    r = core.process_text(_cfg(), "send it to my email thanks", "notepad.exe", "Notepad")
    assert r.text == "Send it to me@example.com, thanks."
    assert sent == ["send it to my email thanks"] and "me@example.com" not in str(sent)


def test_snippets_come_after_lists_and_keep_their_line_breaks(monkeypatch):
    # TXT-12: before, the list pass ran on the saved text and flattened its line break ("Best regards, Yuvi")
    r = core.process_text(_cfg(cleanup=False), "First, my email. Second, my signature.", "x.exe", "x")
    assert r.text == "1. me@example.com\n2. Best regards,\nYuvi"


def test_a_saved_text_with_list_markers_no_longer_stops_the_lists(monkeypatch):
    cfg = dict(_cfg(cleanup=False), snippets={"my list": "- a\n- b"})
    r = core.process_text(cfg, "Send my list. First, milk. Second, eggs.", "x.exe", "x")
    assert r.text == "Send - a\n- b.\n1. Milk\n2. Eggs"


def test_snippets_work_in_a_code_app_and_when_cleanup_is_off():
    assert core.process_text(_cfg(cleanup=False), "my email", "x.exe", "x").text == "me@example.com"
    assert core.process_text(_cfg(), "git config user dot email quote my email quote", "pwsh.exe", "x").text == \
        'git config user.email "me@example.com"'


def test_the_improve_run_sends_the_trigger_not_the_saved_text():
    hist = [{"t": 10, "raw": "send it to my email", "text": "Send it to me@example.com.\n\n" + SIG}]
    pairs = improve.select_transcripts(hist, 0, 10_000, S)
    assert pairs[0]["cleaned"] == "Send it to my email.\n\nmy signature"
    assert improve.selection(hist, 0, 100, S)[1] == pairs
    assert improve.preview(dict(core.DEFAULT_CONFIG, snippets=S), hist, 0, 100)["chars"] == len(hist[0]["raw"]) + len(pairs[0]["cleaned"])


def test_the_pipeline_records_where_each_saved_text_landed():
    r = core.process_text(_cfg(cleanup=False), "First, my email. Second, my signature.", "x.exe", "x")
    assert r.snippets == [[3, 17, "My email"], [21, 21 + len(SIG), "My signature"]]
    assert snippets.put_back(r.text, r.snippets) == "1. My email\n2. My signature"
    assert core.process_text(_cfg(cleanup=False), "no snippet here", "x.exe", "x").snippets == []


def test_a_deleted_snippet_is_still_never_sent_by_improve():
    # PRV-3: the entry's own spans put the phrase back; today's snippets (here: none, the snippet was deleted) do not matter
    text, spans = snippets.expand("Send it to my address.", {"my address": "Flat 4, 12 Secret Road, London"})
    hist = [{"t": 10, "raw": "send it to my address", "text": text, "snippets": spans}]
    for now in (None, {}, {"my address": "Somewhere else"}):
        assert improve.select_transcripts(hist, 0, 10_000, now)[0]["cleaned"] == "Send it to my address."
    legacy = [{"t": 10, "raw": "send it to my address", "text": text}]   # before entries recorded spans: today's snippets
    assert improve.select_transcripts(legacy, 0, 10_000, None)[0]["cleaned"] == text


@pytest.mark.parametrize("spans", [[[5, 2, "x"]], [[0, 999, "x"]], [[0, 3, 4]], "x", [[0, 1]], [[True, 2, "x"]],
                                   [[4, 6, "a"], [0, 2, "b"]]])
def test_an_entry_whose_spans_do_not_fit_is_left_out(spans):
    hist = [{"t": 10, "raw": "a b c", "text": "secret text", "snippets": spans}, {"t": 11, "raw": "ok", "text": "Ok."}]
    assert [p["cleaned"] for p in improve.select_transcripts(hist, 0, 10_000, None)] == ["Ok."]


def test_snippets_are_part_of_the_synced_profile():
    assert "snippets" in sync.PROFILE_FIELDS


def test_merge_keeps_local_snippets_when_the_relay_has_only_a_blank_default():
    assert sync.merge3({}, {"snippets": S}, {"snippets": {}}) == {"snippets": S}


# ------------------------------------------------------------------ the pages

@pytest.mark.parametrize("page", [os.path.join("windows", "ui", "index.html"), os.path.join("android", "assets", "index.html")])
def test_both_dictionary_pages_have_a_snippets_list(page):
    with open(os.path.join(ROOT, page), encoding="utf-8") as f:
        html = f.read()
    dict_page = re.search(r'<section class="page[^"]*" id="(?:dictionary|dict)">.*?</section>', html, re.S).group(0)
    for part in ('id="snip-trigger"', 'id="snip-text"', 'id="snip-add"', 'id="snips"', "<h2>Snippets</h2>"):
        assert part in dict_page, part
    assert 'maxlength="2000"' in dict_page
    # Android saves the map; Windows edits one snippet in the file's current map (DAT-9 of the v2 review)
    assert ("save({ snippets: " in html or "api().snippet_set(" in html) and "snippetsAdd(" in html


NODE = __import__("shutil").which("node")


def _add(tmp, snips, trigger, text):
    """snippetsAdd from ui-shared/common.js, run by node (from a file: the maps are too long for a command line)."""
    import json
    import subprocess
    with open(os.path.join(ROOT, "ui-shared", "common.js"), encoding="utf-8") as f:
        code = f.read()
    code += "\n;process.stdout.write(JSON.stringify(snippetsAdd(%s, %s, %s)));" % (json.dumps(snips), json.dumps(trigger), json.dumps(text))
    script = tmp / "add.js"
    script.write_text(code, encoding="utf-8")
    r = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_page_refuses_what_the_app_would_drop_and_says_so(tmp_path):
    """Final fixes (android 4): the page said "Added" for a snippet the app then dropped on save."""
    full = {"t%d" % i: "क" * 1000 for i in range(3)}          # 18,006 bytes once escaped
    res = _add(tmp_path, full, "one more", "क" * 1000)
    assert res["ok"] is False and "too much" in res["msg"].lower() and res["map"] == full
    res = _add(tmp_path, {}, "!!", "x")                                   # no letter or digit in the phrase
    assert res["ok"] is False and res["map"] == {}
    res = _add(tmp_path, {}, "sig", "s" * 2500)
    assert res["ok"] is True and len(res["map"]["sig"]) == 2000 and "2,000" in res["msg"] and res["msg"] != "Added"
    res = _add(tmp_path, {}, "my email", "me@example.com")
    assert res == {"ok": True, "map": {"my email": "me@example.com"}, "msg": "Added"}
    assert all(snippets.clean_snippets(r["map"]) == r["map"] for r in (res,))
