"""Learn from my corrections: the pure rules (autolearn.py). The shared cases are in spec/golden.txt (kinds autocorrect and
autolearn) and run here through test_parity.py and on Android through ParityTest; these are the extra unit checks."""
import os
import re

import pytest

import autolearn as al
import vox_core as core

ROOT = os.path.join(os.path.dirname(__file__), "..")


# ------------------------------------------------------------------ detect

def test_a_typo_fixed_inside_the_typed_text_is_found():
    assert al.detect("send it to Minhaj today", "send it to Minhajuddin today") == [["Minhaj", "Minhajuddin"]]


def test_text_before_and_after_the_typed_text_is_ignored():
    assert al.detect("ask you vrag about it", "Dear team, please ask Yuvraj about it. Thanks!") == [["you vrag", "Yuvraj"]]


def test_an_edit_of_the_first_word_is_still_found():
    assert al.detect("Minhaj said hi", "Hi Minhaj. Minhajuddin said hi") == [["Minhaj", "Minhajuddin"]]


def test_a_rewrite_is_not_learned():
    assert al.detect("I will send it today", "I will ship it tomorrow") == []


def test_capitals_only_are_not_learned():
    assert al.detect("ping yuvraj about it", "ping Yuvraj about it") == []


def test_stop_word_swaps_are_not_learned():
    assert al.detect("meet at the cafe", "meet at a cafe") == []
    assert al.detect("we need to go", "we need too go") == []


def test_nothing_is_found_when_the_typed_text_is_gone():
    assert al.locate("send it to Minhaj today", "something else entirely") is None
    assert al.detect("send it to Minhaj today", "something else entirely") == []


def test_a_much_longer_or_shorter_span_is_a_rewrite():
    assert al.detect("one two three four five six seven", "one 2 3 4 5 6 seven") == []


def test_at_most_three_pairs():
    ins = "alpha vox beta vox gamma vox delta vox epsilon vox zeta"
    cur = "alpha voxx beta vix gamma vax delta voxx epsilon vix zeta"
    assert len(al.detect(ins, cur)) == 3


def test_no_break_spaces_split_words_like_spaces():
    assert al.tokens("a b c  d") == ["a", "b", "c", "d"]
    assert al.detect("send it to Minhaj today", "send it to Minhajuddin today") == [["Minhaj", "Minhajuddin"]]


def test_empty_inputs():
    assert al.detect("", "") == [] and al.detect("", "x") == [] and al.detect("x", "") == [] and al.detect(None, None) == []


# ------------------------------------------------------------------ the similarity rules

def test_looks_like_fix_rules():
    assert al.looks_like_fix("recieve", "receive")          # edit distance (a swap of two letters is one edit)
    assert al.looks_like_fix("nite", "night")               # same first letter, same length
    assert al.looks_like_fix("user underscore id", "user_id")   # an identifier for plain words
    assert not al.looks_like_fix("send", "ship")
    assert not al.looks_like_fix("vox", "Vox")              # capitals only
    assert not al.looks_like_fix("e-mail", "email")         # punctuation only
    assert not al.looks_like_fix("to", "too")               # a stop word would change everywhere


@pytest.mark.parametrize("wrong, right", [
    ("grok", "gr"), ("grok", "Gro"), ("Minhajuddin", "Minhaj"), ("grok", "rok"),      # cut short (final fixes, android 1)
    ("1234", "1243"), ("5551234", "5551243"), ("4821", "4812"), ("$1,200", "$1,250"), ("0042", "0024"),
    ("10pm", "11pm"), ("v2", "v3"), ("4", "for"), ("two", "2"),                       # numbers (android 2)
    ("their", "there"), ("now", "not"), ("hai", "hain"), ("your", "you're"), ("then", "than"), ("there", "their"),
])                                                                                   # two ordinary words (windows 4)
def test_pairs_that_are_never_learned(wrong, right):
    assert not al.looks_like_fix(wrong, right)


@pytest.mark.parametrize("wrong, right", [
    ("Minhaj", "Minhajuddin"), ("teh", "the"), ("recieve", "receive"), ("grok", "Groq"), ("ec two", "EC2"),
    ("their", "Thier"),
])
def test_pairs_that_are_still_learned(wrong, right):
    assert al.looks_like_fix(wrong, right)


