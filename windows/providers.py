"""Providers: which server, key and model each role uses, model discovery and connection tests.

Two roles: "stt" (speech to text) and "llm" (cleanup). Both talk to an OpenAI-compatible API. The main
`base_url`/`api_key` serve both; `stt_*` and `llm_*` settings override them for one role (for example Whisper on
one server and Ollama on another). Design: documentation/specs/p1-providers-and-models.md.
"""
import io
import re
import time
import wave

GROQ_BASE = "https://api.groq.com/openai/v1"
DEFAULT_MODELS = {"stt": "whisper-large-v3-turbo", "llm": "openai/gpt-oss-20b"}
ROLES = ("stt", "llm")

# id = stored in the `provider` setting (a UI convenience only: the address decides behaviour).
PRESETS = [
    {"id": "groq", "name": "Groq (free tier)", "base_url": GROQ_BASE, "key_url": "https://console.groq.com/keys",
     "stt_model": "whisper-large-v3-turbo", "llm_model": "openai/gpt-oss-20b"},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1", "key_url": "https://platform.openai.com/api-keys",
     "stt_model": "whisper-1", "llm_model": "gpt-4o-mini"},
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "key_url": "https://openrouter.ai/keys"},
    {"id": "together", "name": "Together AI", "base_url": "https://api.together.xyz/v1", "key_url": "https://api.together.ai/settings/api-keys"},
    {"id": "mistral", "name": "Mistral", "base_url": "https://api.mistral.ai/v1", "key_url": "https://console.mistral.ai/api-keys",
     "stt_model": "voxtral-mini-latest", "llm_model": "mistral-small-latest"},
    {"id": "ollama", "name": "Ollama (this PC)", "base_url": "http://localhost:11434/v1", "key_url": ""},
    {"id": "lmstudio", "name": "LM Studio (this PC)", "base_url": "http://localhost:1234/v1", "key_url": ""},
    {"id": "speaches", "name": "Speaches / faster-whisper server", "base_url": "http://localhost:8000/v1", "key_url": ""},
    {"id": "whispercpp", "name": "whisper.cpp server", "base_url": "http://localhost:8080/v1", "key_url": ""},
    {"id": "custom", "name": "Other (OpenAI-compatible)", "base_url": "", "key_url": ""},
]

_HIDE = re.compile(r"orpheus|(?<![a-z])tts|guard|embed|rerank|moderation|dall-e|imagen|image|diffusion|flux|playai", re.I)
_STT = re.compile(r"whisper|transcribe|voxtral|parakeet|moonshine|canary|speech-to-text|(?<![a-z])stt(?![a-z])", re.I)


# ------------------------------------------------------------- settings per role

def _norm(url):
    return (url or "").strip().rstrip("/")


def role_settings(cfg, role):
    """(base_url, api_key, model) for a role.

    A role with its own address uses only its own key: the main key never goes to a different server.
    """
    main = _norm(cfg.get("base_url")) or GROQ_BASE
    own = _norm(cfg.get(f"{role}_base_url"))
    own_key = (cfg.get(f"{role}_api_key") or "").strip()
    if own and own != main:
        base, key = own, own_key
    else:
        base, key = main, own_key or (cfg.get("api_key") or "").strip()
    model = (cfg.get(f"{role}_model") or "").strip() or DEFAULT_MODELS[role]
    return base, key, model


def key_missing(cfg):
    """True when a role talks to a server outside the private network without a key."""
    import vox_core as core
    from urllib.parse import urlparse
    for role in ROLES:
        base, key, _ = role_settings(cfg, role)
        host = urlparse(base).hostname or ""
        if not key and not core.is_private_host(host):
            return True
    return False


# ------------------------------------------------------------- model discovery

def classify(entry):
    """'stt', 'llm' or 'hidden' for one entry of a provider's model list.

    Uses an explicit field when the provider gives one (OpenRouter output_modalities, Speaches task,
    Together type), otherwise the model id. Groq's `active: false` is hidden.
    """
    if isinstance(entry, str):
        entry = {"id": entry}
    if entry.get("active") is False:
        return "hidden"
    mid = str(entry.get("id") or entry.get("name") or "")
    task = str(entry.get("task") or entry.get("type") or "").lower()
    if "speech-recognition" in task or task in ("transcribe", "transcription", "stt", "audio-transcription"):
        return "stt"
    mods = (entry.get("architecture") or {}).get("output_modalities") or []
    if "transcription" in mods:
        return "stt"
    if mods and "text" not in mods:
        return "hidden"
    if _HIDE.search(mid):
        return "hidden"
    if _STT.search(mid):
        return "stt"
    return "llm"


