# Spec P1: any provider, per-role server, model list, Test button

Status: Implemented on branch `feat/providers` (awaiting on-device checks). Date: 2026-09-30. Decision record: [0016](../decisions/0016-per-role-server-and-model-discovery.md).

## Goal
Any OpenAI-compatible provider, any open-source model and any key works in both apps. With a Groq key the user picks the voice model and the cleanup model from a list. Nothing in the UI or code assumes Groq.

## Not in scope
Bundled local models (server-only, see the roadmap), a list of saved profiles with automatic fallback (one profile is enough for now), non-OpenAI STT protocols (xAI, Deepgram, AssemblyAI), the relay.

## Today (verified in the code)
- One address, one key, two free-text model fields (`base_url`, `api_key`, `stt_model`, `llm_model`).
- `GET {base}/models` exists only as a key check; the list is thrown away.
- Groq is assumed in constants, UI copy, the key rule, `gpt-oss` parameters, and `meeting.py` model defaults.
- Ollama and LM Studio cannot do speech-to-text, so one address for both roles cannot work for a local setup.

## Design

### Settings (Windows `config.json`, Android `Prefs`)
Existing keys keep their meaning (back-compatible). New keys, all optional; an empty value means "use the main one":

| Key | Meaning |
|---|---|
| `provider` | Preset id that filled the form (`groq`, `openai`, `openrouter`, `together`, `mistral`, `ollama`, `lmstudio`, `speaches`, `whispercpp`, `custom`). Only a UI convenience; the address decides behaviour. |
| `stt_base_url`, `stt_api_key` | Separate server/key for speech-to-text. |
| `llm_base_url`, `llm_api_key` | Separate server/key for cleanup. |
| `llm_reasoning` | `auto` (default) or `off`: whether to send `reasoning_effort`. |

### Module `providers` (Python `windows/providers.py`, Java `Providers.java`)
Deep module: the callers know four calls; everything provider-specific stays inside.

```
role_settings(cfg, role)   -> (base_url, key, model)          # role = "stt" | "llm"; applies the inherit rule
list_models(cfg, role)     -> {models: [{id, kind, active}], error}   # kind = "stt" | "llm" | "hidden"
classify(entry)            -> "stt" | "llm" | "hidden"
test(cfg, role)            -> {ok, status, ms, message}
```
`vox_core.transcribe/cleanup`, `check_key`, and the meeting code call `role_settings` instead of reading the four keys directly. `GroqClient.java` is renamed `ApiClient.java` and calls the Java twin.

### Model discovery
- `GET {base}/models` with the role's key, 5 s timeout; on failure for an Ollama-looking address try `/api/tags`.
- `classify` uses an explicit field when the provider gives one (OpenRouter `architecture.output_modalities`, Together `type`, Speaches `task`), otherwise the id:
  - `whisper*`, `distil-whisper*`, `*transcribe*`, `voxtral*`, `parakeet*`, `moonshine*` -> `stt`
  - `*orpheus*`, `*tts*`, `*guard*`, `*safeguard*`, `*embed*`, `*rerank*`, image models -> `hidden`
  - everything else -> `llm`
  - `active == false` (Groq) is dropped.
- The UI shows two pickers (voice from `stt`, cleanup from `llm`), a Refresh button, and always a "Type a model id" field. If the list call fails the pickers fall back to the text field and say why.
- The rules are shared through `spec/golden.txt` (new `models` rows: `models<TAB>id<TAB>expected`), run by `tests/test_parity.py` and `ParityTest.java`.

### Reasoning parameters
Send `reasoning_effort:"low"` and `include_reasoning:false` only when `llm_reasoning != off` and the model id contains `gpt-oss`. If the server answers 400/422, retry once without them and remember that (address + model) for the process, so a provider that rejects them costs one extra call once.
Strip a leading `<think>...</think>` block from cleanup output.

### Test button (per role)
- STT: upload a bundled 1 s silent WAV to `/audio/transcriptions`; success is HTTP 200 (the text may be empty or invented: ignore it).
- LLM: `chat/completions` with a fixed 3-word prompt and `max_tokens` 8.
- Result shows status, milliseconds and a plain reason: 401 wrong key, 404 the server has no such endpoint (say "this server cannot do speech-to-text" for Ollama/LM Studio), 429 rate limit, network error, or an address rule error.

### Key and address rules
- Each role's key is sent only to that role's address. The existing private-host/https rule (`endpoint_error`, `Endpoint.error`) is applied to each role address.
- A key is required only when the role's address is not a private host (replaces "only when the address is Groq").
- Windows keeps DPAPI for all keys (`api_key`, `stt_api_key`, `llm_api_key`); Android stays as today (known open item).

### UI (both apps)
Settings gets an **AI provider** card: preset dropdown (fills the address and links to where to get a key), key, Voice model picker, Cleanup model picker, Test buttons, and an "Use a different server for voice or cleanup" toggle that reveals the per-role address and key. Groq copy in the sidebar footer, welcome card, Android setup step, `strings.xml`, `docs/privacy.html` becomes provider-neutral (shows the actual provider host). Meeting notes model settings (`notes_model`, `final_stt_model`) use the same pickers under Advanced.

## Steps (each a small commit)
1. `providers.py` + `tests/test_providers.py` (classification, inherit rule, discovery parsing, reasoning retry) and golden `models` rows; stub-server smoke test.
2. Wire `vox_core`, `ui_app`, `meeting.py`; new config keys named in `07-config-and-data.md`.
3. Windows UI (Settings card, pickers, Test); Playwright mock-bridge check.
4. Java `Providers.java` + `ProvidersTest.java`, rename `GroqClient`, `Prefs`, `MainActivity` bridge.
5. Android UI; provider-neutral copy and privacy page.
6. Docs sync: pages 04-09, 12, tree, changelog, devlog, ADR 0016.

## Done when
- pytest and the Java tests pass in CI; docs checker passes.
- With a Groq key both pickers list only usable models on Windows and phone; choosing them changes the model used (checked against a stub server).
- Pointing voice at a Speaches/whisper.cpp server and cleanup at Ollama works (a home GPU PC over Tailscale), or the Test button says exactly which side fails.
- No "Groq" string is shown when another provider is configured.

## Open questions (for the review)
1. Keep `GroqClient` -> `ApiClient` rename in this PR, or a separate PR to keep the diff readable?
2. Should the Groq preset also pre-select the current defaults, or leave pickers empty until Refresh?
3. Is one profile enough, or is a saved-profile list wanted before the relay (which will sync one profile)?
