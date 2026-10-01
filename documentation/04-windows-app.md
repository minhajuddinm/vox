# 4. Windows app

Python 3.13, packaged with PyInstaller (`--onedir --windowed`) and an Inno Setup installer. Source in `windows/`. Overview of processes and threads: [02-architecture.md](02-architecture.md).

## Entry point (`windows/vox_app.py`)

| Command | Result |
|---|---|
| `Vox.exe` / `python windows\vox_app.py` | Starts the engine. If mutex `Local\VoxEngine` is already held, only opens the window (`engine.open_window`). Logs to `vox.log`. |
| `Vox.exe --window` | Opens the main window. If mutex `Local\VoxWindow` is held, focuses the existing window instead. Logs to `window.log`. |
| `Vox.exe --relay [relay options]` | Runs the relay server (`relay/relay.py`, `main(argv)` with the rest of the command line: `--data-dir`, `--port`, ...) and nothing else: handled first thing in `main`, before any GUI, audio or keyboard module is imported, so it needs no display. No mutex. Logs to `relay.log`. Started by the engine (see "Relay on this PC" below); you can also run it by hand. In the frozen exe the relay is bundled (`--paths ..\relay --hidden-import relay` in `build_app.bat` and the workflow); from source `vox_app.py` adds `..\relay` to the import path. |

The engine and window modes set DPI awareness, install `sys.excepthook` / `threading.excepthook` that log uncaught errors, and use a `RotatingFileHandler` (1 MB, 2 backups). See [11-logs-and-diagnostics.md](11-logs-and-diagnostics.md).

## Engine (`windows/engine.py`, class `Engine`)

### Hotkey behaviour

- Shortcuts (`ui_app.HOTKEYS`): Ctrl+Win (default, keys `ctrl_l` + `cmd`), Right Ctrl, Right Alt, Ctrl+Alt, Ctrl+Shift. Stored in `config.json` as a list of key names; `engine.KEY_ALIASES` maps names to pynput keys.
- **Hold to talk:** press starts recording (`start`), release stops (`stop`). A press shorter than 0.3 s (`TAP_SECONDS`) is a tap: recording is cancelled and the tap time remembered.
- **Hands-free:** a second tap within 0.5 s (`DOUBLE_TAP_GAP`) starts recording that continues after release; press the shortcut once more to finish, Esc to cancel. Limit is `MAX_SECONDS` (360 s) normally, three times that in hands-free.
- When the Windows key is part of the shortcut, the engine taps virtual key `0xE8` so releasing Win does not open the Start menu.
- `on_combo_down` ignores presses while `busy` (a dictation is being sent).

### Recording (`Engine.start`, `_audio`, `stop`)

1. `start` refuses (with a tray notification, a red ! on the pill and by opening the window) if `core.endpoint_error(cfg)` reports a bad server address or `core.key_missing(cfg)` is true.
2. It remembers the foreground app's exe name (`foreground_app`, via psutil; the window title is never read); `paste` later checks the focused window against it.
3. It opens a `sounddevice.InputStream` (16 kHz, mono, int16) on the device chosen in Settings (`audio_devices.input_index(cfg["input_device"])`; empty means the Windows default; a missing device falls back to the default with a notification).
4. `_audio` appends chunks and computes the level for the overlay.
5. `stop` calls `_end_recording` (atomic under `_rec_lock`, because the audio callback and the hotkey thread can both stop), joins the chunks, then:
   - shorter than 0.4 s (`MIN_SECONDS`): back to idle;
   - `core.is_silent(pcm)` (peak below 655 of 32768): notification "Vox did not hear anything (loudest sound N of 32768)…" and a red ! on the pill, back to idle;
   - otherwise `busy`, and `_process` runs on a new thread.

### Processing and paste (`_process`, `paste`)

