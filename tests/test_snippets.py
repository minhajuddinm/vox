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


# ------------------------------------------------------------------ the pipeline

def _cfg(**kw):
    return dict(core.DEFAULT_CONFIG, api_key="k", snippets=S, **kw)


def test_snippets_come_after_the_cleanup_and_never_go_to_it(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "cleanup", lambda cfg, raw, style, label: sent.append(raw) or "Send it to my email, thanks.")
    r = core.process_text(_cfg(), "send it to my email thanks", "notepad.exe", "Notepad")
    assert r.text == "Send it to me@example.com, thanks."
    assert sent == ["send it to my email thanks"] and "me@example.com" not in str(sent)


def test_snippets_come_before_lists(monkeypatch):
    r = core.process_text(_cfg(cleanup=False), "First, my email. Second, my signature.", "x.exe", "x")
    assert r.text == "1. me@example.com\n2. Best regards, Yuvi"


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
    assert "save({ snippets: " in html and "snippetsAdd(" in html
