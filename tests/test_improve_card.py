"""The "Improve my cleanup" card (task F2): what the card shows before anything is sent, the version list, the weekly
reminder rule and the one call to a server (windows/improve.py), plus the window bridge (windows/ui_app.py) and the tray
reminder (windows/engine.py). The pure rules of the run itself are in tests/test_improve.py. No real network."""
import json

import pytest

import improve
import vox_core as core

DAY = 86400
NOW = 1_000_000_000


def entry(t, raw="so we shipped the thing on friday", text="So we shipped the thing on Friday.", **extra):
    return {"t": t, "app": "chrome.exe", "raw": raw, "text": text, "words": len(text.split()), "secs": 3.0, **extra}


def cfg_of(**extra):
    return dict(core.DEFAULT_CONFIG, **extra)


def answer(**fields):
    return json.dumps(fields)


# ------------------------------------------------------------------ what is shown before anything is sent

def test_the_defaults_are_the_big_model_one_week_and_no_reminder():
    assert core.DEFAULT_CONFIG["improve_model"] == "openai/gpt-oss-120b" == improve.DEFAULT_MODEL
    assert core.DEFAULT_CONFIG["improve_days"] == 7 and core.DEFAULT_CONFIG["improve_remind"] is False


def test_preview_counts_what_would_be_sent_and_words_the_confirm_sentence():
    hist = [entry(NOW - 8 * DAY, raw="too old", text="Too old."), entry(NOW - DAY, raw="a" * 100, text="b" * 100),
            entry(NOW - 60, raw="c" * 50, text="d" * 50, private=True), entry(NOW - 30, raw="e" * 20, text="f" * 20)]
    p = improve.preview(cfg_of(), hist, 7, NOW)
    assert (p["count"], p["chars"], p["days"]) == (2, 240, 7)
    assert p["confirm"] == "This sends 2 transcripts (about 240 characters) to Groq (free tier)"
    assert p["model"] == "openai/gpt-oss-120b" and p["provider"] == "Groq (free tier)"
    assert p["tokens"] >= 240 // 4 and p["can_run"] is True


def test_preview_with_nothing_to_send_cannot_run_and_says_why():
    p = improve.preview(cfg_of(), [entry(NOW - 9 * DAY)], 7, NOW)
    assert p["count"] == 0 and p["can_run"] is False and p["tokens"] == 0
    assert "No saved dictations" in p["note"]
    assert "History is off" in improve.preview(cfg_of(keep_history=False), [], 7, NOW)["note"]
    assert improve.preview(cfg_of(), [entry(NOW - 5)], 7, NOW)["note"] == ""


def test_the_confirm_sentence_is_singular_for_one_and_groups_thousands():
    assert improve.confirm_text(1, 12, "My relay") == "This sends 1 transcript (about 12 characters) to My relay"
    assert improve.confirm_text(40, 12345, "X") == "This sends 40 transcripts (about 12,345 characters) to X"


def test_preview_says_how_much_else_goes_along():
    p = improve.preview(cfg_of(user_context="I lead Atlas.", dictionary=["Kubernetes"], my_cleanup_rules="Rule."), [entry(NOW - 5)], 7, NOW)
    assert p["extra"] == len("I lead Atlas.") + len("Kubernetes") + len("Rule.")
    assert improve.preview(cfg_of(), [entry(NOW - 5)], 7, NOW)["extra"] == 0


def test_the_date_range_is_one_of_the_offered_ones_and_zero_means_everything():
    hist = [entry(NOW - 400 * DAY), entry(NOW - 60 * DAY), entry(NOW - 3 * DAY)]
    assert [improve.preview(cfg_of(), hist, d, NOW)["count"] for d in (7, 30, 90, 0)] == [1, 1, 2, 3]
    for bad in (5, -1, "7", None, 1e9, True, False):
        assert improve.preview(cfg_of(), hist, bad, NOW)["days"] == 7


