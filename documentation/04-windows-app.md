# 4. Windows app

Python 3.13, packaged with PyInstaller (`--onedir --windowed`) and an Inno Setup installer. Source in `windows/`. Overview of processes and threads: [02-architecture.md](02-architecture.md).

## Entry point (`windows/vox_app.py`)

| Command | Result |
|---|---|
| `Vox.exe` / `python windows\vox_app.py` | Starts the engine. If mutex `Local\VoxEngine` is already held, only opens the window (`engine.open_window`). Logs to `vox.log`. |
| `Vox.exe --window` | Opens the main window. If mutex `Local\VoxWindow` is held, focuses the existing window instead. Logs to `window.log`. |

Both set DPI awareness, install `sys.excepthook` / `threading.excepthook` that log uncaught errors, and use a `RotatingFileHandler` (1 MB, 2 backups). See [11-logs-and-diagnostics.md](11-logs-and-diagnostics.md).

## Engine (`windows/engine.py`, class `Engine`)

### Hotkey behaviour

- Shortcuts (`ui_app.HOTKEYS`): Ctrl+Win (default, keys `ctrl_l` + `cmd`), Right Ctrl, Right Alt, Ctrl+Alt, Ctrl+Shift. Stored in `config.json` as a list of key names; `engine.KEY_ALIASES` maps names to pynput keys.
- **Hold to talk:** press starts recording (`start`), release stops (`stop`). A press shorter than 0.3 s (`TAP_SECONDS`) is a tap: recording is cancelled and the tap time remembered.
- **Hands-free:** a second tap within 0.5 s (`DOUBLE_TAP_GAP`) starts recording that continues after release; press the shortcut once more to finish, Esc to cancel. Limit is `MAX_SECONDS` (360 s) normally, three times that in hands-free.
- When the Windows key is part of the shortcut, the engine taps virtual key `0xE8` so releasing Win does not open the Start menu.
- `on_combo_down` ignores presses while `busy` (a dictation is being sent).

### Recording (`Engine.start`, `_audio`, `stop`)

1. `start` refuses (with a tray notification and by opening the window) if `core.endpoint_error(cfg)` reports a bad server address or `core.key_missing(cfg)` is true.
2. It remembers the foreground app's exe name (`foreground_app`, via psutil; the window title is never read).
3. It opens a `sounddevice.InputStream` (16 kHz, mono, int16) on the device chosen in Settings (`audio_devices.input_index(cfg["input_device"])`; empty means the Windows default; a missing device falls back to the default with a notification).
4. `_audio` appends chunks and computes the level for the overlay.
5. `stop` calls `_end_recording` (atomic under `_rec_lock`, because the audio callback and the hotkey thread can both stop), joins the chunks, then:
   - shorter than 0.4 s (`MIN_SECONDS`): back to idle;
   - `core.is_silent(pcm)` (peak below 655 of 32768): notification "Vox did not hear anything (loudest sound N of 32768)…", back to idle;
   - otherwise `busy`, and `_process` runs on a new thread.

### Processing and paste (`_process`, `paste`)

- Calls `core.process_detailed(cfg, pcm, exe, exe)` (see [06-pipeline.md](06-pipeline.md)). The app label given to the model is the exe name.
- If cleanup was wanted but failed, a notification says the words were pasted as spoken.
- `paste`: waits (up to 2 s) until the hotkey modifiers are released, copies the text to the clipboard, sends Ctrl+V, waits 0.4 s. The old clipboard is restored only when `keep_clipboard` is false (default true: dictated text stays on the clipboard).
- History: one JSON line appended to `history.jsonl` unless `keep_history` is false.
- Errors: `ApiError` (401 key rejected, 429 rate limit, other) and `requests.RequestException` set `Engine.pending = (pcm, exe)` and notify with "Your recording is kept: tray icon > Retry last dictation." `retry_last` re-runs `_process` on the kept audio. Success clears `pending`.

### Tray menu (pystray)

Open Vox (default action) · Retry last dictation (visible only while `pending` is set) · Start/Stop meeting notes · Quit Vox. Icon colour follows state (`logo.draw`: idle blue, recording red, busy amber).

### Config reload

`_watch_config` polls the modified time of `config.json` every second and reloads settings (and the hotkey) when it changes, but never while recording. This is how the window process changes engine behaviour.

### Quit (`Engine.quit`)

If a meeting is active it is stopped; if notes are still being written the engine waits up to 180 s, then stops the tray icon and overlay, deletes `engine.json` and exits with `os._exit(0)`. Ctrl+C in the launching terminal calls `quit` too.

### Local control server

`Engine._serve`: `ThreadingHTTPServer` on `127.0.0.1` with a random port and a random 32-hex-character token, written with the process id to `%APPDATA%\Vox\engine.json`. Every request must be `POST` with header `X-Vox-Token`. Routes:

