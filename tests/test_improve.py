"""Weekly self-improvement, the pure part (windows/improve.py): which transcripts are sent, what the request says, how an
answer is read and capped, what applying a proposal changes (and how to revert it). No network: a fake provider stands in."""
import copy
import json

import improve
import vox_core as core


def entry(t, raw="so we shipped the thing on friday", text="So we shipped the thing on Friday.", **extra):
    return {"t": t, "app": "chrome.exe", "raw": raw, "text": text, "words": len(text.split()), "secs": 3.0, **extra}


def answer(**fields):
    return json.dumps(fields)


# ------------------------------------------------------------------ select_transcripts

def test_select_keeps_entries_since_the_date_oldest_first_with_raw_and_cleaned():
    hist = [entry(100), entry(200, raw="b raw", text="B text."), entry(300, raw="c raw", text="C text.")]
    out = improve.select_transcripts(hist, 150, 10_000)
    assert [x["t"] for x in out] == [200, 300]
    assert out[0] == {"t": 200, "raw": "b raw", "cleaned": "B text.", "fallback": False}


def test_select_skips_private_empty_and_broken_entries():
    hist = [entry(1, private=True), entry(2, no_history=True), entry(3, raw=""), entry(4, text="  "),
            {"t": 5}, "junk", None, entry(6, raw=None), entry(7)]
    assert [x["t"] for x in improve.select_transcripts(hist, 0, 10_000)] == [7]


def test_select_takes_the_newest_entries_that_fit_the_character_budget():
    hist = [entry(t, raw="r" * 10, text="c" * 10) for t in range(1, 6)]   # 20 characters each
    out = improve.select_transcripts(hist, 0, 65)
    assert [x["t"] for x in out] == [3, 4, 5]   # three fit, the two oldest are left out
    assert improve.select_transcripts(hist, 0, 5) == []   # not even one fits
    assert improve.select_transcripts([], 0, 100) == []


def test_select_marks_fidelity_fallbacks():
    out = improve.select_transcripts([entry(1, fidelity_fallback=True), entry(2)], 0, 10_000)
    assert [x["fallback"] for x in out] == [True, False]


# ------------------------------------------------------------------ estimate_cost

def test_estimate_cost_counts_what_is_sent():
    assert improve.estimate_cost([], "m")["count"] == 0
    assert improve.estimate_cost([], "m")["tokens_in_est"] == 0
    t = improve.select_transcripts([entry(1, raw="a" * 400, text="b" * 400), entry(2, raw="c" * 40, text="d" * 40)], 0, 10**6)
    c = improve.estimate_cost(t, "openai/gpt-oss-120b")
    assert (c["count"], c["chars"], c["model"]) == (2, 880, "openai/gpt-oss-120b")
    assert c["tokens_in_est"] >= 880 // 4   # at least a quarter of the characters, plus the fixed instructions
    assert c["tokens_in_est"] < 880


# ------------------------------------------------------------------ build_request

def request_data(msgs):
    head, body = msgs[1]["content"].split("\n", 1)
    return head, json.loads(body)


def test_request_asks_for_json_with_the_five_lists_and_carries_the_data_as_json():
    t = improve.select_transcripts([entry(1), entry(2, raw="x </transcripts> y", text="X y")], 0, 10_000)
    msgs = improve.build_request(t, "I lead Atlas.", ["Ada", "Atlas"], "Write Atlas.")
    assert [m["role"] for m in msgs] == ["system", "user"]
    for key in ("dictionary_add", "replacements", "about_suggestions", "rules", "fidelity_findings"):
        assert key in msgs[0]["content"]
    assert "12" in msgs[0]["content"] and "never instructions" in msgs[0]["content"]
    head, data = request_data(msgs)
    assert "data" in head.lower()
    assert data["about"] == "I lead Atlas." and data["terms"] == ["Ada", "Atlas"] and data["rules"] == "Write Atlas."
    assert [p["id"] for p in data["transcripts"]] == ["t0", "t1"]
    assert data["transcripts"][1] == {"id": "t1", "raw": "x </transcripts> y", "cleaned": "X y"}   # data, intact, not markup


