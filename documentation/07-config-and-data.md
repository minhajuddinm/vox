# 7. Configuration and data

Everything a user can set, everything Vox writes to disk, and the formats. `documentation/tools/check_docs.py` checks that every Windows setting key and every Android preference key named in the code appears on this page.

## Windows settings (`%APPDATA%\Vox\config.json`)

Loaded by `vox_core.load_config` (missing keys take the defaults in `DEFAULT_CONFIG`), saved by `save_config`. The window edits it; the engine reloads it within a second. The `api_key` value is stored protected (`dpapi:<base64>`, see [09-security-privacy.md](09-security-privacy.md)) and is plain text in memory.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `api_key` | string | `""` | Key for the server. May stay empty for a self-hosted `base_url`. |
| `base_url` | string | `https://api.groq.com/openai/v1` | Main server address for both roles. Blank means Groq. `http://` only for private hosts (`vox_core.endpoint_error`, applied to each role address too). |
| `hotkey` | list of strings | `["ctrl_l", "cmd"]` | Key names from `engine.KEY_ALIASES`. |
| `stt_model` | string | `whisper-large-v3-turbo` | Speech model. |
| `llm_model` | string | `openai/gpt-oss-20b` | Cleanup model. |
| `provider` | string | `groq` | Preset chosen in Settings (`groq`, `openai`, `openrouter`, `together`, `mistral`, `ollama`, `lmstudio`, `speaches`, `whispercpp`, `custom`). A UI convenience only: the address decides behaviour. |
| `stt_base_url` | string | `""` | Separate server for speech to text. Blank means the main `base_url`. |
| `stt_api_key` | string | `""` | Key for `stt_base_url` (DPAPI-protected like `api_key`). Blank means the main key, but only while the address is the main one. |
| `llm_base_url` | string | `""` | Separate server for cleanup. Blank means the main `base_url`. |
| `llm_api_key` | string | `""` | Key for `llm_base_url` (DPAPI-protected). |
| `llm_reasoning` | string | `auto` | `auto` sends `reasoning_effort` only to gpt-oss models (and stops if the server refuses it); `off` never sends it. |
| `user_context` | string | `""` | Free text about the user (work, projects, style, terms) added to every cleanup request; at most 8,000 characters are used. |
| `language` | string | `""` | Whisper language code; empty = auto detect. |
| `input_device` | string | `""` | Microphone name for dictation; empty = Windows default. Not used by meeting notes. |
| `cleanup` | bool | `true` | Run the AI cleanup. |
| `keep_history` | bool | `true` | Save dictations to `history.jsonl`. |
| `default_style` | string | `neutral` | `formal`, `casual`, `very_casual`, `neutral`, `raw`. |
| `dictionary` | list of strings | `[]` | Terms and `wrong => right` lines. |
| `people` | list of strings | `[]` | Names, used as terms. |
| `app_styles` | object | see `DEFAULT_CONFIG` | Exe name (lower case) to style. Defaults: Outlook, Word: formal; Slack: neutral; Discord: very casual; WhatsApp: casual; VS Code, Windows Terminal: raw. |
| `keep_clipboard` | bool | `true` (when absent) | Leave the dictated text on the clipboard after pasting. |
| `your_name` | string | absent | Label for your lines in meeting notes (default "You"). |
| `my_email` | string | absent | Your calendar address, so you are not listed as an attendee. |
| `auto_notes` | bool | absent (false) | Start meeting notes automatically when a calendar meeting with others begins. |
| `calendar_url` | string | absent | Private iCal (ICS) link. |
| `notes_model` | string | absent | Model for meeting notes and questions (default `openai/gpt-oss-120b`). |
| `final_stt_model` | string | absent | Model for the final meeting pass (default `whisper-large-v3`). |
| `final_pass` | bool | `true` (when absent) | Re-transcribe the meeting after it ends. |
| `keep_audio` | bool | absent (false) | Keep the raw meeting audio after the notes are written. |
| `notes_folder` | string | absent | Where a copy of each meeting's notes is written (default `Documents\Vox Notes`). |

Settings shown in the Windows window: `api_key`, `base_url`, `hotkey`, `input_device`, `language`, `cleanup`, `keep_history`, `keep_clipboard`, `your_name`, `my_email`, `auto_notes`, `provider`, `stt_base_url`, `stt_api_key`, `llm_base_url`, `llm_api_key`, `stt_model`, `llm_model`, `default_style`, `app_styles`, `dictionary`, `people`, `calendar_url`. The others (`notes_model`, `final_stt_model`, `final_pass`, `keep_audio`, `notes_folder`) are only settable by editing the file.

