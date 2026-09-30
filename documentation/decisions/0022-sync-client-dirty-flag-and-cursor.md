# 0022. Sync client: a dirty flag per note and the relay's cursor

Status: Accepted
Date: 2026-09-30

## Context

The Windows app must share voice notes through the relay ([0020](0020-relay-design.md)) without ever depending on it: notes work offline, and two devices may edit the same note. The engine and the window are separate processes that both use `notes.db`.

## Decision

- Each local note has `dirty` (1 = changed here, not yet accepted by the relay) and `seq` (the relay's sequence number when known). Adding, editing and deleting set `dirty`; notes from before sync existed count as changed and are sent on the first sync.
- A sync run sends every dirty note (`PUT /notes/{id}`), clearing `dirty` only if the note was not changed again while it was being sent (`mark_synced` compares `updated_at`), then fetches `GET /changes?since=<cursor>` in pages and merges each note: the newer `updated_at` wins, equal versions change nothing (so a note we just sent is not applied twice), a delete marker for a note this device never had is ignored. The cursor is kept in `sync_meta` (`relay_cursor`) and advanced page by page.
- The engine owns one background thread (`SyncWorker`): sync at start, every 90 seconds, and whenever a note is saved; the window asks it through the control server (`/sync/now`, `/sync/status`) after an edit or delete and for the "Sync now" button. A failed run only sets a message; nothing is lost and the next run retries.
- Settings: `relay_sync`, `relay_url` (same http/https rule as the provider address), `relay_token` (DPAPI-protected like API keys), `device_name`.
- Only voice notes sync in this step: not dictation history, meetings or the profile.

## Consequences

- Works with the relay off, unreachable or refusing; edits made offline are sent later.
- Last writer wins by device clocks on `updated_at`: a device with a wrong clock can overwrite newer edits. The relay's sequence numbers only decide what is new, not who wins.
- Two edits to the same note at nearly the same time lose the older one.
- A note pushed but refused because the relay holds a newer version is replaced by the relay's version (the local edit is lost).

## Alternatives considered

- Comparing whole notes or hashes to find changes: more work per sync, no benefit for a few thousand notes.
- Syncing from the window process: it is not always running; the engine is.
- Vector clocks or CRDT text merging: too much for one user's short notes.