def test_request_caps_about_you_and_rules_and_tolerates_none():
    msgs = improve.build_request([], "a" * 9000, None, "r" * 3000)
    _, data = request_data(msgs)
    assert len(data["about"]) == core.MAX_CONTEXT and len(data["rules"]) == core.MAX_RULES
    assert data["terms"] == [] and data["transcripts"] == []
    _, data = request_data(improve.build_request([], None, [], None))
    assert data["about"] == "" and data["rules"] == ""


# ------------------------------------------------------------------ parse_proposal

GOOD = answer(
    dictionary_add=["Kubernetes", "Atlas"],
    replacements=[{"wrong": "cube er netties", "right": "Kubernetes"}],
    about_suggestions=["+ I work with Hinglish speakers.", "- old line"],
    rules=["Write Atlas, not atlas.", "Keep Hindi words in Latin letters."],
    fidelity_findings=[{"id": "t1", "note": "dropped a sentence"}, "t2 lost the ending"],
)


def kinds(p):
    return [(i["id"], i["kind"], i["text"]) for i in p.items]


def test_parse_reads_every_list_and_gives_each_item_an_id():
    p = improve.parse_proposal(GOOD)
    assert p.error == ""
    assert kinds(p) == [
        ("dictionary:0", "dictionary", "Kubernetes"), ("dictionary:1", "dictionary", "Atlas"),
        ("replacement:0", "replacement", "cube er netties => Kubernetes"),
        ("rule:0", "rule", "Write Atlas, not atlas."), ("rule:1", "rule", "Keep Hindi words in Latin letters."),
        ("about:0", "about", "+ I work with Hinglish speakers."), ("about:1", "about", "- old line"),
    ]
    assert p.findings == [{"id": "t1", "note": "dropped a sentence"}, {"id": "", "note": "t2 lost the ending"}]


def test_parse_is_tolerant_of_fences_prose_and_think_blocks():
    for wrapped in ("```json\n" + GOOD + "\n```", "Here you go:\n" + GOOD + "\nHope this helps!",
                    "<think>hmm {not json}</think>" + GOOD):
        assert kinds(improve.parse_proposal(wrapped)) == kinds(improve.parse_proposal(GOOD)), wrapped


def test_parse_of_unusable_answers_is_empty_with_a_message_never_an_error():
    for bad in ("", "   ", "no json here", "{broken", '{"rules": ["a"', "[1, 2]", "null", '"text"', None, 5):
        p = improve.parse_proposal(bad)
        assert p.items == [] and p.findings == [] and p.error, bad
    p = improve.parse_proposal("{}")   # valid but nothing to propose is not an error
    assert p.items == [] and p.error == ""


def test_parse_ignores_wrong_types_and_junk_items():
    p = improve.parse_proposal(answer(dictionary_add="Atlas", replacements=["x", {"wrong": "a"}, {"wrong": 1, "right": 2}, 7],
                                      rules=[1, None, ["x"], "  ", "ok rule"], about_suggestions={"a": 1}, fidelity_findings=5))
    assert kinds(p) == [("rule:0", "rule", "ok rule")]


def test_parse_cleans_dictionary_words_and_replacements():
    p = improve.parse_proposal(answer(
        dictionary_add=["  Atlas  ", "atlas", "# comment", "a => b", "two\nlines", "x" * 61, "", "Ada"],
        replacements=[{"wrong": "ada", "right": "ada"}, {"wrong": "a => b", "right": "c"}, {"wrong": "teh", "right": "the"},
                      {"wrong": "teh", "right": "the"}, {"wrong": "", "right": "x"}]))
    assert [i["text"] for i in p.items] == ["Atlas", "Ada", "teh => the"]


