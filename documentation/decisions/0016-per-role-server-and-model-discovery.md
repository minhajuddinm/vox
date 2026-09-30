# 0016. Per-role server and model discovery by id

Status: Accepted
Date: 2026-09-30

## Context

Vox has one address and one key for both roles (speech-to-text and cleanup) and two free-text model fields. Local servers split the roles (Ollama and LM Studio have no `/audio/transcriptions`; Speaches and whisper.cpp have no chat). Groq's `GET /models` returns no field that says whether a model is for speech or chat, so a picker must classify models itself. Some providers reject `reasoning_effort`. Source: the v2 research (2026-09-30).

## Decision

- One main address/key stays; each role may override it (`stt_base_url`, `stt_api_key`, `llm_base_url`, `llm_api_key`). Empty means inherit.
- Model lists come from `GET {base}/models`; models are classified by an explicit provider field when present, else by id patterns shared through `spec/golden.txt`; a free-text model id is always allowed.
- A Test button checks each role separately with a real call and explains the failure.
- `reasoning_effort` is sent only for `gpt-oss` models, retried once without on 400/422, and remembered per address and model.
- One profile only for now; a saved-profile list and automatic fallback wait until the relay needs them.

## Consequences

- Local, mixed and cloud setups all work without new code paths per provider.
- Classification by id will sometimes be wrong for new model names; the free-text field and the shared golden rows are the safety valve.
- Each role's key must only go to that role's address (tested).

## Alternatives considered

- A list of saved profiles with fallback: more UI and state than the need justifies now.
- Provider-specific adapters (Deepgram, xAI, AssemblyAI): not OpenAI-compatible; out of scope.
- Classifying with a hard-coded model list: goes stale when providers change lineups (Groq removed two models on 2026-08-16).
