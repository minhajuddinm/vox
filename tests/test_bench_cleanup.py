"""The cleanup benchmark (tools/bench_cleanup.py, tools/bench_metrics.py): metrics, corpus, table and the run loop.

Offline only: a fake provider stands in for the cleanup call, so no network, no key and no money is involved."""
import json
import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bench_cleanup as bench
import bench_metrics as m
import vox_core as core

CORPUS = os.path.join(ROOT, "tools", "bench", "corpus.jsonl")
RAW = "so i spent most of today on the billing bug it turns out the retry job charged customers twice"


# ------------------------------------------------------------------ metrics

def test_recall_is_one_when_every_word_is_kept_and_falls_when_words_are_lost():
    assert m.recall(RAW, "So I spent most of today on the billing bug. It turns out the retry job charged customers twice.") == 1.0
    assert m.recall(RAW, "I fixed the billing bug.") < 0.3
    assert m.recall("", "anything") == 1.0


def test_recall_counts_spoken_numbers_as_digits():
    assert m.recall("it costs twenty five dollars", "It costs $25.") == 1.0


def test_added_rate_is_the_share_of_cleaned_words_that_were_not_spoken():
    assert m.added_rate("send the report", "Send the report.") == 0.0
    assert m.added_rate("send the report", "Please send the report right away.") == pytest.approx(3 / 6)
    assert m.added_rate("anything", "") == 0.0


def test_length_ratio_compares_word_counts():
    assert m.length_ratio("a b c d", "A b c d.") == 1.0
    assert m.length_ratio("a b c d", "a b") == 0.5
    assert m.length_ratio("", "x") == 1.0


def test_term_hits_needs_the_exact_spelling_and_whole_words():
    assert m.term_hits("Priya met Anirudh Menon.", ["Priya", "Anirudh Menon"]) == 1.0
    assert m.term_hits("priya met anirudh menon", ["Priya", "Anirudh Menon"]) == 0.0   # case counts
    assert m.term_hits("Priyanka met them", ["Priya", "Anirudh Menon"]) == 0.0         # a longer word is not the term
    assert m.term_hits("Priya met them", ["Priya", "Anirudh Menon"]) == 0.5
    assert m.term_hits("anything", []) == 1.0
    assert m.term_hits("It uses gRPC.", ["gRPC"]) == 1.0
    assert m.term_hits("Uses (gRPC)", ["gRPC"]) == 1.0                                 # punctuation around it is fine


@pytest.mark.parametrize("cleaned,ok", [
    ("So I spent most of today on the billing bug.\n\nIt turns out the retry job charged customers twice.", True),
    ("- so\n- i spent most of today on the billing bug it turns out the retry job charged customers twice", True),
    ("So, I spent most of today on the billing bug. It turns out, the retry job charged customers twice!", True),
    ("So I spent most of today on the billing bug. It turns out the retry job double charged customers.", False),
    ("I spent most of today on the billing bug.", False),
    ("So I spent most of today on the billing bug it turns out the retry job charged customers twice also", False),
    ("So I spent most of today on the retry job it turns out the billing bug charged customers twice", False),   # reordered
])
def test_structure_only_means_the_same_words_in_the_same_order(cleaned, ok):
    assert m.structure_only(RAW, cleaned) is ok


def test_structure_only_lets_pure_noises_go_and_reads_numbers_and_commands():
    assert m.structure_only("um so it costs twenty five dollars new line thanks", "So it costs $25.\nThanks.")
    assert not m.structure_only("so like it costs five", "So it costs five.")   # "like" is a word, not a noise
    assert m.structure_only("so um it costs five", "So, um, it costs five.")      # a noise may stay too


def test_percentile_is_nearest_rank():
    v = [5, 1, 3, 2, 4, 10, 9, 8, 7, 6]
    assert m.percentile(v, 50) == 5
    assert m.percentile(v, 95) == 10
    assert m.percentile([7], 95) == 7
    assert m.percentile([], 95) == 0


# ------------------------------------------------------------------ one row and the summary

def test_score_collects_every_metric_for_one_row():
    row = {"raw": RAW, "style": "neutral", "terms": [], "about": "", "must_keep_terms": ["Billing"]}
    s = m.score(row, "So I spent most of today on the billing bug. It turns out the retry job charged customers twice.", "light")
    assert s == {"recall": 1.0, "added": 0.0, "ratio": 1.0, "guard": True, "terms": 0.0, "structure": True}
    row["must_keep_terms"] = []
    assert m.score(row, "I fixed it.", "light")["guard"] is False
    assert m.score(row, "I fixed it.", "light")["terms"] is None   # nothing to check on this row