| Route | Body | Effect |
|---|---|---|
| `/meeting/start` | `{"uid", "manual"}` | Start meeting notes (calendar event by uid, or a manual title/attendees) |
| `/meeting/stop` | | Stop and start the final pass |
| `/meeting/status` | | Live status, transcript, questions and answers |
| `/meeting/catchup` | | "What did I miss?" summary of the last 10 minutes (exposed by `Api.meeting_catchup`; the current page does not call it) |
| `/meeting/ask` | `{"q"}` | Ask a question about the live meeting |

### Calendar watcher

Every 30 s, if a calendar is connected and no meeting is running, the engine looks for an event with attendees that starts within 60 s before to 180 s after now. It then either starts notes (`auto_notes`) or shows a reminder. Each event is reminded once per run.

## Overlay (`windows/overlay.py`)

A small Tk pill at the bottom of the work area: live waveform while recording, bouncing dots while sending, a timer while taking meeting notes. Created with extended window styles so it is click-through, never takes focus, has no taskbar button and stays on top. Tk runs on the main thread and polls `Engine.state` and `Engine.level` every 16 ms; other threads only set those fields. If the overlay fails to start the engine logs it and keeps running without it.

## Main window (`windows/ui_app.py` and `windows/ui/index.html`)

`ui_app.main` creates one `pywebview` window titled "Vox" (1040x700, minimum 860x580) showing `ui/index.html` with `js_api=Api()`. WebView2 (Edge) renders it. Pages:

| Page | Purpose |
|---|---|
| Home | Stats (words this week, total, words per minute, time saved vs typing at 40 wpm), first-run key box, searchable history with copy, delete and "Fix a word" |
| Notes (beta) | Calendar, start/stop meeting notes, live transcript with a question box, saved meetings with detail view, questions across all meetings |
| Dictionary | Words, People, Replacements (`wrong => right`) |
| Styles | Default style and a style per app exe |
| Settings | API key + Test, Server address, shortcut, microphone, language, AI cleanup, keep history, keep clipboard, your name, calendar email, auto notes, start with Windows, clear history, data folder |

`Api` methods (called from JavaScript as `pywebview.api.<name>`): `get_state`, `save_config`, `set_hotkey`, `check_key`, `list_models`, `test_role`, `note_toggle`, `note_status`, `notes_list`, `note_edit`, `note_delete`, `endpoint_problem`, `suggest_corrections`, `copy`, `delete_history`, `clear_history`, `open_url`, `open_data_folder`, the `meeting_*` and `meetings*` group, `calendar`, `google_*`, `connect_calendar`, `get_autostart`, `set_autostart`. Live meeting calls go through `Api._engine` to the control server; everything else reads or writes files directly.

Start with Windows is a `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value named `Vox`.

## Meeting notes (`windows/meeting.py`)

Class `Meeting`; started from the tray, the window or the calendar watcher.

- **Two recorders** (`_Source`, one thread each) with the `soundcard` library: `You` = default microphone, `Others` = loopback of the default speaker (what the PC plays). This is why your own lines are always labelled correctly. The Microphone setting does **not** apply to meetings (it uses the default microphone).
- **Live transcript:** each source cuts speech at natural pauses using a simple energy detector (30 ms frames, adaptive noise floor, pieces of 6 to 20 s, at least 0.5 s of speech), stores each speech piece in `you.raw` / `others.raw`, and queues it. One worker (`_transcribe_loop`) sends pieces to Whisper (`transcribe_segments`, at least 3.2 s apart to respect Groq's free-plan rate), filters hallucinations (`_good`, `_dedupe_repeats`), and merges lines (`_add`), dropping mic echo of the call audio.
- **Final pass** (`_final_pass`, when `final_pass` is true, default): re-transcribes the stored speech with `whisper-large-v3` in pieces up to 480 s and replaces the live lines.
- **Speaker naming** (`attribute_speakers`): asks the notes model to name `Others` lines from context and the attendee list; results show as "Name (likely)".
- **Notes** (`_notes`): the notes model (default `openai/gpt-oss-120b`, `notes_model`) writes Markdown with fixed sections (Summary, Discussion, Decisions, Action items, Open questions, Next steps, Who said what). Transcripts over 60,000 characters are condensed in parts first.
- **Saving:** `%APPDATA%\Vox\meetings\<id>\` gets `transcript.json`, `notes.md`, `meta.json`, optional `my_notes.md`; a copy of the notes goes to `Documents\Vox Notes` (or `notes_folder`). Raw audio is deleted afterwards unless `keep_audio` is true.
- **Ask:** `ask_live` (during a meeting), `ask_meeting` (one saved meeting), `ask` (searches all meetings by keyword score and sends the best four to the model).
- **Concurrency:** `ctl` lock serialises start/stop; `lock` guards `entries`/`qa` for status and the final-pass swap.

All model calls go through `core.post_with_retry` to the configured server. Details of each prompt are in the constants at the top of `meeting.py`.

## Calendar (`windows/gcal.py`, `windows/vcalendar.py`)

- `vcalendar.fetch(cfg)` returns `{"events", "error", "fetched", "source"}` for 12 h ago to 7 days ahead, cached 5 minutes in `calendar.json`. Source is Google (if connected) or the ICS link in `calendar_url` (`webcal://` accepted).
- `gcal`: OAuth 2.0 for desktop apps with a loopback redirect and PKCE; scope `calendar.events.readonly` plus `openid email`. Needs a `google_client.json` (from a Google Cloud project) next to the app, bundled at build time from the `GOOGLE_CLIENT_JSON` secret, or in `%APPDATA%\Vox`. Without it the Google button is unavailable and the ICS link still works. Tokens are in `%APPDATA%\Vox\google_token.json` (plain JSON, see [09-security-privacy.md](09-security-privacy.md)).
- All-day events and cancelled events are skipped; attendees exclude you, rooms and declined guests.

