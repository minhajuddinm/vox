# 9. Security and privacy

What leaves the device, what is stored, what protects it, and what is still weak. The public promise to users is `docs/privacy.html`; keep it consistent with this page.

## Data that leaves the device

| Data | Goes to | When | Notes |
|---|---|---|---|
| Recorded audio (WAV; on Android AAC in m4a for clips of 4 s or more) | The speech server (Groq or the user's own) | Every dictation and meeting | Never processed on the device. On Windows a long recording is sent in pieces while the user is still speaking (`stream_stt`, default on); Android does the same since v2 part 3 branch B (not run on a phone yet) |
| Transcript text | The cleanup server (chat model) | Cleanup, meeting notes and questions | Skipped when cleanup is off, the style is Raw, or the text is under `cleanup_min_words` |
| Dictionary and people | The speech server (Whisper `prompt`, at most 600 characters) and the cleanup server (system prompt) | Every dictation | `vox_core.transcribe`, `ApiClient.transcribe` |
| Language setting; on Windows the last 150 characters of the previous piece of a long recording | The speech server | Every dictation | `streaming.CONTEXT_CHARS` |
| "About you" text (`user_context`) | The cleanup server only | Every cleaned dictation | Never the speech server |
| Voice notes, profile (see "Sync from the apps") | The user's relay | Only with `relay_sync` on | Full text, raw transcript, tags, device name; profile fields; provider settings and keys only with `relay_sync_keys` |
| Speech and cleanup calls (Windows and Android) | The user's relay, which forwards them | Only with `relay_proxy` on | Audio and cleanup text transit the relay |
| App name (Windows exe name such as `slack.exe`; Android app label) | The server, inside the cleanup prompt | Every cleaned dictation | Never the window title |
| Meeting title, attendee names, organizer, and the user's own name (`your_name`, default "You") | The cleanup server; the speech server gets the first 12 attendee names as a Whisper hint | Meeting notes, live questions, speaker guesses | From the calendar or typed by the user. Attendee names are the display name, else the email's part before the `@` (not the address). The organizer is the display name, else (Google events) the raw email address; it is left out when the organizer is the user (Google). `Meeting._context`, `_context_prompt`, `_label` |
| API key | The server, as `Authorization: Bearer` | Every request | Sent only to the configured address |
| Calendar read requests | Google, or the ICS host | Optional, Windows | Read-only scope `calendar.events.readonly` |

There is no analytics, crash reporting or Vox backend. Audio and text go only to servers the user chose: the speech server, the cleanup server and, if switched on, the relay.

## What is stored, and how

| Item | Where | Protection |
|---|---|---|
| Windows API key | `%APPDATA%\Vox\config.json` | Windows DPAPI (`secret.py`), value `dpapi:<base64>`; only the same Windows user on the same PC can open it |
| Android API keys and relay token | SharedPreferences `vox` | App-private storage only (no extra encryption); `allowBackup="false"`. The relay token (`relay_token`) is handled like the API key: never logged, sent only to the relay's own address |
| History | `history.jsonl` (Windows) / SharedPreferences (Android) | Plain text. Switch it off with **Keep dictation history**; then nothing is saved. Each line also carries its `timing` (stage times in milliseconds, model names, provider host or `relay`) for the Speed card; it is never sent or synced ([decision 0031](decisions/0031-timings-stay-on-the-device.md)) |
| Meeting audio | `meetings\<id>\*.raw` | Deleted after the notes are written unless `keep_audio`; a crash can leave it behind |
| Meeting notes and transcripts | `%APPDATA%\Vox\meetings`, `Documents\Vox Notes` | Plain text |
| Google tokens | `google_token.json` | Plain JSON with default file permissions |
| Control token | `engine.json` | Random per run, deleted on quit; readable by the same Windows user |
| Failed dictation audio (Android) | `cache/vox_pending_<id>_<dest>.wav` (up to 5) | Until sent, cleared in the notification, cancelled (that recording only), dropped as the oldest, or 7 days old; kept across a service stop |
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

- Android API keys, the relay token and all history are unencrypted at rest inside the app sandbox.
- History and meeting data on Windows are plain text; Google tokens are plain JSON.
- Dictated text is on the clipboard for a moment while it is pasted (clipboard history and other apps can read it). `keep_clipboard` defaults to false, so the earlier clipboard text is put back afterwards (an image or files on the clipboard cannot be read as text and are not restored); with it on, or when the window changed and the text is only copied, the dictation stays on the clipboard. The "window changed" check compares only the program's exe name, so two windows of the same program (for example two Chrome or Slack windows) are not told apart and the paste goes ahead.
- The API key goes to whatever address is configured; a tampered `config.json` could redirect it (mitigated only by the https/private rule).
- Whisper and the chat model are third parties; their handling of the data is covered by their own policies.
- `ACTION_SET_TEXT` rewrites the whole field, which can drop rich text or race with typing.
- Relay upstream keys are plain text in `relay.json` (see "What proxy mode means, plainly"); a relay token holder can make the relay fetch from any address the rule allows.

## Keys per role

`api_key`, `stt_api_key` and `llm_api_key` are all stored DPAPI-protected on Windows (`vox_core.KEY_FIELDS`). A role with its own server address never receives the main key: `role_settings` returns only that role's key for it, and `tests/test_providers.py` checks this. The model list request and the Test buttons go to the role's own address and are subject to the same private-host rule for plain http.

## The "About you" text

`user_context` is personal text the user writes. It is stored in the settings (Windows `config.json`, Android preferences; not encrypted) and sent, with each cleanup request, to the cleanup server only, never to the speech-to-text server. Users should not put secrets in it.

## Voice notes

Notes are stored in `%APPDATA%\Vox\notes.db` (SQLite, not encrypted; a deleted note keeps only an empty marker row). Recording a note sends the audio to the speech server and the text to the cleanup server exactly like a dictation, but without the name of the focused app (the note branch passes an empty label). The note is stored on the Android phone in `notes.db` in the app's private database folder (`NotesStore`, not encrypted). Notes leave the device only with `relay_sync` on: then the full text, raw transcript, tags and device name go to the relay (see "Sync from the apps").

## The relay

`relay/relay.py` listens on `127.0.0.1` only and needs a random bearer token on every request (compared in constant time); the optional `owner` setting also checks the `Tailscale-User-Login` header, which `tailscale serve` sets but a local process could forge, so the token stays required. It is meant to be published to one's own tailnet with `tailscale serve` and never with Funnel. `relay.json` (token, and the AI server keys described below) and `relay.db` (notes, profile) are plain files; anyone who can read them, or who holds the token and can reach the relay, can read every note and the profile, including any API keys a client stores in it. There is no access log. The apps send data to a relay only when sync is switched on (see "Sync from the apps" below); the Windows and Android apps also send their speech and cleanup calls to it when "Use my relay as the AI server" (`relay_proxy`) is on (see "The relay as the AI server" below). Nothing in the apps contacts a relay unless one of these switches is on or you press Test next to the relay address or Refresh on the Devices card (and the address and token are filled in); the Devices card then asks `GET /devices` and shows only each device's name, whether it is this one, and when it was last seen. Sync and Test requests carry the device name in an `X-Vox-Device` header. When the Windows app runs the relay itself (tray item "Run relay on this PC"), the relay is a child process of Vox under the same Windows user, still bound to `127.0.0.1` only, with `relay.json` (the token and the AI server keys) and `relay.db` in `%APPDATA%\VoxRelay`; Vox never runs `tailscale` for the user, it only shows the `tailscale serve --bg PORT` command, and the only network thing it does itself is one connection to `127.0.0.1:PORT` to see if the port is taken. Details: [14-relay.md](14-relay.md).

### The relay's proxy routes

`POST /proxy/stt/audio/transcriptions`, `GET /proxy/stt/models`, `POST /proxy/llm/chat/completions` and `GET /proxy/llm/models` make the relay a small, fixed-purpose forwarder, so a client can use an AI server without holding its key. What was designed in, and what tests check (`tests/test_relay_proxy.py`, with a stand-in server that records everything it receives):
- **No open proxy.** The upstream URL is the address saved for the role plus a suffix fixed by the route; nothing from the request (path, query, headers, body) can change host, port, scheme or path. Only four exact method-and-path pairs work; `..`, encoded slashes, `//`, `;params`, queries and absolute-form targets either get 404 or land on the same fixed URL.
- **The token and the key stay apart.** The relay token (the client's `Authorization`) is never forwarded, and neither are `Tailscale-User-Login`, `X-Vox-Device`, cookies or forwarding headers: only `Content-Type`, `Accept`, `Content-Length`, `User-Agent: vox-relay` and the role's own key go upstream. A role's key is sent only to that role's address. The key is in no response, log, export or backup; if an upstream repeats it in an error message it is replaced with `***` before the answer goes on (keys shorter than 8 characters are not covered).
- **What the relay and the upstream see.** With these routes the relay holds the audio and text of each call in memory while it forwards it (nothing is written to disk or logged), and the upstream server sees them as before. The hop from the app to the relay is protected by `tailscale serve` (HTTPS); the hop to the upstream by its own scheme (`https` checks the certificate; plain `http` only for this machine, the local network and Tailscale).
- **Limits against abuse by a token holder.** Bodies are capped (25 MB audio, 1 MB chat) and read only after the size is known (`Content-Length` is required, chunked is refused, repeated or malformed values are refused); answers are capped at 8 MB; the whole exchange has a deadline (180, 240 or 15 s; name lookup, connecting, upload, wait and download all count, and a timer cuts the connection at the deadline, so an upstream that trickles bytes cannot keep a slot for ever; on Windows a read that is already waiting can run to its own timeout, so the total there can be almost twice the limit); at most 4 calls run at once and the fifth is refused at once; a client that hangs up gives its slot back within about a quarter of a second (the relay polls the client's connection while it waits), so abandoned requests do not fill the slots. An upstream redirect is not followed. Answers are sent with `Content-Security-Policy: default-src 'none'; sandbox` and `nosniff`, so they are never a page on the relay's origin. Not covered: a slow uploader that keeps sending can hold one of the four slots (each read waits at most 30 s); the token is single-user, so this is a device you already trust.
- **Not checked.** A real speech or chat server, `https` upstreams with real certificates, the systemd unit's new `AF_INET6` setting on a real Pi, the deadline's cut on a real Linux machine (only CI runs those tests there) and on an `https` connection (the timer shuts down the TLS socket the same way, but only plain HTTP on localhost was run), and a phone through `tailscale serve` (the tests use HTTP on localhost and a fake `https` connection).

### What proxy mode means, plainly

Read this before switching "Use my relay as the AI server" on ([decisions/0027-relay-proxy-per-role-whitelisted-write-only-keys.md](decisions/0027-relay-proxy-per-role-whitelisted-write-only-keys.md), [specs/p7f-relay-proxy.md](specs/p7f-relay-proxy.md)).
- **Audio and text transit the relay.** With the switch on, every dictation's audio and every cleanup text (and model-list and Test calls) go to the relay first and from there to the AI server. The relay holds them in memory while it forwards them; it writes and logs none of it. Whoever controls the relay machine can see them. Over `tailscale serve` the hop from the app is HTTPS; on a tailnet address with plain `http` it is not encrypted by Vox itself.
- **The upstream keys sit in plain text in `relay.json`.** The file is created with mode `0600` and its folder `0700` on Linux and macOS (POSIX only). On Windows those modes do nothing and no access control list is set, so any program of the same Windows user, and an administrator, can read the file. A backup or export does not contain the keys, but a copy of the data folder does.
- **A token holder can point a role anywhere the address rule allows, and read the answer.** `PUT /admin/upstream` needs only the relay token. Someone with the token can set a role's address to any loopback, LAN, link-local or tailnet `http` address (and any `https` address), as long as the request path ends in `/models`, `/chat/completions` or `/audio/transcriptions` (the fixed suffixes: `GET /models`, `POST /chat/completions`, `POST /audio/transcriptions`), and then make the relay send a GET or POST there and read the response (up to 8 MB). That reaches things on the relay's own machine and network that the token holder could not reach directly. This is acceptable for the single-user token model, where every device that has the token is the user's own; it is not safe to share the token with anyone you would not trust on your network. Changing the address without a new key removes the stored key, so the old key cannot be sent to a server the token holder chose.
- **An upstream 401 or 403 passes through unchanged.** A wrong key stored on the relay therefore looks, in the app, the same as a wrong relay token. The apps say so in their message ("check the relay token and the AI server key set on the relay page").
- **What was not verified:** a real speech or chat server, `https` upstreams with real certificates, Python 3.9 at runtime, Linux, the systemd unit with `AF_INET6` on a Pi, and a phone through `tailscale serve` (see "Not checked" above).

### The relay's management page

The page shell at `/` is public on whatever address the relay is published to (it contains no data and asks for the token); everything it shows comes from `/admin/*` and data endpoints that need the token. It is served with a Content-Security-Policy (per-response nonce, no framing, own origin only) and renders all data with `textContent`. The `/admin` endpoints share the token with the data endpoints, so any device that can sync could also download a backup, rotate the token or change the AI server settings. The token is stored in the browser (`sessionStorage`, or `localStorage` when "Remember on this device" is ticked). `/admin/profile` hides values whose key contains `key`, `token`, `secret` or `password`; the raw profile is still available to any client holding the token. On POSIX the data folder is `0700` and `relay.json` is created `0600`; on Windows those modes do nothing. The recent-requests list is in memory only and holds no note text or ids. The AI server tab stores an address and a key for each of speech to text and text cleanup in `relay.json` (plain text, `0600` on POSIX). The key is write-only: no endpoint, download, error message or output returns it (tests check `/admin/upstream`, status, activity, the masked profile, export, backup, `/profile` and the process output), so a device that only holds the token cannot read it back. It can, though, replace or clear it, and change the address; saving a different address without a new key removes the stored key, so the old key cannot be sent to a server someone else chose. Plain `http` is accepted only for private hosts (same rule as the apps), and addresses with a user name, password, query or fragment are refused. The relay's four proxy routes send requests to these servers (see the next section). The systemd unit (`relay/vox-relay.service`) runs the relay as a throw-away user with a private state folder and a hardened sandbox; that unit has not been tried on a real Raspberry Pi.

### The relay as the AI server

With **Use my relay as the AI server** (`relay_proxy`) on and the relay address and token filled in, the app (Windows and Android) sends each dictation's audio, and the text to be cleaned up, to `<relay address>/proxy/stt` and `<relay address>/proxy/llm` instead of to the AI provider; the relay forwards them (see the proxy routes above), so the provider key is held only on the relay. Rules the code keeps (tests in `tests/test_providers.py` record every request the app makes, through all the paths: model lists, both Test buttons, the warm-up, a dictation, a cleanup): the only requests are to the four relay routes; each carries only the relay token; no provider key (`api_key`, `stt_api_key`, `llm_api_key`) is in any request to the relay; and with the switch off the relay token is sent to no provider. The relay address passes the same rule as every address (`vox_core.endpoint_error`: plain http only for this PC, the local network and Tailscale), so the token does not cross the internet in the clear. The provider keys stay stored on this PC (DPAPI-protected) unless the user clears them; the switch only stops them being used. The relay sees the audio and text of every call (it holds them in memory while forwarding), as the provider does.

## Sync from the apps

With **Sync voice notes with my relay** on, the full text of every voice note (title, cleaned text, the raw transcript, tags, device name, times) and delete markers go to the relay address in Settings, and each request carries the relay token and this device's name. The address must pass the same rule as the provider address (plain http only for this PC, the local network and Tailscale). The token is stored DPAPI-protected in `config.json` like the API keys. Audio is never sent. Nothing syncs unless the switch is on; dictation history and meetings are never synced (the profile is: see below). A relay that is taken over (or a stolen token) exposes every note. The Android app does the same with its own settings (`relay_sync`, `relay_url`, `relay_token`, `device_name`): every request carries `Authorization: Bearer <token>` and `X-Vox-Device`, the token goes only to the address it was saved with, and `RelayClient` never follows a redirect (a `Location` header could otherwise carry the token to another server). The token and the address are not in any message or log line; the log (`vox`, debug) has only counts and "ok" or "failed". The token is kept like the API key, in the app-private preferences without further encryption. The Android client has not run on a phone yet.

### Profile sync and API keys

While sync is on, the "About you" text, dictionary, people, default style, cleanup switch and language go to the relay. Provider settings and **API keys go to the relay only if the user turns on "Also share my provider settings and API keys"** (`relay_sync_keys`, off by default); turning it off on a device that had sent keys removes them from the relay once, on its next sync (a device that never sent keys leaves other devices' keys alone, and an install from before the `profile_keys_sent` flag existed gets the flag at the first sync that records the relay address, when the switch is on then; a changed relay address does not clear it). Any other device that still has the switch on puts its keys back at its next sync, so turn it off on every device. The relay stores the profile as plain JSON in `relay.db` and returns it to anyone with the token (its management page hides key values, its API does not), so while the switch is on the relay and its token are as sensitive as the keys themselves. Keys received from the relay are saved on this PC DPAPI-protected like any other key (on the phone they go to the app-private preferences like a key typed there). A device that has not switched keys on ignores keys on the relay.
