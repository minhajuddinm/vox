# Spec P5: voice notes on Windows

Status: Implemented on branch `feat/voice-notes`. Date: 2026-09-30. Decision record: [0019](../decisions/0019-voice-notes-in-sqlite.md).

## Goal
Record a quick note by voice on Windows, then find it again by search and filters.

## Design
- Store: `windows/notes.py` (SQLite, FTS5 with LIKE fallback): `add`, `get`, `update`, `delete`, `search(query, source, since, until, tag, limit)`, `count`.
- Recording: `Engine.toggle_note` (tray item "New voice note" / "Finish voice note", control endpoints `/note/toggle` and `/note/status`, used by the window). It records hands-free; Esc cancels; the dictation hotkey also finishes it. `Engine._process(pcm, exe, note)` saves the note instead of pasting; the app name is not sent. A failed note is kept for "Retry last dictation" and stays a note (`pending` is now `(pcm, exe, note)`).
- Window: a Voice notes page with a New voice note button, search box, time filter and cards with Copy, Edit and Delete (`Api.notes_list`, `note_edit`, `note_delete`, `note_toggle`, `note_status`).

## Not in this step
Android notes and note mode (P6), one list for dictations and meetings, tags in the UI (the store supports them), audio files, sync (P7), a hotkey for notes.

## Done when
Tests pass (`tests/test_notes.py`, `tests/test_engine_notes.py`); on a PC a note can be recorded from the tray and from the page, appears in the list, can be searched, edited, copied and deleted. Not seen on a screen yet.
