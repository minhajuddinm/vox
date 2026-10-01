# 0031. Dictation timings stay on the device

Status: Accepted
Date: 2026-10-01

## Context

Branch B of v2 part 3 ([p9b](../specs/p9b-measure-and-speed-up.md)) measures where the time of each dictation goes (waiting for the microphone, speech to text, cleanup, typing the text in) so that speed work and the choice of cleanup model rest on numbers. A timing record is small, but it says when the user dictated, how long they spoke, which app they were in (the history line already has that) and which voice and cleanup models and which host or relay they used. Vox promises that nothing goes anywhere except to the servers the user chose, and that there is no analytics ([09](../09-security-privacy.md), `docs/privacy.html`). The relay already syncs voice notes and the profile; the history is deliberately not synced.

## Decision

- The timing of a dictation is one more field, `timing`, in the history entry of that dictation (`history.jsonl` on Windows, the history preference on Android). It is written by the app that made the dictation and read only by that app's Speed card.
- It is never sent to the speech server, the cleanup server or the relay, never synced between devices (history is not synced, and `PROFILE_FIELDS` does not list it), and there is no analytics, crash report or benchmark upload. The Speed card needs no network.
- It follows the history: with "Keep dictation history" off nothing is timed or saved; deleting a history line, clearing the history or uninstalling removes the timing with it. A Retry and a voice note are not timed.
- The only fields kept are stage durations in milliseconds, the voice and cleanup model names, a provider label (the host name of the cleanup server, or `relay`) and whether the relay was used. No text, no audio, no keys, no window titles.
- Comparing devices or sharing numbers is done by the user (a screenshot or reading them out), not by the app. If a future feature needs to upload timings, it needs a new decision record and an explicit button, like the planned "Improve my cleanup" run.

## Consequences

- Privacy text needs one line, not a new data flow. The phone and the PC each show only their own numbers; there is no combined view and no way to see a speed problem without the device in hand.
- The cleanup benchmark planned for branch A of the same round (`tools/bench_cleanup.py`) is meant to follow the same rule: local results, the user's own key. It is not part of this branch.
- Old history lines have no `timing` and are ignored by the card. Each history line is a little longer.
- On Android the history preference is not encrypted ([09](../09-security-privacy.md)); timings add model names and timestamps to what is already stored there.

## Alternatives considered

- Send timings to the relay for a shared view: crosses the "history is not synced" line, and the relay cannot tell which step of the phone was slow.
- A separate timing log file: a second place to clear when the user clears history, and a second privacy surface.
- Counters only (no per-dictation record): cannot show the last 10 dictations or the median per model.
- Anonymous telemetry to the developer: contradicts the "no server of its own" promise.
