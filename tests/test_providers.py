"""Tests for windows/providers.py: per-role settings, model discovery, Test button, reasoning retry."""
import os
import sys
import types
from html.parser import HTMLParser

import pytest

import providers
import secret
import vox_core as core


class Resp:
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


# ------------------------------------------------------------ role settings

def test_defaults_are_groq_with_default_models():
    assert providers.role_settings({}, "stt") == (core.BASE, "", "whisper-large-v3-turbo")
    assert providers.role_settings({}, "llm") == (core.BASE, "", "openai/gpt-oss-20b")


def test_roles_inherit_the_main_server_and_key():
    cfg = {"base_url": "https://api.openai.com/v1/", "api_key": " k ", "stt_model": "whisper-1", "llm_model": "gpt-4o-mini"}
    assert providers.role_settings(cfg, "stt") == ("https://api.openai.com/v1", "k", "whisper-1")
    assert providers.role_settings(cfg, "llm") == ("https://api.openai.com/v1", "k", "gpt-4o-mini")


def test_own_address_never_gets_the_main_key():
    cfg = {"base_url": core.BASE, "api_key": "main", "llm_base_url": "http://localhost:11434/v1"}
    assert providers.role_settings(cfg, "llm")[:2] == ("http://localhost:11434/v1", "")
    assert providers.role_settings(cfg, "stt")[:2] == (core.BASE, "main")
    cfg["llm_api_key"] = "own"
    assert providers.role_settings(cfg, "llm")[1] == "own"


def test_same_address_override_still_inherits_the_key():
    cfg = {"base_url": core.BASE, "api_key": "main", "stt_base_url": core.BASE + "/"}
    assert providers.role_settings(cfg, "stt")[:2] == (core.BASE, "main")


def test_core_helpers_follow_the_role():
    cfg = {"base_url": core.BASE, "api_key": "main", "llm_base_url": "http://localhost:11434/v1"}
    assert core.api_base(cfg, "llm") == "http://localhost:11434/v1"
    assert core.api_base(cfg) == core.BASE
    assert core.auth_headers(cfg, "llm") == {}
    assert core.auth_headers(cfg, "stt") == {"Authorization": "Bearer main"}


def test_key_missing_only_for_servers_outside_the_private_network():
    assert providers.key_missing({})
    assert providers.key_missing({"base_url": "https://api.openai.com/v1"})
    assert not providers.key_missing({"base_url": "https://api.openai.com/v1", "api_key": "k"})
    assert not providers.key_missing({"base_url": "http://localhost:8000/v1"})
    # voice on a local server, cleanup on Groq without a key: the cleanup role is missing its key
    assert providers.key_missing({"base_url": "http://localhost:8000/v1", "llm_base_url": core.BASE})


def test_endpoint_error_checks_role_addresses():
    assert core.endpoint_error({"llm_base_url": "http://cloud.example.com/v1"})
    assert core.endpoint_error({"stt_base_url": "ftp://x"})
    assert core.endpoint_error({"llm_base_url": "http://localhost:11434/v1"}) == ""


# ------------------------------------------------------------ classify / parse

def test_classify_uses_explicit_fields_first():
    assert providers.classify({"id": "x", "task": "automatic-speech-recognition"}) == "stt"
    assert providers.classify({"id": "x", "type": "transcribe"}) == "stt"
    assert providers.classify({"id": "x", "architecture": {"output_modalities": ["transcription"]}}) == "stt"
    assert providers.classify({"id": "x", "architecture": {"output_modalities": ["text"]}}) == "llm"
    assert providers.classify({"id": "x", "architecture": {"output_modalities": ["image"]}}) == "hidden"
    assert providers.classify({"id": "whisper-large-v3", "active": False}) == "hidden"