def test_score_uses_the_strength_for_the_guard():
    row = {"raw": "um i think uh you know it is fine and we can ship it today", "must_keep_terms": []}
    cleaned = "I think it is fine and we can ship it today."
    assert m.score(row, cleaned, "standard")["guard"] is True
    assert m.score(row, "I think we can ship it today.", "light")["guard"] is False


def result(ms, error="", **score):
    s = {"recall": 1.0, "added": 0.0, "ratio": 1.0, "guard": True, "terms": None, "structure": True}
    s.update(score)
    return {"id": "x", "ms": ms, "error": error, "cleaned": "" if error else "text", "score": None if error else s}


def test_summarize_takes_medians_and_rates_and_leaves_errors_out_of_the_quality_numbers():
    res = [result(1000, recall=1.0, terms=1.0), result(2000, recall=0.5, guard=False, structure=False, terms=0.0),
           result(3000, recall=1.0), result(0, error="API 429: slow down")]
    s = m.summarize(res)
    assert s["rows"] == 4 and s["errors"] == 1
    assert s["median_ms"] == 2000 and s["p95_ms"] == 3000
    assert s["recall"] == pytest.approx(2.5 / 3)
    assert s["guard_pass"] == pytest.approx(2 / 3)
    assert s["structure_ok"] == pytest.approx(2 / 3)
    assert s["term_accuracy"] == 0.5          # only the two rows that have terms
    assert s["length_ratio"] == 1.0 and s["added_rate"] == 0.0


def test_summarize_with_only_errors_does_not_crash():
    s = m.summarize([result(0, error="boom")])
    assert s["rows"] == 1 and s["errors"] == 1 and s["median_ms"] == 0 and s["recall"] == 0.0 and s["term_accuracy"] is None


# ------------------------------------------------------------------ the corpus

def corpus():
    return bench.load_corpus(CORPUS)


def test_corpus_has_enough_rows_with_the_fields_and_unique_ids():
    rows = corpus()
    assert len(rows) >= 30
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert set(r) == {"id", "raw", "style", "terms", "about", "must_keep_terms"}
        assert r["raw"].strip() and r["style"] in core.STRUCTURE_BY_STYLE
        assert isinstance(r["terms"], list) and isinstance(r["must_keep_terms"], list)


def test_corpus_covers_every_kind_of_dictation_the_plan_lists():
    kinds = {r["id"].split("-")[0] for r in corpus()}
    assert {"chat", "long", "fill", "enum", "hing", "name", "num", "cmd"} <= kinds
    assert max(len(r["raw"].split()) for r in corpus()) >= 400   # one very long dictation


def test_every_term_to_keep_is_in_the_dictionary_or_in_about_you():
    for r in corpus():
        for t in r["must_keep_terms"]:
            assert t in r["terms"] or t in r["about"], (r["id"], t)


def test_the_corpus_is_synthetic_no_keys_no_addresses():
    text = open(CORPUS, encoding="utf-8").read()
    for bad in ("gsk_", "@gmail", "api_key", "password"):
        assert bad not in text
    assert not re.search(r"(?<![a-z])sk-\w", text)


def test_giving_the_raw_text_back_keeps_every_word_in_both_strengths():
    for strength in ("light", "standard"):
        res = bench.run(corpus(), lambda row: row["raw"], strength)
        s = m.summarize(res)
        assert s["errors"] == 0 and s["guard_pass"] == 1.0 and s["recall"] == 1.0 and s["added_rate"] < 0.05   # only the spoken commands count as added


# ------------------------------------------------------------------ the run loop and the table

def row_cfg_base():
    return dict(core.DEFAULT_CONFIG, api_key="SECRET-KEY-123", user_context="my own about text", people=["Mine"],
                dictionary=["a => B"], app_styles={"x.exe": "formal"}, default_style="formal",
                my_cleanup_rules="Dana means Dani")