def test_a_longer_word_waits_for_the_final_look():
    assert al.looks_like_fix("Minhaj", "Minhajuddin") and not al.looks_like_fix("Minhaj", "Minhajuddin", final=False)
    assert al.looks_like_fix("grok", "Groq", final=False)


def test_python_and_java_share_one_short_word_list():
    src = open(os.path.join(ROOT, "android", "src", "com", "minhaj", "vox", "AutoLearn.java"), encoding="utf-8").read()
    m = re.search(r'SHORT_WORDS_TEXT\s*=\s*((?:\s*"[^"]*"\s*\+?)+);', src)
    assert m, "AutoLearn.java has no SHORT_WORDS_TEXT"
    assert set("".join(re.findall(r'"([^"]*)"', m.group(1))).split()) == set(al.SHORT_WORDS)
    assert all(len(w) < core.FUZZY_MIN_LEN and w == w.lower() for w in al.SHORT_WORDS)


def test_soundex_and_osa():
    assert al.soundex("Robert") == "R163" == al.soundex("Rupert")
    assert al.soundex("Ashcraft") == "A261"
    assert al.soundex("Tymczak") == "T522"
    assert al.soundex("123") == ""
    assert al.osa("teh", "the") == 1 and al.osa("kitten", "sitting") == 3 and al.osa("", "abc") == 3


def test_name_like_and_plain():
    assert al.name_like("Yuvraj") and al.name_like("user_id") and al.name_like("v2") and al.name_like("React.js")
    assert not al.name_like("hello") and not al.name_like("Groq Cloud")
    assert al.plain("you vrag") and not al.plain("Yuvraj") and not al.plain("v2")


# ------------------------------------------------------------------ learn and the learned log

def test_learn_adds_the_replacement_and_a_name_like_word():
    assert al.learn([], [], [["you vrag", "Yuvraj"]]) == {"replacements": [["you vrag", "Yuvraj"]], "words": ["Yuvraj"]}


def test_learn_skips_a_wrong_word_that_already_has_a_replacement():
    assert al.learn({"Minhaj": "Minhajuddin"}, [], [["minhaj", "Minhaj Uddin"]]) == {"replacements": [], "words": []}


def test_learn_does_not_add_a_word_twice():
    assert al.learn([], ["yuvraj"], [["you vrag", "Yuvraj"]])["words"] == []


def test_learn_respects_the_dictionary_cap():
    full = [["w%d" % i, "r%d" % i] for i in range(al.MAX_DICTIONARY_LINES)]
    assert al.learn(full, [], [["you vrag", "Yuvraj"]]) == {"replacements": [], "words": []}
    one_left = full[:-1]
    assert al.learn(one_left, [], [["you vrag", "Yuvraj"]]) == {"replacements": [["you vrag", "Yuvraj"]], "words": []}


def test_learn_refuses_lines_that_would_not_read_back():
    assert al.learn([], [], [["a=>b", "c"], ["#x", "y"], ["", "z"]]) == {"replacements": [], "words": []}


def test_apply_and_remove_learned_round_trip():
    cfg = {"dictionary": ["LoomXR", "vox => Vox"], "learned_log": []}
    parts, added = al.apply_learned(cfg, [["you vrag", "Yuvraj"], ["recieve", "receive"]], now=1000.0)
    assert added == [["you vrag", "Yuvraj"], ["recieve", "receive"]]
    assert parts["dictionary"] == ["LoomXR", "vox => Vox", "you vrag => Yuvraj", "recieve => receive", "Yuvraj"]
    assert parts["learned_log"] == [{"t": 1000.0, "wrong": "you vrag", "right": "Yuvraj", "word": True},
                                    {"t": 1000.001, "wrong": "recieve", "right": "receive", "word": False}]
    after = al.remove_learned(dict(cfg, **parts), 1000.0)
    assert after["dictionary"] == ["LoomXR", "vox => Vox", "recieve => receive"]
    assert [e["wrong"] for e in after["learned_log"]] == ["recieve"]


def test_apply_learned_with_nothing_new_changes_nothing():
    assert al.apply_learned({"dictionary": ["vox => Vox"]}, [["vox", "VOX"]]) == ({}, [])


def test_remove_learned_keeps_a_word_the_user_had_before():
    cfg = {"dictionary": ["Yuvraj"]}
    parts, _ = al.apply_learned(cfg, [["you vrag", "Yuvraj"]], now=5.0)
    assert parts["dictionary"] == ["Yuvraj", "you vrag => Yuvraj"] and parts["learned_log"][0]["word"] is False
    assert al.remove_learned(dict(cfg, **parts), 5.0)["dictionary"] == ["Yuvraj"]


