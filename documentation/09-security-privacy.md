# 9. Security and privacy

What leaves the device, what is stored, what protects it, and what is still weak. The public promise to users is `docs/privacy.html`; keep it consistent with this page.

## Data that leaves the device

| Data | Goes to | When | Notes |
|---|---|---|---|
| Recorded audio (WAV) | The server (Groq or the user's own) | Every dictation and meeting | Never processed on the device |
| Transcript text | The server (chat model) | Cleanup, meeting notes and questions | |
| App name (Windows exe name such as `slack.exe`; Android app label) | The server, inside the cleanup prompt | Every cleaned dictation | Never the window title |
| Meeting title and attendee names | The server | Meeting notes | From the calendar or typed by the user |
| API key | The server, as `Authorization: Bearer` | Every request | Sent only to the configured address |
| Calendar read requests | Google, or the ICS host | Optional, Windows | Read-only scope `calendar.events.readonly` |

There is no analytics, crash reporting or Vox backend.

## What is stored, and how

| Item | Where | Protection |
|---|---|---|
| Windows API key | `%APPDATA%\Vox\config.json` | Windows DPAPI (`secret.py`), value `dpapi:<base64>`; only the same Windows user on the same PC can open it |
| Android API key | SharedPreferences `vox` | App-private storage only (no extra encryption); `allowBackup="false"` |
| History | `history.jsonl` (Windows) / SharedPreferences (Android) | Plain text. Switch it off with **Keep dictation history**; then nothing is saved |
| Meeting audio | `meetings\<id>\*.raw` | Deleted after the notes are written unless `keep_audio`; a crash can leave it behind |
| Meeting notes and transcripts | `%APPDATA%\Vox\meetings`, `Documents\Vox Notes` | Plain text |
| Google tokens | `google_token.json` | Plain JSON with default file permissions |
| Control token | `engine.json` | Random per run, deleted on quit; readable by the same Windows user |
| Failed dictation audio (Android) | `cache/vox_pending.wav` | Until sent, cancelled or the service stops |
| Failed dictation audio (Windows) | Memory only (`Engine.pending`) | Lost when Vox quits |

## Network rules

- Default server is `https://api.groq.com/openai/v1`.
- `endpoint_error` / `Endpoint.error`: the address must start with `http://` or `https://`; plain `http://` is accepted only for private hosts: loopback, 10/8, 172.16/12, 192.168/16, link-local, Tailscale `100.64.0.0/10`, IPv6 loopback/fc00::/7/fe80::/10, single-label names, `*.local`, `*.lan`, `*.ts.net`. Anything else needs `https://`, so the key and voice never cross the internet unencrypted ([decisions/0005-configurable-endpoint-private-http.md](decisions/0005-configurable-endpoint-private-http.md)).
- Android's network security config allows cleartext for the whole app because it cannot express ranges; the rule above is enforced in code (`ApiClient.open`) before every request.
- The Windows control server listens on `127.0.0.1` only, on a random port, and rejects requests without the per-run token.

## Android permissions and access

- `RECORD_AUDIO` for dictation; `INTERNET`; foreground-service permissions; notifications; vibration.
- The accessibility service reads the focused text field and the current app's package name to insert text. It never reads other screen content on purpose, but `canRetrieveWindowContent` is on because inserting text needs the field node. The bubble does nothing in password fields.
- `<queries>` for launcher apps is used only to show the Styles list.
- The WebView allows no file access; the page is loaded from assets and only the `Vox` bridge is exposed.

## Secrets in the repository and CI

- Never commit `config.json`, `google_client.json`, `client_secret*.json`, keystores (all in `.gitignore`).
- CI secrets: `GOOGLE_CLIENT_JSON`, `ANDROID_KEYSTORE_B64`, and optionally `ANDROID_KEYSTORE_PASS`. Without the keystore secret each CI run creates a new throwaway signing key.
- Workflow permissions are read-only except the `release` job; third-party Actions are pinned by commit SHA.

## Known gaps (also listed in [12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md))

- Android API key and all history are unencrypted at rest inside the app sandbox.
- History and meeting data on Windows are plain text; Google tokens are plain JSON.
- Dictated text is on the clipboard for a moment while it is pasted (clipboard history and other apps can read it). `keep_clipboard` defaults to false, so the old clipboard text is put back afterwards; with it on, or when the window changed and the text is only copied, the dictation stays on the clipboard.
- The API key goes to whatever address is configured; a tampered `config.json` could redirect it (mitigated only by the https/private rule).
- Whisper and the chat model are third parties; their handling of the data is covered by their own policies.
- `ACTION_SET_TEXT` rewrites the whole field, which can drop rich text or race with typing.

## Keys per role

`api_key`, `stt_api_key` and `llm_api_key` are all stored DPAPI-protected on Windows (`vox_core.KEY_FIELDS`). A role with its own server address never receives the main key: `role_settings` returns only that role's key for it, and `tests/test_providers.py` checks this. The model list request and the Test buttons go to the role's own address and are subject to the same private-host rule for plain http.

## The "About you" text

`user_context` is personal text the user writes. It is stored in the settings (Windows `config.json`, Android preferences; not encrypted) and sent, with each cleanup request, to the cleanup server only, never to the speech-to-text server. Users should not put secrets in it.

## Voice notes

Notes are stored in `%APPDATA%\Vox\notes.db` (SQLite, not encrypted; a deleted note keeps only an empty marker row). Recording a note sends the audio to the speech server and the text to the cleanup server exactly like a dictation, but without the name of the focused app. Nothing else leaves the device.

## The relay (server only so far)

`relay/relay.py` listens on `127.0.0.1` only and needs a random bearer token on every request (compared in constant time); the optional `owner` setting also checks the `Tailscale-User-Login` header, which `tailscale serve` sets but a local process could forge, so the token stays required. It is meant to be published to one's own tailnet with `tailscale serve` and never with Funnel. `relay.json` (token, and the AI server keys described below) and `relay.db` (notes, profile) are plain files; anyone who can read them, or who holds the token and can reach the relay, can read every note and the profile, including any API keys a client stores in it. There is no access log. Nothing in the apps sends data to the proxy routes yet (only the Windows app's note and profile sync uses the relay). When the Windows app runs the relay itself (tray item "Run relay on this PC"), the relay is a child process of Vox under the same Windows user, still bound to `127.0.0.1` only, with `relay.json` (the token and the AI server keys) and `relay.db` in `%APPDATA%\VoxRelay`; Vox never runs `tailscale` for the user, it only shows the `tailscale serve --bg PORT` command, and the only network thing it does itself is one connection to `127.0.0.1:PORT` to see if the port is taken. Details: [14-relay.md](14-relay.md).

