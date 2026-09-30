# 2. Architecture

Two independent apps that share no code at run time. They share **behaviour** (the cleanup rules) and a **test file** (`spec/golden.txt`) that keeps the Python and Java copies of those rules identical.

```
                 +-----------------------------------------------+
                 |  Speech + cleanup server (OpenAI-compatible)  |
                 |  Groq by default, or the user's own server    |
                 |   POST /audio/transcriptions  (Whisper)       |
                 |   POST /chat/completions      (cleanup, notes)|
                 |   GET  /models                (key test)      |
                 +----------------------^------------------------+
                                        | HTTPS (HTTP only for private hosts)
             +--------------------------+--------------------------+
             |                                                     |
  +----------+-----------+                              +----------+-----------+
  |     Windows app      |                              |     Android app      |
  |   (Python, windows/) |                              |   (Java, android/)   |
  +----------------------+                              +----------------------+
```

## Windows app: two processes and the files between them

```
 Vox.exe  (engine process)                      Vox.exe --window  (window process)
 mutex Local\VoxEngine, log vox.log             mutex Local\VoxWindow, log window.log
 +----------------------------------+           +----------------------------------+
 | main thread : Tk overlay (pill)  |           | pywebview + Edge WebView2        |
 | pynput listener  (hotkey)        |           |   loads windows/ui/index.html    |
 | pystray icon     (tray menu)     |           |   JS  <->  ui_app.Api (js_api)   |
 | sounddevice callback (mic)       |           +--------------+-------------------+
 | _process worker (one per dictation)                         |
 | _watch_config  (1 s poll)        |   reads/writes           |
 | _watch_calendar (30 s poll)      |<-------------------------+
 | control HTTP server 127.0.0.1    |   %APPDATA%\Vox\config.json, history.jsonl, meetings\...
 | Meeting threads (recorders, STT, |   POST /meeting/* with X-Vox-Token (port+token in engine.json)
 |   final pass)                    |<---------------------------------------------------------
 +----------------------------------+
```

