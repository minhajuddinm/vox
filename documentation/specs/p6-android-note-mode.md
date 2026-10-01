# Spec P6: Android note mode

Status: Implemented in the Android notes group (group B of the v2 part 2 plan), **not run on a phone**. Date: 2026-09-30. Decision records: [0025](../decisions/0025-note-bubble-in-the-accessibility-service.md), [0026](../decisions/0026-android-notes-and-sync-are-ports-with-shared-golden-rows.md). Follows [p5](p5-voice-notes-windows.md) (the Windows notes) and [p8c](p8c-quick-wins.md). Sync of these notes is [p7e](p7e-android-sync.md). Behaviour as built: [05-android-app.md](../05-android-app.md), "Voice note mode".

## Goal
Record a voice note on the phone without a text box: speak, stop, and the cleaned-up words are saved to a searchable list instead of being typed anywhere. Starting one must be as quick as a dictation.

## Design

### Faster start (task 6)
Before notes, a bubble tap with the dictation service off went through `TrampolineActivity`, a fixed 400 ms wait, and a second 350 ms wait. Now the trampoline passes its intent extras (`EXTRA_START`, `EXTRA_PKG`, `EXTRA_LABEL`, `EXTRA_DEST`, `EXTRA_TAP_AT`) to `DictationService`. `onStartCommand` calls `startForeground`, sets `instance`, starts the recording itself and then calls `TrampolineActivity.finishNow()`; a 1500 ms safety finish remains. The microphone is allowed because the foreground service starts while the trampoline is visible. The recording thread logs `tap->recording ms=N` (`adb logcat -s vox`). Side change: a cold bubble tap in a password field now refuses, like a warm tap.

### Pure rules (task 7): `NoteLogic`
Port of the pure parts of `windows/notes.py` and `windows/sync.py`: `autoTitle`, `ftsQuery`, `cleanTags`, `remoteWins`, `deviceName` and the push batch size, plus `startAction(state, noteJob, wantNote)` (START, STOP or BUSY). Python and Java are held equal by golden rows (`title`, `ftsq`, `remotewins`, `devname`; sync adds `merge3`, `profilefields` and `permanent`, see [p7e](p7e-android-sync.md)) run by `tests/test_parity.py` and `ParityTest.java`. Tags are the one known difference ([12](../12-known-issues-and-roadmap.md)).

### Store (task 8): `NotesStore`, `Note`, `SyncStore`
`NotesStore` is the phone's `notes.db` (Android `SQLiteOpenHelper`, same columns as Windows including `dirty` and `seq`, table `sync_meta`), written against `notes.py` function by function. Search uses FTS5 when the phone's SQLite has it and a `LIKE` fallback otherwise; which one a given phone uses is not known. `SyncStore` is the five calls the sync needs (`dirtyNotes`, `markSynced`, `applyRemote`, `getMeta`, `setMeta`).

### Note mode (task 9)
`DictationService` gets a destination (`dictation` or `note`) that is copied with `pkg` and `label` when a recording stops and kept with the pending recording, so Retry cannot send a note as typed text. A note forgets the app (no app name reaches the model), runs the same cleanup gate as Windows, and is stored with `NotesStore.add` (source "voice note", the pre-cleanup text as `raw`, the device name). Nothing is typed and nothing goes to the dictation history. After the save: `NoteEvents.fireSaved()`, a "Note saved: title" notification and `NoteListener.onNoteSaved`.

Three entry points, one start path (`NoteEntry.startIntent` to the trampoline):

| Entry point | Needs | Stop |
|---|---|---|
| Note bubble: a second, blue bubble with a page glyph, drawn by `VoxAccessibilityService`, visible when `note_bubble` is on, whether or not a text field has focus | the accessibility service | tap again |
| "Record note" notification (`note_notification`), ongoing, one action | nothing else | a "Stop" action on the service notification while recording |
| Quick settings tile "Voice note" (`NoteTileService`, an active tile) | nothing else | tap while a note records |

Related rules: a dictation in progress is never stopped by a note control (the bubble says "Finish the dictation first", the tile "Vox is busy"); a long press cancels only the bubble's own job; `postError` falls back to a toast when no accessibility service is listening; the bridge (`noteToggle`, `noteStatus`) serves the Notes page ([05](../05-android-app.md)).

### Follow-ups from review (tasks F1 and F2)
A note counts as saved once `store.add` succeeds (the title read-back cannot fail it); `saveNote` re-checks that its job is still current right before the write; the service notification channel was replaced by a low-importance one (`vox_service_low`) so the Stop action is visible; the tile became an active tile and is refreshed when a note starts, ends, and when the service starts or is destroyed; a stale stop calls `stopSelf(startId)`; `DictationService.currentDest()` lets the Notes page tell a note from a dictation.