### The relay's proxy routes

`POST /proxy/stt/audio/transcriptions`, `GET /proxy/stt/models`, `POST /proxy/llm/chat/completions` and `GET /proxy/llm/models` make the relay a small, fixed-purpose forwarder, so a client can use an AI server without holding its key. What was designed in, and what tests check (`tests/test_relay_proxy.py`, with a stand-in server that records everything it receives):
- **No open proxy.** The upstream URL is the address saved for the role plus a suffix fixed by the route; nothing from the request (path, query, headers, body) can change host, port, scheme or path. Only four exact method-and-path pairs work; `..`, encoded slashes, `//`, `;params`, queries and absolute-form targets either get 404 or land on the same fixed URL.
- **The token and the key stay apart.** The relay token (the client's `Authorization`) is never forwarded, and neither are `Tailscale-User-Login`, `X-Vox-Device`, cookies or forwarding headers: only `Content-Type`, `Accept`, `Content-Length`, `User-Agent: vox-relay` and the role's own key go upstream. A role's key is sent only to that role's address. The key is in no response, log, export or backup; if an upstream repeats it in an error message it is replaced with `***` before the answer goes on (keys shorter than 8 characters are not covered).
- **What the relay and the upstream see.** With these routes the relay holds the audio and text of each call in memory while it forwards it (nothing is written to disk or logged), and the upstream server sees them as before. The hop from the app to the relay is protected by `tailscale serve` (HTTPS); the hop to the upstream by its own scheme (`https` checks the certificate; plain `http` only for this machine, the local network and Tailscale).
- **Limits against abuse by a token holder.** Bodies are capped (25 MB audio, 1 MB chat) and read only after the size is known (`Content-Length` is required, chunked is refused, repeated or malformed values are refused); answers are capped at 8 MB; the whole exchange has a deadline (180, 60 or 15 s; name lookup, connecting, upload, wait and download all count, and a timer cuts the connection at the deadline, so an upstream that trickles bytes cannot keep a slot for ever; on Windows a read that is already waiting can run to its own timeout, so the total there can be almost twice the limit); at most 4 calls run at once and the fifth is refused at once. An upstream redirect is not followed. Answers are sent with `Content-Security-Policy: default-src 'none'; sandbox` and `nosniff`, so they are never a page on the relay's origin. Not covered: a slow uploader that keeps sending can hold one of the four slots (each read waits at most 30 s); the token is single-user, so this is a device you already trust.
- **Not checked.** A real speech or chat server, `https` upstreams with real certificates, the systemd unit's new `AF_INET6` setting on a real Pi, the deadline's cut on a real Linux machine (only CI runs those tests there) and on an `https` connection (the timer shuts down the TLS socket the same way, but only plain HTTP on localhost was run), and a phone through `tailscale serve` (the tests use HTTP on localhost and a fake `https` connection).

### The relay's management page

The page shell at `/` is public on whatever address the relay is published to (it contains no data and asks for the token); everything it shows comes from `/admin/*` and data endpoints that need the token. It is served with a Content-Security-Policy (per-response nonce, no framing, own origin only) and renders all data with `textContent`. The `/admin` endpoints share the token with the data endpoints, so any device that can sync could also download a backup, rotate the token or change the AI server settings. The token is stored in the browser (`sessionStorage`, or `localStorage` when "Remember on this device" is ticked). `/admin/profile` hides values whose key contains `key`, `token`, `secret` or `password`; the raw profile is still available to any client holding the token. On POSIX the data folder is `0700` and `relay.json` is created `0600`; on Windows those modes do nothing. The recent-requests list is in memory only and holds no note text or ids. The AI server tab stores an address and a key for each of speech to text and text cleanup in `relay.json` (plain text, `0600` on POSIX). The key is write-only: no endpoint, download, error message or output returns it (tests check `/admin/upstream`, status, activity, the masked profile, export, backup, `/profile` and the process output), so a device that only holds the token cannot read it back. It can, though, replace or clear it, and change the address; saving a different address without a new key removes the stored key, so the old key cannot be sent to a server someone else chose. Plain `http` is accepted only for private hosts (same rule as the apps), and addresses with a user name, password, query or fragment are refused. The relay's four proxy routes send requests to these servers (see the next section). The systemd unit (`relay/vox-relay.service`) runs the relay as a throw-away user with a private state folder and a hardened sandbox; that unit has not been tried on a real Raspberry Pi.

## Sync from the Windows app

With **Sync voice notes with my relay** on, the full text of every voice note (title, cleaned text, the raw transcript, tags, device name, times) and delete markers go to the relay address in Settings, and each request carries the relay token and this device's name. The address must pass the same rule as the provider address (plain http only for this PC, the local network and Tailscale). The token is stored DPAPI-protected in `config.json` like the API keys. Audio is never sent. Nothing syncs unless the switch is on; dictation history, meetings and the profile are not synced. A relay that is taken over (or a stolen token) exposes every note.

### Profile sync and API keys

While sync is on, the "About you" text, dictionary, people, default style, cleanup switch and language go to the relay. Provider settings and **API keys go to the relay only if the user turns on "Also share my provider settings and API keys"** (`relay_sync_keys`, off by default); turning it off removes them from the relay on the next sync. The relay stores the profile as plain JSON in `relay.db` and returns it to anyone with the token (its management page hides key values, its API does not), so while the switch is on the relay and its token are as sensitive as the keys themselves. Keys received from the relay are saved on this PC DPAPI-protected like any other key. A device that has not switched keys on ignores keys on the relay.