def test_row_config_uses_the_rows_context_and_never_the_users_own():
    row = {"about": "Row about", "terms": ["Ledgerly", "Kubernetes"], "style": "casual"}
    cfg = bench.row_config(row_cfg_base(), row, "standard")
    assert cfg["user_context"] == "Row about"
    assert cfg["my_cleanup_rules"] == ""   # the learned rules hold personal names: they never go to the provider
    assert "Dana" not in core.system_prompt("neutral", [], "App", cfg["user_context"], "light", cfg["my_cleanup_rules"])
    assert core.dictionary_terms(cfg) == ["Ledgerly", "Kubernetes"]
    assert cfg["cleanup_strength"] == "standard" and cfg["default_style"] == "casual" and cfg["app_styles"] == {}
    assert cfg["api_key"] == "SECRET-KEY-123"   # the key still reaches the server it is meant for


class Clock:
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def test_run_times_each_call_scores_it_and_records_errors():
    rows = [{"id": "a", "raw": "hello there my friend", "style": "neutral", "terms": [], "about": "", "must_keep_terms": []},
            {"id": "b", "raw": "second row of text here", "style": "neutral", "terms": [], "about": "", "must_keep_terms": []}]

    def call(row):
        if row["id"] == "b":
            raise core.ApiError(429, "API 429: rate limit")
        return "Hello there, my friend."

    out = bench.run(rows, call, "light", clock=Clock(0.5))
    assert [r["id"] for r in out] == ["a", "b"]
    assert out[0]["ms"] == 500 and out[0]["cleaned"] == "Hello there, my friend." and out[0]["score"]["guard"] is True
    assert out[1]["error"] == "API 429: rate limit" and out[1]["score"] is None


def test_run_pauses_between_calls_not_after_the_last_one():
    pauses = []
    rows = [{"id": str(i), "raw": "one two three four", "style": "neutral", "terms": [], "about": "", "must_keep_terms": []}
            for i in range(3)]
    bench.run(rows, lambda row: row["raw"], "light", pause=2.0, sleep=pauses.append)
    assert pauses == [2.0, 2.0]


def test_table_has_one_column_per_model_and_every_metric():
    a = m.summarize([result(1400), result(1600, guard=False, recall=0.9)])
    b = m.summarize([result(800, terms=1.0), result(900, terms=1.0)])
    text = bench.render_table({"model-a": a, "model-b": b})
    lines = text.splitlines()
    assert "model-a" in lines[0] and "model-b" in lines[0]
    for label in ("latency median", "latency p95", "word recall", "added words", "length ratio", "guard pass", "term accuracy",
                  "format only", "errors"):
        assert any(ln.startswith(label) for ln in lines), label
    row = next(ln for ln in lines if ln.startswith("latency median"))
    assert "1.50 s" in row and "0.85 s" in row
    assert "50%" in next(ln for ln in lines if ln.startswith("guard pass"))
    assert next(ln for ln in lines if ln.startswith("term accuracy")).split()[-2:] == ["-", "100%"]


def test_table_shows_a_dash_where_there_is_no_number():
    s = m.summarize([result(0, error="boom")])
    line = next(ln for ln in bench.render_table({"m": s}).splitlines() if ln.startswith("term accuracy"))
    assert line.split()[-1] == "-"


# ------------------------------------------------------------------ main

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    os.makedirs(tmp_path / "Vox")

    def write(**kw):
        with open(tmp_path / "Vox" / "config.json", "w", encoding="utf-8") as f:
            json.dump(dict(core.DEFAULT_CONFIG, **kw), f)
    write(api_key="SECRET-KEY-123")
    return tmp_path, write


def small_corpus(tmp_path):
    p = tmp_path / "small.jsonl"
    rows = [{"id": f"r{i}", "raw": "send me the report today please", "style": "neutral", "terms": ["Report"], "about": "",
             "must_keep_terms": []} for i in range(3)]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(p)


def echo(cfg, raw, style, app_label):
    return raw.capitalize() + "."


def test_main_runs_the_corpus_prints_a_table_and_saves_json_without_the_key(app, capsys):
    tmp_path, _ = app
    out = tmp_path / "result.json"
    code = bench.main(["--corpus", small_corpus(tmp_path), "--out", str(out), "--pause", "0"], call=echo)
    shown = capsys.readouterr()
    assert code == 0
    assert "word recall" in shown.out and "guard pass" in shown.out and "100%" in shown.out
    saved = json.loads(out.read_text(encoding="utf-8"))
    label = core.DEFAULT_LLM
    assert saved["strength"] == "light" and saved["models"][label]["summary"]["rows"] == 3
    assert len(saved["models"][label]["rows"]) == 3
    for text in (shown.out, shown.err, out.read_text(encoding="utf-8")):
        assert "SECRET-KEY-123" not in text


