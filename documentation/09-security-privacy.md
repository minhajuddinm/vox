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
- Android's network security config allows cleartext for the whole app because it cannot express ranges; the rule above is enforced in code (`GroqClient.open`) before every request.
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
- `keep_clipboard` defaults to true, so dictated text stays on the clipboard where clipboard history and other apps can read it.
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

`relay/relay.py` listens on `127.0.0.1` only and needs a random bearer token on every request (compared in constant time); the optional `owner` setting also checks the `Tailscale-User-Login` header, which `tailscale serve` sets but a local process could forge, so the token stays required. It is meant to be published to one's own tailnet with `tailscale serve` and never with Funnel. `relay.json` (token) and `relay.db` (notes, profile) are plain files; anyone who can read them, or who holds the token and can reach the relay, can read every note and the profile, including any API keys a client stores in it. There is no access log. Nothing in the apps sends data to a relay yet. Details: [14-relay.md](14-relay.md).

### The relay's management page

The page shell at `/` is public on whatever address the relay is published to (it contains no data and asks for the token); everything it shows comes from `/admin/*` and data endpoints that need the token. It is served with a Content-Security-Policy (per-response nonce, no framing, own origin only) and renders all data with `textContent`. The `/admin` endpoints share the token with the data endpoints, so any device that can sync could also download a backup or rotate the token. The token is stored in the browser (`sessionStorage`, or `localStorage` when "Remember on this device" is ticked). `/admin/profile` hides values whose key contains `key`, `token`, `secret` or `password`; the raw profile is still available to any client holding the token. On POSIX the data folder is `0700` and `relay.json` is created `0600`; on Windows those modes do nothing. The recent-requests list is in memory only and holds no note text or ids. The systemd unit (`relay/vox-relay.service`) runs the relay as a throw-away user with a private state folder and a hardened sandbox; that unit has not been tried on a real Raspberry Pi.

## Sync from the Windows app

With **Sync voice notes with my relay** on, the full text of every voice note (title, cleaned text, the raw transcript, tags, device name, times) and delete markers go to the relay address in Settings, and each request carries the relay token and this device's name. The address must pass the same rule as the provider address (plain http only for this PC, the local network and Tailscale). The token is stored DPAPI-protected in `config.json` like the API keys. Audio is never sent. Nothing syncs unless the switch is on; dictation history, meetings and the profile are not synced. A relay that is taken over (or a stolen token) exposes every note.
