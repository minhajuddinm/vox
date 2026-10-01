# 0025. The note bubble lives in the accessibility service; the tile and the notification are other entry points

Status: Accepted
Date: 2026-09-30

## Context

Android voice notes need a way to start and stop a recording without a text field to type into. The dictation bubble is drawn by `VoxAccessibilityService` and only shows while a text field has focus ([0003](0003-android-accessibility-bubble.md)). A note must be startable from anywhere, including the home screen and a locked phone, and Android 14 and later restrict starting a microphone foreground service from the background.

## Decision

- The note bubble is a second, blue bubble with a page glyph, drawn by `VoxAccessibilityService` with the same accessibility overlay window type as the dictation bubble. It shows whenever the setting `note_bubble` is on, independent of focus and of `only_typing`. The permission `SYSTEM_ALERT_WINDOW` is not requested.
- Two more entry points that do not need the accessibility service: an ongoing "Record note" notification (`note_notification`) and a Quick Settings tile "Voice note" (`NoteTileService`, an active tile). All three start through the same `NoteEntry.startIntent` to `TrampolineActivity`, so the microphone service starts while a visible activity exists, as for dictation.
- A note recording goes to `DictationService` with `dest = note`; it is stopped by `ACTION_STOP_RECORDING`. `ACTION_STOP` keeps its old meaning, "Turn off".
- A note control never stops a dictation in progress, and a dictation control never stops a note.

## Consequences

- No extra permission prompt; the bubble works only while the accessibility service is on (the other two entry points work without it).
- The ongoing notification is not restored after a reboot until the app or the accessibility service starts (no boot receiver), and on Android 14 a user can swipe it away.
- The tile can stay active if the app's process is killed in the middle of a note.
- None of this has run on a device; starting the microphone from each entry point on Android 14 and 15 is the first thing to check ([p6](../specs/p6-android-note-mode.md)).

## Alternatives considered

- A `SYSTEM_ALERT_WINDOW` overlay: a new permission with a scary settings screen, for what the accessibility service can already do.
- One shared bubble that switches between dictation and note: the user would have to choose a mode before each use, and the single bubble hides when no text field has focus.
- Only the tile and the notification: slower to reach than a bubble on the screen.
