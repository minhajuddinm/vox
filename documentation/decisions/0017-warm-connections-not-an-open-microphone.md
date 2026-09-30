# 0017. Warm the connections at key-down; never keep the microphone open

Status: Accepted
Date: 2026-09-30

## Context

Speed after the key is released is upload plus transcription plus cleanup. Groq transcribes 30 s of audio in about 0.14 s, so the network (a new TLS handshake per request) matters more than the model. Handy keeps a pre-roll of 450 ms by leaving the microphone stream open ("always on"), which also means the microphone is live while nothing is being dictated. Research: v2 research notes (2026-09-30).

## Decision

- At key-down (Windows `Engine.start`) and at the start of a recording (Android `DictationService.startRecording`) Vox opens the connection to each distinct server in the background (`vox_core.warm`, `GroqClient.warm`). The requests then reuse it: Windows posts through one shared `requests.Session`; Android reads responses to the end and does not `disconnect()`, so the connection returns to the pool.
- The microphone is opened only when recording starts, as before. No pre-roll, no always-open stream.
- Not done in this step: shortening the Android start delay (a 400 ms transparent activity plus a 350 ms wait in the accessibility service) and skipping cleanup for short phrases. Both change behaviour that needs device testing and are listed in known issues.

## Consequences

- Saves roughly one to two round trips (TLS handshake) per dictation on hosted servers; no gain for a server on the same machine. The saving is an estimate, not a measurement.
- The very first syllable after the key goes down can still be lost while the microphone opens.
- Warm-up sends one small `GET /models` per server per dictation (with the key, to the server it belongs to).

## Alternatives considered

- Always-open microphone with a pre-roll buffer: better first syllable, but a live microphone at all times is a privacy cost this project has avoided.
- HTTP/2 or a persistent WebSocket: more code for a small further gain.
