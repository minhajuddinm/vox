"""Meeting notes, the meeting final pass and Improve my cleanup use the chosen provider's models (ARC-01 of the v2
review): Groq model names only on Groq, otherwise the role's own model, unless the feature's model is set. No network."""
import sys
import types

import pytest

import improve
import vox_core as core

OPENAI = {"base_url": "https://api.openai.com/v1", "stt_model": "whisper-1", "llm_model": "gpt-4o-mini", "api_key": "k"}


def cfg_of(**extra):
    return dict(core.DEFAULT_CONFIG, **extra)


@pytest.fixture
def meeting_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    try:
        import numpy  # noqa: F401
    except ImportError:   # CI's tests job has no numpy; meeting.py imports it at module level
        monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
        monkeypatch.delitem(sys.modules, "meeting", raising=False)
    import meeting
    return meeting


def test_the_feature_settings_exist_and_are_blank_by_default():
    assert core.DEFAULT_CONFIG["notes_model"] == "" and core.DEFAULT_CONFIG["final_stt_model"] == ""
    assert core.DEFAULT_CONFIG["improve_model"] == ""


@pytest.mark.parametrize("cfg, llm, stt", [
    (cfg_of(), "openai/gpt-oss-120b", "whisper-large-v3"),                                  # Groq: the big Groq models
    (cfg_of(base_url="https://api.groq.com/openai/v1/"), "openai/gpt-oss-120b", "whisper-large-v3"),
    (cfg_of(**OPENAI), "gpt-4o-mini", "whisper-1"),                                         # another provider: its own
    (cfg_of(llm_base_url="http://localhost:11434/v1", llm_model="llama3.1:8b"), "llama3.1:8b", "whisper-large-v3"),
    (cfg_of(relay_proxy=True, relay_url="https://pi.example.ts.net", relay_token="t", stt_model="whisper-1",
            llm_model="gpt-4o-mini"), "gpt-4o-mini", "whisper-1"),                          # through the relay: unknown
])
def test_the_models_follow_the_role_server(cfg, llm, stt, meeting_mod):
    assert meeting_mod.notes_model(cfg) == llm
    assert meeting_mod.final_stt_model(cfg) == stt
    assert improve.model_for(cfg) == llm
    assert improve.preview(cfg, [], 7, 0)["model"] == llm


def test_a_model_set_for_the_feature_wins(meeting_mod):
    cfg = cfg_of(notes_model="my-notes", final_stt_model="my-stt", improve_model="my-improve", **OPENAI)
    assert (meeting_mod.notes_model(cfg), meeting_mod.final_stt_model(cfg), improve.model_for(cfg)) == (
        "my-notes", "my-stt", "my-improve")


def test_the_old_saved_improve_default_counts_as_blank_off_groq():
    # every config.json saved before this fix holds the old default, which no other provider has
    assert improve.model_for(cfg_of(improve_model="openai/gpt-oss-120b", **OPENAI)) == "gpt-4o-mini"
    assert improve.model_for(cfg_of(improve_model="openai/gpt-oss-120b")) == "openai/gpt-oss-120b"


def test_meeting_notes_send_the_role_model(meeting_mod, monkeypatch):
    sent = []

    class Reply:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(core, "post_with_retry", lambda url, json=None, **kw: sent.append((url, json["model"])) or Reply())
    assert meeting_mod._llm(cfg_of(**OPENAI), "system", "user") == "ok"
    assert sent == [("https://api.openai.com/v1/chat/completions", "gpt-4o-mini")]