def test_main_saves_under_the_vox_bench_folder_by_default(app, capsys):
    tmp_path, _ = app
    assert bench.main(["--corpus", small_corpus(tmp_path), "--pause", "0"], call=echo) == 0
    files = os.listdir(tmp_path / "Vox" / "bench")
    assert len(files) == 1 and files[0].startswith("bench-") and files[0].endswith(".json")


def test_main_compare_runs_every_model_on_the_same_rows(app, capsys):
    tmp_path, _ = app
    seen = []

    def call(cfg, raw, style, app_label):
        seen.append(cfg["llm_model"])
        return echo(cfg, raw, style, app_label)

    out = tmp_path / "r.json"
    code = bench.main(["--corpus", small_corpus(tmp_path), "--out", str(out), "--pause", "0",
                       "--compare", "model-a,model-b", "--strength", "standard"], call=call)
    shown = capsys.readouterr().out
    assert code == 0 and seen == ["model-a", "model-b", "model-b", "model-a", "model-a", "model-b"]   # taking turns
    assert "model-a" in shown and "model-b" in shown
    assert set(json.loads(out.read_text(encoding="utf-8"))["models"]) == {"model-a", "model-b"}


def test_main_model_option_sets_the_model(app):
    tmp_path, _ = app
    seen = []
    bench.main(["--corpus", small_corpus(tmp_path), "--pause", "0", "--model", "other-model", "--out", str(tmp_path / "o.json")],
               call=lambda cfg, *a: seen.append(cfg["llm_model"]) or "Send me the report today please.")
    assert set(seen) == {"other-model"}


def test_main_provider_must_be_a_known_preset(app):
    with pytest.raises(SystemExit):
        bench.main(["--provider", "nope"], call=echo)


def test_main_never_sends_the_main_key_to_a_different_provider(app, capsys):
    tmp_path, _ = app   # the config has one key, for Groq
    called = []
    code = bench.main(["--corpus", small_corpus(tmp_path), "--provider", "openai", "--out", str(tmp_path / "o.json")],
                      call=lambda *a: called.append(1))
    shown = capsys.readouterr()
    assert code == 2 and not called
    assert "openai" in (shown.out + shown.err).lower() and "key" in (shown.out + shown.err).lower()
    assert "SECRET-KEY-123" not in shown.out + shown.err


def test_main_uses_a_providers_own_key_when_the_settings_have_one(app):
    tmp_path, write = app
    write(api_key="GROQ-KEY", llm_base_url="https://api.openai.com/v1", llm_api_key="OPENAI-KEY")
    keys = []
    code = bench.main(["--corpus", small_corpus(tmp_path), "--provider", "openai", "--pause", "0", "--out", str(tmp_path / "o.json")],
                      call=lambda cfg, *a: keys.append(core.providers.role_settings(cfg, "llm")[:2]) or "Send me the report today please.")
    assert code == 0 and set(keys) == {("https://api.openai.com/v1", "OPENAI-KEY")}


def test_main_with_a_provider_goes_direct_even_when_the_relay_is_the_ai_server(app):
    tmp_path, write = app
    write(api_key="GROQ-KEY", relay_proxy=True, relay_url="https://pi.example.ts.net", relay_token="tok")
    bases = []
    bench.main(["--corpus", small_corpus(tmp_path), "--provider", "groq", "--pause", "0", "--out", str(tmp_path / "o.json")],
               call=lambda cfg, *a: bases.append(core.providers.role_settings(cfg, "llm")[0]) or "Send me the report today please.")
    assert set(bases) == {core.providers.GROQ_BASE}


def test_main_without_a_key_stops_before_any_request(app, capsys):
    tmp_path, write = app
    write(api_key="")
    called = []
    code = bench.main(["--corpus", small_corpus(tmp_path)], call=lambda *a: called.append(1))
    assert code == 2 and not called
    assert "key" in capsys.readouterr().err.lower()


def test_main_refuses_plain_http_to_a_public_host(app, capsys):
    tmp_path, write = app
    write(api_key="K", base_url="http://example.com/v1")
    called = []
    assert bench.main(["--corpus", small_corpus(tmp_path)], call=lambda *a: called.append(1)) == 2 and not called


