# 0040. The relay stores the sent note time and orders writes by a bounded copy of it

Status: Accepted (tested with two in-memory devices against the real relay in Python and Java; not run on a real relay or phone)
Date: 2026-10-02

## Context

Issue #44/#49 (stream C of the final pass): a phone with a fast clock could pin a note, because its far-ahead `updated_at` beat every later write. The fix then cut the **stored** time back to the relay clock plus 5 s (`MAX_CLOCK_SKEW`). The final review (relay-docs 1) found that this split devices for good: both apps keep their own sent `updated_at` after the relay answers `applied: true`, so the fast phone held a time an hour ahead of what the relay stored, and every later edit or delete from another device looked older to it (`notes.apply_remote`, `NoteLogic.remoteWins`) and was dropped. The phone's copy was clean, so it was never sent again.

## Decision

- **`updated_at` is stored as the device sent it** (still refused when more than a day ahead, `MAX_CLOCK_AHEAD`). The device that wrote a version and the relay agree on its time, whatever the device clock says.
- **The order is decided by a separate column, `order_at`**: the sent time cut back to the relay clock plus `MAX_CLOCK_SKEW` at the moment of the write. It is never sent to clients (`RelayStore._row` drops it). A database of an older relay gets the column on open (`ALTER TABLE`); its rows have none and fall back to `updated_at`, so the `RECENT_WRITE` rule for rows stored far ahead stays.
- **A write that wins is raised just above the stored `updated_at`** when its own time is not larger, so every device that holds the old version, the fast phone included, takes the new one. The management page's delete always replaces the stored version.

## Consequences

- All devices converge with any client version: no client change was needed, so a phone or PC that is not updated still converges with an updated relay.
- A fast phone still cannot pin a note: its write counts as no later than the relay clock plus 5 s when writes are compared.
- The `updated_at` a device sees can be ahead of real time (a winning write raised above a fast phone's time). It orders writes and the queue of unsent notes; the notes lists sort by `created_at` (checked in `notes.search`).
- An old offline edit from a fast phone counts as "now" when it finally arrives (as before the change: the bound uses the relay clock at the write).

## Alternatives considered

- **Clients adopt the stored time** (`mark_synced` sets `updated_at` to the relay's): the reviewer's suggestion. Needs the same change in `notes.py`, `NotesStore`, `SyncStore` and `SyncEngine`, and a phone or PC that is not updated keeps diverging from an updated relay.
- **Drop the bound and rely only on the `RECENT_WRITE` replace rule:** converges, but then any write from the last 5 minutes beats a note from a fast phone even when it was made earlier (an existing last-writer-wins test failed).