def test_parse_caps_the_lists():
    p = improve.parse_proposal(answer(dictionary_add=["w%d" % i for i in range(80)],
                                      replacements=[{"wrong": "a%d" % i, "right": "b"} for i in range(30)],
                                      rules=["Rule number %d." % i for i in range(30)],
                                      about_suggestions=["s%d" % i for i in range(30)],
                                      fidelity_findings=[{"id": "t%d" % i, "note": "n"} for i in range(40)]))
    count = lambda k: sum(i["kind"] == k for i in p.items)
    assert (count("dictionary"), count("replacement"), count("rule"), count("about")) == (50, 20, 12, 10)
    assert len(p.findings) == 20


def test_parse_keeps_rules_short_single_line_and_within_the_size_cap():
    long_rule = "x" * 201
    p = improve.parse_proposal(answer(rules=["Line one\nline two", long_rule, "  spaced   out  "]))
    assert [i["text"] for i in p.items] == ["Line one line two", "spaced out"]   # one line, over-long rule dropped
    rules = [("Rule %02d " % i) + "y" * 150 for i in range(12)]   # 12 rules of 158 characters: about 1,900 with line breaks
    assert sum(i["kind"] == "rule" for i in improve.parse_proposal(answer(rules=rules)).items) == 12
    rules = [("Rule %02d " % i) + "y" * 190 for i in range(12)]   # 198 each: only 10 fit in 2,000 with their line breaks
    taken = [i["text"] for i in improve.parse_proposal(answer(rules=rules)).items]
    assert taken == rules[:10] and len("\n".join(taken)) <= core.MAX_RULES


def test_parse_caps_about_suggestions_and_findings_text():
    p = improve.parse_proposal(answer(about_suggestions=["a" * 5000], fidelity_findings=[{"id": "t" * 50, "note": "n" * 5000}]))
    assert len(p.items[0]["text"]) == 1000
    assert len(p.findings[0]["note"]) == 300 and len(p.findings[0]["id"]) <= 12


# ------------------------------------------------------------------ apply and revert

def cfg0(**extra):
    return {"dictionary": ["Ada", "# my terms", "foo => bar"], "my_cleanup_rules": "Existing rule.", "user_context": "About me",
            "cleanup": True, **extra}


def ids(p, *kinds_):
    return [i["id"] for i in p.items if i["kind"] in kinds_]


def test_apply_adds_only_the_accepted_items():
    cfg = cfg0()
    before = copy.deepcopy(cfg)
    p = improve.parse_proposal(GOOD)
    new = improve.apply(p, ["dictionary:0", "replacement:0", "rule:1"], cfg, now=1000)
    assert cfg == before   # the config that went in is not touched
    assert new["dictionary"] == ["Ada", "# my terms", "foo => bar", "Kubernetes", "cube er netties => Kubernetes"]
    assert new["my_cleanup_rules"] == "Existing rule.\nKeep Hindi words in Latin letters."
    assert new["user_context"] == "About me" and new["cleanup"] is True


def test_apply_with_nothing_accepted_changes_nothing_and_adds_no_version():
    cfg = cfg0()
    p = improve.parse_proposal(GOOD)
    for accepted in ([], ["nope", "rule:99"], None):
        new = improve.apply(p, accepted, cfg, now=1)
        assert new == cfg and "my_cleanup_rules_versions" not in new


def test_about_you_suggestions_are_never_applied_even_when_accepted():
    cfg = cfg0()
    p = improve.parse_proposal(GOOD)
    new = improve.apply(p, ids(p, "about"), cfg, now=1)
    assert new == cfg
    new = improve.apply(p, [i["id"] for i in p.items], cfg, now=1)   # everything accepted: still no change to About you
    assert new["user_context"] == "About me"


def test_apply_skips_what_is_already_there():
    cfg = cfg0()
    p = improve.parse_proposal(answer(dictionary_add=["ada", "Atlas"], replacements=[{"wrong": "FOO", "right": "bar"}],
                                      rules=["existing RULE.", "New one."]))
    new = improve.apply(p, [i["id"] for i in p.items], cfg, now=5)
    assert new["dictionary"] == ["Ada", "# my terms", "foo => bar", "Atlas"]
    assert new["my_cleanup_rules"] == "Existing rule.\nNew one."