def test_main_exit_code_is_1_when_every_row_failed(app):
    tmp_path, _ = app

    def boom(*a):
        raise core.ApiError(500, "API 500: down")
    assert bench.main(["--corpus", small_corpus(tmp_path), "--pause", "0", "--out", str(tmp_path / "o.json")], call=boom) == 1


# ------------------------------------------------------------------ token usage (vox_core.last_usage)

class UsageReply:
    status_code, text = 200, ""

    def __init__(self, usage):
        self.usage = usage

    def json(self):
        body = {"choices": [{"message": {"content": "Hello there."}, "finish_reason": "stop"}]}
        if self.usage is not None:
            body["usage"] = self.usage
        return body


def test_chat_reply_keeps_the_answers_cached_and_reasoning_tokens(monkeypatch):
    usage = {"prompt_tokens": 900, "completion_tokens": 40, "prompt_tokens_details": {"cached_tokens": 768},
             "completion_tokens_details": {"reasoning_tokens": 25}}
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: UsageReply(usage))
    core.cleanup(dict(core.DEFAULT_CONFIG, api_key="k"), "hello there", "neutral", "")
    assert core.last_usage() == {"prompt_tokens": 900, "completion_tokens": 40, "cached_tokens": 768, "reasoning_tokens": 25}


@pytest.mark.parametrize("usage", [None, "junk", {"prompt_tokens": "9", "prompt_tokens_details": None}])
def test_chat_reply_without_usable_usage_gives_none_counts(monkeypatch, usage):
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: UsageReply(usage))
    core.chat_reply(dict(core.DEFAULT_CONFIG, api_key="k"), {"model": "llama-3.3-70b-versatile", "messages": []})
    got = core.last_usage()
    assert got is None or set(got.values()) == {None}


# ------------------------------------------------------------------ variants, rate limits, the key

def test_run_variants_takes_turns_and_scores_every_answer():
    order = []
    rows = [{"id": str(i), "raw": "one two three four"} for i in range(3)]
    variants = {"A": lambda row: order.append("A") or "One two three four.", "B": lambda row: order.append("B") or "x"}
    out = bench.run_variants(rows, variants, lambda row, text: {"len": len(text)}, usage=lambda: {"prompt_tokens": 5})
    assert order == ["A", "B", "B", "A", "A", "B"]
    assert [r["id"] for r in out["A"]] == ["0", "1", "2"] and out["B"][0]["score"] == {"len": 1}
    assert out["A"][0]["usage"] == {"prompt_tokens": 5}


def test_run_variants_waits_out_a_rate_limit_and_does_not_time_the_wait():
    clock, sleeps, tries = Clock(0.1), [], []

    def flaky(row):
        tries.append(1)
        if len(tries) == 1:
            raise core.ApiError(429, "API 429: slow down", retry_after=7)
        return "Fine."
    out = bench.run_variants([{"id": "a", "raw": "fine"}], {"A": flaky}, lambda r, t: {}, sleep=sleeps.append, clock=clock,
                             usage=lambda: None)
    assert sleeps == [7] and len(tries) == 2
    assert out["A"][0]["error"] == "" and out["A"][0]["ms"] == 100


def test_run_variants_records_other_errors_and_gives_up_on_a_rate_limit_that_stays():
    sleeps = []

    def busy(row):
        raise core.ApiError(429, "API 429")
    out = bench.run_variants([{"id": "a", "raw": "x"}], {"A": busy}, lambda r, t: {}, sleep=sleeps.append, usage=lambda: None)
    assert out["A"][0]["error"] == "API 429" and out["A"][0]["score"] is None
    assert len(sleeps) == core.RATE_LIMIT_TRIES


def test_redact_masks_the_key_and_keeps_an_api_errors_status():
    e = bench.redact(core.ApiError(429, "bad key SECRET in url", retry_after=3), "SECRET")
    assert isinstance(e, core.ApiError) and e.code == 429 and e.retry_after == 3 and "SECRET" not in str(e)
    assert str(bench.redact(ValueError("SECRET"), "SECRET")) == "***"
    assert str(bench.redact(ValueError("plain"), "")) == "plain"