def test_parse_models_handles_openai_ollama_and_plain_lists():
    openai = {"data": [{"id": "whisper-large-v3"}, {"id": "openai/gpt-oss-20b"}, {"id": "playai-tts"},
                       {"id": "old", "active": False}, {"id": "whisper-large-v3"}]}
    assert providers.parse_models(openai) == [{"id": "openai/gpt-oss-20b", "kind": "llm"},
                                              {"id": "whisper-large-v3", "kind": "stt"}]
    assert providers.parse_models({"models": [{"name": "qwen2.5:3b"}]}) == [{"id": "qwen2.5:3b", "kind": "llm"}]
    assert providers.parse_models(["b", "a"]) == [{"id": "a", "kind": "llm"}, {"id": "b", "kind": "llm"}]
    assert providers.parse_models(None) == []


# ------------------------------------------------------------ list_models

def test_list_models_returns_only_the_role_and_sends_the_key(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen.update(url=url, headers=kw["headers"])
        return Resp({"data": [{"id": "whisper-large-v3-turbo"}, {"id": "openai/gpt-oss-20b"}]})

    monkeypatch.setattr(core.requests, "get", fake_get)
    res = providers.list_models({"api_key": "k"}, "stt")
    assert res == {"models": [{"id": "whisper-large-v3-turbo", "kind": "stt"}], "error": ""}
    assert seen["url"] == core.BASE + "/models" and seen["headers"] == {"Authorization": "Bearer k"}
    assert providers.list_models({"api_key": "k"}, "llm")["models"] == [{"id": "openai/gpt-oss-20b", "kind": "llm"}]


def test_list_models_explains_failures(monkeypatch):
    monkeypatch.setattr(core.requests, "get", lambda url, **kw: Resp(status=401))
    assert "refused the key" in providers.list_models({"api_key": "bad"}, "llm")["error"]

    def boom(url, **kw):
        raise core.requests.ConnectionError("down")

    monkeypatch.setattr(core.requests, "get", boom)
    assert "Could not reach" in providers.list_models({"api_key": "k"}, "llm")["error"]
    assert "http" in providers.list_models({"base_url": "http://cloud.example.com/v1"}, "llm")["error"]


def test_list_models_falls_back_to_ollama_tags(monkeypatch):
    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        return Resp(status=404) if url.endswith("/v1/models") else Resp({"models": [{"name": "llama3.2:3b"}]})

    monkeypatch.setattr(core.requests, "get", fake_get)
    res = providers.list_models({"base_url": "http://localhost:11434/v1"}, "llm")
    assert urls == ["http://localhost:11434/v1/models", "http://localhost:11434/api/tags"]
    assert res["models"] == [{"id": "llama3.2:3b", "kind": "llm"}]


def test_empty_list_tells_the_user_to_type_the_model(monkeypatch):
    monkeypatch.setattr(core.requests, "get", lambda url, **kw: Resp({"data": []}))
    assert "Type the model" in providers.list_models({"api_key": "k"}, "stt")["error"]


# ------------------------------------------------------------ Test button

def test_test_stt_uploads_a_silent_wav_and_reports_success(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return Resp({"text": ""})

    monkeypatch.setattr(core.requests, "post", fake_post)
    res = providers.test({"api_key": "k"}, "stt")
    assert res["ok"] and res["status"] == 200
    url, kw = calls[0]
    assert url == core.BASE + "/audio/transcriptions" and kw["data"]["model"] == "whisper-large-v3-turbo"
    assert kw["files"]["file"][1][:4] == b"RIFF"


def test_test_llm_uses_a_tiny_chat_call(monkeypatch):
    calls = []
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: calls.append((url, kw)) or Resp({}))
    assert providers.test({"api_key": "k"}, "llm")["ok"]
    assert calls[0][0] == core.BASE + "/chat/completions" and calls[0][1]["json"]["max_tokens"] == 8


def test_test_explains_404_for_a_server_without_stt(monkeypatch):
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Resp(status=404))
    res = providers.test({"base_url": "http://localhost:11434/v1"}, "stt")
    assert not res["ok"] and "cannot do speech-to-text" in res["message"]


def test_test_needs_a_key_for_public_servers_and_makes_no_call(monkeypatch):
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: (_ for _ in ()).throw(AssertionError("no call")))
    res = providers.test({}, "llm")
    assert not res["ok"] and "API key" in res["message"]


