# 0019. Voice notes in SQLite, separate from history and meetings for now

Status: Accepted
Date: 2026-09-30

## Context

Vox needs quick voice notes that can be found, searched and filtered, and later synced between devices through the relay. Dictation history is an append-only JSONL file (no search, no edit, no delete of single items) and meetings are folders of JSON and Markdown; neither suits a searchable, syncable list.

## Decision

- Voice notes live in one SQLite database (`notes.db` in the Vox data folder, WAL mode) in `windows/notes.py`. Each note has a random 32-character id, `updated_at`, a `device` and a `deleted` flag. Deleting empties the content and keeps a marker row, so a later sync can tell other devices and no text stays behind.
- Search uses FTS5 (`"word"*` prefix match, every word must match) when the SQLite build has it, otherwise LIKE matching. Filters: time range, source, tag.
- A note is created by recording with the engine in note mode (tray menu or the Voice notes page): the text goes through the same speech and cleanup pipeline with no app name and the default style, and is saved instead of pasted.
- History and meetings are not migrated in this step; the Voice notes page lists notes only.

## Consequences

- Notes can be searched, edited and deleted one by one, and the row shape is ready for sync (ids, timestamps, tombstones).
- Three places now hold text the user dictated (history, meetings, notes); a unified list is later work.
- The database is not encrypted.

## Alternatives considered

- Another JSONL file: no search or single-item edits without rewriting the file.
- Migrating history and meetings into the database now: bigger and riskier, with no user-visible gain yet.
