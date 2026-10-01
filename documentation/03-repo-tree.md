# 3. Repository tree

Every tracked file is listed here with its purpose. `documentation/tools/check_docs.py` fails CI if a tracked file is missing from this page or a listed file no longer exists. Update this page in the same commit that adds, renames or removes a file.

## Shape

```
.github/workflows/      CI: tests, Windows installer, Android APK, release
android/                Android app (Java, no Gradle)
  assets/               the app's screens (one HTML file)
  res/                  icons, strings, accessibility and network config
  src/com/minhaj/vox/   all Java source
  test/com/minhaj/vox/  plain-Java tests (no device, no JUnit)
docs/                   the public website (GitHub Pages): landing page and privacy policy
documentation/          THIS folder: developer and agent documentation
.claude/skills/         project skills for Claude Code (documentation sync)
spec/                   golden.txt, expected results shared by Python and Java tests
relay/                 Optional relay server (Python, runs on Linux, Raspberry Pi, macOS, Windows)
tests/                  pytest tests for the Windows Python code
tools/                  repo scripts (generate the shared UI parts into both pages)
ui-shared/              palette, component CSS and helper JS shared by both pages (generated into them)
windows/                Windows app (Python) and its installer scripts
  ui/                   the main window's screens (one HTML file)
```

## Root

| Path | What it is |
|---|---|
| `README.md` | End-user guide for both apps, plus maintainer notes (release, signing, local build). User-facing; not this documentation. |
| `AGENTS.md` | Short entry point for coding agents; points here. |
| `CHANGELOG.md` | Release-style change history. |
| `.gitattributes` | Forces LF line endings for `*.sh` and `*.list` so the test runner works on Windows checkouts with `core.autocrlf=true`. |
| `.gitignore` | Keeps secrets (`config.json`, `google_client.json`, keystores), logs, build output, the local `.venv/` and the local agent scratch folder `.superpowers/` out of git. |

## CI

| Path | What it is |
|---|---|
| `.github/workflows/build.yml` | Jobs `tests` (pytest and the documentation checker), `windows` (PyInstaller + Inno Setup), `android` (Java tests + APK), `release` (on tags `v*`). Runs on tag push or manually. See [10-build-test-release.md](10-build-test-release.md). |

## Windows app (`windows/`)

| Path | What it is |
|---|---|
| `windows/vox_app.py` | Entry point. No argument: start the engine (or, if one is running, open the window). `--window`: open the main window. Sets up rotating logs and the single-instance mutexes. |
| `windows/vox.py` | Five-line shim that runs `vox_app.main`, kept for a launcher script that is not in git. |
| `windows/engine.py` | The background engine: tray icon, global hotkey, recording, paste, retry, config watcher, calendar watcher, local control server. Class `Engine`. |
| `windows/vox_core.py` | Pure-ish logic shared by everything on Windows: config load/save, dictionary, prompts, the HTTP calls to the server, the whole dictation pipeline (`process_detailed`), endpoint safety rules, silence gate, correction suggestions, history file. |
| `windows/secret.py` | Windows DPAPI protection for the API key stored in `config.json`. |
| `windows/audio_devices.py` | Lists microphones and resolves the chosen one by name. |
| `windows/overlay_mode.py` | Pure function `overlay_mode(...)`: which picture the pill shows (rec, busy, sent, error, meet, or hidden); no Tk, unit tested. |
| `windows/overlay.py` | The small recording pill (Tk window, click-through, never takes focus); also shows the green check / red ! after a dictation. |
| `windows/logo.py` | Draws the tray icons and generates `windows/vox.ico`. |
| `windows/ui_app.py` | The main window's Python side: pywebview window and the `Api` class the page calls. |
| `windows/providers.py` | Which server, key and model each role (speech, cleanup) uses; model list from `GET /models` and its classification; the Test button; reasoning-field retry. |
| `windows/notes.py` | Voice notes store: SQLite with search and filters, delete markers for a later sync. |
| `windows/sync.py` | Syncs voice notes and the profile with a relay: send changed notes (a note the relay refuses for good is skipped, not blocking), fetch new ones, background worker, connection test. |
| `windows/relay_host.py` | Runs the relay as a child process of the engine (`Vox.exe --relay`): the command line, start and stop, a hidden window, and a Windows job object so the child never outlives Vox. |
| `windows/paste.py` | `paste_text`: pastes into the focused app only if the window is still the one the dictation started in, and restores the old clipboard only if it still holds our text. The real Win32, clipboard and key calls are in `SystemDeps`; tests pass their own. |
| `windows/streaming.py` | Sends the finished parts of a long recording to speech-to-text while the user is still speaking (worker thread, falls back to the whole recording). |
| `windows/timing.py` | Pure timing core (stdlib): `Timing` marks (`key_down` ... `inserted`) become the six stage durations (`start`, `rec`, `stt`, `llm`, `insert`, `total`); `median`, `p90`, `biggest`, `format_ms`, `summarize` over the newest N history entries, `by_model` (medians per voice and cleanup model pair) and `speed_view` (everything the Speed card shows, from the history). Local only, nothing is sent. Java twin `Timing.java`. |
| `windows/ui/index.html` | The main window's screens: Home, Notes (meetings), Dictionary, Styles, Settings. One file with CSS and JavaScript. |
| `windows/meeting.py` | Meeting notes: records mic and PC audio, live transcript, final pass, speaker naming, notes generation, saved-meeting search. |
| `windows/gcal.py` | Optional Google sign-in (OAuth with PKCE, loopback redirect) and calendar reading. |
| `windows/vcalendar.py` | Calendar from an iCal (ICS) link; merges with Google events; 5-minute cache. |
| `windows/requirements.txt` | Pinned Python dependencies of the Windows app. |
| `windows/build_app.bat` | Local build-and-install script (creates a venv, runs PyInstaller, installs to `%LOCALAPPDATA%\Programs\Vox`). |
| `windows/installer.iss` | Inno Setup script for `VoxSetup.exe` (per-user, no admin rights). |
| `windows/vox.ico` | The app icon (generated by `windows/logo.py`). |