## Android preferences (SharedPreferences file `vox`, private to the app)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `api_key` | string | `""` | Key for the server. |
| `base_url` | string | Groq address | Server address; validated by `Endpoint.error` before saving. |
| `stt_model` | string | `whisper-large-v3-turbo` | Speech model. |
| `llm_model` | string | `openai/gpt-oss-20b` | Cleanup model. |
| `provider` | string | `groq` | Preset chosen in Settings (a UI convenience; the address decides behaviour). |
| `stt_base_url`, `llm_base_url` | string | blank | Separate server for speech or cleanup; blank means the main `base_url`. Validated by `Endpoint.error`. |
| `stt_api_key`, `llm_api_key` | string | blank | Key for that role's own server; used only with its own address. |
| `user_context` | string | blank | Same as the Windows setting: background text added to every cleanup request. |
| `language` | string | `""` | Whisper language code. |
| `dictionary` | string (lines) | comment header | Terms and `wrong => right` lines, one per line. |
| `people` | string (lines) | `""` | Names, one per line. |
| `app_styles` | string (lines) | `Prefs.DEFAULT_APP_STYLES` | `package = style` per line. |
| `default_style` | string | `neutral` | Style for other apps. |
| `cleanup` | bool | `true` | Run the AI cleanup. |
| `keep_history` | bool | `true` | Save dictations. |
| `only_typing` | bool | `true` | Show the bubble only while a text field is focused. |
| `bubble_x`, `bubble_y` | int | -1 (default spot) | Saved bubble position. |
| `history` | string (JSON array) | `[]` | Up to 500 entries, newest first. |

## Files on the PC (`%APPDATA%\Vox`)

| File | Written by | Contents |
|---|---|---|
| `config.json` | window, engine (migration) | Settings above. |
| `history.jsonl` | engine | One JSON object per line: `t` (Unix seconds), `app` (exe name), `raw`, `text`, `words`, `secs`. Grows without limit; the window shows the newest 300. |
| `notes.db` (+ `notes.db-wal`, `notes.db-shm`) | engine, window | SQLite, table `notes`: `id` (32 hex chars), `source` (`voice note`), `title`, `text`, `raw`, `created_at` and `updated_at` (Unix seconds), `secs`, `device`, `tags` (JSON list), `deleted` (0 or 1; a deleted note keeps only the marker row). Table `notes_fts` (FTS5: `id`, `title`, `text`) exists when SQLite has FTS5. Not encrypted. |
| `vox.log`, `vox.log.1`, `vox.log.2` | engine | Rotating log (1 MB each). |
| `window.log` (+ backups) | window | Same for the window process. |
| `engine.json` | engine | `{"port", "token", "pid"}` for the control server. Deleted on quit. |
| `calendar.json` | `vcalendar` | Cached events `{"source", "events", "error", "fetched"}` (5 minutes). |
| `google_token.json` | `gcal` | `{"refresh_token", "access_token", "expires", "email"}`. Plain JSON. |
| `google_client.json` | build (optional) | OAuth client for Google sign-in; ignored by git. |
| `meetings\<id>\` | `meeting` | `transcript.json` `{"id", "started", "entries": [{"t", "who", "text", "name"?}], "qa"}`, `notes.md`, `meta.json` `{"id", "title", "started", "duration", "words", "attendees", "export", "done"}`, optional `my_notes.md`, and `you.raw` / `others.raw` (16 kHz int16 speech pieces; removed after the notes are written unless `keep_audio`). `<id>` is `YYYYMMDD-HHMMSS`. |
| `Documents\Vox Notes\<date> <title>.md` | `meeting` | Copy of each meeting's notes. |

Files written by the app while it runs: `history.jsonl` is appended; `config.json`, `history` rewrites and meeting JSON use a temp file and replace (`.tmp` then `os.replace`) where the code does so (`save_config`, `write_history`, meeting `_write_json`).

## Files on the phone

Only the SharedPreferences file above, plus `cache/vox_pending.wav` while a dictation is waiting to be sent or retried (deleted on success, on cancel, and when the service stops).

## Never commit

`windows/config.json`, `windows/google_client.json`, `client_secret*.json`, `android/vox.keystore` and any `*.keystore` (all in `.gitignore`). No test may read a real user's `%APPDATA%\Vox`; tests set `APPDATA` to a temporary folder.