def test_a_key_in_an_error_text_never_reaches_the_output(app, capsys):
    tmp_path, _ = app
    out = tmp_path / "o.json"

    def leaky(cfg, raw, style, app_label):
        raise core.ApiError(500, "server said SECRET-KEY-123 is odd")
    bench.main(["--corpus", small_corpus(tmp_path), "--pause", "0", "--out", str(out)], call=leaky)
    shown = capsys.readouterr()
    assert "SECRET-KEY-123" not in shown.out + shown.err + out.read_text(encoding="utf-8")


# ------------------------------------------------------------------ --compare-prompt and --compare-guard

def test_compare_prompt_sends_v1_through_the_frozen_cleanup_and_the_rest_through_the_app(app, capsys):
    tmp_path, _ = app
    calls = []

    def current(cfg, raw, style, app_label):
        calls.append("current")
        return raw.capitalize() + "."

    def frozen(cfg, raw, style, app_label):
        calls.append("v1")
        return raw.capitalize() + "."
    out = tmp_path / "o.json"
    code = bench.main(["--corpus", small_corpus(tmp_path), "--pause", "0", "--out", str(out), "--compare-prompt", "v1,v3"],
                      call=current, legacy_call=frozen)
    assert code == 0 and calls == ["v1", "current", "current", "v1", "v1", "current"]
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert set(saved["models"]) == {"prompt v1", "prompt v3"}
    assert saved["models"]["prompt v1"]["prompt"] == "v1" and "compare" in saved
    shown = capsys.readouterr()
    assert "What gets pasted" in shown.out and "Against prompt v1" in shown.out


def test_a_note_says_when_the_apps_prompt_is_still_v1(monkeypatch):
    monkeypatch.setattr(core, "system_prompt", bench.legacy.system_prompt)
    assert bench._same_prompt()
    monkeypatch.setattr(core, "system_prompt", lambda *a: "another prompt")
    assert not bench._same_prompt()
    monkeypatch.setattr(core, "system_prompt", lambda style: "a changed signature")
    assert not bench._same_prompt()


def test_compare_guard_scores_each_answer_under_each_guard_without_more_requests(app, monkeypatch, capsys):
    tmp_path, _ = app
    monkeypatch.setattr(core, "looks_valid", lambda raw, cleaned, strength="light": False)   # a "v2" that rejects all
    calls = []
    out = tmp_path / "o.json"
    code = bench.main(["--corpus", small_corpus(tmp_path), "--pause", "0", "--out", str(out), "--compare-guard", "v1,v2"],
                      call=lambda *a: calls.append(1) or "Send me the report today please.")
    assert code == 0 and len(calls) == 3
    saved = json.loads(out.read_text(encoding="utf-8"))
    pasted = saved["models"][core.DEFAULT_LLM]["pasted"]
    assert pasted["v1"]["guard_pass"] == 1.0 and pasted["v2"]["guard_pass"] == 0.0
    assert set(saved["guard_set"]) == {"v1", "v2"} and saved["guard_set"]["v1"]["light"]["rows"] == 189
    shown = capsys.readouterr().out
    assert "Guards on the labelled pairs" in shown and "guard v2" in shown


def test_guard_only_needs_no_key_and_sends_nothing(app, capsys):
    tmp_path, write = app
    write(api_key="")
    out = tmp_path / "g.json"
    assert bench.main(["--guard-only", "--compare-guard", "v1,v2", "--out", str(out)], call=lambda *a: 1 / 0) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["guard_set"]["v1"]["standard"]["heldout"]["rows"] == 30 and saved["models"] == {}


def test_two_names_for_the_current_code_are_refused(app, capsys):
    tmp_path, _ = app
    assert bench.main(["--corpus", small_corpus(tmp_path), "--compare-prompt", "v2,v3"], call=echo) == 2
    assert "only v1" in capsys.readouterr().err


# ------------------------------------------------------------------ the clips as the source

def write_clips(folder, entries):
    import bench_clips as clips
    os.makedirs(folder, exist_ok=True)
    for cid, raw, intended, terms, when in entries:
        clips.append_row(folder, {"id": cid, "audio": cid + ".wav", "kind": "chat", "ref_verbatim": raw,
                                  "ref_intended": intended, "terms": terms})
        if when:
            clips.save_stt(folder, cid, {"groq m prompt-on": {"text": raw, "when": when, "ms": 300}})