- **Engine process** (`windows/vox_app.py` -> `engine.Engine().run()`): owns everything that must run in the background. If a second copy starts it only opens the window.
- **Window process** (`vox_app.py --window` -> `ui_app.main()`): a normal window. It never records. It edits settings by writing `config.json`; the engine notices within a second (it compares the file's modified time). Live meeting actions go through the engine's local HTTP control server.
- **No shared memory.** The two processes communicate only through files in `%APPDATA%\Vox` and the localhost control server.

Threads in the engine process:

| Thread | Started by | Job |
|---|---|---|
| main | `Engine.run` | Tk `mainloop` of the overlay pill (Tk must own the main thread) |
| pynput listener | `Engine.run` | key press/release -> `on_press` / `on_release` -> hold-to-talk logic |
| pystray | `icon.run_detached()` | tray icon and menu |
| sounddevice callback | `Engine.start` | appends 16 kHz mono int16 chunks, updates `level` |
| `_process` | `Engine.stop` / `retry_last` | sends the audio, pastes the text, saves history |
| `_watch_config` | `Engine.run` | reloads `config.json` when its mtime changes (not while recording) |
| `_watch_calendar` | `Engine.run` | reminds or auto-starts meeting notes |
| `control` | `Engine.run` | localhost HTTP server for the window |
| Meeting threads | `Meeting.start` | one recorder per source (You, Others), one STT worker, one finisher |

Engine states (`Engine.state`, read by the overlay): `idle` -> `rec` (recording) -> `busy` (sending) -> `idle`. `hands_free` is a flag on `rec`. `Engine.pending` holds `(pcm, exe)` of a dictation that failed to send.

## Windows dictation flow

```
 hotkey down -> start(): check endpoint/key, remember foreground exe, start the StreamingStt worker,
                open the server connections (core.warm), open mic stream (chosen device)
                while recording: _audio feeds the level meter and the worker; the worker sends finished pieces
                (pauses, at least 12 s) of a LONG recording to speech-to-text (streaming.py)
 hotkey up   -> stop():  atomically leave recording
                  too short (< 0.4 s)?  -> idle
                  silent (peak < 655)?  -> notify "did not hear anything (loudest sound N)"
                  else                  -> _process(pcm, exe, note, streamer) on a worker thread
 _process: streamer.finish() gave text (long recording cut into pieces)?  -> core.process_text(text)
           otherwise                                                        -> core.process_detailed(whole pcm)
              transcribe (Whisper)  -> silence-hallucination filter
              cleanup (chat model)  -> looks_valid guard  (fallback: raw text + spoken commands)
              apply_replacements (dictionary "wrong => right")
           -> paste (Ctrl+V), history line (unless keep_history is off)
              or, in note mode (tray / Voice notes page): save to notes.db and ask the sync worker to send it
           on error: keep (pcm, exe, note) in Engine.pending, notify, tray "Retry last dictation"
```

Details: [04-windows-app.md](04-windows-app.md), [06-pipeline.md](06-pipeline.md).

## Android app: three components

```
 MainActivity (WebView UI)     VoxAccessibilityService            DictationService
 assets/index.html             (foreground detection, bubble,     (foreground service, type
 Java bridge "Vox"             text insertion)                    microphone)
      |  settings, setup           |  tap -> startRecording/stop       |  AudioRecord 16 kHz
      v                            |<---- Listener callbacks ----------|  ApiClient (HTTP)
   Prefs (SharedPreferences "vox") |  onState/onLevel/onResult/onError |  retry, pending audio
```

- **`DictationService`** (`android/src/com/minhaj/vox/DictationService.java`): foreground service with the microphone type. Android only allows it to start from a visible activity, so the bubble uses `TrampolineActivity` (an invisible activity that stays up only until the service is in the foreground and recording, at most 1500 ms) when the service is not running yet. The service starts recording itself from the extras of that start intent. It records, uploads, cleans up, and reports through the static `Listener`.
- **`VoxAccessibilityService`**: draws the bubble as an accessibility overlay (no "draw over apps" permission), tracks the focused editable field and its package, and inserts the result. It is the `Listener` of the service.
- **`MainActivity`**: a `WebView` showing `android/assets/index.html`. The page calls Java through the `Vox` JavaScript interface (`Bridge`) to read/save settings, test the key, list apps, and so on.
- **`Prefs`**: the only persistent store (SharedPreferences file `vox`).

States of `DictationService`: `IDLE` -> `RECORDING` -> `PROCESSING` -> `IDLE`. A monotonically increasing `jobId` makes every result and state change conditional on the job still being current, so a cancelled or replaced dictation cannot touch state or insert text.

Details: [05-android-app.md](05-android-app.md).

## Shared behaviour, duplicated code

The same functions exist in both languages:

| Behaviour | Python (`windows/vox_core.py`) | Java (`android/src/com/minhaj/vox/`) |
|---|---|---|
| Cleanup system prompt | `system_prompt` | `ApiClient.systemPrompt` |
| Whisper spelling hint | `whisper_prompt` | `ApiClient.whisperPrompt` |
| Strip model tags/quotes | `sanitize` | `ApiClient.sanitize` |
| Reject runaway cleanup answers | `looks_valid` | `ApiClient.looksValid` |
| Dictionary replacements | `apply_replacements` | `ApiClient.applyReplacements` |
| Dictionary terms | `dictionary_terms` | `Terms.terms` |
| Spoken "new line" | `apply_spoken_commands` | `ApiClient.applySpokenCommands` |
| Silence hallucinations | `is_silence_hallucination` | `ApiClient.isSilenceHallucination` |
| Silence gate | `is_silent` | `Pcm.isSilent` |
| Server address rules | `endpoint_error`, `is_private_host` | `Endpoint.error`, `Endpoint.isPrivateHost` |
| Correction suggestions | `suggest_corrections` | `Corrections.suggest` |
| Per-role settings, model classification | `providers.role_settings`, `providers.classify` | `Providers.roleSettings`, `Providers.classify` |
| About-you cleaning and the prompt with context | `clean_context`, `system_prompt` | `ApiClient.cleanContext`, `systemPrompt` |
| Meter level | `level_from_rms` | `Pcm.levelFromRms` |

`spec/golden.txt` holds expected results for the first eight rows; `tests/test_parity.py` and `android/test/.../ParityTest.java` both run it. See [decisions/0007-shared-golden-file.md](decisions/0007-shared-golden-file.md).

## External services

| Service | Used for | Where |
|---|---|---|
| Groq (or your own server) | Whisper speech-to-text, chat cleanup, meeting notes | `vox_core.py`, `providers.py`, `meeting.py`, `ApiClient.java`, `Providers.java` |
| Relay (optional, your own machine, over Tailscale) | Voice notes and profile shared between devices; management web page | `relay/relay.py` (server), `windows/sync.py` (Windows client) |
| Google Calendar API (optional, Windows) | Read-only event list for meeting notes | `gcal.py` |
| Private iCal (ICS) link (optional, Windows) | Same, without sign-in | `vcalendar.py` |
| GitHub Actions | Tests and builds | `.github/workflows/build.yml` |

There is no telemetry and no Vox backend.

## Modules added by the v2 series

| Module | Role |
|---|---|
| `windows/providers.py`, `Providers.java` | Which server, key and model each role uses; model lists; the Test button |
| `windows/notes.py` | Voice notes store (SQLite, search, sync flags) |
| `windows/sync.py` | Windows sync client (notes and profile) and its background worker |
| `windows/streaming.py` | Sends the finished pieces of a long recording while the user speaks |
| `relay/relay.py` | The optional relay server with a management web page; runs on a PC, Linux box or Raspberry Pi |

Sync in one line: the engine's `SyncWorker` sends changed notes to the relay (`PUT /notes/{id}`), fetches what changed elsewhere (`GET /changes`), then merges the shared profile settings; notes and settings always work without the relay. Details: [14-relay.md](14-relay.md), [decisions/0022-sync-client-dirty-flag-and-cursor.md](decisions/0022-sync-client-dirty-flag-and-cursor.md).
