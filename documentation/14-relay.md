# 14. The relay

An optional server the user runs on their own machine (PC, Raspberry Pi, any Linux box) so a phone and a PC can share voice notes and a profile. **This page describes what exists in the code today: the server, its management page, and the Windows client (voice notes only). The Android app does not use it yet.** Set-up steps for a Raspberry Pi are in [../relay/README.md](../relay/README.md).

## What it is

`relay/relay.py`: one Python file, standard library only, no Vox imports, Python 3.9 or newer. It runs on Linux (including a Raspberry Pi 5, arm64), macOS and Windows; CI runs its tests on Python 3.9 and 3.13 on x86 Linux and on Python 3.13 on arm64 Linux. It listens on `127.0.0.1` only (there is no option to listen elsewhere). It is not part of the installer or the exe.

| Item | Detail |
|---|---|
| Start | `python relay.py [--data-dir DIR] [--port N] [--owner LOGIN] [--show-token]`; stops cleanly on Ctrl+C and on SIGTERM (systemd) |
| Files | `relay/relay.py`, `relay/vox-relay.service` (systemd unit for Linux), `relay/README.md` (set-up guide) |
| Data folder | Windows `%APPDATA%\VoxRelay`, macOS `~/Library/Application Support/VoxRelay`, elsewhere `$XDG_DATA_HOME/vox-relay` (default `~/.local/share/vox-relay`): `relay.json` (token, port, owner, AI server settings) and `relay.db` (SQLite, WAL). On POSIX the folder is `0700` and `relay.json` is created `0600`. |
| Reaching it | publish to the user's own tailnet: `tailscale serve --bg 8765` (HTTPS with a `*.ts.net` certificate, tailnet only). Never Funnel. |
| Auth | every data request needs `Authorization: Bearer <token>` (constant-time compare). If `owner` is set, the `Tailscale-User-Login` header (added by `tailscale serve`) must match it. |
| Limits | body 1 MB (a bigger upload is read and dropped, up to 5 MB, then answered 413 so the client sees the answer); note text 100,000 characters; 20 tags; profile 64 KB; a connection that stalls for 30 s is dropped |
| Logging | no access log (paths carry search words and note ids); the management page keeps the last 100 requests in memory only (method, path with ids replaced, result, device name) |

## Management page

`GET /` (or `/ui`) serves one HTML page with no data in it and no login of its own: it asks for the token in the browser and then calls the JSON endpoints below with it. The token is kept in `sessionStorage`, or in `localStorage` if "Remember on this device" is ticked. Tabs:

| Tab | Shows or does |
|---|---|
| Overview | notes, delete markers, database size, sync position, uptime, requests, errors, refused (bad token) requests, profile version, platform and Python, the `tailscale serve` command; refreshes every 10 s |
| Notes | search, list, delete one note, export everything as JSON |
| Devices and activity | devices that sent `X-Vox-Device` (last seen, request count, Tailscale user) and the last 100 requests |
| Profile | the profile with keys, tokens, secrets and passwords hidden (any JSON key containing `key`, `token`, `secret` or `password`) |
| AI server (proxy) | one block per role (speech to text, text cleanup): an address field, "key set: yes/no", a write-only key field, Save and Clear; the key is never shown again |
| Maintenance | download a backup of the database, compact it, purge old delete markers, make a new token (the old one stops working at once) |

The page is served with a Content-Security-Policy that allows only its own inline script and style through a fresh nonce per response, no framing, `connect-src 'self'`; it builds the page with `textContent` only, so note text is never interpreted as HTML (a test and a browser check with a `<script>` note confirm this).

## Protocol (JSON)

| Request | Meaning |
|---|---|
| `GET /health` | `{ok, version, notes, seq}` |
| `GET /changes?since=SEQ&limit=N` | notes and delete markers written after `SEQ`, oldest first: `{notes, next, more}`. Clients keep `next` as their cursor. |
| `PUT /notes/{id}` | upsert one note (body = the note). The id in the path wins. Answer `{note, applied}`; `applied` is false when a newer or identical version is already stored. |
| `GET /notes/{id}` | one note (404 if unknown or deleted) |
| `DELETE /notes/{id}` | turns the note into a marker: content, title and tags removed, `deleted: true`, new sequence number |
| `GET /notes?q=&tag=&from=&to=&limit=` | search (words match as word starts, all must match; times are epoch seconds), newest first, deleted notes excluded |
| `GET /profile` | `{version, data}`; version 0 and empty data before the first save |
| `PUT /profile` with `If-Match: VERSION` | replaces the profile if `VERSION` is current (`0` or `*` for the first save). 200 with the new version; 412 with the current profile when stale; 428 when `If-Match` is missing. |
| `GET /admin/status` | numbers for the Overview tab |
| `GET /admin/activity` | `{events, devices}` |
| `GET /admin/profile` | the profile with secrets hidden |
| `GET /admin/export` | all live notes as a JSON download |
| `GET /admin/backup` | a consistent copy of `relay.db` (SQLite backup API) as a download |
| `GET /admin/upstream` | `{stt: {base_url, key_set}, llm: {base_url, key_set}}`: the AI server address of each role and whether a key is stored; never the key |
| `PUT /admin/upstream` with `{role, base_url, api_key?}` | sets one role (`stt` or `llm`); `api_key` omitted keeps the stored key (only while the address is unchanged), `""` clears it; `base_url` `""` clears the address and the key. Answer: the same shape as the GET. 400 with a message for a bad role, address or key; 409 when the relay has no data folder. |
| `POST /admin/vacuum` | compacts the database |
| `POST /admin/purge` with `{days}` | forgets delete markers older than `days` |
| `POST /admin/rotate-token` | writes a new token to `relay.json` and adopts it; answer `{token}` |