# ------------------------------------------------------------ reasoning parameters

def test_reasoning_only_for_gpt_oss_and_can_be_switched_off():
    assert providers.reasoning_params({}, "b1", "openai/gpt-oss-20b") == {"reasoning_effort": "low", "include_reasoning": False}
    assert providers.reasoning_params({}, "b1", "llama3.2:3b") == {}
    assert providers.reasoning_params({"llm_reasoning": "off"}, "b1", "openai/gpt-oss-20b") == {}


def test_cleanup_retries_once_without_reasoning_fields_and_remembers(monkeypatch):
    providers._rejected.clear()
    bodies = []

    def fake_post(url, **kw):
        bodies.append(dict(kw["json"]))
        if "reasoning_effort" in kw["json"]:
            return Resp({"error": {"message": "unknown field"}}, status=400)
        return Resp({"choices": [{"message": {"content": "Hello there."}}]})

    monkeypatch.setattr(core.requests, "post", fake_post)
    cfg = {"api_key": "k"}
    assert core.cleanup(cfg, "hello there", "neutral", "") == "Hello there."
    assert "reasoning_effort" in bodies[0] and "reasoning_effort" not in bodies[1]
    bodies.clear()
    core.cleanup(cfg, "hello there", "neutral", "")
    assert len(bodies) == 1 and "reasoning_effort" not in bodies[0]
    providers._rejected.clear()


def test_strip_think_removes_a_leading_block_only():
    assert providers.strip_think("<think>hmm\nlong</think>\nHi.") == "Hi."
    assert providers.strip_think("Hi <think>x</think> there") == "Hi <think>x</think> there"


# ------------------------------------------------------------ the relay as the AI server

RELAY = {"relay_proxy": True, "relay_url": "https://yuvipi.tail1234.ts.net", "relay_token": "RELAY-TOKEN"}
PROVIDER_KEYS = {"api_key": "MAIN-PROVIDER-KEY", "stt_api_key": "STT-PROVIDER-KEY", "llm_api_key": "LLM-PROVIDER-KEY"}
RELAY_HINT = "check the relay token and the AI server key set on the relay page"
# the requests the relay forwards, taken from the relay itself (relay/relay.py PROXY_ROUTES): nothing else may be asked of it
import relay  # noqa: E402

RELAY_ROUTES = {RELAY["relay_url"] + path for (_, path) in relay.PROXY_ROUTES}


def test_proxy_on_sends_each_role_to_the_relay_with_the_relay_token():
    cfg = dict(RELAY, llm_model="gpt-4o-mini")
    assert providers.role_settings(cfg, "stt") == (RELAY["relay_url"] + "/proxy/stt", "RELAY-TOKEN", "whisper-large-v3-turbo")
    assert providers.role_settings(cfg, "llm") == (RELAY["relay_url"] + "/proxy/llm", "RELAY-TOKEN", "gpt-4o-mini")


def test_proxy_off_changes_nothing_even_with_a_relay_filled_in():
    cfg = dict(RELAY, relay_proxy=False, api_key="main", llm_model="gpt-4o-mini")
    assert providers.role_settings(cfg, "stt") == (core.BASE, "main", "whisper-large-v3-turbo")
    assert providers.role_settings(cfg, "llm") == (core.BASE, "main", "gpt-4o-mini")
    assert providers.proxy_problem(cfg) == ""
    assert not providers.uses_relay(cfg)


@pytest.mark.parametrize("missing", [{"relay_url": ""}, {"relay_token": ""}, {"relay_url": "  ", "relay_token": " "}, {"relay_token": None}])
def test_proxy_on_without_a_relay_falls_back_and_reports_the_problem(missing):
    cfg = dict(RELAY, api_key="main", **missing)
    assert providers.role_settings(cfg, "stt") == (core.BASE, "main", "whisper-large-v3-turbo")
    assert providers.proxy_problem(cfg) == "Turn on the relay first"
    assert not providers.uses_relay(cfg)


