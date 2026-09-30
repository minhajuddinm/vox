"""Tests for windows/providers.py: per-role settings, model discovery, Test button, reasoning retry."""
import providers
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