def test_preview_caps_the_amount_that_is_sent():
    big = [entry(NOW - i, raw="r" * 5000, text="c" * 5000) for i in range(1, 8)]   # 70,000 characters
    p = improve.preview(cfg_of(), big, 7, NOW)
    assert p["chars"] <= improve.MAX_SEND_CHARS and 0 < p["count"] < 7


def test_preview_counts_the_cleanups_that_lost_words():
    lost = entry(NOW - 9, raw="we met on monday and then we agreed the budget and then we all went home together", text="We met.")
    assert improve.preview(cfg_of(), [entry(NOW - 5), lost, entry(NOW - 4, fidelity_fallback=True)], 7, NOW)["lost"] == 2


def test_the_model_is_the_saved_one_or_the_default():
    assert improve.preview(cfg_of(improve_model="llama-3.3-70b"), [], 7, NOW)["model"] == "llama-3.3-70b"
    assert improve.preview(cfg_of(improve_model="  "), [], 7, NOW)["model"] == improve.DEFAULT_MODEL


def test_provider_label_names_where_the_run_goes():
    assert improve.provider_label(cfg_of()) == "Groq (free tier)"
    assert improve.provider_label(cfg_of(base_url="https://api.openai.com/v1")) == "OpenAI"
    assert improve.provider_label(cfg_of(base_url="https://llm.example.org/v1")) == "llm.example.org"
    assert improve.provider_label(cfg_of(llm_base_url="http://localhost:11434/v1")) == "Ollama (this PC)"
    assert improve.provider_label(cfg_of(relay_proxy=True, relay_url="https://pi.tail.ts.net", relay_token="t")) == "My relay"


# ------------------------------------------------------------------ versions and the weekly reminder

def applied(cfg, text, now):
    p = improve.parse_proposal(text)
    return improve.apply(p, [i["id"] for i in p.items], cfg, now=now)


def test_versions_view_lists_newest_first_with_what_each_change_added():
    cfg = applied(cfg_of(), answer(dictionary_add=["Atlas"], rules=["Write Atlas."]), 100)
    cfg = applied(cfg, answer(rules=["Keep Hindi words.", "Spell Ada right."]), 200)
    v = improve.versions_view(cfg)
    assert [x["index"] for x in v] == [1, 0] and [x["t"] for x in v] == [200, 100]
    assert v[0]["text"] == "2 rules" and v[1]["text"] == "1 dictionary line, 1 rule"


def test_versions_view_is_empty_without_versions_and_survives_junk():
    assert improve.versions_view(cfg_of()) == []
    assert improve.versions_view({"my_cleanup_rules_versions": ["x", None, {"t": "no"}]}) == []


def test_the_reminder_is_off_by_default_and_first_only_stamps_the_day_it_was_turned_on():
    assert improve.remind_action({}, NOW) == ""
    assert improve.remind_action({"improve_remind": False, "improve_remind_last": 1}, NOW) == ""
    assert improve.remind_action({"improve_remind": True}, NOW) == "stamp"      # nothing is said on the first day
    assert improve.remind_action({"improve_remind": True, "improve_remind_last": 0, "improve_last_run": 0}, NOW) == "stamp"


def test_the_reminder_comes_a_week_after_the_last_run_or_the_last_reminder():
    on = {"improve_remind": True}
    assert improve.remind_action(dict(on, improve_remind_last=NOW - 6 * DAY), NOW) == ""
    assert improve.remind_action(dict(on, improve_remind_last=NOW - 7 * DAY), NOW) == "remind"
    assert improve.remind_action(dict(on, improve_remind_last=NOW - 30 * DAY, improve_last_run=NOW - DAY), NOW) == ""   # ran yesterday
    assert improve.remind_action(dict(on, improve_remind_last="junk", improve_last_run=NOW - 9 * DAY), NOW) == "remind"


# ------------------------------------------------------------------ ask: the one call to a server

class Reply:
    def __init__(self, status=200, content="{}"):
        self.status_code, self._content, self.text = status, content, content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]} if self.status_code == 200 else {"error": {"message": "nope"}}


