# 0001. OpenAI-compatible API with Groq as the default

Status: Accepted
Date: 2026-09-27 (original design; recorded 2026-09-30)

## Context

Vox needs speech-to-text and a language model for cleanup, and it must be free to run for the user with no Vox-run server.

## Decision

Talk to the OpenAI-style HTTP API (`/audio/transcriptions`, `/chat/completions`, `/models`). The default provider is Groq with the user's own free API key (Whisper `whisper-large-v3-turbo` for speech, `openai/gpt-oss-20b` for cleanup). Everyone uses their own key and their own free quota. (Reasons are **inferred** from the README: free tier, speed, no server to run.)

## Consequences

- No subscription, no Vox backend, no accounts. The maintainer never sees user data.
- Free limits belong to Groq and can change; the README documents the limits at the time of writing and Vox falls back to the raw transcript when cleanup is unavailable.
- Because the wire format is the common OpenAI one, any compatible server works, which made [0005](0005-configurable-endpoint-private-http.md) cheap to add.
- Model names are provider-specific; `gpt-oss` models get extra parameters (`reasoning_effort`, `include_reasoning`).

## Alternatives considered

- A hosted Vox service: rejected (cost, privacy, operations).
- On-device speech recognition: not attempted; would not run well on the target phones and PCs.