- Calls `core.process_detailed(cfg, pcm, exe, exe)` (see [06-pipeline.md](06-pipeline.md)). The app label given to the model is the exe name.
- If cleanup was wanted but failed, a notification says the words were pasted as spoken.
- `paste` calls `paste.paste_text(text, target, keep_clipboard)` (`windows/paste.py`; `target` is the exe name remembered when recording started):
  1. waits (up to 2 s) until Shift, Ctrl, Alt and Win are all up, so Ctrl+V is not combined with Win;
  2. compares the focused window's exe name (`GetForegroundWindow` and `QueryFullProcessImageNameW`, never the title) with `target`, ignoring case. If it differs, the text is left on the clipboard, **no Ctrl+V is sent**, and the engine shows "Copied; the window changed". If the target was never captured, the window cannot be named, or the lookup fails (logged as a warning), the paste goes ahead rather than losing the text;
  3. otherwise copies the text, sends Ctrl+V and waits 0.4 s;
  4. restores the old clipboard text only when `keep_clipboard` is false (the default) **and** the clipboard still holds the dictated text, so something the user copied in the meantime is never overwritten. If the old clipboard could not be read it is not restored (the text stays).

  Afterwards `Engine.paste` flashes the pill: green for `"pasted"`, red for `"copied"` ([Result signal on the pill](#result-signal-on-the-pill)).
- History: one JSON line appended to `history.jsonl` unless `keep_history` is false.
- Errors: `ApiError` (401 key rejected, 429 rate limit, other) and `requests.RequestException` set `Engine.pending = (pcm, exe)` and notify with "Your recording is kept: tray icon > Retry last dictation." `retry_last` re-runs `_process` on the kept audio. Success clears `pending`. Each of these failures also flashes a red ! on the pill; a saved voice note flashes the green check.

### Tray menu (pystray)

Open Vox (default action) · Retry last dictation (visible only while `pending` is set) · New voice note / Finish voice note · Start/Stop meeting notes · Run relay on this PC (a checkbox, ticked while `relay_run` is true) · Quit Vox. Icon colour follows state (`logo.draw`: idle blue, recording red, busy amber).

### Relay on this PC (`windows/relay_host.py`)

`Engine.relay` is a `RelayHost(data_dir, port, notify)` for the relay data folder `%APPDATA%\VoxRelay` (the relay's own default, so `python relay.py` and Vox share one relay) and the port from `relay_port` (default 8765; `relay_host.port_from` falls back to 8765 for an unusable value). The tray item "Run relay on this PC" (`Engine.toggle_relay`) flips `relay_run`, writes it into the settings file (it loads the file first, so settings the window saved meanwhile are kept) and starts or stops the host. `Engine.run` starts it at launch when `relay_run` is true. Changing `relay_run` or `relay_port` by editing `config.json` while Vox runs does not start or stop anything until the next Vox start or the next tray click (`relay_port` is read each time the relay is started). The tick mark shows the saved setting, not whether the relay process is running: the engine re-reads `config.json`, so the box follows the file, and it stays ticked when the relay did not start (busy port, launcher error) or ended by itself. Whether it runs is shown only by the notifications and `vox.log`.

- `relay_command(data_dir, port)` is `[Vox.exe, "--relay", "--data-dir", D, "--port", P]` in the frozen app and `[python, windows\vox_app.py, "--relay", ...]` from source. `RelayHost.start()` starts it once (a second call while it runs does nothing), `running()` asks the process, `stop()` terminates it, waits 5 s and kills it if it does not end. The launcher (`spawn_hidden`) gives it no console window and discards its output.
- **Never orphaned:** `Engine.quit` calls `relay.stop()` before `os._exit(0)`. The launcher also puts the child in a Windows job object with "kill on close", so if the engine is killed or crashes, Windows ends the relay with it. Stopping is a hard `TerminateProcess`: the relay's SIGTERM handler does not run on Windows. The relay keeps its data in SQLite, whose commits are atomic, so a killed relay should not leave a half-written note (inferred from SQLite's design; not tested by killing a relay mid-write).
- **Port check:** before starting, `start()` asks `port_busy(port)` (a TCP connect to `127.0.0.1:port`). If something already answers, for example a relay started by hand, it does not start a second one and says so. This is needed because the relay's server class (`ThreadingHTTPServer`) inherits `allow_reuse_address = 1` from `http.server.HTTPServer`, so it binds with `SO_REUSEADDR`, which on Windows lets a second relay bind a taken port without any error (checked on this PC on 2026-09-30: a second `relay.make_server` on the port of a listening one succeeded). The check does not cover a relay started by hand *after* Vox's one: `python relay.py` has no such check.
- **Messages (tray notifications):** the first time the relay ever starts on this PC (no `relay.json` yet) the notification says `tailscale serve --bg <port>` with the real port, which is how the phone reaches it; a busy port is reported as above; if the child ends within 10 s of starting the notification says so and points to `relay.log`; a launcher error (the exe could not be started) is shown with its message. The setting stays on in each case; untick the item to stop Vox trying.

### Config reload

`_watch_config` polls the modified time of `config.json` every second and reloads settings (and the hotkey) when it changes, but never while recording. This is how the window process changes engine behaviour.

### Quit (`Engine.quit`)

If a meeting is active it is stopped; if notes are still being written the engine waits up to 180 s, then stops the relay child process (if running), the tray icon and overlay, deletes `engine.json` and exits with `os._exit(0)`. Ctrl+C in the launching terminal calls `quit` too.

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

A small Tk pill at the bottom of the work area: live waveform while recording, bouncing dots while sending, a timer while taking meeting notes, and for a moment after a dictation a green check or a red ! (next section). Created with extended window styles so it is click-through, never takes focus, has no taskbar button and stays on top. Tk runs on the main thread and polls `Engine.state`, `Engine.level`, `Engine.flash_kind` and `Engine.flash_until` every 33 ms (`FPS_MS`); other threads only set those fields. If the overlay fails to start the engine logs it and keeps running without it.

## Result signal on the pill

`Engine.flash(kind)` makes the pill show **sent** (a green check, 0.7 s) or **error** (a red !, 1.8 s) and then go back to whatever the real state is. It is only a signal: `Engine.state` keeps its values (`idle`, `rec`, `busy`) and every error keeps its tray notification text, because the pill says *that* something failed, not *what*.

- **State:** `flash_kind` (`"sent"`, `"error"` or `""`) and `flash_until` (`time.monotonic()` deadline, `FLASH_SECONDS` gives the durations). `active_flash(now=None)` returns the kind while the deadline has not passed, else `""`; it never writes, so the Tk thread and the worker threads do not race on clearing it. `flash` does nothing when `Engine.overlay` is `None` (the pill failed to start), and raises `KeyError` for any other kind.
- **Cancelling:** `set_state("rec")` and `set_state("busy")` clear the flash, so a new recording or a retry replaces it at once; `set_state("idle")` keeps it (the result is flashed while the state is still `busy`, then the engine goes idle). A flash is also not drawn while the state is `rec` or `busy`.
- **Drawing (`overlay.py`):** each tick the overlay asks the pure function `overlay_mode(state, flash_kind, flash_until, now, meeting_active)` in `windows/overlay_mode.py` (no Tk, unit tested in `tests/test_overlay_mode.py`) what to show: `rec` or `busy` always; while idle a running flash (`sent` / `error`) instead of the meeting timer (`meet`), then the timer (or nothing, `None`) returns. The drawing code itself is Tk and is not unit tested. `_draw_sent` and `_draw_error` draw one line, and one line plus a dot, from coordinates worked out once in `Overlay.__init__`; each frame clears and redraws the canvas as before. The pill stays click-through. Not yet seen on a real screen: the canvas items were checked in a live Tk window and the shapes previewed from the same coordinates, ; not yet seen on a real screen.
- **Green (`sent`):** a pasted dictation (`paste_text` returned `"pasted"`) and a saved voice note.
- **Red (`error`):** the dictation text only reached the clipboard (`"copied"`, together with "Copied; the window changed"); "Add your API key" or a bad server address; a microphone error; nothing heard; every send failure (`ApiError`, network error, an unexpected exception in `_process`).
- **Nothing:** a recording cancelled or shorter than 0.4 s, a dictation with no text, the "Cleanup did not work" notice (the words were still pasted, so that one is `sent`), and the informational notices (chosen microphone missing, meeting notes).

The phone does the same with `BubbleView.flash` ([05-android-app.md](05-android-app.md#result-flash-on-the-bubble)).

## Main window (`windows/ui_app.py` and `windows/ui/index.html`)

`ui_app.main` creates one `pywebview` window titled "Vox" (1040x700, minimum 860x580) showing `ui/index.html` with `js_api=Api()`. WebView2 (Edge) renders it. Pages:

| Page | Purpose |
|---|---|
| Home | Stats (words this week, total, words per minute, time saved vs typing at 40 wpm), first-run key box, searchable history with copy, delete and "Fix a word" |
| Notes (beta) | Calendar, start/stop meeting notes, live transcript with a question box, saved meetings with detail view, questions across all meetings |
| Dictionary | Words, People, Replacements (`wrong => right`) |
| Styles | Default style and a style per app exe (selected from recent apps or entered manually as "Other...") |
| Settings | Use my relay as the AI server, API key + Test, Server address, shortcut, microphone, language, AI cleanup, skip cleanup below N words, keep history, keep clipboard, your name, calendar email, auto notes, start with Windows, clear history, data folder |

`Api` methods (called from JavaScript as `pywebview.api.<name>`): `get_state`, `save_config`, `set_hotkey`, `check_key`, `list_models`, `test_role`, `note_toggle`, `note_status`, `sync_status`, `sync_now`, `sync_test`, `notes_list`, `note_edit`, `note_delete`, `endpoint_problem`, `proxy_problem`, `suggest_corrections`, `copy`, `delete_history`, `clear_history`, `open_url`, `open_data_folder`, the `meeting_*` and `meetings*` group, `calendar`, `google_*`, `connect_calendar`, `get_autostart`, `set_autostart`. Live meeting calls go through `Api._engine` to the control server; everything else reads or writes files directly.

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

Settings starts with an **AI provider** block. Its first row is the switch **Use my relay as the AI server** (`relay_proxy`) with a status line: red "Turn on the relay first" while the relay address and token (Sync between devices) are missing, green "Using the relay at ..." once they are there; the text comes from `Api.proxy_problem` (`providers.proxy_problem`, read from the saved settings after each change). While the switch is on the card gets the class `proxy-on`, which hides the rows for the preset, server address, API key, and the separate voice and cleanup servers; the model boxes, Refresh and the Test buttons stay and work through the relay (the sidebar footer says "My relay"). The welcome banner that asks for a Groq key is hidden too. The model lists load from the relay only once it is filled in (`serverReady`). How the calls change: [06-pipeline.md](06-pipeline.md). Below it come a preset list (`providers.PRESETS`, delivered to the page in `get_state` as `presets`), the server address, the key, a Voice model box and a Cleanup model box. Each model box is a text field with a `<datalist>`: the list comes from `Api.list_models(role, form)` (which calls `providers.list_models` with the unsaved form values laid over the saved settings), and any model name can still be typed. The Test buttons call `Api.test_role`. A switch reveals separate server and key fields for voice and for cleanup. Choosing a preset fills the address and, for Groq, OpenAI and Mistral, suggested models; other presets clear the model boxes. The sidebar footer shows the preset name and the speech model. The status text of the address, key, model, server and relay rows (for example "Works (120 ms)" or an error) is shown under the row's description, not beside the controls, because a long message beside them squeezed the setting's name into a narrow column. The list loads once when Settings first renders (only if a key is set or the preset needs none) and again on Refresh or after an address or key change. Design: [specs/p1-providers-and-models.md](specs/p1-providers-and-models.md).

## Connection warm-up

`Engine.start` calls `core.warm(cfg)` right after it notes the target app and before the microphone opens. It opens (in a background thread) one connection per distinct role server with a small `GET /models`, on the shared `vox_core._session`; `post_with_retry` posts through the same session, so the upload after the key is released reuses the connection. The microphone is still opened only at key-down ([decisions/0017-warm-connections-not-an-open-microphone.md](decisions/0017-warm-connections-not-an-open-microphone.md)).

## About you

The Dictionary page starts with an **About you** card: a text box bound to `user_context` (saved on change, character count against 8,000). `core.cleanup` passes it to `system_prompt`. It is sent only to the cleanup server.

## Recording meter

`Engine._audio` (the PortAudio callback) stores `core.level_from_rms(rms)` in `self.level`. The overlay polls it on the Tk thread every 33 ms and, every 80 ms, pushes the smoothed value into a `core.LevelHistory` of 11 values; `_draw_recording` draws one bar per value, newest on the right. The history is cleared when the pill appears. The curve is shared with the phone ([specs/p4-live-voice-level.md](specs/p4-live-voice-level.md)).

## Voice notes

`Engine.toggle_note` (tray menu "New voice note" / "Finish voice note", and the control endpoints `/note/toggle` and `/note/status` that the window calls through `Api.note_toggle` and `note_status`) records in note mode: `note_mode` is true, `hands_free` is set so recording continues until it is toggled again, Esc cancels (`cancel` clears `note_mode`), and the dictation hotkey also finishes it. `stop` hands `note` to `_process(pcm, exe, note)`, which calls `process_detailed` with an empty app name (so the default style applies and no app name is sent) and saves the text with `notes.add` instead of pasting; the tray notification says "Note saved: <title>". A failed note stays in `Engine.pending` as `(pcm, exe, note)` so "Retry last dictation" saves it as a note. The **Voice notes** page (`renderVoiceNotes`, `pollNote` in `ui/index.html`) has a New voice note button, a search box, a time filter (any time, today, 7 days, 30 days) and cards with Copy, Edit and Delete; `Api.notes_list` maps the filter to `notes.search`. The automatic title (`notes.auto_title`, first 7 words) and the FTS5 search string (`notes.fts_query`) are public functions because the phone's twin, `NoteLogic`, is tested against them through the `title` and `ftsq` rows of `spec/golden.txt`. Store details: [decisions/0019-voice-notes-in-sqlite.md](decisions/0019-voice-notes-in-sqlite.md).

## Syncing voice notes

`Engine.__init__` creates a `sync.SyncWorker`; `run` starts it (it syncs at once), `quit` stops it, and saving a note calls `self.sync.trigger()`. The control server has `/sync/now` (trigger, answer with the status) and `/sync/status` (`enabled`, `running`, `last_run`, `last_ok`, `error`, `pushed`, `pulled`). The window calls them through `Api.sync_now` and `sync_status`; `Api.note_edit` and `note_delete` ask the engine to sync after a change, and `Api.sync_test` checks an address and token without saving them. Settings has a **Sync between devices** block (switch, relay address, token, device name, Test) and the Voice notes page shows "Synced 2 min ago" or the last error, with a Sync now button. How a run works and why: [decisions/0022-sync-client-dirty-flag-and-cursor.md](decisions/0022-sync-client-dirty-flag-and-cursor.md). A note that the relay refuses for good (`SyncError.permanent`: a 4xx other than 401, 403 and 429, for example a bad note id) is skipped for that run and counted in the result (`error` says "1 note could not be sent: ..."); it stays dirty and is tried again once per run, and the other notes, the pull and the profile still sync. Any other failure (no network, 401, 403, 429, 5xx) stops the run. Notes made on this PC carry the device name (`sync.device_name`).

### Profile sync

After the notes, `sync.sync_once` calls `sync_profile`: it reads the relay's profile, merges it field by field with this PC's `config.json` (the side that changed since the last sync wins; if both changed, the relay's value wins), saves what came in, and writes the merged document back with `If-Match` when it differs (a refused write is retried up to three times). The shared fields are `user_context`, `dictionary`, `people`, `default_style`, `cleanup` and `language`; with **Also share my provider settings and API keys** (`relay_sync_keys`) also the provider, models and keys. Per-app styles, hotkey, microphone and the relay settings never sync. The engine triggers a sync whenever it reloads changed settings. See [decisions/0023-profile-sync-three-way-merge.md](decisions/0023-profile-sync-three-way-merge.md).

## Long recordings in pieces

`Engine.start` creates a `streaming.StreamingStt` (when `stream_stt` is on) and starts its thread; the microphone callback `_audio` feeds it (a queue put only). `stop` hands it to `_process(pcm, exe, note, streamer)`, which calls `streamer.finish()` in the processing thread: a text means the recording was cut and transcribed in pieces, so only `core.process_text` runs (cleanup once on the joined text); `None` means it was too short to cut, a piece failed or it timed out, so `core.process_detailed` transcribes the whole recording as before. `cancel`, a too-short or silent recording and a microphone error cancel the streamer. The whole recording stays in `Engine.pending` for Retry. See [decisions/0024-stream-long-dictations-in-pieces.md](decisions/0024-stream-long-dictations-in-pieces.md).