def test_the_learned_log_keeps_the_last_twenty_and_drops_junk():
    log = [{"t": i, "wrong": "w%d" % i, "right": "r"} for i in range(30)] + ["junk", {"t": True, "wrong": "a", "right": "b"}]
    out = al.learned_log({"learned_log": log})
    assert len(out) == al.LEARNED_LOG_MAX and out[0]["wrong"] == "w10" and out[-1]["wrong"] == "w29"
    assert al.learned_log({"learned_log": "nope"}) == [] and al.learned_log({}) == []


def test_a_learned_entry_whose_line_left_the_dictionary_leaves_the_log():
    log = [{"t": 1, "wrong": "fubar", "right": "Foobar"}, {"t": 2, "wrong": "grok", "right": "Groq"}]
    cfg = {"dictionary": ["Vox", "grok => Groq", "# fubar => Foobar"], "learned_log": log}   # DAT-6: dropped on the phone
    assert [e["wrong"] for e in al.learned_log(cfg)] == ["grok"]
    parts, _ = al.apply_learned(cfg, [["teh", "the"]], now=3.0)
    assert [e["wrong"] for e in parts["learned_log"]] == ["grok", "teh"]


def test_enabled_defaults_to_on():
    assert al.enabled({}) and al.enabled({"auto_learn": True}) and not al.enabled({"auto_learn": False})


# ------------------------------------------------------------------ the watch

class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


TYPED = "send it to Minhaj today"
FIXED = "Hello. send it to Minhajuddin today"


def watch():
    c = Clock()
    w = al.Watch(clock=c)
    w.arm("app", TYPED)
    return w, c


GROK = "we use grok for speech"
GROQ = "Hello. we use Groq for speech"


def test_a_fix_is_reported_once_the_text_has_settled():
    w, c = watch()
    w.arm("app", GROK)
    assert w.observe("app", "Hello. " + GROK) == []
    c.t += 2
    assert w.observe("app", GROQ) == []           # just changed: not analysed yet
    c.t += 1.0
    assert w.observe("app", GROQ) == []           # 1.0 s quiet
    c.t += 0.6
    assert w.observe("app", GROQ) == [["grok", "Groq"]]
    c.t += 5
    assert w.observe("app", GROQ) == []           # reported once only
    assert w.is_armed()


def test_a_change_after_a_quiet_period_analyses_the_quiet_text_at_once():
    w, c = watch()
    w.arm("app", GROK)
    w.observe("app", GROQ)
    c.t += 1.6
    assert w.observe("app", GROQ + " more") == [["grok", "Groq"]]


def test_a_word_cut_short_while_retyping_is_never_learned_and_the_real_fix_still_is():
    """Final fixes (android 1): "grok" backspaced to "gr", a pause, then "Groq". "grok => gr" used to be learned and then
    blocked the real fix for good."""
    w, c = watch()
    w.arm("app", GROK)
    for half in ("we use gr for speech", "we use Gro for speech"):
        w.observe("app", half)
        c.t += 2
        assert w.observe("app", half) == []
    w.observe("app", "we use Groq for speech")
    c.t += 2
    assert w.observe("app", "we use Groq for speech") == [["grok", "Groq"]]


def test_a_word_made_longer_is_learned_only_when_the_watch_ends():
    """While the user may still be typing, "Minhaj" -> "Minhaju" is not taken; the last text is when the watch ends."""
    w, c = watch()
    w.observe("app", "Hello. send it to Minhaju today")
    c.t += 2
    assert w.observe("app", "Hello. send it to Minhaju today") == []
    w.observe("app", FIXED)
    c.t += 2
    assert w.observe("app", FIXED) == []
    assert w.end() == [["Minhaj", "Minhajuddin"]]


def test_still_watching_at_179_seconds_and_ended_at_181():
    w, c = watch()
    c.t += 179
    w.observe("app", TYPED)
    assert w.is_armed() and w.armed
    c.t += 2
    assert not w.is_armed()
    assert w.observe("app", TYPED) == [] and not w.armed