def test_a_working_relay_has_no_problem():
    assert providers.proxy_problem(RELAY) == ""
    assert providers.uses_relay(RELAY)


def test_relay_address_and_token_are_stripped():
    cfg = dict(RELAY, relay_url="  https://r.example.ts.net///  ", relay_token="  tok\t")
    assert providers.role_settings(cfg, "llm")[:2] == ("https://r.example.ts.net/proxy/llm", "tok")


def test_proxy_ignores_the_per_role_servers_and_provider_keys():
    cfg = dict(RELAY, **PROVIDER_KEYS, stt_base_url="https://stt.example.com/v1", llm_base_url="http://localhost:11434/v1")
    assert providers.role_settings(cfg, "stt")[:2] == (RELAY["relay_url"] + "/proxy/stt", "RELAY-TOKEN")
    assert providers.role_settings(cfg, "llm")[:2] == (RELAY["relay_url"] + "/proxy/llm", "RELAY-TOKEN")


def test_core_helpers_follow_the_relay():
    assert core.api_base(RELAY, "llm") == RELAY["relay_url"] + "/proxy/llm"
    assert core.auth_headers(RELAY, "stt") == {"Authorization": "Bearer RELAY-TOKEN"}
    assert not core.key_missing(RELAY)   # the relay token is the key


def test_relay_proxy_is_off_by_default():
    assert core.DEFAULT_CONFIG["relay_proxy"] is False


def test_the_saved_relay_token_is_read_back_in_plain_text(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(secret, "_backend", (lambda b: b[::-1], lambda b: b[::-1]))   # a reversible stand-in for DPAPI
    core.save_config(dict(core.DEFAULT_CONFIG, **RELAY))
    assert "RELAY-TOKEN" not in (tmp_path / "Vox" / "config.json").read_text(encoding="utf-8")
    assert providers.role_settings(core.load_config(), "stt")[:2] == (RELAY["relay_url"] + "/proxy/stt", "RELAY-TOKEN")


def test_endpoint_error_checks_the_relay_address_when_the_proxy_is_on():
    plain_public = dict(RELAY, relay_url="http://relay.example.com")
    assert "http" in core.endpoint_error(plain_public)   # the token must not travel in the clear
    assert core.endpoint_error(dict(RELAY, relay_url="http://100.64.1.2:8765")) == ""
    assert core.endpoint_error(dict(RELAY, base_url="ftp://unused")) == ""   # the provider address is not used now
    assert core.endpoint_error(dict(plain_public, relay_proxy=False)) == ""   # and the relay address is not used when off
    assert core.endpoint_error({"relay_proxy": True, "base_url": "ftp://x"})   # no relay filled in: the normal check runs


def record_traffic(monkeypatch):
    """Every request Vox makes, as (url, headers, all other arguments as text), whichever way it is sent."""
    sent = []

    def fake(url, **kw):
        sent.append((url, dict(kw.get("headers") or {}), repr({k: v for k, v in kw.items() if k != "headers"})))
        if url.endswith("/chat/completions"):
            return Resp({"choices": [{"message": {"content": "Hello there."}}]})
        if url.endswith("/models"):
            return Resp({"data": [{"id": "whisper-large-v3-turbo"}, {"id": "openai/gpt-oss-20b"}]})
        return Resp({"text": "hello there"})

    monkeypatch.setattr(core.requests, "get", fake)
    monkeypatch.setattr(core.requests, "post", fake)
    monkeypatch.setattr(core._session, "get", fake)
    return sent


def use_every_server_call(cfg):
    """One of everything Vox sends to an AI server: model lists, both Test buttons, warm-up, a dictation."""
    for role in providers.ROLES:
        assert providers.list_models(cfg, role)["models"]
        assert providers.test(cfg, role)["ok"]
    core.warm(cfg).join(5)
    assert core.transcribe(cfg, b"RIFF")
    assert core.cleanup(cfg, "hello there", "neutral", "")


def test_with_the_proxy_on_only_the_relay_is_called_and_no_provider_key_leaves(monkeypatch):
    sent = record_traffic(monkeypatch)
    cfg = dict(RELAY, **PROVIDER_KEYS, stt_base_url="https://stt.example.com/v1", llm_base_url="http://localhost:11434/v1")
    use_every_server_call(cfg)
    assert {url for url, _, _ in sent} == RELAY_ROUTES
    for url, headers, rest in sent:
        assert headers == {"Authorization": "Bearer RELAY-TOKEN"}, url
        assert not any(key in url + str(headers) + rest for key in PROVIDER_KEYS.values()), url


def test_with_the_proxy_off_the_relay_token_is_never_sent_to_a_provider(monkeypatch):
    sent = record_traffic(monkeypatch)
    cfg = dict(RELAY, relay_proxy=False, api_key="MAIN-PROVIDER-KEY", llm_base_url="http://localhost:11434/v1", llm_api_key="LLM-PROVIDER-KEY")
    use_every_server_call(cfg)
    assert sent
    for url, headers, rest in sent:
        assert "/proxy/" not in url and "yuvipi" not in url, url
        assert "RELAY-TOKEN" not in url + str(headers) + rest, url
    assert {h.get("Authorization") for u, h, _ in sent if u.startswith(core.BASE)} == {"Bearer MAIN-PROVIDER-KEY"}
    assert {h.get("Authorization") for u, h, _ in sent if u.startswith("http://localhost")} == {"Bearer LLM-PROVIDER-KEY"}


def test_with_the_proxy_on_but_no_relay_the_provider_key_still_goes_only_to_its_own_server(monkeypatch):
    sent = record_traffic(monkeypatch)
    use_every_server_call(dict(RELAY, relay_token="", api_key="MAIN-PROVIDER-KEY"))
    assert {u.split("/openai/v1")[0] for u, _, _ in sent} == {"https://api.groq.com"}
    assert not any("RELAY" in str(h) for _, h, _ in sent)


def test_a_refused_request_through_the_relay_says_where_to_look(monkeypatch):
    monkeypatch.setattr(core.requests, "get", lambda url, **kw: Resp(status=401))
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Resp(status=403))
    assert RELAY_HINT in providers.list_models(RELAY, "llm")["error"]
    assert RELAY_HINT in providers.test(RELAY, "stt")["message"]
    assert RELAY_HINT in providers.test(RELAY, "llm")["message"]
    # the same refusals from a provider keep their own wording
    assert "relay" not in providers.list_models({"api_key": "k"}, "llm")["error"]
    assert "relay" not in providers.test({"api_key": "k"}, "llm")["message"]