## Android app (`android/`)

| Path | What it is |
|---|---|
| `android/AndroidManifest.xml` | Permissions, the five components, network security config, version code and name. |
| `android/build.sh` | Builds and signs `Vox.apk` with plain SDK tools (aapt2, javac, d8, zipalign, apksigner). |
| `android/run-tests.sh` | The one Java test runner, used by CI and locally: compiles `android/testsrc.list` plus `android/test/**`, runs every `*Test` class, stops at the first failure. Needs `ANDROID_JAR`. `--integration` also runs `RelayIntegrationTest` (skipped otherwise) (it starts `relay/relay.py` itself; nothing is downloaded, because `RelayClient` reads JSON with `PlainJson`). |
| `android/compile-check.sh` | Local type-check of every file in `android/src` against `android.jar` (no aapt2, no APK): writes a stub `R.java` from the `R.<type>.<name>` uses, then `javac --release 8`. Needs `ANDROID_JAR`; not used by CI. |
| `android/testsrc.list` | The pure source files (no Android framework needed) that the tests compile, one path per line; add a line for each new pure class. |
| `android/assets/index.html` | The app's screens: Home (setup checklist, history), Dictionary, Styles, Settings. One file with CSS and JavaScript. |
| `android/src/com/minhaj/vox/MainActivity.java` | The screen: a WebView plus the `Bridge` object exposed to JavaScript as `Vox`. |
| `android/src/com/minhaj/vox/DictationService.java` | Foreground microphone service: record, save, send with retry, clean up, report. |
| `android/src/com/minhaj/vox/VoxAccessibilityService.java` | The floating bubbles (mic bubble and optional note bubble), focused-field tracking and text insertion. |
| `android/src/com/minhaj/vox/BubbleView.java` | Draws the bubble (idle, recording with level ring, processing spinner); the voice note variant has its own colour and icon. |
| `android/src/com/minhaj/vox/TrampolineActivity.java` | Invisible activity that lets the microphone service start from the foreground. |
| `android/src/com/minhaj/vox/Multipart.java` | The speech upload's multipart body with its exact length known up front (`length()` equals the bytes `writeTo` writes), so `ApiClient.transcribe` can send a `Content-Length` (the relay refuses chunked uploads with 411). Pure Java. |
| `android/src/com/minhaj/vox/ApiClient.java` | HTTP calls to the server plus the pure cleanup helpers (prompt, sanitize, replacements, spoken commands, silence filter, retry policy). |
| `android/src/com/minhaj/vox/Prefs.java` | All settings and the history, in SharedPreferences. |
| `android/src/com/minhaj/vox/Providers.java` | Java twin of `windows/providers.py`: per-role settings, model classification and parsing, messages, reasoning fields. |
| `android/src/com/minhaj/vox/Terms.java` | Parses the dictionary text into terms and replacements. |
| `android/src/com/minhaj/vox/Timing.java` | Pure Java twin of `windows/timing.py` (marks, stages, median, p90, biggest stage, "1.4 s" text, summary, `byModel`, `speedView` for the Speed card, `historyMap` the keys of a history row's timing); pinned by the `timing_*` rows of `spec/golden.txt`. |
| `android/src/com/minhaj/vox/Segmenter.java` | Pure Java twin of `Segmenter` in `windows/vox_core.py`: cuts a recording that is still going on into pieces at pauses (12 s minimum, 28 s maximum, 0.6 s pause); the `segcuts` golden rows prove it cuts where Windows does, whatever the block size. |
| `android/src/com/minhaj/vox/StreamingStt.java` | Pure Java twin of `windows/streaming.py`: a worker thread cuts the audio with `Segmenter` and sends each piece to speech to text (with the end of the text before it as context) while the user is still talking; `finish` returns the text, or null when the caller should send the whole recording. The server call is a `Transcriber` callback, so it is tested with a fake. |
| `android/src/com/minhaj/vox/Latency.java` | Pure latency rules: 5 s connect timeout, speech and cleanup read timeouts that grow with the audio and the words, which failures count as "never reached the server" (the fast retry), the cleanup `max_tokens` bound (floor of 256, headroom for thinking models, the cut-off check), and when to warm the connection again. |
| `android/src/com/minhaj/vox/UploadFormat.java` | Pure rule for the audio container of an upload (WAV under 4 s, m4a from 4 s), its type and file name, and when an encoded file is used. |
| `android/src/com/minhaj/vox/AudioUpload.java` | Makes the uploaded file: encodes the 16 kHz PCM as AAC in an m4a file (`MediaCodec` and `MediaMuxer`, 64 kbit/s) when `UploadFormat` says so, and falls back to the WAV on any encoder failure. Android classes, so it is only compile-checked here. |
| `android/src/com/minhaj/vox/NotificationActions.java` | Pure choice of the foreground notification buttons (at most three) and its Retry hint line. |
| `android/src/com/minhaj/vox/InsertGuard.java` | Pure typing guard: never type a restored dictation (empty target package), refuse a switched app, and the toast words. |
| `android/src/com/minhaj/vox/PinnedUrlConfig.java` | A `SyncConfig` with the relay address fixed for one sync run (the address is read once per run). |
| `android/src/com/minhaj/vox/Endpoint.java` | Server address rules (which hosts may use plain http). |
| `android/src/com/minhaj/vox/Pcm.java` | Silence gate for raw 16-bit audio. |
| `android/src/com/minhaj/vox/Corrections.java` | Suggests dictionary entries from a user's fix to a dictation. |
| `android/src/com/minhaj/vox/PendingQueue.java` | Pure queue of the unsent recordings of `DictationService` (one entry and file per failed recording, oldest first, at most 5, 7-day age rule, file-name format, and which in-flight job a cancel may discard). |
| `android/src/com/minhaj/vox/NoteLogic.java` | Pure voice-note rules shared with `windows/notes.py`: automatic title, search words and string, which side wins a sync merge, tag clean-up, push batch size. |
| `android/src/com/minhaj/vox/Note.java` | Plain value class for one voice note (or delete marker): the columns of the notes table. No Android or JSON classes, so the sync code and its tests can use it. |
| `android/src/com/minhaj/vox/NoteEvents.java` | The "a note was saved" hook: a static, thread-safe list of `Runnable` listeners (`addSavedListener`, `removeSavedListener`, `fireSaved`). Pure Java. |
| `android/src/com/minhaj/vox/NoteEntry.java` | What voice notes show outside the app: the intent that starts a note, the ongoing "Record note" notification and the "Note saved" notification. |
| `android/src/com/minhaj/vox/NoteTileService.java` | The quick settings tile "Voice note": starts a note through the trampoline, or stops the one being recorded. |
| `android/src/com/minhaj/vox/SyncStore.java` | Interface for what the relay sync needs from the notes on the device (`dirtyNotes`, `markSynced`, `applyRemote`, `getMeta`, `setMeta`); pure Java. |
| `android/src/com/minhaj/vox/NotesStore.java` | The voice notes database on the phone (`notes.db`, SQLite): a literal port of `windows/notes.py`, implements `SyncStore`. Needs Android's SQLite, so it is only compile-checked. |
| `android/src/com/minhaj/vox/DevicesView.java` | Pure rows for the Devices card from the relay's device list (name, this device, active/recent/old, "5 min ago"), the twin of `sync.devices_view`; run by the `devices` golden rows. |
| `android/src/com/minhaj/vox/RelayCheck.java` | Pure decision behind Test connection from the status and answer of `GET /health`: `ok`, `reachable`, `token_ok`, the relay version (kept only as 1 to 20 of letters, digits and `. + _ -`), the notes count and the message; the twin of `sync.relay_check`, run by the `relaycheck` golden rows. |
| `android/src/com/minhaj/vox/ProfileMerge.java` | Pure merge of the profile that follows the user between devices, shared with `windows/sync.py`: `merge3` for one field (the side that changed wins, the relay wins a clash), `mergeProfile` over a set of fields, and the two field lists `SHARED_FIELDS` and `KEY_FIELDS`. |
| `android/src/com/minhaj/vox/SyncEngine.java` | One relay sync run, a port of `windows/sync.py` `sync_once` and `sync_profile`: push changed notes, pull changes by cursor, merge the profile. Pure Java over `SyncStore`, `RelayApi` and `SyncConfig` with plain maps; never throws. |
| `android/src/com/minhaj/vox/RelayApi.java` | Interface for the four relay calls the sync needs (`putNote`, `changes`, `getProfile`, `putProfile`) with the `Changes`, `Profile` and `RelayError` types; `RelayError.permanent()` is the rule for a note the relay refuses for good. Pure Java. |
| `android/src/com/minhaj/vox/SyncConfig.java` | Interface for the settings side of the sync: the profile fields of the phone, the "share my keys" switch, and saving what arrives. Pure Java. |
| `android/src/com/minhaj/vox/SyncResult.java` | What one sync run did: notes sent, notes received, the error words, what happened to the profile. Pure Java. |
| `android/src/com/minhaj/vox/RelayClient.java` | `RelayApi` over `HttpURLConnection`: headers, paths, no redirects, every status and error-body shape turned into plain words; also `problem` (is this address and token usable) and `check` (Test connection, decided by `RelayCheck`). Pure Java, tested against a real local HTTP server. |
| `android/src/com/minhaj/vox/ProfileMap.java` | Pure conversion between the phone's settings and the relay's profile fields, in the encodings Windows uses (the dictionary and people as lists of text); cleans every value the same way both ways. |
| `android/src/com/minhaj/vox/PlainJson.java` | Small strict JSON reader and writer in plain Java (org.json cannot run in the off-device tests); used by `RelayClient` and for the profile snapshot. |
| `android/src/com/minhaj/vox/SyncWorker.java` | The `vox-sync` thread: runs the engine on app resume, service start, settings save and every 90 s while the process lives; keeps the status the page shows. Needs a device, so it is only compile-checked. |
| `android/res/values/strings.xml` | App name, the tile label and the accessibility service label and description. |
| `android/res/xml/accessibility_config.xml` | Accessibility service configuration (event types, content access). |
| `android/res/xml/network_security_config.xml` | Allows cleartext at OS level; the app enforces the private-host rule itself. |
| `android/res/drawable/ic_launcher_bg.xml` | Launcher icon background. |
| `android/res/drawable/ic_launcher_fg.xml` | Launcher icon foreground. |
| `android/res/drawable/ic_stat_mic.xml` | Notification icon. |
| `android/res/drawable/ic_note.xml` | Note page icon: the quick settings tile and the small icon of the voice note notifications. |
| `android/res/mipmap-anydpi-v26/ic_launcher.xml` | Adaptive launcher icon definition. |

## Tests

| Path | What it covers |
|---|---|
| `tests/conftest.py` | Puts `windows/` on the import path for the tests. |
| `tests/requirements.txt` | Pinned test dependencies (`requests`, `pytest`). |
| `tests/test_vox_core.py` | Prompt, sanitize, replacements, dictionary, style, WAV and silence-phrase helpers. |
| `tests/test_endpoint_config.py` | Configurable server address, auth header, key test, cleanup fallback. |
| `tests/test_endpoint_safety.py` | Private-host rule, `endpoint_error`, `key_missing`, `ApiError`. |
| `tests/test_secret.py` | DPAPI wrapper and how `config.json` stores the key. |
| `tests/test_robustness.py` | HTTP retry policy and the silence gate. |
| `tests/test_suggest_corrections.py` | Dictionary suggestions from user fixes. |
| `tests/test_spoken_commands.py` | Spoken "new line" and the cleanup-failure result. |
| `tests/test_audio_devices.py` | Microphone name resolution. |
| `tests/test_parity.py` | Runs `spec/golden.txt` against the Python helpers. |
| `tests/test_meeting_stt.py` | The meeting recorder's own speech-to-text retry loop: a read timeout through the relay is sent once, otherwise four attempts (stubs numpy when it is missing). |
| `tests/test_providers.py` | Per-role settings, key isolation, model discovery, Test button, reasoning retry, the relay as the AI server (routes, headers, no key leaks, error shapes, the Settings page hiding the provider fields). |
| `tests/test_warmup.py` | Connection warm-up (`vox_core.warm`) and the shared session. |
| `tests/test_user_context.py` | The "about you" context: cleaning, prompt placement, sent with cleanup. |
| `tests/test_level.py` | The meter curve and the scrolling level history. |
| `tests/test_notes.py` | The notes store: add, edit, delete, search with FTS5 and the LIKE fallback, filters. |
| `tests/test_relay.py` | The relay over real HTTP: auth, sync cursor, conflicts, delete markers, search, profile versions, limits. |
| `tests/test_relay_admin.py` | The relay's management page and endpoints, portability and file permissions, and the AI server (proxy) settings: address rules, write-only keys that never appear in any response, download or output. |
| `tests/test_relay_cli.py` | Running the relay from the app: `vox_app.py --relay` as a real subprocess (no GUI libraries loaded), the command line, `RelayHost` with a fake process, the child dying with its parent, the tray toggle, and the build inputs. |
| `tests/test_sync_devices.py` | The Devices card's Windows side against a real relay: `fetch_devices` (wrong token, unreachable, relay too old, answers that are not a device list), `devices_for_ui` (rows, "this device", an empty list plus the reason on failure) and `devices_view` edge cases. |
| `tests/test_ui_devices.py` | The Devices card on both pages: the shared `devicesHtml` builder run with node (rows, badge, empty and error states, escaping), its styles, ids, position under the relay settings, each page's bridge call, the preview stand-in. |
| `tests/test_relay_check.py` | Test connection against a real relay: `sync.test_relay` and `relay_check` (version, token, device name, wrong token, another tailnet user, unreachable, answers that are not a relay's, no request for unusable settings, the token in no field) and the Windows `sync_test` bridge. |
| `tests/test_ui_relay_help.py` | The "How to set up the relay" card and the Test connection rows: the steps source and its parser, the generated block in both pages, every command in `relay/README.md`, copy buttons, position, escaping, `sync_ui` failing on a stale or hand-edited block, `relayCheckRows` run with node. |
| `tests/test_relay_devices.py` | `GET /devices`: token required, newest first, same fields as the management page, owner check, other methods refused. |
| `tests/test_relay_proxy.py` | The relay's proxy routes against a stand-in upstream server that records what it receives: fixed URL and path tricks, headers and keys (the relay token never goes on, the upstream key never comes back), size limits, 411/413/429/502/503, slots and timeouts. |
| `tests/test_sync.py` | The Windows sync client against a real relay: two devices, edits, deletes, conflicts, failures, notes the relay refuses for good, upgrade of old databases. |
| `tests/test_sync_profile.py` | Profile sync between two devices through a real relay: merge rules, keys switch, races. |
| `tests/test_streaming.py` | The pause finder (`Segmenter`), the streaming worker, and the text half of the pipeline. |
| `tests/test_timing.py` | The timing core: stage maths with missing marks and a backwards clock, median and p90, biggest stage, text format, summaries (skipped cleanup not counted as 0 ms), per-model medians and the Speed card's `speed_view`. |
| `tests/test_timing_pipeline.py` | Where the Windows marks are set: the per-thread `core.timing_scope` (stt and llm marks, a failed cleanup still closes its mark, one thread only), `core.timing_info`, and the window's `get_speed`. |
| `tests/test_ui_speed.py` | The Speed card: the shared renderer `speedHtml` / `fmtMs` run with node (biggest stage marked, dash for a stage that did not run, names escaped, empty state), and the card's ids and bridge call on both pages. |
| `tests/test_engine_notes.py` | The engine's voice-note mode (skipped where the Windows runtime packages are missing). |
| `tests/test_engine_flash.py` | The pill's "sent" and "error" signal: `Engine.flash` timing, expiry, what cancels it, no flash without a pill, and which events raise which one (skipped where the Windows runtime packages are missing). |
| `tests/test_overlay_mode.py` | Every branch of `overlay_mode` (flash over the meeting timer, flash only while idle). |
| `tests/test_flash_constants.py` | Drift guard: `BubbleView.SENT_MS` / `ERROR_MS` equal `FLASH_SECONDS` in `engine.py`. |
| `tests/test_paste.py` | `paste_text` with injected fakes (window unchanged or changed, clipboard restore rules) and the engine's "Copied; the window changed" notice. |
| `tests/test_docs_todo.py` | The path-to-page rules of `documentation/tools/docs_todo.py`. |
| `tests/test_ui_shared.py` | `tools/sync_ui.py --check` passes on the committed pages and fails when a generated block is edited by hand (on temp copies). |
| `tests/test_ui_static.py` | Static checks of both HTML pages: every looked-up id exists, no duplicate ids, every bridge call (`api().NAME`, `V.NAME(`) names a real method of `Api` / `MainActivity.Bridge`. |
| `spec/golden.txt` | Shared expected results (sanitize, looks_valid, replacements, whisper prompt, terms, system prompt, spoken commands, silence, note titles, note search strings, sync merge, profile merge and its field lists, the Devices card's rows). Read by the Python and Java parity tests. |
| `android/test/com/minhaj/vox/ApiClientTest.java` | Prompt, sanitize, replacements, retry policy, silence phrases. |
| `android/test/com/minhaj/vox/EndpointTest.java` | Server address rules. |
| `android/test/com/minhaj/vox/NotificationActionsTest.java` | Notification buttons (never more than three in any state), the Retry hint and the typing guard. |
| `android/test/com/minhaj/vox/PcmTest.java` | Silence gate. |
| `android/test/com/minhaj/vox/TimingTest.java` | The Java timing core: stages, skipped cleanup, clock, summary rules, per-model medians, `speedView` from history rows. |
| `android/test/com/minhaj/vox/SegmenterTest.java` | `Segmenter` beyond the golden rows: nothing lost, the same pieces for any block size, reuse after `rest()`. |
| `android/test/com/minhaj/vox/StreamingSttTest.java` | `StreamingStt` with a fake server: order and context, the first piece going out before the recording ends, only the tail left after, failure, slow server, silent and hallucinated pieces, cancel. |
| `android/test/com/minhaj/vox/LatencyTest.java` | The timeout and token rules of `Latency`, the connect-failure classification and the `UploadFormat` rule. |
| `android/test/com/minhaj/vox/CorrectionsTest.java` | Correction suggestions. |
| `android/test/com/minhaj/vox/PendingQueueTest.java` | The unsent-recordings queue: oldest-first order, cap drops the oldest, cancel rules (live recording and Retry discard nothing, only a fresh queued entry), remove on success, age purge, file names. |
| `android/test/com/minhaj/vox/DevicesViewTest.java` | `DevicesView` beyond the golden rows: order, entries that are not objects, unusable times, the Android header spelling of a name, age rounding. |
| `android/test/com/minhaj/vox/NoteLogicTest.java` | Note rules beyond the golden rows: Python-style whitespace and `strip`, search words, tag clean-up and its cap, null inputs, merge edge cases. |
| `android/test/com/minhaj/vox/NoteTest.java` | The `Note` value class: defaults and `copy`. |
| `android/test/com/minhaj/vox/NoteEventsTest.java` | `NoteEvents`: order, no double add, remove, a failing listener, adding during a fire, several threads. |
| `android/test/com/minhaj/vox/ParityTest.java` | Runs `spec/golden.txt` against the Java helpers. |
| `android/test/com/minhaj/vox/ProfileMergeTest.java` | Profile merge beyond the golden rows: lists and booleans, removals, null maps, fields outside the set, inputs left unchanged, the field lists. |
| `android/test/com/minhaj/vox/SyncEngineTest.java` | The sync engine against an in-memory store and a fake relay that follows the relay's rules: push, not-applied, offline and killed runs, notes the relay refuses, stopping errors, paging and cursor, delete markers, profile (version 0, both changed, 412 retry, keys on and off), "never throws", loop guards, the wire form. |
| `android/test/com/minhaj/vox/MultipartTest.java` | The computed length equals the bytes written for empty, unicode and large combinations; the framing; a file that shrank. |
| `android/test/com/minhaj/vox/ProxyUploadIntegrationTest.java` | Only with `run-tests.sh --integration`: `ApiClient.transcribeRaw` through the real relay's `/proxy/stt` to a stub AI server (JDK `HttpServer`): the upload arrives with a `Content-Length`, not chunked, byte for byte. |
| `android/test/com/minhaj/vox/RelayIntegrationTest.java` | Only with `run-tests.sh --integration`: starts the real `relay/relay.py` (free port, temp data folder) and syncs two or three phones (`SyncEngine` over `RelayClient`, in-memory notes) through it: a note and its delete marker travel, an older edit loses, a note the relay refuses does not block the next, a profile conflict and a real 412, a wrong token, 201 notes over several pages. |
| `android/test/com/minhaj/vox/RelayClientTest.java` | `RelayClient` against a real HTTP server on this computer: headers, paths, bodies, every status and error-body shape, network failure, no redirects, `check`, `listDevices`, `problem`. |
| `android/test/com/minhaj/vox/ProfileMapTest.java` | Phone settings to profile fields and back: round trips of each shared field, empty About you, the Windows shape, wrong types, addresses, key fields. |
| `android/test/com/minhaj/vox/PlainJsonTest.java` | The JSON reader and writer: values, escapes, numbers, strict errors, depth limit, exact round trip of timestamps. |
| `android/test/com/minhaj/vox/ProvidersTest.java` | Per-role settings, key rule, reasoning fields, messages (Java twin of part of `tests/test_providers.py`). |

## Agent tooling (`.claude/`)

| Path | What it is |
|---|---|
| `.claude/skills/vox-doc-sync/SKILL.md` | Project skill for Claude Code: the end-of-session routine that syncs the documentation with the code (uses `documentation/tools/docs_todo.py` and `check_docs.py`). |

## Relay (`relay/`)

| Path | What it is |
|---|---|
| `relay/relay.py` | The optional relay server (standard library only, Python 3.9+): notes with a change cursor, one versioned profile, bearer-token auth, management web page, AI server (proxy) settings with write-only keys; listens on 127.0.0.1. |
| `relay/vox-relay.service` | systemd unit to run the relay as a service on Linux (Raspberry Pi). |
| `relay/README.md` | Set-up guide for the relay: Raspberry Pi, Linux, Windows, macOS. |

## Public website (`docs/`)

| Path | What it is |
|---|---|
| `docs/index.html` | Landing page (GitHub Pages). |
| `docs/privacy.html` | Privacy policy (it also covers the optional Google Calendar use). |
| `docs/style.css` | Styles for the two pages. |
| `docs/favicon.svg` | Site icon. |

## This documentation (`documentation/`)

| Path | What it is |
|---|---|
| `documentation/README.md` | Index and the rules for keeping the docs true. |
| `documentation/01-overview.md` | What Vox is and is not. |
| `documentation/02-architecture.md` | Processes, threads, components, flows. |
| `documentation/03-repo-tree.md` | This page. |
| `documentation/04-windows-app.md` | The Windows app in depth. |
| `documentation/05-android-app.md` | The Android app in depth. |
| `documentation/06-pipeline.md` | Speech and cleanup pipeline, prompts, errors, golden file. |
| `documentation/07-config-and-data.md` | Settings, files on disk, data formats. |
| `documentation/08-features.md` | Feature list with locations and tests. |
| `documentation/09-security-privacy.md` | Data flows, protections, gaps. |
| `documentation/10-build-test-release.md` | Build, test, CI, release, environment traps. |
| `documentation/11-logs-and-diagnostics.md` | Logs and troubleshooting. |
| `documentation/12-known-issues-and-roadmap.md` | Open problems and plans. |
| `documentation/13-glossary.md` | Terms. |
| `documentation/14-relay.md` | The relay server: what it is, protocol, rules, what is not built. |
| `documentation/devlog.md` | Chronological development log. |
| `documentation/decisions/README.md` | Index of architecture decision records and the template. |
| `documentation/decisions/0001-openai-compatible-api-groq-default.md` | ADR: OpenAI-compatible API, Groq default. |
| `documentation/decisions/0002-two-independent-apps.md` | ADR: separate Python and Java apps. |
| `documentation/decisions/0003-android-accessibility-bubble.md` | ADR: accessibility overlay bubble and text insertion. |
| `documentation/decisions/0004-windows-two-process-model.md` | ADR: engine process plus window process. |
| `documentation/decisions/0005-configurable-endpoint-private-http.md` | ADR: server address, http only for private hosts. |
| `documentation/decisions/0006-app-name-only-to-the-model.md` | ADR: send the exe or app name, never the window title. |
| `documentation/decisions/0007-shared-golden-file.md` | ADR: parity through a shared test file, not shared code. |
| `documentation/decisions/0008-dpapi-for-the-windows-api-key.md` | ADR: protect the key with DPAPI. |
| `documentation/decisions/0009-keep-failed-recordings-and-retry.md` | ADR: never lose a dictation. |
| `documentation/decisions/0010-job-token-for-android-dictation-state.md` | ADR: job ids instead of a shared cancelled flag. |
| `documentation/decisions/0011-silence-gate-before-upload.md` | ADR: level check before upload. |
| `documentation/decisions/0012-no-gradle-android-build.md` | ADR: build the APK with plain SDK tools. |
| `documentation/decisions/0013-raw-fallback-when-cleanup-fails.md` | ADR: raw transcript as the safe fallback. |
| `documentation/decisions/0014-documentation-checked-in-ci.md` | ADR: this folder is machine-checked. |
| `documentation/decisions/0015-sync-docs-every-session.md` | ADR: documentation sync at the end of every session. |
| `documentation/decisions/0016-per-role-server-and-model-discovery.md` | ADR (proposed): separate server per role, model list classified by id. |
| `documentation/decisions/0017-warm-connections-not-an-open-microphone.md` | ADR: warm the server connections at key-down; the microphone is never open while idle. |
| `documentation/decisions/0018-about-you-context-in-the-prompt.md` | ADR: the "About you" context is constant, fenced and capped. |
| `documentation/decisions/0019-voice-notes-in-sqlite.md` | ADR: voice notes in SQLite, separate from history and meetings for now. |
| `documentation/decisions/0020-relay-design.md` | ADR: relay on loopback with tailnet transport, token auth and a sequence cursor. |
| `documentation/decisions/0021-relay-portable-with-a-web-page.md` | ADR: relay moved to `relay/`, portable, with a management web page. |
| `documentation/decisions/0022-sync-client-dirty-flag-and-cursor.md` | ADR: the Windows sync client uses a dirty flag per note and the relay's cursor. |
| `documentation/decisions/0023-profile-sync-three-way-merge.md` | ADR: profile sync with a fixed field set, field-by-field merge, keys only by choice. |
| `documentation/decisions/0024-stream-long-dictations-in-pieces.md` | ADR: long dictations are transcribed in pieces while the user speaks. |
| `documentation/decisions/0025-note-bubble-in-the-accessibility-service.md` | ADR: the note bubble is drawn by the accessibility service; the tile and the notification are other entry points. |
| `documentation/decisions/0026-android-notes-and-sync-are-ports-with-shared-golden-rows.md` | ADR: the Android notes store and sync are ports of the Windows code, with pure logic shared through golden rows. |
| `documentation/decisions/0027-relay-proxy-per-role-whitelisted-write-only-keys.md` | ADR: the relay proxy is per role, whitelisted, with write-only keys. |
| `documentation/decisions/0028-shared-ui-parts-are-generated-into-both-pages.md` | ADR: the palette, component CSS and helpers both pages share are generated into them from `ui-shared/`. |
| `documentation/decisions/0029-paste-checks-the-window-clipboard-default-off.md` | ADR: paste only into the window the dictation started in; `keep_clipboard` defaults to off. |
| `documentation/decisions/0031-timings-stay-on-the-device.md` | ADR: dictation timings are a field of the history entry, kept on the device, never sent or synced. |
| `documentation/decisions/0034-devices-list-is-the-relays-own-list-asked-on-a-switch-or-a-press.md` | ADR: the devices list is the relay's own (`GET /devices`), asked only on a switch or a press; a 401 or 403 counts as reachable. |
| `documentation/specs/README.md` | Index of design specs (written before the code they describe). |
| `documentation/specs/p1-providers-and-models.md` | Spec for sub-project P1: any provider, per-role server, model list, Test button. |
| `documentation/specs/p2a-keydown-warmup.md` | Spec for P2a: warm connections at key-down. |
| `documentation/specs/p3-about-you-context.md` | Spec for P3: "About you" context. |
| `documentation/specs/p4-live-voice-level.md` | Spec for P4: live voice level. |
| `documentation/specs/p5-voice-notes-windows.md` | Spec for P5: voice notes on Windows. |
| `documentation/specs/p7a-relay-server.md` | Spec for P7a: the relay server. |
| `documentation/specs/p7b-relay-portable-and-web-page.md` | Spec for P7b: relay on a Raspberry Pi with a web page. |
| `documentation/specs/p7c-windows-sync-client.md` | Spec for P7c: the Windows sync client. |
| `documentation/specs/p7d-profile-sync.md` | Spec for P7d: profile sync. |
| `documentation/specs/p7f-relay-proxy.md` | Spec for P7f: the relay as the AI server (proxy routes, upstream settings, the apps' switch). |
| `documentation/specs/p2b-stream-long-dictations.md` | Spec for P2b: send long recordings in pieces while speaking. |
| `documentation/specs/p6-android-note-mode.md` | Spec for P6: Android note mode (faster start, note bubble, notification, tile), with the device checklist. |
| `documentation/specs/p7e-android-sync.md` | Spec for P7e: Android relay sync and profile merge, with the device checklist and known limits. |
| `documentation/specs/p8c-quick-wins.md` | Spec for P8c: the quick wins (Java test runner and compile check, `ApiClient` rename, `cleanup_min_words`, the relay run from the Windows app), with what was and was not verified. |
| `documentation/specs/p9c-devices-and-relay-setup.md` | Spec for P9c (part 3, branch C): the relay's device list, the Devices card, Test connection details and the relay set-up card, with the device checklist and what was not verified. |
| `documentation/specs/p8b-design-refresh.md` | Spec for P8b: shared UI parts, regrouped settings and Status card, result flash, safer paste, privacy rewrite, with what was not verified. |
| `documentation/specs/p9b-measure-and-speed-up.md` | Spec for P9b: timings of every dictation, the Speed card on both apps and the Android speed work. |
| `ui-shared/tokens.css` | The palette both pages share (light values and a `@dark` block; Windows gets a `prefers-color-scheme` media query, Android a `.dark` class rule). |
| `ui-shared/components.css` | The CSS declarations that are identical in both pages for `.card .btn .chips .chip .switch .status .srow .hint .day .entry`; each page keeps its own sizes and spacing next to it. |
| `ui-shared/relay-steps.txt` | The one source of the "How to set up the relay" card (intro, numbered steps, the commands of each step, closing notes); `tools/sync_ui.py` writes it into both pages and `relay/README.md` must show the same commands. |
| `ui-shared/common.js` | Pure helpers: `STYLES`, `ABOUT_MAX`, `$`, `esc`, `toast`, `dictRepls`, `dictLines`, `aboutCount`, `agoText`, `combineTests`, `statusRows`, `statusHtml` (the Home status card), `devicesHtml`, `relayCheckRows` (the rows under Test connection). Bridges stay in each page. |
| `tools/sync_ui.py` | Writes the `ui-shared` blocks into `windows/ui/index.html` and `android/assets/index.html` between the `ui-shared:css`, `ui-shared:js` and `ui-shared:steps` marker comments (the last one is HTML made from `ui-shared/relay-steps.txt`); `--check` verifies. |
| `documentation/tools/check_docs.py` | The documentation checker (tree, config keys, links, ADR index). |
| `documentation/tools/docs_todo.py` | Prints which pages to update for the code that changed (checklist only, edits nothing). |
