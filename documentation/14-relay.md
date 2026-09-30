# 14. The relay

An optional server the user runs on their own machine so a phone and a PC can share voice notes and a profile. **This page describes what exists in the code today (P7a: the server only).** Nothing in the Windows or Android app talks to it yet.

## What it is

`windows/relay.py`: one Python file, standard library only, no Vox imports, so it runs on any machine with Python 3 (`python relay.py`). It listens on `127.0.0.1` only (there is no option to listen elsewhere). It is not part of the installer or the exe yet.

| Item | Detail |
|---|---|
| Start | `python relay.py [--data-dir DIR] [--port N] [--owner LOGIN] [--show-token]` |
| Data folder | default `%APPDATA%\VoxRelay` (or `~/.config/VoxRelay`): `relay.json` (token, port, owner) and `relay.db` (SQLite, WAL) |
| Reaching it | publish to the user's own tailnet: `tailscale serve --bg 8765` (HTTPS with a `*.ts.net` certificate, tailnet only). Never Funnel. |
| Auth | every request needs `Authorization: Bearer <token>` (constant-time compare). If `owner` is set, the `Tailscale-User-Login` header (added by `tailscale serve`) must match it. |
| Limits | body 1 MB; note text 100,000 characters; 20 tags; profile 64 KB |
| Logging | no access log (paths carry search words and note ids) |

## Protocol (JSON)

| Request | Meaning |
|---|---|
| `GET /health` | `{ok, notes, seq}` |
| `GET /changes?since=SEQ&limit=N` | notes and delete markers written after `SEQ`, oldest first: `{notes, next, more}`. Clients keep `next` as their cursor. |
| `PUT /notes/{id}` | upsert one note (body = the note). The id in the path wins. Answer `{note, applied}`; `applied` is false when a newer or identical version is already stored. |
| `GET /notes/{id}` | one note (404 if unknown or deleted) |
| `DELETE /notes/{id}` | turns the note into a marker: content, title and tags removed, `deleted: true`, new sequence number |
| `GET /notes?q=&tag=&from=&to=&limit=` | search (words match as word starts, all must match; times are epoch seconds), newest first, deleted notes excluded |
| `GET /profile` | `{version, data}`; version 0 and empty data before the first save |
| `PUT /profile` with `If-Match: VERSION` | replaces the profile if `VERSION` is current (`0` or `*` for the first save). 200 with the new version; 412 with the current profile when stale; 428 when `If-Match` is missing. |

Note fields: `id` (32 lowercase hex characters, made by the client), `source`, `title`, `text`, `raw`, `created_at`, `updated_at` (epoch seconds), `secs`, `device`, `tags` (list), `deleted`; the relay adds `seq`.

## Rules

- **Conflicts:** the write with the larger `updated_at` wins; on a tie the later arrival wins; an identical repeat changes nothing and gets no new `seq`. A delete marker written with a later `updated_at` beats an older edit.
- **Sequence numbers:** the relay assigns `seq` (SQLite `AUTOINCREMENT`), so clients never compare clocks with each other to know what is new.
- **Deletes:** kept as markers with no content so other devices learn about them; nothing is purged yet.
- **Profile:** an opaque JSON object; the relay does not look inside. If clients put API keys in it, they sit in `relay.db` unencrypted (see [09-security-privacy.md](09-security-privacy.md)).

## Not built yet

Clients (Windows sync loop, Android outbox), audio blobs, keys served to devices, proxy mode, a tray toggle or `Vox.exe --relay`, purging old markers, `tailscale serve` set-up help in the app. Decision record: [decisions/0020-relay-design.md](decisions/0020-relay-design.md).
