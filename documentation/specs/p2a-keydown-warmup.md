# Spec P2a: warm connections at key-down

Status: Implemented on branch `feat/keydown-warmup`. Date: 2026-09-30. Decision record: [0017](../decisions/0017-warm-connections-not-an-open-microphone.md).

## Goal
Less waiting after the user releases the key, without changing behaviour or opening the microphone earlier.

## Design
- Windows: `vox_core._session` (shared `requests.Session`), `_post` used by `post_with_retry`, `warm(cfg)` (one `GET {base}/models` per distinct role server, keys kept apart, failures ignored) called from `Engine.start` before the microphone opens. `tests/conftest.py` routes `_post` back through `requests.post` so older tests that replace it still work; tests that need the real session use the `real_session` marker.
- Android: `GroqClient.warm()`; `DictationService.startRecording` warms the speech and cleanup servers on a background thread; `readJson` no longer calls `disconnect()` so the connection is reused. `startRecording` now checks the address of each role, not only the main one (a gap from P1).

## Not in this step (needs device testing)
Shortening the Android start delay (400 ms trampoline plus 350 ms in `VoxAccessibilityService.onDictationServiceReady`), skipping cleanup for short phrases, pre-roll, streaming cleanup, chunked speech-to-text.

## Done when
Tests pass (`tests/test_warmup.py`); CI green; Yuvraj notices no regression on Windows and phone. Time saved is not measured.
