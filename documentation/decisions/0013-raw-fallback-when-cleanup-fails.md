# 0013. Raw transcript is the fallback when cleanup fails

Status: Accepted (the text used on a failure is now the rules layer's: [0042](0042-a-rules-layer-when-the-ai-gives-no-text.md))
Date: 2026-09-29 (original fallback: 2026-09-27; notice and spoken commands added 2026-09-29)

## Context

Cleanup is a second network call to a language model. It can fail (limits, network), be skipped (raw style, cleanup off, fewer than 3 words), or answer the transcript instead of cleaning it. The user still wants their words.

## Decision

- If cleanup does not produce valid text, use the raw Whisper transcript. `looks_valid` rejects empty answers and answers longer than `1.6 x raw + 40` characters.
- Because the model would normally convert spoken commands, `apply_spoken_commands` handles "new line" and "new paragraph" whenever cleanup did not produce the text.
- Tell the user when cleanup was wanted but failed (Windows notification, Android toast) instead of falling back silently. `process_detailed` returns `cleaned` and `cleanup_error` for this.
- The original design always kept the raw transcript on failure; the notice and the spoken-command handling were added later.

## Consequences

- A dictation is never lost because cleanup failed.
- Spoken punctuation words ("comma", "period") are not converted in the raw path; only line breaks are. A phrase like "a new line of code" also becomes a break in the raw path.
- Cleanup is not retried automatically on Android and rides `post_with_retry` on Windows.

## Alternatives considered

- Convert all spoken punctuation deterministically: ambiguous words ("period", "comma" as ordinary words) risk corrupting text.
- Fail the whole dictation when cleanup fails: violates "never lose a dictation".