def test_ask_posts_to_the_cleanup_server_with_the_chosen_model_and_returns_the_text(monkeypatch):
    seen = []
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.append((url, kw)) or Reply(content='<think>x</think>{"rules": []}'))
    text = improve.ask(cfg_of(api_key="k"), [{"role": "user", "content": "hi"}], "openai/gpt-oss-120b")
    assert text == '{"rules": []}'
    url, kw = seen[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions" and kw["headers"] == {"Authorization": "Bearer k"}
    assert kw["json"]["model"] == "openai/gpt-oss-120b" and kw["json"]["messages"] == [{"role": "user", "content": "hi"}]
    assert kw["json"]["reasoning_effort"] == "low"      # a gpt-oss model, like the cleanup call
    assert kw["json"]["max_tokens"] >= 2000             # room for every list


def test_ask_goes_through_the_relay_when_it_is_the_ai_server(monkeypatch):
    seen = []
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.append((url, kw)) or Reply())
    improve.ask(cfg_of(relay_proxy=True, relay_url="https://pi.tail.ts.net", relay_token="tok"), [], "m")
    assert seen[0][0] == "https://pi.tail.ts.net/proxy/llm/chat/completions"
    assert seen[0][1]["headers"] == {"Authorization": "Bearer tok"}


def test_ask_raises_the_servers_error(monkeypatch):
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Reply(status=401))
    with pytest.raises(core.ApiError) as e:
        improve.ask(cfg_of(api_key="k"), [], "m")
    assert e.value.code == 401


def test_cleanup_still_sends_the_same_request(monkeypatch):
    """ask and cleanup share one chat call; the cleanup request keeps its fields."""
    seen = []
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.append(kw) or Reply(content="Hello there."))
    assert core.cleanup(cfg_of(api_key="k"), "hello there", "neutral", "") == "Hello there."
    body = seen[0]["json"]
    assert body["temperature"] == 0 and body["max_tokens"] == 1024 and body["reasoning_effort"] == "low"
    assert body["messages"][1] == {"role": "user", "content": "<transcript>\nhello there\n</transcript>"}


def test_a_server_that_refuses_the_reasoning_fields_gets_one_retry_without_them(monkeypatch):
    seen = []

    def post(url, **kw):
        seen.append(dict(kw["json"]))
        return Reply(status=400) if "reasoning_effort" in kw["json"] else Reply(content="ok")

    monkeypatch.setattr(core.requests, "post", post)
    monkeypatch.setattr(core.providers, "_rejected", set())
    assert improve.ask(cfg_of(api_key="k"), [], "openai/gpt-oss-120b") == "ok"
    assert "reasoning_effort" in seen[0] and "reasoning_effort" not in seen[1]


# ------------------------------------------------------------------ the window bridge

@pytest.fixture
def api(tmp_path, monkeypatch):
    ui_app = pytest.importorskip("ui_app")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return object.__new__(ui_app.Api)


@pytest.fixture
def no_network(monkeypatch):
    calls = []
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: calls.append(url) or Reply(content="{}"))
    monkeypatch.setattr(core.requests, "get", lambda url, **kw: calls.append(url) or Reply())
    return calls


def seed(now=None, n=3, **cfg):
    """A saved config with a key and n recent dictations in the history."""
    import time
    c = core.load_config()
    c.update(api_key="k", **cfg)
    core.save_config(c)
    for i in range(n):
        core.add_history(entry((now or time.time()) - 10 * (i + 1), raw="raw %d words here" % i, text="Raw %d words here." % i))


def test_the_state_call_sends_nothing(api, no_network):
    seed()
    s = api.improve_state()
    assert no_network == []
    assert s["count"] == 3 and s["can_run"] and s["confirm"].startswith("This sends 3 transcripts (about ")
    assert s["days"] == 7 and s["model"] == "openai/gpt-oss-120b" and s["remind"] is False and s["versions"] == []


