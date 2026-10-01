# Spec P7c: Windows sync client

Status: Implemented on branch `feat/sync-windows`. Date: 2026-09-30. Decision record: [0022](../decisions/0022-sync-client-dirty-flag-and-cursor.md). Relay protocol: [14-relay.md](../14-relay.md).

## Goal
Voice notes recorded on Windows appear on the other devices through the relay, and notes from other devices appear here, without the notes ever depending on the relay being reachable.

## Design
- `windows/notes.py`: columns `dirty` and `seq` (added to existing databases on first open), table `sync_meta`, and `dirty_notes`, `mark_synced`, `apply_remote`, `get_meta`, `set_meta`.
- `windows/sync.py`: `sync_once(cfg)`, `test_relay(url, token)`, `SyncWorker`, `device_name`.
- Engine: starts the worker, triggers it when a note is saved, stops it on quit, control endpoints `/sync/now` and `/sync/status`.
- Window: Settings block "Sync between devices" (switch, address, token, device name, Test) and, on the Voice notes page, a status line and "Sync now"; editing or deleting a note asks the engine to sync.

## Not in this step
Android side, syncing dictation history, meetings and the profile (About you, dictionary, API keys), audio, an installer-friendly way to start a relay.

## Done when
Tests pass (`tests/test_sync.py` runs two devices against a real relay on localhost); a user can enter the relay address and token, press Test, record a note and see it on the relay's page. Not seen on a screen yet.