def test_clip_rows_take_the_newest_transcript_and_the_whole_benchmark_dictionary(tmp_path):
    folder = str(tmp_path / "clips")
    write_clips(folder, [("clip-001", "meet priya on friday", "Meet Priya on Friday.", ["Priya"], "2026-10-05T10:00:00"),
                         ("clip-002", "ship ledgerly today", "Ship Ledgerly today.", ["Ledgerly"], "2026-10-05T11:00:00"),
                         ("clip-003", "not transcribed", "Not transcribed.", [], None)])
    rows, label = bench.clip_rows(folder)
    assert label == "groq m prompt-on" and [r["id"] for r in rows] == ["clip-001", "clip-002"]
    assert rows[0]["terms"] == ["Priya", "Ledgerly"] and rows[0]["must_keep_terms"] == ["Priya"]
    assert rows[0]["ref_intended"] == "Meet Priya on Friday." and rows[0]["about"] == "" and rows[0]["style"] == "neutral"
    assert bench.clip_rows(folder, "other label")[0] == []


def test_main_cleans_the_clips_when_they_are_transcribed(app, monkeypatch, capsys):
    tmp_path, _ = app
    monkeypatch.setattr(core, "looks_valid", lambda *a: True)   # whatever the app's guard is by then
    write_clips(os.path.join(str(tmp_path), "Vox", "bench", "clips"),
                [("clip-001", "meet priya on thursday no wait friday", "Meet Priya on Friday.", ["Priya"], "2026-10-05T10:00:00")])
    out = tmp_path / "o.json"
    code = bench.main(["--pause", "0", "--out", str(out), "--strength", "standard"], call=lambda *a: "Meet Priya on Friday.")
    assert code == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["stt"] == "groq m prompt-on" and "your clips" in saved["corpus"]
    p = saved["models"][core.DEFAULT_LLM]["pasted"]["current"]
    assert p["self_correction"] is not None and p["term_accuracy"] == 1.0 and p["fwer"] is not None


def test_main_without_clips_uses_the_text_corpus_and_the_clips_flag_says_what_to_do(app, capsys):
    tmp_path, _ = app
    out = tmp_path / "o.json"
    assert bench.main(["--pause", "0", "--out", str(out)], call=lambda cfg, raw, *a: raw) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["corpus"] == "corpus.jsonl"
    assert bench.main(["--clips"], call=echo) == 2
    assert "bench_record.py" in capsys.readouterr().err


def test_the_real_cleanup_calls_of_both_prompts_reach_a_fake_server_and_log_their_usage(app, monkeypatch, capsys):
    tmp_path, _ = app
    prompts = []

    def post(url, **kw):
        prompts.append(kw["json"]["messages"][0]["content"])
        n = len(prompts)
        return UsageReply({"prompt_tokens": 1000, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 900 * (n > 2)},
                           "completion_tokens_details": {"reasoning_tokens": 10}})
    monkeypatch.setattr(core.requests, "post", post)
    out = tmp_path / "o.json"
    code = bench.main(["--corpus", small_corpus(tmp_path), "--pause", "0", "--out", str(out), "--compare-prompt", "v1,current"])
    assert code == 0 and len(prompts) == 6
    assert prompts[0] == bench.legacy.system_prompt("neutral", ["Report"], "", "", "light", "", "auto")
    saved = json.loads(out.read_text(encoding="utf-8"))
    v1 = saved["models"]["prompt v1"]
    assert v1["rows"][0]["usage"] == {"prompt_tokens": 1000, "completion_tokens": 20, "cached_tokens": 0, "reasoning_tokens": 10}
    assert v1["summary"]["prompt_tokens"] == 1000 and v1["summary"]["cached_share"] == pytest.approx(0.6)
    assert "cached share" in capsys.readouterr().out


def test_a_note_says_when_the_apps_guard_is_still_v1(app, monkeypatch, capsys):
    tmp_path, _ = app
    monkeypatch.setattr(core, "looks_valid", bench.legacy.looks_valid)
    bench.main(["--guard-only", "--compare-guard", "v1,v2", "--out", str(tmp_path / "g.json")])
    assert "still gives the v1 verdicts" in capsys.readouterr().err
    monkeypatch.setattr(core, "looks_valid", lambda *a: True)
    bench.main(["--guard-only", "--compare-guard", "v1,v2", "--out", str(tmp_path / "g.json")])
    assert "still gives the v1 verdicts" not in capsys.readouterr().err