def test_run_refuses_without_the_numbers_that_were_shown_and_sends_nothing(api, no_network):
    seed()
    s = api.improve_state()
    for args in ((7, "m", 0, 0), (7, "m", None, None), (7, "m", s["count"] + 1, s["chars"]), (7, "m", s["count"], s["chars"] + 1)):
        r = api.improve_run(*args)
        assert r["ok"] is False and r["error"], args
    assert no_network == []
    assert api.improve_run(7, "m", s["count"] + 1, s["chars"]).get("stale") is True   # the page then asks for the numbers again


def test_run_refuses_when_there_is_nothing_to_send_or_the_key_is_missing(api, no_network):
    seed(n=0)
    assert api.improve_run(7, "m", 0, 0)["ok"] is False
    core.save_config(dict(core.load_config(), api_key=""))
    core.add_history(entry(__import__("time").time() - 5))
    s = api.improve_state()
    r = api.improve_run(7, "m", s["count"], s["chars"])
    assert r["ok"] is False and "key" in r["error"].lower()
    assert no_network == []


def test_run_sends_one_request_after_the_confirmed_numbers_and_returns_the_proposal(api, monkeypatch):
    seed()
    sent = []
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: sent.append(kw["json"]) or Reply(content=answer(
        dictionary_add=["Atlas"], rules=["Write Atlas."], about_suggestions=["+ I lead Atlas."], fidelity_findings=["t0 lost words"])))
    s = api.improve_state()
    r = api.improve_run(7, "llama-3.3-70b", s["count"], s["chars"])
    assert len(sent) == 1 and sent[0]["model"] == "llama-3.3-70b"
    assert "raw 0 words here" in sent[0]["messages"][1]["content"]
    assert r["ok"] is True and [i["kind"] for i in r["items"]] == ["dictionary", "rule", "about"]
    assert r["findings"] == [{"id": "", "note": "t0 lost words"}]
    cfg = core.load_config()
    assert cfg["improve_model"] == "llama-3.3-70b" and cfg["improve_days"] == 7 and cfg["improve_last_run"] > 0
    assert cfg["my_cleanup_rules"] == "" and cfg["dictionary"] == []      # nothing is applied by a run


def test_run_reports_a_server_error_in_plain_words_and_keeps_the_last_run_unset(api, monkeypatch):
    seed()
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Reply(status=429))
    s = api.improve_state()
    r = api.improve_run(7, "m", s["count"], s["chars"])
    assert r["ok"] is False and "Rate limit" in r["error"]
    assert core.load_config().get("improve_last_run", 0) == 0


def test_run_reports_a_network_failure(api, monkeypatch):
    seed()

    def boom(url, **kw):
        raise core.requests.ConnectionError("down")

    monkeypatch.setattr(core.requests, "post", boom)
    s = api.improve_state()
    r = api.improve_run(7, "m", s["count"], s["chars"])
    assert r["ok"] is False and "ConnectionError" in r["error"]


def test_an_answer_that_is_not_json_is_an_error_not_an_empty_proposal(api, monkeypatch):
    seed()
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Reply(content="Sorry, I cannot do that."))
    s = api.improve_state()
    r = api.improve_run(7, "m", s["count"], s["chars"])
    assert r["ok"] is True and r["items"] == [] and r["error"]


def run_with(api, monkeypatch, text):
    seed()
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Reply(content=text))
    s = api.improve_state()
    return api.improve_run(7, "m", s["count"], s["chars"])


def test_apply_writes_only_the_accepted_items_and_never_the_about_text(api, monkeypatch):
    r = run_with(api, monkeypatch, answer(dictionary_add=["Atlas", "Ada"], rules=["Write Atlas."], about_suggestions=["+ I lead Atlas."]))
    out = api.improve_apply(["dictionary:1", "rule:0", "about:0", "nonsense"])
    cfg = core.load_config()
    assert out["ok"] is True and out["applied"] == 2
    assert cfg["dictionary"] == ["Ada"] and cfg["my_cleanup_rules"] == "Write Atlas." and cfg["user_context"] == ""
    assert [v["text"] for v in out["versions"]] == ["1 dictionary line, 1 rule"]