Any client may send `X-Vox-Device: <name>` on every request so the page can show which devices use the relay. The `/admin` endpoints use the same token as the data endpoints (single user).

Note fields: `id` (32 lowercase hex characters, made by the client), `source`, `title`, `text`, `raw`, `created_at`, `updated_at` (epoch seconds), `secs`, `device`, `tags` (list), `deleted`; the relay adds `seq`.

## Rules

- **Conflicts:** the write with the larger `updated_at` wins; on a tie the later arrival wins; an identical repeat changes nothing and gets no new `seq`. A delete marker written with a later `updated_at` beats an older edit.
- **Sequence numbers:** the relay assigns `seq` (SQLite `AUTOINCREMENT`), so clients never compare clocks with each other to know what is new.
- **Deletes:** kept as markers with no content so other devices learn about them. Purging markers means a device that was offline for longer than the purge age could bring a deleted note back.
- **Profile:** an opaque JSON object; the relay does not look inside. If clients put API keys in it, they sit in `relay.db` unencrypted (see [09-security-privacy.md](09-security-privacy.md)).

## AI server settings (proxy mode, settings only)

Each role, `stt` (speech to text) and `llm` (text cleanup), can have an address and a key for an OpenAI-compatible server. They live in `relay.json` under `upstream` (see [07-config-and-data.md](07-config-and-data.md)); `rotate-token` and every save keep the rest of the file. **Nothing uses them yet:** the proxy routes that would send requests to these servers are the next step, so today the relay stores the settings and shows whether a key is set.

- **Address rules** (`upstream_problem` in `relay.py`; a short copy of the app's rule in `windows/vox_core.py`, because the relay cannot import app code): `http` or `https` only, a host, no user name or password, no query and no fragment (the relay will add fixed paths), no spaces or non-ASCII characters, at most 2048 characters. Plain `http` only for this machine, the home or office network and Tailscale (loopback, private and link-local addresses, `100.64.0.0/10`, one-label names, `.local`, `.lan` and `.ts.net` names); any other server needs `https`. A trailing `/` is dropped. The error messages never repeat the address.
- **The key is write-only.** It is stored in `relay.json` in plain text (mode `0600` on POSIX), used only by the relay, and returned by no endpoint: not by `/admin/upstream` (only `key_set`), `/admin/status`, `/admin/activity`, `/admin/profile`, `/admin/export`, `/admin/backup` (the database does not hold it), `/profile`, error messages or the process output; tests check each of these. A key is 1 to 1024 printable ASCII characters without spaces (surrounding spaces are trimmed).
- **A key belongs to its address.** Saving a different address without a new key removes the stored key, so a device that holds the token cannot point a role at another server and have the old key sent there. To move a role and keep working, send the new address together with its key.
- Saves are written through a temporary file and a rename, one at a time, so `relay.json` is never half written.

## Clients

- **Windows:** `windows/sync.py` (settings `relay_sync`, `relay_url`, `relay_token`, `device_name`). It sends changed voice notes with `PUT /notes/{id}`, fetches `GET /changes` from its stored cursor and merges by `updated_at`; it runs at start, every 90 seconds and after each saved note, and sends `X-Vox-Device`. See [decisions/0022-sync-client-dirty-flag-and-cursor.md](decisions/0022-sync-client-dirty-flag-and-cursor.md).
- **Profile:** the same client also syncs the profile document (`GET`/`PUT /profile` with `If-Match`): a fixed set of settings merged field by field, provider settings and keys only when `relay_sync_keys` is on. See [decisions/0023-profile-sync-three-way-merge.md](decisions/0023-profile-sync-three-way-merge.md).
- **Android:** not built.

## Not built yet

Android client and outbox, syncing dictation history, meetings and per-app styles, audio blobs, the rest of proxy mode (the routes that make the speech and cleanup calls so keys never leave the relay; only the settings exist), a tray toggle or `Vox.exe --relay`, restoring a backup from the page, `tailscale serve` set-up help inside the apps. Verified only on Windows and, through CI, on x86 and arm64 Linux; the systemd unit, the Raspberry Pi steps and a real phone or PC reaching the relay through `tailscale serve` have not been tried. Decision records: [decisions/0020-relay-design.md](decisions/0020-relay-design.md), [decisions/0021-relay-portable-with-a-web-page.md](decisions/0021-relay-portable-with-a-web-page.md).