def test_a_refused_dictation_through_the_relay_carries_the_hint(monkeypatch):
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Resp({"error": "unauthorised"}, status=401))
    with pytest.raises(core.ApiError) as ei:
        core.transcribe(RELAY, b"RIFF")
    assert ei.value.code == 401 and RELAY_HINT in str(ei.value)
    with pytest.raises(core.ApiError) as ei:
        core.cleanup(RELAY, "hello there", "neutral", "")
    assert ei.value.code == 401 and RELAY_HINT in str(ei.value)
    with pytest.raises(core.ApiError) as ei:   # not through the relay: no hint
        core.transcribe({"api_key": "k"}, b"RIFF")
    assert "relay" not in str(ei.value)


# what the relay answers when it cannot serve a request: plain text errors, except 502 which is OpenAI-shaped
RELAY_ERRORS = [
    (411, {"error": "Content-Length is required"}, "Content-Length is required", "Content-Length is required"),
    (413, {"error": "request too large"}, "request too large", "request too large"),
    (429, {"error": "busy"}, "busy", "Rate limit"),
    (503, {"error": "The speech to text server is not configured on the relay. Set its address on the relay's management page (AI server tab)."},
     "is not configured on the relay", "is not configured on the relay"),
    (502, {"error": {"message": "the AI server could not be reached"}}, "the AI server could not be reached",
     "the AI server could not be reached"),
]