## Other modules

- `windows/vox_core.py`: see [06-pipeline.md](06-pipeline.md) and [07-config-and-data.md](07-config-and-data.md).
- `windows/secret.py`: DPAPI wrapper (`protect`, `unprotect`); values are stored as `dpapi:<base64>`.
- `windows/audio_devices.py`: `input_names()` lists each microphone of the default audio system once; `input_index(name)` resolves the stored name.
- `windows/logo.py`: `draw(size, state)` and `write_ico`.

## Build and install

See [10-build-test-release.md](10-build-test-release.md). Installed per user to `%LOCALAPPDATA%\Programs\Vox` (no admin rights).

## AI provider settings

Settings starts with an **AI provider** block: a preset list (`providers.PRESETS`, delivered to the page in `get_state` as `presets`), the server address, the key, a Voice model box and a Cleanup model box. Each model box is a text field with a `<datalist>`: the list comes from `Api.list_models(role, form)` (which calls `providers.list_models` with the unsaved form values laid over the saved settings), and any model name can still be typed. The Test buttons call `Api.test_role`. A switch reveals separate server and key fields for voice and for cleanup. Choosing a preset fills the address and, for Groq, OpenAI and Mistral, suggested models; other presets clear the model boxes. The sidebar footer shows the preset name and the speech model. The list loads once when Settings first renders (only if a key is set or the preset needs none) and again on Refresh or after an address or key change. Design: [specs/p1-providers-and-models.md](specs/p1-providers-and-models.md).

## Connection warm-up

`Engine.start` calls `core.warm(cfg)` right after it notes the target app and before the microphone opens. It opens (in a background thread) one connection per distinct role server with a small `GET /models`, on the shared `vox_core._session`; `post_with_retry` posts through the same session, so the upload after the key is released reuses the connection. The microphone is still opened only at key-down ([decisions/0017-warm-connections-not-an-open-microphone.md](decisions/0017-warm-connections-not-an-open-microphone.md)).

## About you

The Dictionary page starts with an **About you** card: a text box bound to `user_context` (saved on change, character count against 8,000). `core.cleanup` passes it to `system_prompt`. It is sent only to the cleanup server.

## Recording meter

`Engine._audio` (the PortAudio callback) stores `core.level_from_rms(rms)` in `self.level`. The overlay polls it on the Tk thread every 33 ms and, every 80 ms, pushes the smoothed value into a `core.LevelHistory` of 11 values; `_draw_recording` draws one bar per value, newest on the right. The history is cleared when the pill appears. The curve is shared with the phone ([specs/p4-live-voice-level.md](specs/p4-live-voice-level.md)).

## Voice notes

`Engine.toggle_note` (tray menu "New voice note" / "Finish voice note", and the control endpoints `/note/toggle` and `/note/status` that the window calls through `Api.note_toggle` and `note_status`) records in note mode: `note_mode` is true, `hands_free` is set so recording continues until it is toggled again, Esc cancels (`cancel` clears `note_mode`), and the dictation hotkey also finishes it. `stop` hands `note` to `_process(pcm, exe, note)`, which calls `process_detailed` with an empty app name (so the default style applies and no app name is sent) and saves the text with `notes.add` instead of pasting; the tray notification says "Note saved: <title>". A failed note stays in `Engine.pending` as `(pcm, exe, note)` so "Retry last dictation" saves it as a note. The **Voice notes** page (`renderVoiceNotes`, `pollNote` in `ui/index.html`) has a New voice note button, a search box, a time filter (any time, today, 7 days, 30 days) and cards with Copy, Edit and Delete; `Api.notes_list` maps the filter to `notes.search`. Store details: [decisions/0019-voice-notes-in-sqlite.md](decisions/0019-voice-notes-in-sqlite.md).
