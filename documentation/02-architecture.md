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
| pynput listener | `Engine.run` | key press/release -> `on_press` / `on_release`, which only queue the key (the hook must answer quickly) |
| vox-hotkey | `Engine.run` | `_hotkey_loop`: the queued keys in order -> `_on_press` / `_on_release` -> hold-to-talk, shortcuts, opening the microphone ([decision 0037](decisions/0037-esc-cancels-and-the-hook-only-queues-keys.md)) |
| pystray | `icon.run_detached()` | tray icon and menu |
| sounddevice callback | `Engine.start` | appends 16 kHz mono int16 chunks, updates `level` |
| `_process` | `Engine.stop` / `retry_last` | sends the audio, pastes the text, saves history |
| `_watch_config` | `Engine.run` | reloads `config.json` when its mtime changes (not while recording) |
| `_watch_calendar` | `Engine.run` | reminds or auto-starts meeting notes |
| `control` | `Engine.run` | localhost HTTP server for the window |
| Meeting threads | `Meeting.start` | one recorder per source (You, Others), one STT worker (`meeting-stt`), one finisher (`meeting-finish`) |
| `vox-stream` | `StreamingStt.start` | sends the finished pieces of a long recording to speech to text while it is spoken |
| `vox-warm` | `core.warm` | opens the connections to the speech and cleanup servers when a recording starts |
| `vox-last` | `Engine.insert_last` | paste last or copy last dictation (it may wait for Shift and Alt to come up) |
| `vox-listen-feed`, `vox-listen-stt`, `vox-listen-type` | `Listening.start` | keep listening: cut the audio at pauses, transcribe the pieces, type them (Type target) |
| `vox-autolearn` | `correction_watch.Watcher` | Learn from my corrections: reads the focused control every 2 s for up to 3 minutes after a paste |
| `vox-sync` | `sync.SyncWorker.start` | syncs voice notes and the profile with the relay |
| `overlay-watch` | `Engine.run` | `Engine.check_overlay` every second: the pill's heartbeat and stuck states |
| `improve` | `Engine.run` | `_watch_improve`: the weekly reminder for Improve my cleanup (a tray message only) |
| `relay-watch` | `RelayHost.start` | waits for the relay child process and reports an early exit |

Engine states (`Engine.state`, read by the overlay): `idle` -> `rec` (recording) -> `busy` (sending) -> `idle`. `hands_free` is a flag on `rec`. `Engine.pending` holds `(pcm, exe)` of a dictation that failed to send. A dictation's end is also signalled on the pill for a moment (`Engine.flash`: a green check or a red !) without changing `Engine.state`; see [04-windows-app.md](04-windows-app.md#result-signal-on-the-pill).

## Windows dictation flow