## Deviations from the plan
- `DictationService.ACTION_STOP` already meant "Turn off" (`stopSelf`), so a second action `ACTION_STOP_RECORDING` means "finish and send the recording". The tile and the notification use the second one.
- There is no `SYSTEM_ALERT_WINDOW`. The bubble is drawn by the accessibility service (a window type it may use without that permission), so no new permission is asked ([0025](../decisions/0025-note-bubble-in-the-accessibility-service.md)).
- The tile does more than the plan said: stop only a note, say "busy" for a dictation, start via `startActivityAndCollapse` (a `PendingIntent` on Android 14 and later).
- `ensureNotification` posts unconditionally; `applySettings` is the variant that reads `note_notification`.

## Not in this step
A Settings row for `note_bubble` and `note_notification` (both default to off; the bridge reads and saves them), a boot receiver for the notification, tags on the phone's note screen beyond what the Notes page edits, one list for dictations and notes.

## Done when
The code compiles (`javatest.cmd compile`), the pure parts pass the Java tests (`NoteLogicTest`, `NoteTest`, `NoteEventsTest`, `ParityTest`) and the device checklist below passes on an Android 14 and an Android 15 phone. **The checklist has not been run.**

## Device checklist (nothing here has run on a phone)
Setup: install the APK from CI, grant the microphone, set an API key, enable the accessibility service. Force-stop Vox before each "cold" row. Read timings with `adb logcat -s vox`.

Start speed (task 6):
1. Cold tap to first audio, 5 times: note `tap->recording ms` and its range; it should be well under the old floor of about 750 ms (400 + 350). Warm tap: no trampoline flash, a very small number.
2. The first word spoken right after the bubble turns red is in the transcript (cold start).
3. Android 14 and 15, cold start from the bubble: the trampoline closes by itself, the target field keeps its text and focus, the recording is real speech and not silence (a silent clip would say "Vox did not hear anything"). If it is silent, move `finishNow()` to the first audio frame.
4. No API key: the toast appears, the trampoline is gone within 1.5 s, the bubble is idle. Microphone permission revoked: the toast, Vox opens, nothing records.
5. Password field, cold and warm tap: "Vox does not type into password fields", nothing records.
6. Double tap on a cold start: at most one recording, no crash, no stuck bubble.
7. Cold-start dictation ends with text in the same field; the history shows the right app name; switching apps mid-dictation still copies to the clipboard.
8. Regression: stop with a tap, cancel with a long press, Retry, "Turn off" from the notification, the Settings "dictation service" switch.

Note mode (task 9):
9. `note_bubble` on: the bubble shows without a focused text field and with `only_typing` on; it is blue with a page icon; it drags, snaps to the edge, and keeps its position after the accessibility service restarts.
10. Start from the bubble with the service off and on: red with the level ring, tap to stop, amber while working, a heads-up "Note saved: ..." appears, nothing is typed, and the note is in `notes.db` with source "voice note", cleaned text, pre-cleanup `raw`, a sensible length and this phone's device name.
11. Start from the "Record note" notification (`note_notification` on), service off and on: while recording the service notification says "Recording a voice note" with Stop; Stop ends and sends it; the normal notification returns.
12. Start from the tile (add it from the edit menu): the shade collapses, the note starts; with the shade open mid-recording the tile is active; tap stops; tap during a dictation says "Vox is busy".
13. Android 14 and 15: the microphone service starts from each path (bubble, notification, tile) with the app in the background and with the screen locked (the tile unlocks first).
14. Double tap the bubble on a cold start: no transparent trampoline stays on screen.
15. Airplane mode: stop a note, the error says the recording is saved, the notification says "Last voice note not sent"; back online, Retry saves it as a note (not typed) even after a too-short dictation was started in between.
16. Notification permission denied: stopping a note shows a "Note saved: ..." toast instead.
17. No accessibility service: the tile with no API key shows the "Add your API key" toast.

Review follow-ups (task F1):
18. A second "Record note" while a note records stops it; during a dictation it shows "Vox is busy".
19. The tile flips between active and inactive with the panel open.
20. The Stop action is visible in the notification (new channel; the old one is removed).
21. Cancel at the end of a long note stores nothing. (A note is saved even if the title read fails: not possible to simulate.)

Notes store (task 8, once notes can be made):
22. A saved note survives closing the app; its title is the first 7 words (plus "...").
23. Search by a word start finds it; a middle-of-word search finds it only without FTS5. Write down which this phone does (is there a `notes_fts` table).
24. Two-word search needs both words; punctuation only returns everything. Filters: source, date range, a tag with `/` or an accent.
25. Edit title, text and tags: the list updates and search finds the new words. Delete: gone from list, search and count, and the next sync sends a delete marker.
26. An offline edit stays `dirty`; after a sync it clears and `seq` is set.

## Not verified
Everything that needs Android: `NotesStore` against a real SQLite (and FTS5 on API 26 and later), the microphone starting from each path on Android 14 and 15, the tile and its refresh, the notification actions and channel, the bubble, the heads-up notification, and the real tap-to-recording time. Only the pure classes and the type check ran.
