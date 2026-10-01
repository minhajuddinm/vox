"""The dictionary is applied to the final text: a term spelled slightly wrong, or in the wrong case, is put right.

The same rules run on the phone (Terms.fuzzy); spec/golden.txt (kind fuzzydict) ties the two together. These tests add
what does not fit one line there: the pipeline wiring, idempotence over every golden row, and the shared stoplist."""
import os
import re

import pytest

import vox_core as core

B = chr(92)
ROOT = os.path.join(os.path.dirname(__file__), "..")


def golden_rows():
    with open(os.path.join(ROOT, "spec", "golden.txt"), encoding="utf-8") as fh:
        return [ln.rstrip("\r\n").split("\t") for ln in fh if ln.startswith("fuzzydict\t")]


def test_golden_has_rows_for_every_rule():
    assert len(golden_rows()) >= 25


@pytest.mark.parametrize("row", golden_rows())
def test_applying_twice_changes_nothing(row):
    terms = [t for t in row[1].split("|") if t]
    once = core.fuzzy_dictionary(row[2].replace(B + "n", "\n").replace(B + "t", "\t"), terms)
    assert core.fuzzy_dictionary(once, terms) == once


def test_process_text_applies_the_dictionary_without_cleanup():
    cfg = dict(core.DEFAULT_CONFIG, cleanup=False, dictionary=["Kubernetes"], people=["Mohammed"])
    r = core.process_text(cfg, "we deploy on kubernetis for mohamed", "app.exe", "App")
    assert r.text == "we deploy on Kubernetes for Mohammed"
    assert r.raw == "we deploy on kubernetis for mohamed"   # the raw transcript stays as heard


def test_a_replacement_line_wins_over_the_fuzzy_pass():
    cfg = dict(core.DEFAULT_CONFIG, cleanup=False, dictionary=["Kubernetis => K8s", "Kubernetes"])
    assert core.process_text(cfg, "run kubernetis now", "app.exe", "App").text == "run K8s now"


@pytest.mark.parametrize("term,text", [
    ("Alice", "they look alike to me"),
    ("Jones", "he tells jokes"),
    ("Laura", "lauda"),
    ("Raven", "he raved"),
    ("Yuvraj", "Yuvraaj said hello"),   # six letters: too close to ordinary words for a one-letter guess
])
def test_a_short_term_never_rewrites_a_word_one_letter_off(term, text):
    assert core.fuzzy_dictionary(text, [term]) == text


def test_a_short_term_still_fixes_the_case_and_a_long_term_the_spelling():
    assert core.fuzzy_dictionary("they look alice to me", ["Alice"]) == "they look Alice to me"
    assert core.fuzzy_dictionary("ask mohamed", ["Mohammed"]) == "ask Mohammed"
    assert core.FUZZY_NEAR_MIN_LEN == 7


def test_nothing_to_do_returns_the_same_text():
    text = "a plain sentence with no terms"
    assert core.fuzzy_dictionary(text, []) == text
    assert core.fuzzy_dictionary("", ["Kubernetes"]) == ""


def test_long_text_with_many_terms_stays_fast():
    import time
    terms = ["Term%sabc" % chr(97 + i % 26) + chr(97 + i // 26) for i in range(200)]
    text = ("the quick brown foxes jumped over lazy dogs again " * 200).strip()   # 2,000 words
    t0 = time.perf_counter()
    assert core.fuzzy_dictionary(text, terms) == text
    assert time.perf_counter() - t0 < 0.5


def test_python_and_java_share_one_stoplist():
    with open(os.path.join(ROOT, "android", "src", "com", "minhaj", "vox", "Terms.java"), encoding="utf-8") as fh:
        java = fh.read()
    m = re.search(r'COMMON\s*=\s*((?:\s*"[^"]*"\s*\+?)+);', java)
    assert m, "Terms.java has no COMMON string"
    java_words = set("".join(re.findall(r'"([^"]*)"', m.group(1))).split())
    assert java_words == set(core.COMMON_WORDS)
    assert all(len(w) >= core.FUZZY_MIN_LEN and w.isalpha() and w == w.lower() for w in core.COMMON_WORDS)