def parse_models(payload):
    """[{"id", "kind"}] from an OpenAI-style, plain-list or Ollama /api/tags answer (hidden models dropped)."""
    if isinstance(payload, dict):
        items = payload.get("data")
        if items is None:
            items = payload.get("models") or []
    else:
        items = payload or []
    out, seen = [], set()
    for it in items:
        if isinstance(it, str):
            it = {"id": it}
        if not isinstance(it, dict):
            continue
        mid = str(it.get("id") or it.get("name") or it.get("model") or "").strip()
        if not mid or mid in seen:
            continue
        kind = classify(dict(it, id=mid))
        if kind != "hidden":
            seen.add(mid)
            out.append({"id": mid, "kind": kind})
    return sorted(out, key=lambda m: m["id"].lower())


def explain(status, role, body=""):
    """A plain reason for an HTTP status."""
    if status in (401, 403):
        return "The server refused the key. Check that it is right and belongs to this server."
    if status == 404:
        if role == "stt":
            return "This server cannot do speech-to-text (no /audio/transcriptions). Use a different server for voice."
        return "The server has no such endpoint or model. Check the address and the model name."
    if status == 429:
        return "Rate limit reached. Wait a moment and try again."
    text = f"The server answered HTTP {status}."
    return f"{text} {body}".strip() if body else text


def _error_text(r):
    try:
        e = r.json().get("error")
        return (e.get("message") if isinstance(e, dict) else str(e or ""))[:160]
    except Exception:
        return (r.text or "")[:160]


def list_models(cfg, role):
    """{"models": [{"id", "kind"}], "error": str} for the models this role's server offers."""
    import vox_core as core
    base, key, _ = role_settings(cfg, role)
    problem = core.endpoint_error({"base_url": base})
    if problem:
        return {"models": [], "error": problem}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        r = core.requests.get(f"{base}/models", headers=headers, timeout=5)
        if r.status_code == 404 and base.endswith("/v1"):   # Ollama also answers on its own path
            r = core.requests.get(f"{base[:-3]}/api/tags", headers=headers, timeout=5)
        if r.status_code != 200:
            return {"models": [], "error": explain(r.status_code, role)}
        models = [m for m in parse_models(r.json()) if m["kind"] == role]
    except Exception as e:
        return {"models": [], "error": f"Could not reach the server: {type(e).__name__}"}
    if not models:
        return {"models": [], "error": "The server listed no models for this. Type the model name instead."}
    return {"models": models, "error": ""}


# ------------------------------------------------------------- connection test

def _silent_wav(seconds=1.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def test(cfg, role):
    """{"ok", "status", "ms", "message"}: one real call to the role's server."""
    import vox_core as core
    base, key, model = role_settings(cfg, role)
    problem = core.endpoint_error({"base_url": base})
    if problem:
        return {"ok": False, "status": 0, "ms": 0, "message": problem}
    if key_missing({"base_url": base, "api_key": key}):
        return {"ok": False, "status": 0, "ms": 0, "message": "Add an API key for this server first."}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    started = time.time()
    try:
        if role == "stt":
            r = core.requests.post(f"{base}/audio/transcriptions", headers=headers, timeout=30,
                                   data={"model": model, "response_format": "json", "temperature": "0"},
                                   files={"file": ("test.wav", _silent_wav(), "audio/wav")})
        else:
            r = core.requests.post(f"{base}/chat/completions", headers=headers, timeout=30,
                                   json={"model": model, "max_tokens": 8,
                                         "messages": [{"role": "user", "content": "Reply with the word OK."}]})
    except Exception as e:
        return {"ok": False, "status": 0, "ms": int((time.time() - started) * 1000),
                "message": f"Could not reach the server: {type(e).__name__}"}
    ms = int((time.time() - started) * 1000)
    if r.status_code == 200:
        return {"ok": True, "status": 200, "ms": ms, "message": f"Works ({ms} ms) with {model}."}
    return {"ok": False, "status": r.status_code, "ms": ms, "message": explain(r.status_code, role, _error_text(r))}


# ------------------------------------------------------------- reasoning parameters

_rejected = set()   # (address, model) pairs whose server refused reasoning_effort in this run


def reasoning_params(cfg, base, model):
    """Extra body fields for reasoning models (gpt-oss): low effort, no reasoning text in the answer."""
    if cfg.get("llm_reasoning", "auto") == "off" or "gpt-oss" not in model or (base, model) in _rejected:
        return {}
    return {"reasoning_effort": "low", "include_reasoning": False}


def remember_rejected(base, model):
    _rejected.add((base, model))


def strip_think(text):
    """Removes a leading <think>...</think> block some models put before the answer."""
    return re.sub(r"^\s*<think>.*?</think>\s*", "", text or "", flags=re.S)
