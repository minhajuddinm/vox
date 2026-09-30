# 0009. Keep failed recordings and offer retry

Status: Accepted
Date: 2026-09-29

## Context

If the network dropped or the server returned an error, the recording was thrown away: the user had to say it all again. On the phone, with weak connections, this was the most damaging behaviour.

## Decision

- Retry the speech request automatically on transient failures (Windows: connection errors and 500/502/503/504 through `post_with_retry`; Android: network errors, 5xx, 429, 408 with up to 3 attempts).
- If it still fails, keep the audio and let the user retry: on Windows the tray menu item "Retry last dictation" (audio held in memory in `Engine.pending`); on Android a "Retry" action on the service notification (audio kept in `cache/vox_pending.wav`).
- The audio is deleted only after a successful send, on cancel, or when the Android service is destroyed.
- Cleanup failure never loses text: see [0013](0013-raw-fallback-when-cleanup-fails.md).

## Consequences

- Users do not repeat themselves after a network blip.
- Recorded audio can sit on the phone's cache until sent; it is removed on success or cancel.
- Windows keeps the audio only in memory, so quitting loses it (known issue).
- HTTP 429 is not retried automatically on Windows (waiting seconds would not help a per-minute limit), but the manual retry is available.

## Alternatives considered

- Queue and resend automatically in the background: risks typing text into the wrong app minutes later.
- Persist Windows audio to disk: more privacy exposure; postponed.