```
 hotkey down -> start(): check endpoint/key, remember foreground exe, start the StreamingStt worker,
                open the server connections (core.warm), open mic stream (chosen device)
                while recording: _audio feeds the level meter and the worker; the worker sends finished pieces
                (cut at the first pause after 6 s, at the latest 20 s; a recording under about 7 s goes whole) to speech-to-text (streaming.py)
 hotkey up   -> stop():  atomically leave recording
                  too short (< 0.4 s)?  -> idle
                  silent (peak < 655)?  -> notify "did not hear anything (loudest sound N)"
                  else                  -> _process(pcm, exe, note, streamer) on a worker thread
 _process: streamer.finish() gave text (long recording cut into pieces)?  -> core.process_text(text)
           otherwise                                                        -> core.process_detailed(whole pcm)
              transcribe (Whisper)  -> silence-hallucination filter
              cleanup (chat model)  -> looks_valid guard  (fallback: raw text + spoken commands, with sentence-start capitals when the guard rejected it)
              (code app: spoken formatters and symbols, codemode.py)
              apply_replacements (dictionary "wrong => right"), fuzzy_dictionary, snippets
              lists from spoken cues and pause paragraphs (structure.py; not in a code app)
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
- **`Prefs`**: the store for settings and dictation history (SharedPreferences file `vox`). Voice notes have their own SQLite database, `NotesStore` (`notes.db`; used by note mode and the Notes page, see [05-android-app.md](05-android-app.md)).

States of `DictationService`: `IDLE` -> `RECORDING` -> `PROCESSING` -> `IDLE`. A monotonically increasing `jobId` makes every result and state change conditional on the job still being current, so a cancelled or replaced dictation cannot touch state or insert text.

Details: [05-android-app.md](05-android-app.md).

## Shared behaviour, duplicated code

The same functions exist in both languages:

| Behaviour | Python (`windows/vox_core.py`) | Java (`android/src/com/minhaj/vox/`) |
|---|---|---|
| Cleanup system prompt | `system_prompt` | `ApiClient.systemPrompt` |
| Whisper spelling hint | `whisper_prompt` | `ApiClient.whisperPrompt` |
| Strip model tags/quotes | `sanitize` | `ApiClient.sanitize` |
| Reject runaway or word-losing cleanup answers | `looks_valid`, `fidelity_ok`, `word_recall` | `ApiClient.looksValid`, `Fidelity.ok`, `Fidelity.wordRecall` |
| The text used when the guard rejects an answer; the strength setting as light or standard | `fallback_text`, `clean_strength` | `ApiClient.fallbackText`, `Fidelity.cleanStrength` |
| Dictionary replacements | `apply_replacements` | `ApiClient.applyReplacements` |
| Dictionary terms | `dictionary_terms` | `Terms.terms` |
| Spoken "new line" | `apply_spoken_commands` | `ApiClient.applySpokenCommands` |
| Silence hallucinations | `is_silence_hallucination` | `ApiClient.isSilenceHallucination` |
| Silence gate | `is_silent` | `Pcm.isSilent` |
| Server address rules | `endpoint_error`, `is_private_host` | `Endpoint.error`, `Endpoint.isPrivateHost` |
| Correction suggestions | `suggest_corrections` | `Corrections.suggest` |
| Per-role settings, model classification | `providers.role_settings`, `providers.classify` | `Providers.roleSettings`, `Providers.classify` |
| About-you cleaning and the prompt with context | `clean_context`, `system_prompt` | `ApiClient.cleanContext`, `systemPrompt` |
| The learned cleanup rules (`my_cleanup_rules`) made safe and put into the prompt after the strength rule | `clean_rules`, `system_prompt(..., rules)` | `ApiClient.cleanRules`, `systemPrompt(..., rules)` (golden kinds `rules`, `promptrules`) |
| Meter level | `level_from_rms` | `Pcm.levelFromRms` |
| Voice-note title (first 7 words) and search string (`"word"*` tokens) | `notes.auto_title`, `notes.fts_query` | `NoteLogic.autoTitle`, `NoteLogic.ftsQuery` |
| Which version wins a note sync (strictly newer; a tie keeps the local one; a delete of an unknown note is ignored) | `notes.apply_remote` | `NoteLogic.remoteWins` |
| Profile sync merge (per field: the side that changed since the last sync wins, the relay wins a clash except that a list (dictionary, people) or the snippets map changed on both sides merges item by item: an item added on either side is kept, one removed on either side goes, an absent result drops the field) and which settings travel | `sync.merge3`, `sync.PROFILE_FIELDS`, `sync.PROFILE_KEY_FIELDS` | `ProfileMerge.merge3`, `mergeProfile`, `SHARED_FIELDS`, `KEY_FIELDS` |
| A note the relay refuses for good (a 4xx other than 401, 403, 429) is skipped for that run and does not stop the others; any other failure stops the run | `sync.SyncError.permanent`, `sync_once` | `RelayApi.RelayError.permanent()`, `SyncEngine.push` (not in the golden file; each side has its own test) |
| Dictionary and people in the profile (a list of text on the relay; the phone keeps them as lines) | `config.json` lists (`dictionary`, `people`) | `ProfileMap.lines`, `joinLines`, `toProfile`, `toStored` (see [05-android-app.md](05-android-app.md), "Relay sync") |
| Tag clean-up (quotes removed, trim, no empties, no repeats) | `notes._tags` | `NoteLogic.cleanTags` (not in the golden file; see [12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md)) |
| Device name on the relay and on notes (typed name trimmed and cut at 60 characters, counted as code points) | `sync.device_name` | `NoteLogic.deviceName` (the fallback when no name is typed, computer name or phone model, is not shared) |
| Whether cleanup runs (off, `raw` style, phrase shorter than `cleanup_min_words`) | `clean_min_words`, `needs_cleanup` | `ApiClient.cleanMinWords`, `ApiClient.needsCleanup` |
| The dictionary's spelling pass on the final text | `fuzzy_dictionary` | `Terms.fuzzy` |
| Lists from spoken cues (paragraph breaks at pauses are Windows only) | `structure.format_structure` | `Structure.format` |
| Snippets (a spoken phrase types saved text) | `snippets.apply_snippets`, `clean_snippets` | `Snippets.apply`, `Snippets.clean` |
| Learn from my corrections: finding the fixes and adding them to the dictionary (the watch itself differs: UI Automation on Windows, accessibility events on Android) | `autolearn.detect`, `autolearn.learn` | `AutoLearn.detect`, `AutoLearn.learn` |
| The Speed card's numbers (median, slowest 1 in 10, per model, the whole view) | `timing.speed_view` and the `timing_*` helpers | `Timing.speedView` and the same helpers |
| Whether a failed request is sent again; whether a relay refusal is permanent; the pause finder that cuts a long recording | `retryable`, `SyncError.permanent`, `Segmenter` | `ApiClient.retryable`, `RelayApi.RelayError.permanent`, `Segmenter` |

`spec/golden.txt` holds expected results for the prompt (with and without About you, by strength), spelling hint, sanitize, looks_valid, replacement, terms, spoken-command, silence-phrase, About-you, cleanup-strength, guard-fallback, model-classification, meter-level, cleanup-gate, voice-note title and search-string, note-sync winner, profile-merge, profile-field, device-name, structure, snippet, auto-learn, fuzzy-dictionary, timing, retry, private-host and Android bubble rows (the full list of kinds is in [06-pipeline.md](06-pipeline.md); a few rules have their own test on each side instead); `tests/test_parity.py` and `android/test/.../ParityTest.java` both run it. See [decisions/0007-shared-golden-file.md](decisions/0007-shared-golden-file.md).

## External services

| Service | Used for | Where |
|---|---|---|
| Groq (or your own server) | Whisper speech-to-text, chat cleanup, meeting notes | `vox_core.py`, `providers.py`, `meeting.py`, `ApiClient.java`, `Providers.java` |
| Relay (optional, your own machine, over Tailscale) | Voice notes and profile shared between devices; management web page | `relay/relay.py` (server), `windows/sync.py` (Windows client), `SyncEngine.java`, `RelayClient.java`, `SyncWorker.java` (Android client) |
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
| `windows/relay_host.py` | Starts and stops the relay as a child process of the engine (tray item "Run relay on this PC") |
| `windows/timing.py`, `Timing.java` | The timing of each dictation and the Speed card's numbers |
| `windows/structure.py`, `windows/codemode.py`, `windows/snippets.py` (and `Structure.java`, `Snippets.java`) | Lists from spoken cues, code mode (Windows), snippets: all after the cleanup, never sent to it |
| `windows/hotkeys.py`, `windows/command.py` | Shortcut rules (tap or hold, hands-free, paste and copy last, conflicts) and edit by voice |
| `windows/autolearn.py`, `windows/correction_watch.py` (and `AutoLearn.java`, `AutoLearnWatch.java`) | Learn from my corrections |
| `windows/overlay_guard.py`, `windows/overlay_mode.py` | Keeping the pill on screen; what the pill shows |
| `relay/relay.py` | The optional relay server with a management web page; runs on a PC, Linux box or Raspberry Pi, or inside `Vox.exe --relay` |

Sync in one line (the Android client, `SyncEngine` run by `SyncWorker`, does the same in the same order): the engine's `SyncWorker` sends changed notes to the relay (`PUT /notes/{id}`), fetches what changed elsewhere (`GET /changes`), then merges the shared profile settings; notes and settings always work without the relay. Details: [14-relay.md](14-relay.md), [decisions/0022-sync-client-dirty-flag-and-cursor.md](decisions/0022-sync-client-dirty-flag-and-cursor.md).