def test_the_window_constant_is_three_minutes_and_matches_android():
    assert al.AUTO_LEARN_WINDOW_S == 180
    src = open(os.path.join(ROOT, "android", "src", "com", "minhaj", "vox", "AutoLearnWatch.java"), encoding="utf-8").read()
    assert int(re.search(r"AUTO_LEARN_WINDOW_S\s*=\s*(\d+)", src).group(1)) == al.AUTO_LEARN_WINDOW_S
    assert int(re.search(r"SETTLE_MS\s*=\s*(\d+)", src).group(1)) == al.SETTLE_S * 1000


def test_a_fix_made_just_before_send_is_learned_when_the_field_empties():
    w, c = watch()
    w.observe("app", TYPED)
    c.t += 3
    w.observe("app", FIXED)
    c.t += 0.4                                    # Send pressed within the debounce: the field is cleared
    assert w.observe("app", "") == [["Minhaj", "Minhajuddin"]]
    assert not w.armed


def test_the_span_replaced_by_other_text_ends_the_watch_with_a_final_check():
    w, c = watch()
    w.observe("app", FIXED)
    c.t += 0.2
    assert w.observe("app", "a whole new message is being written here") == [["Minhaj", "Minhajuddin"]]
    assert not w.armed


def test_a_field_shrunk_to_under_half_ends_the_watch():
    w, c = watch()
    w.observe("app", TYPED)
    assert w.observe("app", "send it") == [] and not w.armed


def test_another_app_ends_the_watch_and_flushes():
    w, c = watch()
    w.observe("app", FIXED)
    assert w.observe("other", "anything") == [["Minhaj", "Minhajuddin"]]
    assert not w.armed and w.observe("app", FIXED) == []


def test_unreadable_text_ends_the_watch():
    w, c = watch()
    assert w.observe("app", None) == [] and not w.armed


def test_a_new_dictation_rearms_with_the_new_text_after_a_final_check():
    w, c = watch()
    w.observe("app", FIXED)
    assert w.arm("app", "and then call Ada") == [["Minhaj", "Minhajuddin"]]
    assert w.armed and w.inserted == "and then call Ada"
    w.observe("app", FIXED + " and then call Aida")
    c.t += 2
    assert w.observe("app", FIXED + " and then call Aida") == [["Ada", "Aida"]]


def test_end_drops_the_snapshot():
    w, c = watch()
    w.observe("app", FIXED)
    w.end()
    assert w._snapshot is None and not w.armed and w.end() == []


def test_unarmed_watch_does_nothing():
    w = al.Watch(clock=Clock())
    assert w.observe("app", "x") == [] and not w.is_armed() and w.arm("app", "  ") == [] and not w.armed


# ------------------------------------------------------------------ TXT-1: grammar edits are not learned

@pytest.mark.parametrize("wrong,right", [
    ("complete", "completed"), ("client", "clients"), ("update", "updated"), ("deployment", "deployments"),
    ("invoice", "invoices"), ("happen", "happened"), ("create", "creating"), ("commit", "committed"),
    ("company", "companies"), ("schedule", "scheduled"), ("process", "processes"), ("manager", "managers"),
    ("Client", "Clients"), ("meeting is", "meetings are"), ("users report", "user reports"),
    ("में", "मैं"), ("की", "के"), ("को", "के"), ("है", "हैं"), ("हूं", "हूँ"),
])
def test_a_grammar_edit_is_not_learned(wrong, right):
    assert not al.looks_like_fix(wrong, right)
    assert al.grammar_edit(wrong, right) or al.ordinary(wrong)   # one Hindi function word: the ordinary-word rule


@pytest.mark.parametrize("wrong,right", [
    ("Minhaj", "Minhajuddin"), ("grok", "Groq"), ("jason", "JSON"), ("shital", "Sheetal"), ("you vrag", "Yuvraj"),
    ("ec two", "EC2"), ("get user name", "getUserName"), ("postgre", "Postgres"), ("jone", "Jones"),
    ("wisper", "Whisper"), ("lama", "Llama"), ("cloud flare", "Cloudflare"), ("teh", "the"), ("recieve", "receive"),
    ("nite", "night"), ("seperate", "separate"), ("definately", "definitely"), ("accomodate", "accommodate"),
    ("acha", "accha"), ("यार", "यारा"), ("Mark", "Marc"), ("their", "Thier"),
])
def test_names_and_misheard_words_are_still_learned(wrong, right):
    assert al.looks_like_fix(wrong, right)