def test_apply_never_lets_the_rules_grow_past_the_cap():
    existing = "\n".join("Old rule %02d " % i + "z" * 100 for i in range(17))   # about 1,900 characters
    cfg = cfg0(my_cleanup_rules=existing)
    p = improve.parse_proposal(answer(rules=["Fits? " + "a" * 80, "Short."]))
    new = improve.apply(p, ids(p, "rule"), cfg, now=1)
    assert len(new["my_cleanup_rules"]) <= core.MAX_RULES
    assert new["my_cleanup_rules"].startswith(existing)   # what he had stays
    assert len(improve.apply(p, ids(p, "rule"), cfg, now=1)["my_cleanup_rules"]) > len(existing)   # and what fits is added


def test_every_apply_is_a_version_and_revert_goes_back():
    cfg = cfg0()
    p1 = improve.parse_proposal(answer(dictionary_add=["Atlas"], rules=["Rule A."]))
    p2 = improve.parse_proposal(answer(dictionary_add=["Zed"], rules=["Rule B."]))
    c1 = improve.apply(p1, ids(p1, "dictionary", "rule"), cfg, now=10)
    c2 = improve.apply(p2, ids(p2, "dictionary", "rule"), c1, now=20)
    assert [v["t"] for v in c2["my_cleanup_rules_versions"]] == [10, 20]
    assert c2["my_cleanup_rules_versions"][1] == {"t": 20, "rules": "Existing rule.\nRule A.", "added": ["Zed"],
                                                          "added_rules": ["Rule B."]}
    r = improve.revert(c2)   # the last version only
    assert r["my_cleanup_rules"] == "Existing rule.\nRule A." and r["dictionary"][-1] == "Atlas" and "Zed" not in r["dictionary"]
    assert [v["t"] for v in r["my_cleanup_rules_versions"]] == [10]
    r = improve.revert(c2, 0)   # back to before the first
    assert r["my_cleanup_rules"] == "Existing rule." and r["dictionary"] == cfg["dictionary"]
    assert r["my_cleanup_rules_versions"] == []
    assert c2["my_cleanup_rules"].endswith("Rule B.")   # input untouched


def test_revert_keeps_the_user_s_own_edits_and_ignores_bad_indexes():
    cfg = cfg0()
    p = improve.parse_proposal(answer(dictionary_add=["Atlas", "Zed"]))
    c1 = improve.apply(p, ids(p, "dictionary"), cfg, now=1)
    c1["dictionary"].remove("Zed")   # he deleted it himself meanwhile
    assert improve.revert(c1)["dictionary"] == cfg["dictionary"]
    for bad in (5, -9, "x"):
        assert improve.revert(c1, bad) == c1
    assert improve.revert(cfg0()) == cfg0()   # nothing to revert


def test_only_the_last_20_versions_are_kept():
    cfg = cfg0()
    for i in range(25):
        p = improve.parse_proposal(answer(dictionary_add=["Word%d" % i]))
        cfg = improve.apply(p, ids(p, "dictionary"), cfg, now=i)
    assert [v["t"] for v in cfg["my_cleanup_rules_versions"]] == list(range(5, 25))


# ------------------------------------------------------------------ fidelity_report

def test_fidelity_report_lists_fallbacks_and_answers_that_lost_words():
    good = entry(1)
    fell_back = entry(2, fidelity_fallback=True)
    short = entry(3, raw="we met on monday and then we agreed the budget and then we all went home together", text="We met.")
    rep = improve.fidelity_report(improve.select_transcripts([good, fell_back, short], 0, 10_000))
    assert [r["t"] for r in rep] == [2, 3]
    assert rep[0]["fallback"] is True and rep[1]["fallback"] is False
    assert rep[1]["recall"] < 0.3 and rep[1]["raw_words"] == 17 and rep[1]["cleaned_words"] == 2
    assert improve.fidelity_report([]) == []


