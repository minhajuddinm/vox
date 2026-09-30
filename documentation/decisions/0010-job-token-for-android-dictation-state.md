# 0010. Job ids for Android dictation state

Status: Accepted
Date: 2026-09-29

## Context

`DictationService` used shared flags (`recording`, `cancelled`) and unconditional `setState(IDLE)` calls. A review found: a recorder error left the state at `RECORDING` (the bubble stuck); a negative `AudioRecord.read` made the loop spin; cancelling and starting again reset `cancelled` so the old request could insert its text and its `finally` overwrote the new recording's state; results ignored which app the dictation started in.

## Decision

Every recording, retry and cancel increments `jobId`. Background work captures its job id and checks `isCurrent(job)` before changing state, showing errors, saving history or delivering the result. `failRecording`, `finish` and the public entry points are synchronized. Errors during recording return the service to `IDLE`. `onResult` carries the package the dictation started in and the accessibility service copies to the clipboard instead of typing when focus is in a different app.

## Consequences

- A cancelled or superseded dictation is silent and harmless.
- The single worker thread still finishes an in-flight network call before the next job starts (a cancelled upload is not aborted).
- State logic now depends on remembering to guard new background code with `isCurrent`.
- There are no automated tests for this class (Android framework code); the pure helpers it uses are tested.

## Alternatives considered

- A proper state machine class with tests: better, but larger; noted in [../12-known-issues-and-roadmap.md](../12-known-issues-and-roadmap.md).
- Aborting the HTTP request on cancel: more plumbing; not needed for correctness.
