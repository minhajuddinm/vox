# Spec P9g: the note bubble that shows a note being recorded

Status: Implemented on branch `feat/p9g-notes` (task G1), **not run on a phone**. Date: 2026-10-01. Agreed with the requester on 2026-10-01 (after wave 1 started): a bubble that shows a note is being recorded and saves it on tap. No decision record: the choice is small and easy to reverse. Behaviour as built: [05-android-app.md](../05-android-app.md), [08-features.md](../08-features.md). The earlier note work it builds on: [p6-android-note-mode.md](p6-android-note-mode.md); the bubble rules it sits next to: [p9d-android-bubble.md](p9d-android-bubble.md).

## Goal
Before, the note bubble was a persistent opt-in (`note_bubble`, default off): a note started from the Quick Settings tile, the notification or the app showed nothing on screen. Now, whenever a note is being recorded the note bubble appears by itself, red, with the recording time. A tap on it stops and saves the note. When the note is saved the bubble flashes the green check and goes away, unless the persistent switch is on.

## Design

### Pure rules (`NoteBubbleLogic.java`, golden rows `notebubble`)
- `visible(persistentPref, noteRecording, noteSaving)`: true when any of the three is true. The service adds the screen and service checks (no bubble while the screen is off or the service is not ready), as it does for the other bubbles.
- `timer(ms)`: the recording time as `m:ss`, or `h:mm:ss` from one hour; whole seconds rounded down, never negative, always ASCII digits.
- The bubble exists only on Android, so Python has no implementation of its own: `tests/test_parity.py` keeps a one-line reference copy of `visible` (like the three `BubbleLogic` rules), and both suites run the same 8 rows (every combination of the three inputs). The rows prove the Java rule, not Python/Java parity. The timer has no golden rows (it is display text, checked by `NoteBubbleLogicTest`).

### Service (`VoxAccessibilityService`)
- `refreshVisibility` feeds `visible` with the `note_bubble` switch, `DictationService.isNoteRecording()` and "a note is being sent (`PROCESSING` while the job is a note) or its result flash is still showing". Everything that starts a note goes through `DictationService`, so the tile, the notification, the bubble and the app all bring the bubble up the same way, through the `onState` callback that already existed.
- `flashNote(kind)` (used by `onNoteSaved` and by `onError` for a note) flashes the green check (0.7 s) or the red ! (1.8 s), and holds the bubble up for that long plus a little (`noteFlashUntil`), then refreshes again so the bubble goes. With the persistent switch on nothing is removed.
- A service that connects while a note is recording (after a restart of the accessibility service) shows the bubble at once.
- The mic bubble is unchanged: it still does not come up for a note.

### Bubble (`BubbleView`, note variant)
- While `state` is `RECORDING` the note variant draws the time in the middle (white, bold, shrunk to fit when it is `1:02:03`) instead of the note page. The text is made once a second, not on every frame; the redraw comes from the level updates (about 25 a second) that already invalidate the view.
- The time is counted from the moment the view's state became `RECORDING`. After a service reconnect in the middle of a note it starts again at 0:00.
- `BubbleView.flashMs(kind)` exposes the two flash lengths (still kept equal to `FLASH_SECONDS` in `engine.py` by `tests/test_flash_constants.py`) so the service can hold the bubble for the flash.

### What did not change
- Tap on the note bubble: idle starts a note, recording a note stops and saves it, busy with a dictation says "Finish the dictation first". Long press cancels the note. Drag and snap to the edge, with the same saved position (`note_bubble_x`, `note_bubble_y`) for the persistent and the automatic bubble.
- The notification keeps its Stop action and the tile keeps its toggle.
- No new setting, permission or stored data. Needs the accessibility service, like every bubble: without it a note started from the tile or the notification still works but shows no bubble.

## Tests
- `NoteBubbleLogicTest` (all three inputs, the timer from zero to over an hour, negative), `ParityTest` and `tests/test_parity.py` (the 8 `notebubble` rows), `tests/test_ui_static.py` (the service uses the rule, flashes through `flashNote`, the view draws the timer), `javatest.cmd compile` (every Android source compiles against android.jar).

## Device checklist (for a tester, on the phone; none of this has been run)
Setup: the accessibility service is on, Settings has **Voice note bubble** off and **Bubble only while typing** as you like.
1. Start a note from the **Quick Settings tile**: a red bubble with a running time appears (default spot on the right edge, lower than the mic bubble) in whatever app is open. The time counts up each second.
2. Tap the bubble: it turns amber while the note is cleaned up, then flashes the green check, then it is gone. The "Note saved" notification arrives and the note is in the Notes list.
3. Start a note from the **"Record note" notification**: the same bubble appears; its Stop button also stops the note and the bubble goes the same way.
4. Start a note from the **app** (Voice notes page, New voice note) and from the **Voice note bubble** with the switch on: the red bubble with the time shows in both cases; with the switch on, the bubble stays on screen (blue, note page) after the save.
5. Record a note longer than a minute: the time reads `1:05`, not seconds; the text stays inside the circle.
6. Record for under half a second and tap: the bubble shows the red ! ("did not hear anything"), then goes (switch off).
7. Turn the **network off** (or use a wrong key), record a note, stop: a red ! flashes, the bubble goes (switch off), the recording is kept for Retry in the notification.
8. Long press the bubble while it records: "Cancelled", the bubble goes, no note is saved.
9. Drag the red bubble to the left edge, finish the note, start another one: the bubble comes back at the left edge (the same saved spot as the persistent bubble).
10. Start a note, lock the phone for 10 s and unlock: the bubble is back, still red, the time kept counting. (The screen-off rule of branch D removes the bubble while the screen is off.)
11. Start a note, then open a text field and use the mic bubble: it says "Vox is busy with a voice note" and the note carries on; the mic bubble does not turn red.
12. Switch the **Voice note bubble** on, record and save a note: the bubble stays. Switch it off while no note is recording: the bubble goes at once. Switch it off while a note records: the bubble stays until the note is saved, then goes.
13. Look at **Bubble diagnostics** after step 2: the log has "Bubble added: note bubble, a voice note is in progress" and "Bubble removed: note bubble, voice note bubble is off".

## Deviations and choices
- **No timer thread.** The time is drawn from the level callbacks that already redraw the bubble. If the microphone stopped delivering buffers the time would stop redrawing until the next event; a stalled microphone is a failure that ends the note anyway.
- **The flash hold is not a new input of the rule.** The plan names `visible(persistentPref, noteRecording, noteSaving)`; the short hold after a save is counted as "saving" by the service, so the pure rule keeps the three inputs from the plan.
- **Errors also show the bubble for their flash.** A note that fails to start or send (no key, no network, no speech) flashes the red ! on the note bubble even when the switch is off, so the failure is visible where the user was looking. The toast still appears.

## Not verified
- Nothing ran on a phone: the bubble being added and removed by the real `WindowManager` at note start and end, the text drawing and its size on a real screen, the 100 ms and 150 ms hold timings, the callbacks arriving in the order assumed above (the saved callback before the idle state), the tile and notification starts.
- Everything depends on the accessibility service being connected; with it stopped by Android no bubble is possible.