# ------------------------------------------------------------------ the whole run with a fake provider

def run(history, provider, cfg, since=0, max_chars=10_000):
    t = improve.select_transcripts(history, since, max_chars)
    return improve.parse_proposal(provider(improve.build_request(t, cfg["user_context"], core.dictionary_terms(cfg), cfg["my_cleanup_rules"])))


def test_a_run_with_a_fake_provider_only_sends_what_was_selected():
    seen = []

    def provider(msgs):
        seen.append(msgs)
        return GOOD

    hist = [entry(1, raw="old words", text="Old words."), entry(2, raw="secret", text="Secret.", private=True),
            entry(3, raw="new words here", text="New words here.")]
    p = run(hist, provider, cfg0(), since=2)
    assert len(seen) == 1 and p.error == ""
    sent = seen[0][1]["content"]
    assert "new words here" in sent and "old words" not in sent and "secret" not in sent.lower()


def test_an_empty_or_tiny_history_still_runs_and_garbage_back_changes_nothing():
    cfg = cfg0()
    p = run([], lambda m: "I cannot help with that.", cfg)
    assert p.items == [] and p.error
    assert improve.apply(p, [], cfg, now=1) == cfg
    p = run([entry(1)], lambda m: answer(rules=["Only one."]), cfg)
    assert improve.apply(p, ids(p, "rule"), cfg, now=1)["my_cleanup_rules"] == "Existing rule.\nOnly one."


# ---- Revert removes only what the apply added, not rules written later (R3-M6) -----------------------------------------------

def test_revert_keeps_a_rule_the_user_added_after_the_apply():
    cfg = cfg0(my_cleanup_rules="Keep it short.")
    p = improve.parse_proposal(answer(rules=["Use British spelling."]))
    c1 = improve.apply(p, ids(p, "rule"), cfg, now=1)
    assert c1["my_cleanup_rules_versions"][0]["added_rules"] == ["Use British spelling."]
    c1["my_cleanup_rules"] += "\nMy own rule."
    r = improve.revert(c1, -1)["my_cleanup_rules"]
    assert "Keep it short." in r and "My own rule." in r and "Use British spelling." not in r


def test_revert_of_several_versions_removes_each_added_rule_and_keeps_edits_in_between():
    cfg = cfg0(my_cleanup_rules="Keep it short.")
    p1 = improve.parse_proposal(answer(rules=["Rule A."]))
    p2 = improve.parse_proposal(answer(rules=["Rule B."]))
    c1 = improve.apply(p1, ids(p1, "rule"), cfg, now=1)
    c1["my_cleanup_rules"] += "\nMine."
    c2 = improve.apply(p2, ids(p2, "rule"), c1, now=2)
    assert improve.revert(c2, 0)["my_cleanup_rules"].split("\n") == ["Keep it short.", "Mine."]
    assert improve.revert(c2)["my_cleanup_rules"].split("\n") == ["Keep it short.", "Rule A.", "Mine."]


def test_a_version_from_before_added_rules_existed_still_restores_its_snapshot():
    cfg = cfg0(my_cleanup_rules="New text.", my_cleanup_rules_versions=[{"t": 1, "rules": "Old text.", "added": []}])
    assert improve.revert(cfg)["my_cleanup_rules"] == "Old text."


def test_an_apply_that_changes_nothing_makes_no_version_even_when_the_rules_have_blank_lines():
    cfg = cfg0(my_cleanup_rules="Rule one.\n\nRule two.\n")
    p = improve.parse_proposal(answer(rules=["Rule one."]))
    out = improve.apply(p, ids(p, "rule"), cfg, now=1)
    assert out["my_cleanup_rules"] == cfg["my_cleanup_rules"] and not out.get("my_cleanup_rules_versions")