def test_apply_with_nothing_accepted_changes_nothing_and_a_page_cannot_invent_items(api, monkeypatch):
    run_with(api, monkeypatch, answer(rules=["Write Atlas."]))
    assert api.improve_apply([])["applied"] == 0
    assert api.improve_apply(["rule:7", "rule:0 "])["applied"] == 0
    assert core.load_config()["my_cleanup_rules"] == ""


def test_apply_without_a_proposal_says_so(api):
    out = api.improve_apply(["rule:0"])
    assert out["ok"] is False and out["error"]


def test_a_proposal_can_be_applied_once(api, monkeypatch):
    run_with(api, monkeypatch, answer(rules=["Write Atlas."]))
    assert api.improve_apply(["rule:0"])["applied"] == 1
    assert api.improve_apply(["rule:0"])["ok"] is False
    assert core.load_config()["my_cleanup_rules"] == "Write Atlas."


def test_revert_puts_the_rules_and_the_dictionary_back(api, monkeypatch):
    run_with(api, monkeypatch, answer(dictionary_add=["Atlas"], rules=["Write Atlas."]))
    api.improve_apply(["dictionary:0", "rule:0"])
    out = api.improve_revert(0)
    cfg = core.load_config()
    assert out["ok"] is True and out["versions"] == []
    assert cfg["dictionary"] == [] and cfg["my_cleanup_rules"] == "" and cfg["my_cleanup_rules_versions"] == []


def test_revert_of_an_unknown_version_changes_nothing(api):
    out = api.improve_revert(5)
    assert out["ok"] is False


def test_the_state_lists_the_versions_after_an_apply(api, monkeypatch):
    run_with(api, monkeypatch, answer(rules=["Write Atlas."]))
    api.improve_apply(["rule:0"])
    assert [v["text"] for v in api.improve_state()["versions"]] == ["1 rule"]


# ------------------------------------------------------------------ the tray reminder

class Tray:
    """Just the Engine method under test, with its notify and config."""
    def __init__(self, cfg):
        self.cfg, self.said = cfg, []

    def notify(self, msg):
        self.said.append(msg)


def tray_engine(cfg):
    engine = pytest.importorskip("engine")
    t = Tray(cfg)
    t.check_improve_reminder = engine.Engine.check_improve_reminder.__get__(t)
    return t


def test_the_engine_reminds_once_a_week_with_a_tray_message_and_never_runs_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    saved = dict(core.DEFAULT_CONFIG, improve_remind=True, improve_remind_last=NOW - 8 * DAY)
    core.save_config(saved)
    eng = tray_engine(dict(saved))
    monkeypatch.setattr(core.requests, "post", lambda *a, **k: pytest.fail("a reminder must not send anything"))
    eng.check_improve_reminder(NOW)
    assert len(eng.said) == 1 and "Improve my cleanup" in eng.said[0] and "Nothing is sent" in eng.said[0]
    assert core.load_config()["improve_remind_last"] == NOW
    eng.cfg = core.load_config()
    eng.check_improve_reminder(NOW + DAY)
    assert len(eng.said) == 1                                  # not again the next day


def test_turning_the_reminder_on_only_stamps_the_day(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    core.save_config(dict(core.DEFAULT_CONFIG, improve_remind=True))
    eng = tray_engine(dict(core.DEFAULT_CONFIG, improve_remind=True))
    eng.check_improve_reminder(NOW)
    assert eng.said == [] and core.load_config()["improve_remind_last"] == NOW


def test_the_engine_says_nothing_with_the_reminder_off(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    eng = tray_engine(dict(core.DEFAULT_CONFIG))
    eng.check_improve_reminder(NOW)
    assert eng.said == [] and core.load_config().get("improve_remind_last", 0) == 0