@pytest.mark.parametrize("status,body,text,on_the_test_button", RELAY_ERRORS)
def test_relay_errors_reach_the_user_as_plain_text(monkeypatch, status, body, text, on_the_test_button):
    monkeypatch.setattr(core.time, "sleep", lambda s: None)   # 502 and 503 are retried after a pause
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Resp(body, status=status))
    with pytest.raises(core.ApiError) as ei:
        core.transcribe(RELAY, b"RIFF")
    assert ei.value.code == status and text in str(ei.value) and "{" not in str(ei.value)
    message = providers.test(RELAY, "stt")["message"]
    assert on_the_test_button in message and "{" not in message


# ------------------------------------------------------------ the Settings page

class _OwnServerFields(HTMLParser):
    """Ids of the inputs and selects of windows/ui/index.html, split by whether an element with class `own-server` holds them."""
    VOID = {"input", "br", "img", "meta", "link", "hr"}

    def __init__(self):
        super().__init__()
        self.stack, self.inside, self.outside = [], set(), set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("input", "select") and a.get("id"):
            (self.inside if any(own for _, own in self.stack) else self.outside).add(a["id"])
        if tag not in self.VOID:
            self.stack.append((tag, "own-server" in (a.get("class") or "").split()))

    def handle_endtag(self, tag):
        while self.stack and self.stack.pop()[0] != tag:
            pass


def test_settings_page_hides_the_provider_fields_while_the_relay_is_the_ai_server():
    path = os.path.join(os.path.dirname(__file__), "..", "windows", "ui", "index.html")
    with open(path, encoding="utf-8") as f:
        page = f.read()
    parsed = _OwnServerFields()
    parsed.feed(page)
    # every provider address and key field is in a row the `proxy-on` class hides; the switch and the model fields are not
    assert {"provider", "base-url", "key", "stt-url", "stt-key", "llm-url", "llm-key", "split"} <= parsed.inside
    assert {"relay-proxy", "stt", "llm"} <= parsed.outside
    assert ".proxy-on .own-server { display: none !important; }" in page
    assert 'classList.toggle("proxy-on"' in page


def test_a_404_through_the_relay_blames_the_relay_setup_not_the_server(monkeypatch):
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: Resp(status=404))
    msg = providers.test(RELAY, "stt")["message"]
    assert "relay" in msg and "cannot do speech-to-text" not in msg
    assert "relay page" in providers.explain(404, "llm", via_relay=True)
    assert "cannot do speech-to-text" in providers.explain(404, "stt")   # without the relay the old wording stays


def test_the_meeting_notes_call_through_the_relay_carries_the_hint(monkeypatch):
    # meeting.py imports numpy at module level but _llm never uses it. CI's tests job has no numpy, so stub it for this import only.
    stubbed = False
    try:
        import numpy  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
        stubbed = True
    try:
        import meeting
        monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: Resp({"error": "unauthorised"}, status=401))
        with pytest.raises(core.ApiError) as ei:
            meeting._llm(RELAY, "system", "user")
        assert RELAY_HINT in str(ei.value)
    finally:
        if stubbed:
            sys.modules.pop("meeting", None)   # never leave a stub-bound meeting module for other tests


def test_proxy_problem_also_reports_an_unusable_relay_address():
    plain_public = dict(RELAY, relay_url="http://relay.example.com")
    assert "http" in providers.proxy_problem(plain_public)
    assert providers.proxy_problem(dict(RELAY, relay_url="http://100.64.1.2:8765")) == ""
    assert providers.proxy_problem(dict(plain_public, relay_proxy=False)) == ""


def test_the_api_proxy_problem_wrapper_reads_the_saved_config(tmp_path, monkeypatch):
    ui_app = pytest.importorskip("ui_app")
    cfg = {}
    monkeypatch.setattr(core, "load_config", lambda: cfg)
    api = object.__new__(ui_app.Api)
    assert api.proxy_problem() == ""
    cfg.update(RELAY)
    assert api.proxy_problem() == ""
    cfg["relay_url"] = "http://relay.example.com"
    assert "http" in api.proxy_problem()
    cfg.update(relay_url="", relay_token="")
    assert api.proxy_problem() == "Turn on the relay first"
