# 0037. Esc cancels a keep-listening session; the keyboard hook only queues keys

Status: Accepted (built on Windows, tested with fakes; not run on a real desktop). Supersedes the Esc rule of [0032](0032-keep-listening-pieces-two-targets-crash-safe-buffer.md) (point 6); the rest of 0032 stands.
Date: 2026-10-02

## Context

Task B1 of the Wispr-parity pass asked that Esc cancel a recording or a keep-listening session without sending anything. Until then Esc cancelled only a hands-free recording (a voice note), was ignored during a held dictation, and **ended and saved** a keep-listening session: decision 0032 had rejected "Esc discards the session" because a stray Esc could lose an hour of speech.

Issue 38 (R2-M1) asked whether slow work runs inside Windows' low-level keyboard hook. It did: pynput calls `on_press` inside the hook, and `on_combo_down` opened the microphone there (`start`, `_open_mic`, a PortAudio restart when the chosen microphone was missing). Windows skips, and after repeated timeouts removes, a hook that does not answer within `LowLevelHooksTimeout`.

## Decision

1. **Esc cancels.** A held, hands-free, note or instruction recording is cancelled (`Engine.cancel_any`). A keep-listening session is cancelled with `Listening.cancel`: nothing more goes to the servers, no note is saved, pieces already typed stay typed, no flash. A session that already ran **30 s or more** (`listen.CANCEL_KEEP_SECONDS`) keeps its audio file, so "Recover listening session" can still turn it into a note; a shorter one leaves nothing. A session that is already saving (busy) is not cancelled. The double press, the stop phrase, the tray and the note shortcut still end and save.
2. **The hook only queues.** Once the engine runs, `on_press`/`on_release` put `(down, key, time)` on a queue and return; one `vox-hotkey` thread handles the events in order with the time they happened (so a slow microphone start does not turn a tap into a hold). Tests that build an `Engine` without `run` have no queue and handle keys at once. The Start-menu suppression tap (`0xE8`) is sent from that thread right after the shortcut completes.

## Consequences

- A stray Esc during a long session no longer saves the note, but the audio is still on disk; the balloon says where to recover it. Short sessions are gone, as the request wants.
- Key handling no longer depends on how long the microphone takes to open, and an exception in it can never reach the hook. A key event waits behind the previous event's work (opening the microphone): the order is kept, the times are the real ones.
- The paste-last, copy-last and edit-by-voice actions run on their own threads, so waiting for the shortcut's keys to come up does not block the hotkey thread.

## Alternatives considered

- Keep Esc as "end and save" for keep listening and only cancel dictations: contradicts the request and the Wispr and Handy behaviour users expect from Esc.
- Discard every cancelled session's audio: the hour-of-speech risk of 0032.
- Move only `start` to a worker: a release that arrives before the microphone is open would see "not recording" and leave a hold dictation running; handling all events on one thread keeps the press-release order without extra state.
