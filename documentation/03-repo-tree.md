# 3. Repository tree

Every tracked file is listed here with its purpose. `documentation/tools/check_docs.py` fails CI if a tracked file is missing from this page or a listed file no longer exists. Update this page in the same commit that adds, renames or removes a file.

## Shape

```
.github/                CI workflow (tests, Windows installer, Android APK, release), issue forms, PR template
android/                Android app (Java, no Gradle)
  assets/               the app's screens (one HTML file)
  res/                  icons, strings, accessibility and network config
  src/com/minhaj/vox/   all Java source
  test/com/minhaj/vox/  plain-Java tests (no device, no JUnit)
docs/                   the public website (GitHub Pages): landing page, privacy policy and the screenshots
documentation/          THIS folder: developer and agent documentation
.claude/skills/         project skills for Claude Code (documentation sync)
spec/                   golden.txt, expected results shared by Python and Java tests
relay/                 Optional relay server (Python, runs on Linux, Raspberry Pi, macOS, Windows)
tests/                  pytest tests for the Windows Python code
tools/                  repo scripts (generate the shared UI parts into both pages; the speech and cleanup benchmarks, their corpus, labelled guard pairs and recorder; render the screenshots)
ui-shared/              palette, component CSS and helper JS shared by both pages (generated into them)
windows/                Windows app (Python) and its installer scripts
  ui/                   the main window's screens (one HTML file)
```

## Root

| Path | What it is |
|---|---|
| `README.md` | The project's front page for outsiders: what Vox is, what is new in 2.0, features, screenshots (rendered with sample data) and the ones still to take, status and known limits, install (Windows, Android with the sideload warnings), short user guide, privacy summary, build and test, repository map, contributing and the licence (MIT). User-facing; not this documentation. |
| `AGENTS.md` | Short entry point for coding agents; points here. |
| `CHANGELOG.md` | Release-style change history. |
| `CONTRIBUTING.md` | How to contribute: setup, running the tests, the golden-rows rule, the docs-sync rule, the pull request checklist, and that contributions are accepted under the MIT licence. |
| `SECURITY.md` | How to report a vulnerability privately (GitHub private vulnerability reporting) and a short trust model; links to [09-security-privacy.md](09-security-privacy.md) and the relay pages. |
| `LICENSE` | The MIT licence, "Copyright (c) 2026 Vox contributors". |
| `CODE_OF_CONDUCT.md` | Points to the Contributor Covenant 2.1 and says how to raise a conduct concern privately. |
| `.gitattributes` | Forces LF line endings for `*.sh` and `*.list` so the test runner works on Windows checkouts with `core.autocrlf=true`. |
| `.gitignore` | Keeps secrets (`config.json`, `google_client.json`, keystores), logs, build output, the local `.venv/` and the local agent scratch folder `.superpowers/` out of git. |

## CI

| Path | What it is |
|---|---|
| `.github/workflows/build.yml` | Jobs `tests` (pytest and the documentation checker), `windows` (PyInstaller + Inno Setup), `android` (Java tests + APK), `release` (on tags `v*`), plus `relay` (relay tests on Python 3.9 and 3.13, x86 and arm64), `relay-exe` (standalone relay programs for Windows, Linux x64 and Linux arm64, each smoke-tested) and `relay-sums` (`SHA256SUMS.txt`). Runs on pull requests (without `windows` and `release`), on tag push or manually. See [10-build-test-release.md](10-build-test-release.md). |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | Issue form for a bug (part, version, system, provider, steps, logs with personal text removed). |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | Issue form for a feature idea (problem, idea, what it would send or store). |
| `.github/ISSUE_TEMPLATE/device_test.yml` | Issue form for the result of a device checklist from `documentation/specs/` (phone, PC or relay), step by step. |
| `.github/ISSUE_TEMPLATE/config.yml` | Allows blank issues; links to the documentation, the relay guide and private security reporting. |
| `.github/PULL_REQUEST_TEMPLATE.md` | Pull request description: what and why, how it was tested, and the checklist from `CONTRIBUTING.md`. |

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
| `windows/overlay_guard.py` | Pure checks that keep the pill on screen: is a re-assert due, is its rectangle on a monitor, what is wrong with the window (`problems`), the log text (`describe`), a rate limit, the Tk-tick stall check, the thread dump, and the stuck-state check (`state_consistent`, `StuckWatch`); no Tk, no Win32, unit tested. |
| `windows/overlay.py` | The small recording pill (Tk window, click-through, never takes focus); also shows the green check / red ! after a dictation. |
| `windows/logo.py` | Draws the tray icons and generates `windows/vox.ico`. |
| `windows/ui_app.py` | The main window's Python side: pywebview window and the `Api` class the page calls. |
| `windows/providers.py` | Which server, key and model each role (speech, cleanup) uses; model list from `GET /models` and its classification; the Test button; reasoning-field retry. |
| `windows/notes.py` | Voice notes store: SQLite with search and filters, delete markers for a later sync. |
| `windows/sync.py` | Syncs voice notes and the profile with a relay: send changed notes (a note the relay refuses for good is skipped, not blocking), fetch new ones, background worker, connection test. |
| `windows/relay_host.py` | Runs the relay as a child process of the engine (`Vox.exe --relay`): the command line, start and stop, a hidden window, and a Windows job object so the child never outlives Vox. |
| `windows/session.py` | Keep listening, pure part (no hardware): `ListenSession` (utterances cut at pauses, texts back in any order, stop phrase, 60 minute limit), `same_target` (type only into the chosen window) and `SessionBuffer` (audio appended to a file, left-over sessions listed for recovery). |
| `windows/listen.py` | Keep listening, running part: `Listening` takes the microphone audio through a `ListenSession`, turns each piece into text in the background (`streaming.piece_text`) and ends in one cleaned note (Note target) or typed pieces (Type target, only in the app it started in); marks `seg_end` and `seg_text` per piece. |
| `windows/paste.py` | `paste_text`: pastes into the focused app only if the window is still the one the dictation started in (Shift+Insert in PuTTY and mintty, Ctrl+Shift+V in other terminals, Ctrl+V elsewhere; only copied when the window runs as administrator and Vox does not), and restores the old clipboard (all copyable formats, read only when it will be restored) only if it still holds our text; the dictation is set with the exclude-from-history markers. `copy_selection` copies the selected text for edit by voice. The real Win32, clipboard and key calls are in `SystemDeps`; tests pass their own. |
| `windows/hotkeys.py` | The extra shortcuts, pure: parse (`hands_free_hotkey`, `paste_last_hotkey`, `copy_last_hotkey`, `command_hotkey`), the conflict rules against the dictation and note shortcuts (`check`), `hotkey_style` and the tap-or-hold rule (`tap_action`). |
| `windows/command.py` | Edit by voice (experimental): the strict prompt, the request to the cleanup server and the guard that refuses an empty, unchanged or far too long or short answer. |
| `windows/streaming.py` | Sends the finished parts of a long recording to speech-to-text while the user is still speaking (worker thread, falls back to the whole recording). |
| `windows/warm_mic.py` | The optional warm microphone (setting `warm_mic`, off by default): `RingBuffer` (the last 400 ms of audio, in memory only) and `WarmMic` (keeps one input stream open, hands the held audio and then the live blocks to a recording, reopens after a device change or a dead stream, one notice per failure spell). Knows nothing about `sounddevice`; `Engine._sync_warm` and `_begin_capture` wire it. |
| `windows/structure.py` | Lists from spoken cues (`format_structure`: ordinals, point/item/step/number one, Hindi ordinals, bullet cues; never commas) and paragraph breaks at long pauses (`add_paragraphs`, Windows only, from the speech server's segment times). Pure; Java twin `Structure.java` (lists only), golden kind `structure`. |
| `windows/codemode.py` | Code mode (Windows only): spoken formatters (camel case, snake case, ...) and the spoken symbol table, `is_code_app` (the `code_apps`, `code_mode` settings and the per-app style `code`). Pure. Table: [15-code-mode.md](15-code-mode.md). |
| `windows/rules_layer.py` | The rules layer: deterministic cleanup without the AI (`rules_cleanup`: pure noises and spoken punctuation commands out, Standard typed-value self-corrections, capitals and the final mark for the style; `capitals`). Used by `vox_core.fallback_text` when the AI cleanup was wanted but gave no text. Pure; Java twin `RulesLayer.java`, golden kinds `rulelayer` and `fallback`. |
| `windows/snippets.py` | Snippets: `clean_snippets` (caps), `apply_snippets` (a trigger phrase becomes its saved text, after the cleanup), `unexpand` (what Improve my cleanup sends). Pure; Java twin `Snippets.java`, golden kind `snippets`. |
| `windows/autolearn.py` | Learn from my corrections, pure rules (no I/O): `detect` (find the typed text again in the field by its first and last words, compare it with `suggest_corrections`, keep only fixes that look like corrections: `looks_like_fix`), `learn` (what to add to the dictionary, capped), the `learned_log` helpers (`apply_learned`, `remove_learned`) and `Watch`, the state machine of one watch (3 minutes at most, ends when the text is sent or gone or the app changes, final check on the last snapshot). Java twins: `AutoLearn.java`, `AutoLearnWatch.java`. |
| `windows/correction_watch.py` | Learn from my corrections, Windows side: after a paste, a daemon thread reads the focused control's text through UI Automation (`UiaProvider`, the .NET UIAutomationClient through pythonnet) every 2 s while the watch runs, the pasted-into window is in front and Vox is not recording (through .NET methods only, so a slow app cannot hold the GIL), feeds `autolearn.Watch` and saves what it learns (`Watcher`). `arm()` is the engine's hook. |
| `windows/improve.py` | Pure core of "Improve my cleanup" (no network): picks the history pairs to send, estimates the cost, builds the request, reads and caps the answer (`parse_proposal`), applies the accepted items to the config with versions and `revert`, and lists the cleanups that lost words. |
| `windows/timing.py` | Pure timing core (stdlib): `Timing` marks (`key_down` ... `inserted`) become the six stage durations (`start`, `rec`, `stt`, `llm`, `insert`, `total`); `median`, `p90`, `biggest`, `format_ms`, `summarize` over the newest N history entries, `by_model` (medians per voice and cleanup model pair) and `speed_view` (everything the Speed card shows, from the history). Local only, nothing is sent. Java twin `Timing.java`. |
| `windows/ui/index.html` | The main window's screens: Home, Notes (meetings), Dictionary, Styles, Settings. One file with CSS and JavaScript. |
| `windows/meeting.py` | Meeting notes: records mic and PC audio, live transcript, final pass, speaker naming, notes generation, saved-meeting search. |
| `windows/gcal.py` | Optional Google sign-in (OAuth with PKCE, loopback redirect) and calendar reading. |
| `windows/vcalendar.py` | Calendar from an iCal (ICS) link; merges with Google events; 5-minute cache. |
| `windows/requirements.txt` | The Windows app's direct dependencies, pinned (what it imports). |
| `windows/requirements.lock` | Every package inside `Vox.exe` at an exact version (the direct ones and all they pull in); release builds install only this and `tools/build-requirements.lock`, checked by `tools/check_lock.py`. No hashes yet. |
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
| `android/src/com/minhaj/vox/VoxAccessibilityService.java` | The floating bubbles (mic bubble and note bubble; the note bubble is optional, or comes up by itself while a note records or saves), focused-field tracking and text insertion. |
| `android/src/com/minhaj/vox/BubbleView.java` | Draws the bubble (idle, recording with level ring, processing spinner); the voice note variant has its own colour and icon and shows the recording time while a note records. |
| `android/src/com/minhaj/vox/TrampolineActivity.java` | Invisible activity that lets the microphone service start from the foreground. |
| `android/src/com/minhaj/vox/Multipart.java` | The speech upload's multipart body with its exact length known up front (`length()` equals the bytes `writeTo` writes), so `ApiClient.transcribe` can send a `Content-Length` (the relay refuses chunked uploads with 411). Pure Java. |
| `android/src/com/minhaj/vox/ApiClient.java` | HTTP calls to the server plus the pure cleanup helpers (prompt, sanitize, replacements, spoken commands, silence filter, retry policy). |
| `android/src/com/minhaj/vox/Prefs.java` | All settings and the history, in SharedPreferences. |
| `android/src/com/minhaj/vox/Providers.java` | Java twin of `windows/providers.py`: per-role settings, model classification and parsing, messages, reasoning fields. |
| `android/src/com/minhaj/vox/Terms.java` | Parses the dictionary text into terms and replacements, applies the terms' spellings to the final text (`fuzzy`), and picks the terms the cleanup prompt names (`forPrompt`: the whole dictionary up to 20 terms, else `select`, with the sound key `key`). |
| `android/src/com/minhaj/vox/Timing.java` | Pure Java twin of `windows/timing.py` (marks, stages, median, p90, biggest stage, "1.4 s" text, summary, `byModel`, `speedView` for the Speed card, `historyMap` the keys of a history row's timing); pinned by the `timing_*` rows of `spec/golden.txt`. |
| `android/src/com/minhaj/vox/Segmenter.java` | Pure Java twin of `Segmenter` in `windows/vox_core.py`: cuts a recording that is still going on into pieces at pauses (12 s minimum, 28 s maximum, 0.6 s pause; with no pause by the maximum it cuts at the quietest 30 ms frame of the last 2 s); the `segcuts` golden rows prove it cuts where Windows does, whatever the block size. |
| `android/src/com/minhaj/vox/StreamingStt.java` | Pure Java twin of `windows/streaming.py`: a worker thread cuts the audio with `Segmenter` and sends each piece to speech to text (with the end of the text before it as context) while the user is still talking; `finish` returns the text, or null when the caller should send the whole recording; `inPieces` (twin of `vox_core._transcribe_in_pieces`) sends a recording over `MAX_UPLOAD_BYTES` (20 MB) piece by piece on the caller's thread. The server call is a `Transcriber` callback, so it is tested with a fake. |
| `android/src/com/minhaj/vox/Structure.java` | Pure Java twin of `format_structure` in `windows/structure.py`: lists from spoken cues after the cleanup (golden rows `structure`); no paragraph breaks on the phone. |
| `android/src/com/minhaj/vox/Snippets.java` | Pure Java twin of `windows/snippets.py`: `clean` (caps) and `apply` (golden rows `snippets`); `ProfileMap` syncs the setting. |
| `android/src/com/minhaj/vox/Latency.java` | Pure latency rules: 5 s connect timeout, speech and cleanup read timeouts that grow with the audio and the words, which failures count as "never reached the server" (the fast retry), the cleanup `max_tokens` bound (floor of 256, headroom for thinking models, the cut-off check), and when to warm the connection again. |
| `android/src/com/minhaj/vox/UploadFormat.java` | Pure rule for the audio container of an upload (WAV under 4 s, m4a from 4 s), its type and file name, and when an encoded file is used. |
| `android/src/com/minhaj/vox/AudioUpload.java` | Makes the uploaded file: encodes the 16 kHz PCM as AAC in an m4a file (`MediaCodec` and `MediaMuxer`, 64 kbit/s) when `UploadFormat` says so, and falls back to the WAV on any encoder failure. Android classes, so it is only compile-checked here. |
| `android/src/com/minhaj/vox/RulesLayer.java` | Pure Java twin of `windows/rules_layer.py` (`clean`, `capitals`), called by `ApiClient.fallbackText` (golden rows `rulelayer` and `fallback`). |
| `android/src/com/minhaj/vox/Fidelity.java` | The fidelity guard v2 (`check`, `Verdict`, `lcsPairs`, `pkey`; `ok(raw, cleaned, strength)`), word tokens and word recall (Java twin of `fidelity_check` in `vox_core.py`). |
| `android/src/com/minhaj/vox/NotificationActions.java` | Pure choice of the foreground notification buttons (at most three) and its Retry hint line. |
| `android/src/com/minhaj/vox/OverlayDiag.java` | Pure bubble diagnostics: a ring buffer of the last 50 events that can make the bubble appear or vanish, its one-line event text, the service and battery lines and the copyable report, kept in memory and in a small private file (`files/overlay_diag.log`). |
| `android/src/com/minhaj/vox/BubbleLogic.java` | Pure bubble rules: `clamp` keeps a saved position on the current screen, `shouldShow` is the visibility rule (only-typing, Always show, focused field, screen on, service ready), `action` is the watchdog's decision (none, add, remove, repair) and `WATCHDOG_MS` is its 30 s period. |
| `android/src/com/minhaj/vox/NoteBubbleLogic.java` | Pure note bubble rules: `visible` (the persistent switch, a note recording, a note being saved) and `timer` (the recording time as `m:ss` or `h:mm:ss`). |
| `android/src/com/minhaj/vox/InsertGuard.java` | Pure typing guard: never type a restored dictation (empty target package), refuse a switched app, the toast words, `route` (type, copy to the clipboard when accessibility is off, or nothing for a cancelled job), the messages of a send (`emptyResult`, `sendFailed`, `networkFailed`, `crashed`: action first, server text capped) and the clipboard rules after a paste (`afterPaste`, `markSensitive`, `canClearClip`). |
| `android/src/com/minhaj/vox/RecordLimit.java` | Pure recording limits: 360 s for a dictation, 18 minutes for a voice note (as on Windows), the warning half a minute before and the words at the limit. |
| `android/src/com/minhaj/vox/WebNav.java` | Pure navigation rule of the settings WebView: the bundled files stay, an https link opens in the browser, everything else is refused. |
| `android/src/com/minhaj/vox/MicChoice.java` | Pure microphone choice: the saved key (device type and product name, never the numeric id), labels, the deduplicated list for Settings, which connected device to prefer (or null for the phone default), and when to show the "not connected" notice once. |
| `android/src/com/minhaj/vox/HintGuard.java` | Pure placeholder check: is the "text" an empty field reports only its hint ("Message" in WhatsApp and Telegram)? Typing then starts from an empty field. A caret after the start of the text means the user typed it. |
| `android/src/com/minhaj/vox/PinnedUrlConfig.java` | A `SyncConfig` with the relay address fixed for one sync run (the address is read once per run). |
| `android/src/com/minhaj/vox/Endpoint.java` | Server address rules (which hosts may use plain http). |
| `android/src/com/minhaj/vox/Pcm.java` | Silence gate for raw 16-bit audio and the edge-silence trim (`trimEdges`: a soft first or last word stays). |
| `android/src/com/minhaj/vox/Corrections.java` | Suggests dictionary entries from a user's fix to a dictation. |
| `android/src/com/minhaj/vox/AutoLearn.java` | Pure Java twin of `windows/autolearn.py` (detect, learn, the learned log); golden kinds `autocorrect` and `autolearn`. |
| `android/src/com/minhaj/vox/AutoLearnWatch.java` | Pure Java twin of `autolearn.Watch`: the watch after Vox typed, with an injectable clock; `AUTO_LEARN_WINDOW_S` = 180, `SETTLE_MS` = 1500. |
| `android/src/com/minhaj/vox/PendingQueue.java` | Pure queue of the unsent recordings of `DictationService` (one entry and file per failed recording, oldest first, at most 5, 7-day age rule, file-name format, which in-flight job a cancel may discard, and `sweepUploads`, the deleter of old `vox-up-*` temp upload files). |
| `android/src/com/minhaj/vox/NoteLogic.java` | Pure voice-note rules shared with `windows/notes.py`: automatic title, search words and string, which side wins a sync merge, tag clean-up, push batch size. |
| `android/src/com/minhaj/vox/Note.java` | Plain value class for one voice note (or delete marker): the columns of the notes table. No Android or JSON classes, so the sync code and its tests can use it. |
| `android/src/com/minhaj/vox/NoteEvents.java` | The "a note was saved" hook: a static, thread-safe list of `Runnable` listeners (`addSavedListener`, `removeSavedListener`, `fireSaved`). Pure Java. |
| `android/src/com/minhaj/vox/NoteEntry.java` | What voice notes show outside the app: the intent that starts a note, the ongoing "Record note" notification and the "Note saved" notification. |
| `android/src/com/minhaj/vox/NoteTileService.java` | The quick settings tile "Voice note": starts a note through the trampoline, or stops the one being recorded. |
| `android/src/com/minhaj/vox/SyncStore.java` | Interface for what the relay sync needs from the notes on the device (`dirtyNotes`, `markSynced`, `applyRemote`, `getMeta`, `setMeta`); pure Java. |
| `android/src/com/minhaj/vox/NotesStore.java` | The voice notes database on the phone (`notes.db`, SQLite): a literal port of `windows/notes.py`, implements `SyncStore`. Needs Android's SQLite, so it is only compile-checked. |
| `android/src/com/minhaj/vox/DevicesView.java` | Pure rows for the Devices card from the relay's device list (name, this device, active/recent/old, "5 min ago"), the twin of `sync.devices_view`; run by the `devices` golden rows. |
| `android/src/com/minhaj/vox/RelayCheck.java` | Pure decision behind Test connection from the status and answer of `GET /health`: `ok`, `reachable`, `token_ok`, the relay version (kept only as 1 to 20 of letters, digits and `. + _ -`), the notes count and the message; the twin of `sync.relay_check`, run by the `relaycheck` golden rows. |
| `android/src/com/minhaj/vox/ProfileMerge.java` | Pure merge of the profile that follows the user between devices, shared with `windows/sync.py`: `merge3` for one field (the side that changed wins, the relay wins a clash; a list (dictionary, people) or the snippets map changed on both sides merges item by item: an item added on either side is kept, one removed on either side goes, `mergeItems`), `mergeProfile` over a set of fields, and the two field lists `SHARED_FIELDS` and `KEY_FIELDS`. |
| `android/src/com/minhaj/vox/SyncEngine.java` | One relay sync run, a port of `windows/sync.py` `sync_once` and `sync_profile`: push changed notes, pull changes by cursor, merge the profile. Pure Java over `SyncStore`, `RelayApi` and `SyncConfig` with plain maps; never throws. |
| `android/src/com/minhaj/vox/RelayApi.java` | Interface for the four relay calls the sync needs (`putNote`, `changes`, `getProfile`, `putProfile`) with the `Changes`, `Profile` and `RelayError` types; `RelayError.permanent()` is the rule for a note the relay refuses for good. Pure Java. |
| `android/src/com/minhaj/vox/SyncConfig.java` | Interface for the settings side of the sync: the profile fields of the phone, the "share my keys" switch, and saving what arrives. Pure Java. |
| `android/src/com/minhaj/vox/SyncResult.java` | What one sync run did: notes sent, notes received, the error words, what happened to the profile. Pure Java. |
| `android/src/com/minhaj/vox/RelayProof.java` | The relay proves it holds the token before the phone sends it: `GET /proof?nonce=` and an HMAC check, kept 10 s and forgotten after a failed connection or a 502, 503 or 504; an old relay is used with a warning until its address has proved itself once (pins kept by `Prefs`). Twin of `sync.prove_relay`. Pure Java. |
| `android/src/com/minhaj/vox/RelayClient.java` | `RelayApi` over `HttpURLConnection`: headers, paths, no redirects, every status and error-body shape turned into plain words; also `problem` (is this address and token usable) and `check` (Test connection, decided by `RelayCheck`). Pure Java, tested against a real local HTTP server. |
| `android/src/com/minhaj/vox/ProfileMap.java` | Pure conversion between the phone's settings and the relay's profile fields, in the encodings Windows uses (the dictionary and people as lists of text); cleans every value the same way both ways. |
| `android/src/com/minhaj/vox/PlainJson.java` | Small strict JSON reader and writer in plain Java (org.json cannot run in the off-device tests); used by `RelayClient` and for the profile snapshot. |
| `android/src/com/minhaj/vox/SyncWorker.java` | The `vox-sync` thread: runs the engine on app resume, service start, settings save and every 90 s while the process lives; keeps the status the page shows. Needs a device, so it is only compile-checked. |
| `android/res/values/strings.xml` | App name, the tile label and the accessibility service label and description. |
| `android/res/xml/accessibility_config.xml` | Accessibility service configuration (event types, content access). |
| `android/res/xml/network_security_config.xml` | Allows cleartext at OS level; the app enforces the private-host rule itself. |
| `android/res/xml/data_extraction_rules.xml` | Android 12+ backup and phone-to-phone transfer rules: every domain left out of both. |
| `android/res/drawable/ic_launcher_bg.xml` | Launcher icon background. |
| `android/res/drawable/ic_launcher_fg.xml` | Launcher icon foreground. |
| `android/res/drawable/ic_stat_mic.xml` | Notification icon. |
| `android/res/drawable/ic_note.xml` | Note page icon: the quick settings tile and the small icon of the voice note notifications. |
| `android/res/mipmap-anydpi-v26/ic_launcher.xml` | Adaptive launcher icon definition. |

## Tests

| Path | What it covers |
|---|---|
| `tests/conftest.py` | Puts `windows/` and `relay/` on the import path, routes `vox_core._post` through `requests.post`, and isolates the profile: every test runs with its own empty `APPDATA`, `LOCALAPPDATA`, `HOME`, `USERPROFILE` and `XDG_*` folders, and an audit hook raises `test touched the real profile` for any file, folder or sqlite call on the real Vox folders recorded at import. A test that needs a particular `APPDATA` sets its own. |
| `tests/test_conftest_guard.py` | The isolation: own folders per test, the real Vox profile is refused, a test's own `APPDATA` still works. |
| `tests/test_ci_workflow.py` | Text checks on `.github/workflows/build.yml` and `android/build.sh`: only a tag build gets the release key (after the tests, with its password from the environment), other builds use a throw-away key and say so; a release needs a commit on `main`, checksums cover every file, every job has a time limit, release builds install only the lock files. |
| `tests/test_check_lock.py` | `tools/check_lock.py`: lock lines, platform markers, missing, wrong and extra packages. |
| `tests/test_repo_hygiene.py` | `git check-ignore` for the files that hold secrets (`config.json`, `.env`, `relay.json`, `relay.db`, keystores, `*.pem`, ...) wherever they are in the tree. |
| `tests/requirements.txt` | Pinned test dependencies (`requests`, `pytest`). |
| `tests/test_vox_core.py` | Whisper prompt, sanitize, replacements, dictionary, style, WAV and silence-phrase helpers. |
| `tests/test_endpoint_config.py` | Configurable server address, auth header, key test, cleanup fallback. |
| `tests/test_endpoint_safety.py` | Private-host rule, `endpoint_error`, `key_missing`, `ApiError`; plain http goes only to a private address (a LAN name that also has a global IPv6 address is reached on its private one, a name with only public addresses is refused before connecting), no redirect is followed. |
| `tests/test_secret.py` | DPAPI wrapper and how `config.json` stores the key. |
| `tests/test_robustness.py` | HTTP retry policy and the silence gate. |
| `tests/test_suggest_corrections.py` | Dictionary suggestions from user fixes. |
| `tests/test_autolearn.py` | Learn from my corrections, pure rules: detect, the similarity rules, learn and its cap, the learned log, and the watch with a fake clock (179 s still watching, 181 s ended, sent, span gone, app changed, re-arm); the window constant equals the Java one. |
| `tests/test_correction_watch.py` | The Windows watcher with a fake desktop (no UI Automation, no window): a fix is learned and announced privately, a fix just before Send counts, another window is never read, password controls, the once-only log line, no text in the log, the setting, the thread runs only while armed, a slow read does not hold the lock (`arm` and `end` return while a fake slow desktop is still answering) and its late answer is dropped when a new watch began. |
| `tests/test_engine_autolearn.py` | The engine arms the watch after a real paste only (skipped where the Windows runtime packages are missing). |
| `tests/test_ui_autolearn.py` | The Learn from my corrections switch and the Recently learned list on both pages, the Android service config and description, the settings that stay on the device, and the Windows bridge `learned_remove`. |
| `tests/test_spoken_commands.py` | Spoken "new line" and the cleanup-failure result (the rules layer's text; not for cleanup off or the raw style). |
| `tests/test_rules_layer.py` | The rules layer's promise over the golden rows: in Light no word is added and only noises and spoken commands go; Hinglish and emphatic "actually" stay; "New paragraph." alone goes through `process_text` as the break. |
| `tests/test_audio_devices.py` | Microphone name resolution. |
| `tests/test_parity.py` | Runs `spec/golden.txt` against the Python helpers. |
| `tests/test_meeting_stt.py` | The meeting recorder's own speech-to-text retry loop: a read timeout through the relay is sent once, otherwise four attempts (stubs numpy when it is missing). |
| `tests/test_providers.py` | Per-role settings, key isolation, model discovery, Test button, reasoning retry, the relay as the AI server (routes, headers, no key leaks, error shapes, the Settings page hiding the provider fields). |
| `tests/test_warmup.py` | Connection warm-up (`vox_core.warm`) and the shared session. |
| `tests/test_warm_mic.py` | `RingBuffer` (keeps the last 400 ms, whole samples, oldest first) and `WarmMic` with a fake stream (attach, prime, detach, close, reopen, retry, one notice, a failing sink), and the Settings row on the page. |
| `tests/test_user_context.py` | The "about you" context: cleaning, prompt placement, sent with cleanup. |
| `tests/test_prompt.py` | The cleanup prompt (v3): the static part first for every style and strength (its size printed and bounded), then About you, at most 20 terms, the learned rules and the Layout, Style and App lines; the examples; the same bytes for the same inputs; the strength and the terms `cleanup` sends; the answer `EMPTY` returned as nothing. |
| `tests/test_term_selection.py` | Which dictionary terms go into the prompts: `prompt_terms` (a dictionary of 20 or fewer whole), `select_terms` (sound-alike words, nicknames, spelled letters, order, the cap of 20, under 1 s for 500 terms and 300 words) and the Whisper prompt v2 (people, recently learned and sound-alike terms first, whole terms within the token budget, the earlier text cut at a word, `transcribe` sends it). |
| `tests/test_fuzzy_dictionary.py` | The fuzzy dictionary pass: pipeline wiring, replacement lines win, idempotent over every golden row, speed, Python and Java share one stoplist. |
| `tests/test_bench_cleanup.py` | The cleanup benchmark with a fake provider (no network): each metric, the corpus (size, fields, kinds, no keys), the run loop, the table, and `main` (the key never in the output, never sent to another provider's server, the relay bypassed when a provider is chosen); `--compare-prompt`/`--compare-guard` (v1 through the frozen copy, variants taking turns, guards scored without requests, `--guard-only` offline), a 429 waited out, the recorded clips as the source, and the token usage kept by `vox_core.last_usage`; the guard called with the dictionary as the app does, the Standard rules layer on a rejection, the minimum words, the micro bootstrap, the answer cache (a second run sends nothing), token pacing, the stop on a lasting 429 or Ctrl+C with the rows saved, and the tuning-round fields and decision summary. |
| `tests/test_bench_metrics.py` | The benchmark's reference metrics by hand-computed cases: WER and its normaliser (Hinglish in both scripts), formatted WER, punctuation and case F1, term recall, over-edits, self-corrections, answered-or-obeyed, the pasted-text and usage summaries, the guard confusion on the labelled pairs, the paired bootstrap. |
| `tests/test_bench_legacy.py` | The frozen v1 prompt and guard (`tools/bench/legacy.py`) still give the bytes and verdicts of `8ea15e1` (hash pins over every style, strength and structure, and over the 189 labelled pairs), and the v1 cleanup request. |
| `tests/test_bench_record.py` | The clip recorder with a fake recorder and typed answers (no microphone): two clips saved with their texts and a progress count, a new session continues and never overwrites, texts not typed before a close asked for first, too short, silent, redo, play, discard, q and Ctrl+C, the goal; `bench_clips` (no overwrite, next id, a cut manifest line, the format check, the cache). |
| `tests/test_bench_public.py` | The public-sample downloader against a fake HTTP server on 127.0.0.1 with generated audio (no network): dataset viewer rows spread over pages, one clip per speaker, too-short and broken clips passed over, a rerun that downloads nothing again, a remote zip read only by range requests (code-switched lines first), the start of a `.tar.gz` stream, 429/5xx waited out, pacing, http(s) only, the size limit, a source given up after repeated unreadable samples; integer and float WAV to 16 kHz mono (numpy and plain-Python resamplers agree, no aliasing); no speaker field but the hashed id in the manifest; `bench_stt` and `bench_cleanup` reading the folder. |
| `tests/test_bench_stt.py` | The speech benchmark with a fake speech call or fake server: one request per clip and setting then a free rerun, a changed prompt redone, settings taking turns, a 429 waited out, errors not cached, the app's speech path with only the benchmark terms, `main` (models x prompt on/off, no key, the key never in the output); each clip's own terms in its prompt and the exact prompt hashed, `--trim-pad-ms` / `--prompt-tokens` (the app's values put back, a changed padding redone), the per-clip comparison. |
| `tests/test_cleanup_fidelity.py` | The fidelity guard: tokens, recall (numbers, symbols, spoken commands), Light and Standard, long dictations, `looks_valid`, `process_text` fallback. |
| `tests/test_guard_v2.py` | Guard v2: the labelled golden rows scored per strength and split (at least 95%, no bad answer accepted in Light or on the held-out pairs), speed (300 words, a long rewrite), reasons without dictated words, `lcs_pairs`, and `process_text` with `EMPTY`, the dictionary and a self-correction. |
| `tests/test_cleanup_call.py` | The Windows cleanup request as on Android: temperature 0, `max_tokens` with headroom for thinking models, the wait by words with no retry after a read timeout, an answer cut off at `max_tokens` used as a failed cleanup; line breaks inside a speech answer become spaces. |
| `tests/test_level.py` | The meter curve and the scrolling level history. |
| `tests/test_notes.py` | The notes store: add, edit, delete, search with FTS5 and the LIKE fallback, filters. |
| `tests/test_relay.py` | The relay over real HTTP: auth, sync cursor, conflicts, delete markers, search, profile versions, limits; the connection cap (503 without a thread), silent connections giving up their place at the cap and from half of it, the header deadline. |
| `tests/test_relay_admin.py` | The relay's management page and endpoints, portability and file permissions, and the AI server (proxy) settings: address rules, write-only keys that never appear in any response, download or output. |
| `tests/test_relay_cli.py` | Running the relay from the app: `vox_app.py --relay` as a real subprocess (no GUI libraries loaded), the command line, `RelayHost` with a fake process, the child dying with its parent, the tray toggle, and the build inputs. |
| `tests/test_sync_devices.py` | The Devices card's Windows side against a real relay: `fetch_devices` (wrong token, unreachable, relay too old, answers that are not a device list), `devices_for_ui` (rows, "this device", an empty list plus the reason on failure) and `devices_view` edge cases. |
| `tests/test_ui_devices.py` | The Devices card on both pages: the shared `devicesHtml` builder run with node (rows, badge, empty and error states, escaping), its styles, ids, position under the relay settings, each page's bridge call, the preview stand-in. |
| `tests/test_relay_check.py` | Test connection against a real relay: `sync.test_relay` and `relay_check` (version, token, device name, wrong token, another tailnet user, unreachable, answers that are not a relay's, no request for unusable settings, the token in no field) and the Windows `sync_test` bridge. |
| `tests/test_android_install_safety.py` | G2: the manifest declares only permissions the code uses (no `VIBRATE`), targetSdk matches `build.sh`, no `isAccessibilityTool`, and the Settings page has the Install help card with its App info button and the adb commands. |
| `tests/test_ui_relay_help.py` | The "How to set up the relay" card and the Test connection rows: the steps source and its parser, the generated block in both pages, every command in `relay/README.md`, copy buttons, position, escaping, `sync_ui` failing on a stale or hand-edited block, `relayCheckRows` run with node. |
| `tests/test_relay_devices.py` | `GET /devices`: token required, newest first, same fields as the management page, owner check, other methods refused. |
| `tests/test_relay_proof.py` | The Windows app sends the relay token only to a relay that proved it holds it: a real relay, a squatter with a wrong proof, an old relay (warning, then refused once its address has proved itself), a 404 on `/proof` (not an old relay), the relay as the AI server; a proof is kept 10 s at most, a squatter on a stopped relay's port gets nothing once it ran out, a 502 forgets it, a dictation retry proves again, a `/proof` with no answer is tried again. |
| `tests/test_relay_proxy.py` | The relay's proxy routes against a stand-in upstream server that records what it receives: fixed URL and path tricks, headers and keys (the relay token never goes on, the upstream key never comes back), size limits, 411/413/429/502/503, slots and timeouts. |
| `tests/test_sync.py` | The Windows sync client against a real relay: two devices, edits, deletes, conflicts, failures, notes the relay refuses for good, upgrade of old databases. |
| `tests/test_sync_profile.py` | Profile sync between two devices through a real relay: merge rules, keys switch, races. |
| `tests/test_sync_fixes.py` | Windows sync fixes through a real relay: a save made during the profile request is kept, a slow PC clock keeps its edits, a relay wiped at the same address gets everything again, an odd `next`/`more` does not loop, no API key in plain text in `notes.db`, a wrong-typed profile value is repaired, a learned word survives another device's unrelated change and leaves "Recently learned" for good when another device removes it. |
| `tests/test_streaming.py` | The pause finder (`Segmenter`, including the forced cut at the quietest frame of the last 2 s), the streaming worker (6 to 20 s pieces, pieces sent while speaking), and the text half of the pipeline. |
| `tests/test_upload_format.py` | FLAC or WAV for the speech upload: the choice rule, the file name sent, the 18-minute fallback in pieces, and a lossless FLAC round trip (only where `soundfile` is installed). |
| `tests/test_stt_quality.py` | Speech-to-text filters: the edge-silence trim (numpy and plain loop agree, never to nothing, only the edge asked for, a quiet first and last word sent), the whole upload, streamed first/last pieces and `transcribe_rest` sending trimmed audio with segment times moved back (pause paragraphs still at the pause), `verbose_json` for Whisper models, made-up segments and a prompt echo dropped (Hindi in Devanagari and a quiet middle segment kept, a transcript the filter would empty kept), the 400 -> json fallback, the meeting thresholds, and the Language hint and one-time suggestion in both pages. |
| `tests/test_structure.py` | Lists and paragraphs: the cue rules, prose that stays prose, idempotence over every golden row, the order around the fidelity guard, the prompt per setting, `verbose_json` segments and the pause breaks. |
| `tests/test_codemode.py` | Code mode: every formatter and symbol, formatters with symbols, whole words only, which apps, the pipeline (no AI cleanup by default, the code prompt), the help box and [15-code-mode.md](15-code-mode.md) list the whole table; ordinary English stays words and plain code (`self dot name equals name`, `user dot name`, `if count greater than limit`) still converts. |
| `tests/test_snippets.py` | Snippets: matching, caps, the order (after the cleanup, before lists), never sent to the cleanup or the Improve run, the profile field, both Dictionary pages. |
| `tests/test_listen_session.py` | The keep-listening session, the same-window rule and the crash-safe audio buffer (temp folder, no hardware). |
| `tests/test_listen.py` | The running session with the speech calls and the window replaced: Note and Type targets, stop phrase, limit, window change, failed pieces, recovery, latency marks. |
| `tests/test_engine_listen.py` | The engine side: double press, Esc, tray entries, the `listen_target` setting, microphone errors, recovery. |
| `tests/test_note_hotkey.py` | The note shortcut: the pure parse and duplicate rule, and the window bridge that saves it. |
| `tests/test_hotkeys.py` | The extra shortcuts (`hotkeys.py`): parse, conflicts and duplicates, the style setting, the tap-or-hold rule, and the window bridge (`set_shortcut`, `shortcut_problems`). |
| `tests/test_engine_hotkeys.py` | The engine's shortcuts through `_on_press`/`_on_release`: classic and hold-or-tap, the hands-free shortcut, Esc cancel, paste and copy last, edit by voice, stale keys after a pause (R2-M2), the hotkey thread (R2-M1), and what a dictation leaves behind (last text, timing fields); the hook drops only Vox's own tagged keys and AltGr's made-up Ctrl, keys other programs send still count, checked against a real pynput listener. |
| `tests/test_engine_lifecycle.py` | Issue 63: a config.json that could not be opened is read again on the next tick instead of running on the defaults, and Quit lets a dictation that is being sent finish (skipped where the Windows packages are missing). |
| `tests/test_improve.py` | The pure improvement core with a fake provider: transcript selection and budget, request, tolerant parsing and caps, apply and revert (revert keeps rules written later), About you never applied, the fidelity report. |
| `tests/test_improve_card.py` | The Improve my cleanup card: preview and confirm sentence, versions, reminder rule, the one server call, the window bridge (nothing is sent before the confirmed numbers) and the tray reminder. |
| `tests/test_ui_improve.py` | The card's ids and place on the Windows page, that only the confirm button runs it, and its two renderers (escaping). |
| `tests/test_timing.py` | The timing core: stage maths with missing marks and a backwards clock, median and p90, biggest stage, text format, summaries (skipped cleanup not counted as 0 ms), per-model medians and the Speed card's `speed_view`. |
| `tests/test_timing_pipeline.py` | Where the Windows marks are set: the per-thread `core.timing_scope` (stt and llm marks, a failed cleanup still closes its mark, one thread only), `core.timing_info`, and the window's `get_speed`. |
| `tests/test_ui_speed.py` | The Speed card: the shared renderer `speedHtml` / `fmtMs` run with node (biggest stage marked, dash for a stage that did not run, names escaped, empty state), and the card's ids and bridge call on both pages. |
| `tests/test_config_lock.py` | One writer at a time for `config.json` and `history.jsonl`: parallel read-modify-writes in threads and in two processes lose nothing and never tear the file, a refused replace is retried, a failed save leaves no temp file, history appends during rewrites are kept, an unchanged history is not parsed again, another thread's good load does not let a failed load save defaults. |
| `tests/test_feature_models.py` | Meeting notes, the final pass and Improve use the chosen provider's models: Groq names only on Groq, else the role's model, unless the feature's model is set. |
| `tests/test_config_load.py` | `load_config` with a BOM, a cut-off file, a non-object and a read-only file; a failure to open the file is retried and never moves it aside or lets defaults be saved over it; the old default `cleanup_min_words` 3 becomes 4 once. |
| `tests/test_unreadable_answers.py` | An answer the server got wrong (empty `choices`, null `text`) does not throw a dictation away. |
| `tests/test_engine_safety.py` | `_process` keeps the recording on an unexpected error, a failing paste or note save; a failing hotkey handler or tray icon does not raise; the tray menu is built again when a recording is kept or delivered and on every state change (skipped without the Windows packages). |
| `tests/test_calendar_privacy.py` | The secret iCal address stays out of the log, `calendar.json` and `config.json`; clearing it removes the cache. |
| `tests/test_calendar_fixes.py` | Attendee and organizer names are never e-mail addresses (iCal and Google); the iCal address only over https or to a private host, redirects included; the Google revoke keeps the token out of the URL and the account address out of the log. |
| `tests/test_gcal.py` | Google tokens are protected on disk, a plain legacy file is migrated, an unreadable one asks to connect again. |
| `tests/test_calendar_status.py` | A declined invite is dropped, an unanswered one only reminds, only an accepted one auto-starts (`calendar_action`); invite text is one clean line and bounded; `meeting.export_name`. |
| `tests/test_meeting_store.py` | A failed export still lists the meeting and removes the raw audio; a cut-off meeting is recovered (`recover_unfinished`) and keeps its audio when the transcript is empty or partial; a bad meeting id deletes and writes nothing; no audio is dropped between blocks (`_frames`). |
| `tests/test_ui_app_bridge.py` | Dictionary and People edits change one item in the file's current list, not a stale page list; the meeting bridge refuses a bad id. |
| `tests/test_hostile_note_id.py` | A note id from the relay that is not 32 hex characters is ignored, in `apply_remote` and in a sync. |
| `tests/test_engine_notes.py` | The engine's voice-note mode (skipped where the Windows runtime packages are missing). |
| `tests/test_engine_flash.py` | The pill's "sent" and "error" signal: `Engine.flash` timing, expiry, what cancels it, no flash without a pill, and which events raise which one, with one notice for a noise-only phrase whose cleanup failed (skipped where the Windows runtime packages are missing). |
| `tests/test_overlay_mode.py` | Every branch of `overlay_mode` (flash over the meeting timer, flash only while idle, a flash never longer than 5 s). |
| `tests/test_overlay_guard.py` | `overlay_guard`: on-screen check across monitors, problem names, rebuild rule, rate limit, stall check, thread dump, stuck states. |
| `tests/test_overlay_keep_up.py` | The pill's re-assert, repair log, rebuild limits and focus hand-back, with every Win32 call and the Tk window faked (no window is created). |
| `tests/test_engine_watchdog.py` | `Engine.check_overlay`: the thread dump on a stalled Tk tick and the reset of a stuck state (needs the Windows runtime packages). |
| `tests/test_flash_constants.py` | Drift guard: `BubbleView.SENT_MS` / `ERROR_MS` equal `FLASH_SECONDS` in `engine.py`. |
| `tests/test_engine_mic.py` | `Engine._open_mic` refreshes PortAudio's device list once when a chosen microphone is missing or fails to open, never while the warm microphone is open or being opened, and holds it off during the restart (skipped without the Windows packages). |
| `tests/test_engine_warm_mic.py` | The engine's wiring of the warm microphone with a fake `sounddevice`: nothing opens while the setting is off, the 400 ms before the key come first and the live audio follows in order (also to the streamer), the end of a recording keeps the stream open, off / device change / quit close it, a failed open falls back with one notice, the 400 ms do not turn a tap into a dictation (skipped without the Windows packages). |
| `tests/test_paste.py` | `paste_text` with injected fakes (window unchanged or changed, clipboard snapshot and restore rules, no snapshot while the clipboard is kept, one format over the size limit left out, the exclude-from-history markers, Ctrl+V, Ctrl+Shift+V and Shift+Insert as virtual keys, terminals (PuTTY and mintty get Shift+Insert), elevated windows, the bounded modifier wait), `copy_selection`, and the engine's "Copied; the window changed" notice. |
| `tests/test_command.py` | Edit by voice: the strict request, the guard, the answer clean-up, and the engine flow with a fake server and clipboard (the selection is left alone on any failure). |
| `tests/test_docs_todo.py` | The path-to-page rules of `documentation/tools/docs_todo.py`. |
| `tests/test_ui_shared.py` | `tools/sync_ui.py --check` passes on the committed pages and fails when a generated block is edited by hand (on temp copies). |
| `tests/test_ui_static.py` | Static checks of both HTML pages: every looked-up id exists, no duplicate ids, every bridge call (`api().NAME`, `V.NAME(`) names a real method of `Api` / `MainActivity.Bridge`. |
| `spec/golden.txt` | Shared expected results (sanitize, looks_valid, fidelity, guard, lcs, pkey, tokens, recall, replacements, whisper prompt, terms, the terms a prompt names and their sound keys, the cleanup answer EMPTY, system prompt, spoken commands, silence, note titles, note search strings, sync merge, profile merge and its field lists, the Devices card's rows). Read by the Python and Java parity tests. |
| `android/test/com/minhaj/vox/ApiClientTest.java` | Prompt v3 (static part first, its size printed and bounded, About you, terms, strength, layout, examples), Whisper prompt v2, the answer `EMPTY`, `Terms.select` speed for 500 terms, sanitize, replacements, retry policy, silence phrases. |
| `android/test/com/minhaj/vox/EndpointTest.java` | Server address rules. |
| `android/test/com/minhaj/vox/NotificationActionsTest.java` | Notification buttons (never more than three in any state), the Retry hint and the typing guard. |
| `android/test/com/minhaj/vox/InsertGuardTest.java` | `InsertGuard.route` (typed, copied when no accessibility listener is attached, nothing for a cancelled job), the existing typing check, the send messages (the filler-only message too) and the clipboard rules. |
| `android/test/com/minhaj/vox/RecordLimitTest.java` | The recording limits and their words. |
| `android/test/com/minhaj/vox/WebNavTest.java` | What the settings WebView may load. |
| `android/test/com/minhaj/vox/ManifestTest.java` | Reads `android/AndroidManifest.xml` as text: `.MainActivity` handles `orientation` and `screenSize` changes itself and not `uiMode`; the data extraction rules leave every domain out of a device transfer and cloud backup. |
| `android/test/com/minhaj/vox/MicChoiceTest.java` | Plain-Java checks for `MicChoice` (keys, labels, deduplication, `pick`, the one-time warning). |
| `android/test/com/minhaj/vox/HintGuardTest.java` | Plain-Java checks for `HintGuard`: placeholders are recognised, real text is never mistaken for one. |
| `android/test/com/minhaj/vox/PcmTest.java` | Silence gate. |
| `android/test/com/minhaj/vox/TimingTest.java` | The Java timing core: stages, skipped cleanup, clock, summary rules, per-model medians, `speedView` from history rows. |
| `android/test/com/minhaj/vox/SegmenterTest.java` | `Segmenter` beyond the golden rows: nothing lost, the same pieces for any block size, reuse after `rest()`, the forced cut at the quietest frame of the last 2 s (loud speech with a dip, no dip, a dip at the start of the window, never under the minimum). |
| `android/test/com/minhaj/vox/SttQualityTest.java` | The Android speech-to-text filters: `Pcm.trimEdges` (padding, a soft first and last word sent, never to nothing, one edge), `StreamingStt` trimming only the first piece's start and the last piece's end, `ApiClient.transcriptOf` (made-up segments, prompt echo, unreadable answers) and the request against a stub server (`verbose_json` for Whisper, a 400 gets `json`, other models `json`). |
| `android/test/com/minhaj/vox/StructureTest.java` | `Structure` beyond the golden rows: twice changes nothing on every row, the setting, every other word kept in order. |
| `android/test/com/minhaj/vox/SnippetsTest.java` | `Snippets` beyond the golden rows: the caps (code points), the stored JSON form in `ProfileMap`, the profile field and the blank-map merge rule. |
| `android/test/com/minhaj/vox/M4aFallbackTest.java` | A stand-in server that cannot read m4a (415, 422) or refuses everything (400), or fails (500): the upload is resent as WAV once, the server is remembered only when the WAV got through, a WAV upload or a 500 is not retried. |
| `android/test/com/minhaj/vox/StreamingSttTest.java` | `StreamingStt` with a fake server: order and context, the first piece going out before the recording ends, only the tail left after, failure, slow server, silent and hallucinated pieces, cancel, and `inPieces` (read in chunks, a 429 waited out with Retry-After or the default wait, at most 3 times, a cancel during the wait). |
| `android/test/com/minhaj/vox/LatencyTest.java` | The timeout and token rules of `Latency`, the connect-failure classification and the `UploadFormat` rule. |
| `android/test/com/minhaj/vox/CorrectionsTest.java` | Correction suggestions. |
| `android/test/com/minhaj/vox/AutoLearnTest.java` | `AutoLearn` and `AutoLearnWatch`, mirroring `tests/test_autolearn.py`. |
| `android/test/com/minhaj/vox/PendingQueueTest.java` | The unsent-recordings queue: oldest-first order, cap drops the oldest, cancel rules (live recording and Retry discard nothing, only a fresh queued entry), remove on success, age purge, file names. |
| `android/test/com/minhaj/vox/DevicesViewTest.java` | `DevicesView` beyond the golden rows: order, entries that are not objects, unusable times, the Android header spelling of a name, age rounding. |
| `android/test/com/minhaj/vox/NoteLogicTest.java` | Note rules beyond the golden rows: Python-style whitespace and `strip`, search words, tag clean-up and its cap, null inputs, merge edge cases. |
| `android/test/com/minhaj/vox/NoteTest.java` | The `Note` value class: defaults and `copy`. |
| `android/test/com/minhaj/vox/NoteBubbleLogicTest.java` | `NoteBubbleLogic`: the visibility rule for every input combination and the timer (zero, minutes, over an hour, negative). |
| `android/test/com/minhaj/vox/BubbleLogicTest.java` | `BubbleLogic`: the clamp (inside, corners, too far right or down, negative, a screen smaller than the bubble, overflow), the visibility rule and the watchdog's decision. |
| `android/test/com/minhaj/vox/OverlayDiagTest.java` | `OverlayDiag`: ring size and order, merging of a repeated event, event text, file round trip, damaged and unwritable file, the only-typing reason and kind (also with Always show and a dark screen), the watchdog event, the report text. |
| `android/test/com/minhaj/vox/NoteEventsTest.java` | `NoteEvents`: order, no double add, remove, a failing listener, adding during a fire, several threads. |
| `android/test/com/minhaj/vox/ParityTest.java` | Runs `spec/golden.txt` against the Java helpers. |
| `android/test/com/minhaj/vox/ProfileMergeTest.java` | Profile merge beyond the golden rows: lists and booleans, removals, null maps, fields outside the set, inputs left unchanged, the field lists. |
| `android/test/com/minhaj/vox/SyncEngineTest.java` | The sync engine against an in-memory store and a fake relay that follows the relay's rules: push, not-applied, offline and killed runs, notes the relay refuses, stopping errors, paging and cursor, delete markers, profile (version 0, both changed, 412 retry, keys on and off), "never throws", loop guards, the wire form. |
| `android/test/com/minhaj/vox/MultipartTest.java` | The computed length equals the bytes written for empty, unicode and large combinations; the framing; a file that shrank. |
| `android/test/com/minhaj/vox/ProxyUploadIntegrationTest.java` | Only with `run-tests.sh --integration`: `ApiClient.transcribeRaw` through the real relay's `/proxy/stt` to a stub AI server (JDK `HttpServer`): the upload arrives with a `Content-Length`, not chunked, byte for byte. |
| `android/test/com/minhaj/vox/RelayIntegrationTest.java` | Only with `run-tests.sh --integration`: starts the real `relay/relay.py` (free port, temp data folder) and syncs two or three phones (`SyncEngine` over `RelayClient`, in-memory notes) through it: a note and its delete marker travel, an older edit loses, a note the relay refuses does not block the next, a profile conflict and a real 412, a wrong token, 201 notes over several pages. |
| `android/test/com/minhaj/vox/RelayProofTest.java` | `RelayProof` against a local HTTP server: the HMAC matches the relay's, a squatter gets no token from the sync client nor through `/proxy/`, old relays, pins, the kept answer (10 s at most), a 404 on `/proof`, a 502 that makes the sync client and a dictation through the relay prove again. |
| `android/test/com/minhaj/vox/RelayClientTest.java` | `RelayClient` against a real HTTP server on this computer: headers, paths, bodies, every status and error-body shape, network failure, no redirects, `check`, `listDevices`, `problem`. |
| `android/test/com/minhaj/vox/ProfileMapTest.java` | Phone settings to profile fields and back: round trips of each shared field, empty About you, the Windows shape, wrong types, addresses, key fields. |
| `android/test/com/minhaj/vox/PlainJsonTest.java` | The JSON reader and writer: values, escapes, numbers, strict errors, depth limit, exact round trip of timestamps. |
| `android/test/com/minhaj/vox/ProvidersTest.java` | Per-role settings, key rule, reasoning fields, messages (Java twin of part of `tests/test_providers.py`). |
| `android/test/com/minhaj/vox/FidelityTest.java` | The fidelity guard beyond the golden rows: property checks, a 1,500-word dictation, `looksValid` (Java twin of `tests/test_cleanup_fidelity.py`). |

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
| `docs/index.html` | Landing page (GitHub Pages) for 2.0, kept short: download buttons, a status note, six feature cards, four screenshots, a privacy paragraph and links. Static HTML and CSS only: it loads nothing from other sites. Open Graph and Twitter card tags point at `docs/screenshots/og.png`. |
| `docs/privacy.html` | Privacy policy (it also covers the optional Google Calendar use); the header links back to the landing page. |
| `docs/style.css` | Styles for the two pages (system fonts, light and dark through `prefers-color-scheme`, works down to 360 px). |
| `docs/favicon.svg` | Site icon. |
| `docs/screenshots/windows-home.png` | Screenshot rendered by `tools/render_screenshots.py` from `windows/ui/index.html` with sample data: Home (Status and Speed cards). |
| `docs/screenshots/windows-settings.png` | The same, Settings: AI providers and the start of Voice & audio. |
| `docs/screenshots/windows-dictionary.png` | The same, Dictionary: About you, words, people, replacements, snippets. |
| `docs/screenshots/windows-voice-notes.png` | The same, Voice notes with three sample notes. |
| `docs/screenshots/windows-privacy.png` | The same, Settings: the Privacy section. |
| `docs/screenshots/android-home.png` | Screenshot rendered from `android/assets/index.html` with sample data: Home. |
| `docs/screenshots/android-settings.png` | The same, Settings: AI providers. |
| `docs/screenshots/android-notes.png` | The same, the Notes page. |
| `docs/screenshots/android-install-help.png` | The same, Settings, System, Install help opened. |
| `docs/screenshots/og.png` | The 1200 x 630 social preview image (title and the Windows Home screenshot), rendered by the same tool. |

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
| `documentation/15-code-mode.md` | Code mode (Windows): when it is on, the formatters and the whole symbol table, what it does not do. |
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
| `documentation/decisions/0017-warm-connections-not-an-open-microphone.md` | ADR: warm the server connections at key-down; the microphone is not open while idle unless the opt-in `warm_mic` is on (0041). |
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
| `documentation/decisions/0030-cleanup-keeps-the-spoken-words.md` | ADR: the cleanup contract (keep the spoken words), the fidelity guard, the Light default and the raw text always kept. |
| `documentation/decisions/0031-timings-stay-on-the-device.md` | ADR: dictation timings are a field of the history entry, kept on the device, never sent or synced. |
| `documentation/decisions/0032-keep-listening-pieces-two-targets-crash-safe-buffer.md` | ADR: keep listening is not an assistant; pieces cut at pauses, Note and Type targets, a strict same-window rule, a crash-safe audio file, Esc saves, Android out of scope. |
| `documentation/decisions/0033-the-improvement-run-sends-transcripts-only-on-an-explicit-button.md` | ADR: "Improve my cleanup" sends stored transcripts only after an explicit confirm and Send, applies nothing by itself, never applies About you changes, every change is a revertable version. |
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
| `documentation/decisions/0036-fuzzy-dictionary-guesses-only-for-long-terms.md` | ADR: the one-letter dictionary guess only for terms of 7+ letters; a short name keeps the case fix. |
| `documentation/decisions/0037-esc-cancels-and-the-hook-only-queues-keys.md` | ADR: Esc cancels a keep-listening session (a session of 30 s or more keeps its audio for Recover); the pynput hook only queues keys for a hotkey thread. |
| `documentation/decisions/0038-text-structure-from-spoken-cues-and-code-mode-without-the-ai.md` | ADR: lists only from spoken cues (never commas), after the cleanup and its guard; code apps get rules, not the AI cleanup, by default. |
| `documentation/decisions/0039-auto-learn-reads-the-field-through-ui-automation-with-pythonnet.md` | Learn from my corrections: a short watch of the typed field, UI Automation through pythonnet, a final check before the text goes. |
| `documentation/decisions/0040-the-relay-stores-the-sent-note-time-and-orders-by-a-bounded-copy.md` | The relay stores the sent note time and orders writes by a bounded copy of it (`order_at`), so a fast-clock device still converges. |
| `documentation/decisions/0041-optional-warm-microphone-with-a-400-ms-ring.md` | ADR: an optional warm microphone (`warm_mic`, off by default) with a 400 ms in-memory ring buffer; the opt-in exception to 0017's "never keep the microphone open". |
| `documentation/decisions/0042-a-rules-layer-when-the-ai-gives-no-text.md` | ADR: the rules layer (noises and spoken punctuation commands out, Standard typed-value self-corrections, capitals and the final mark for the style) gives the text when the AI cleanup gives none; `cleanup_min_words` default 4. |
| `documentation/decisions/0043-guard-v2-and-prompt-v3.md` | ADR: guard v2 (self-corrections, insertion budget, protected negations and numbers, echo, EMPTY) and prompt v3 (static part first, matched terms, Whisper prompt v2). |
| `documentation/decisions/0035-sideload-warnings-are-explained-not-engineered-away.md` | ADR: explain the Play Protect and Restricted setting warnings in the app; no `isAccessibilityTool`, targetSdk stays 34, minimum permissions. |
| `documentation/specs/p9h-android-mic-choice.md` | Spec for the Android Microphone setting: what was built, why Bluetooth is not offered yet and the device checklist (not run on a phone). |
| `documentation/specs/p9i-auto-learn.md` | Spec for Learn from my corrections (both apps): what was built, what works where, the limits and the device checklist (not run on a desktop or phone). |
| `documentation/specs/p9g2-install-safety.md` | Spec for part 3 branch G task G2: the Install help card, the permission clean-up, why targetSdk stays 34, what a sideloaded APK cannot avoid, the unverified list. |
| `documentation/specs/p9e-keep-listening.md` | Spec for part 3 branch E: keep listening (Note and Type targets, stop phrase, note shortcut, crash-safe buffer and recovery), the checklist that needs no phone, what was not verified, Android out of scope. |
| `documentation/specs/p9f-improve-my-cleanup.md` | Spec for part 3 branch F: the Improve my cleanup card, what one run sends, the proposal, apply and revert, `my_cleanup_rules` in the prompt and the profile sync, the checklist, known limits. |
| `documentation/specs/p9g-note-bubble.md` | Spec for part 3 branch G: the note bubble that appears while a note records or saves, with the device checklist. |
| `documentation/specs/p6-android-note-mode.md` | Spec for P6: Android note mode (faster start, note bubble, notification, tile), with the device checklist. |
| `documentation/specs/p7e-android-sync.md` | Spec for P7e: Android relay sync and profile merge, with the device checklist and known limits. |
| `documentation/specs/p8c-quick-wins.md` | Spec for P8c: the quick wins (Java test runner and compile check, `ApiClient` rename, `cleanup_min_words`, the relay run from the Windows app), with what was and was not verified. |
| `documentation/specs/p9a-cleanup-keeps-my-words.md` | Spec for P9a: cleanup keeps my words (the fidelity guard, prompt, strength setting, fuzzy dictionary and benchmark are built), with what was and was not verified. |
| `documentation/specs/p9c-devices-and-relay-setup.md` | Spec for P9c (part 3, branch C): the relay's device list, the Devices card, Test connection details and the relay set-up card, with the device checklist and what was not verified. |
| `documentation/specs/p9d-android-bubble.md` | Spec for P9d: the Android bubble that keeps disappearing (diagnostics, watchdog, clamp, Always show the bubble, battery prompt), with the device checklist. |
| `documentation/specs/p8b-design-refresh.md` | Spec for P8b: shared UI parts, regrouped settings and Status card, result flash, safer paste, privacy rewrite, with what was not verified. |
| `documentation/specs/p9b-measure-and-speed-up.md` | Spec for P9b: timings of every dictation, the Speed card on both apps and the Android speed work. |
| `ui-shared/tokens.css` | The palette both pages share (light values and a `@dark` block; Windows gets a `prefers-color-scheme` media query, Android a `.dark` class rule). |
| `ui-shared/components.css` | The CSS declarations that are identical in both pages for `.card .btn .chips .chip .switch .status .srow .hint .day .entry`; each page keeps its own sizes and spacing next to it. |
| `ui-shared/relay-steps.txt` | The one source of the "How to set up the relay" card (intro, numbered steps, the commands of each step, closing notes); `tools/sync_ui.py` writes it into both pages and `relay/README.md` must show the same commands. |
| `ui-shared/common.js` | Pure helpers: `STYLES`, `ABOUT_MAX`, `$`, `esc`, `toast`, `dictRepls`, `dictLines`, `aboutCount`, `agoText`, `combineTests`, `statusRows`, `statusHtml` (the Home status card), `devicesHtml`, `relayCheckRows` (the rows under Test connection). Bridges stay in each page. |
| `tools/bench_cleanup.py` | The cleanup benchmark: runs the app's real cleanup call over your transcribed clips (else the corpus) for one or several models and prompts (`--compare-prompt v1,v3`), scores what gets pasted under one or several guards (`--compare-guard v1,v2`; the current guard called as the app calls it, with the dictionary, and the rules layer of that strength on a rejection or a phrase under the minimum words) against your typed texts, with a rules-only column, compares each column with the first (paired bootstrap 95% interval on the table's micro rate), logs cached and reasoning tokens, prints a decision summary (guards on the run's own answers, rejection reasons, insertion counts, formatted WER by length), and scores the guards on the labelled pairs (`--guard-only`, offline); caches every answer by exact request (`Sender`: a rerun sends nothing), paces requests (`--pause`, `--tpm`), stops and saves on a lasting 429 or Ctrl+C; saves the results under `%APPDATA%\Vox\bench\`. Run by hand, never in CI. |
| `tools/bench_record.py` | Records your own benchmark clips (Enter starts and stops, 16 kHz mono WAV with `sounddevice`) with a suggestion and a progress count, then asks for the verbatim text, the intended text (Enter takes the rules layer's suggestion) and the terms; resumes after a close and never overwrites a clip; a missing microphone falls back to the default one. Clips stay in `%APPDATA%\Vox\bench\clips\`. Run by hand. |
| `tools/bench_stt.py` | Transcribes each recorded clip once per speech setting (server, model, dictionary prompt on/off with the clip's own terms named first, language, and `--trim-pad-ms` / `--prompt-tokens` values to try) through the app's speech path and caches the text next to the clip, so a rerun is free; prints WER (per kind of clip too), term recall, time, and each setting against the first (paired bootstrap). Run by hand. |
| `tools/bench_public.py` | Downloads a small sample (`--count`, default 50) of public speech sets with their transcripts, without a login: Monsoon en-IN (Indian English, CC BY 4.0), HiACC (Hinglish, CC BY 4.0), DisfluencySpeech (disfluent, Apache-2.0), FLEURS en_us (read and punctuated, CC BY 4.0); `--list` shows them. Reads dataset viewer rows, single files of a remote zip by range requests, or the start of a `.tar.gz` stream; converts integer or float WAV to 16 kHz mono 16-bit; writes a clips folder per source in `%APPDATA%\Vox\bench\public\<source>\` (manifest with both references, licence, kind, anonymised speaker; `LICENSE.txt`) that `bench_stt.py --folder` and `bench_cleanup.py --folder` read. Resumable, paced, size-capped (`--max-mb`). Run by hand. |
| `tools/bench_clips.py` | The clips folder of the benchmark: manifest (append-only, newest line per clip wins), WAV write that never overwrites, PCM read, the per-clip transcript cache, the benchmark dictionary (`all_terms`). |
| `tools/bench_metrics.py` | The benchmark's pure metrics (`recall`, `added_rate`, `length_ratio`, `term_hits`, `structure_only`, `percentile`, `score`, `summarize`), which read words the way the fidelity guard does, and the reference metrics (`wer`, `wer_formatted`, `punct_f1`, `case_f1`, `term_recall`, `over_edit_rate`, `self_correction_ok`, `answered_or_obeyed`, `score_pasted`, `summarize_pasted`, `summarize_usage`, `guard_confusion`, `paired_bootstrap`, `by_word_count`, `real_pair_confusion`), which normalise with the frozen v1 word rules; `app_guard` and `guard_detail` call the app's guard as the app does and read its own insertion counts. |
| `tools/bench/legacy.py` | Frozen copies of the v1 cleanup prompt and fidelity guard from `8ea15e1` (before guard v2 and prompt v3) and a v1 cleanup call, so `v1` stays measurable; the app never imports it. |
| `tools/bench/guard_set.jsonl` | 189 labelled (raw, cleaned) pairs with a good/bad label per strength, category and main/held-out split, from the v2 review (D1); scores a guard's precision and recall. |
| `tools/bench/corpus.jsonl` | 45 synthetic transcripts (chat, long, filler-heavy, enumerations, Hinglish, numbers, commands, names from a made-up About you) with style, dictionary terms and the terms the answer must spell exactly. No real person's data. |
| `tools/render_screenshots.py` | Renders `docs/screenshots/*.png` from the apps' own pages: copies each page with a stand-in bridge (`window.pywebview.api`, `window.Vox`) and made-up sample data in front of its scripts and screenshots it with headless Edge or Chrome (fresh profile, every host name blocked). The Windows sample state comes from the app's own modules with `requests` replaced. Standard library only; run by hand, not in CI. |
| `tools/relay_smoke.py` | Smoke test for a standalone relay binary, run by the `relay-exe` CI job: `--help` exits 0, start on `127.0.0.1` with a temp data folder and `--show-token`, `/health` answers 401 without and 200 with the token, `/proof` answers a proof, stop. Never prints the tokens. Standard library only. |
| `tools/check_lock.py` | Fails a release build when the Python it runs in holds anything but the lock files' packages at their versions (`pip` excepted). Standard library only. |
| `tools/build-requirements.lock` | PyInstaller and its dependencies at exact versions, for `Vox.exe` and the relay programs (Windows-only ones marked `sys_platform == "win32"`). |
| `tools/sync_ui.py` | Writes the `ui-shared` blocks into `windows/ui/index.html` and `android/assets/index.html` between the `ui-shared:css` and `ui-shared:js` marker comments; `--check` verifies. |
| `tools/sync_ui.py` | Writes the `ui-shared` blocks into `windows/ui/index.html` and `android/assets/index.html` between the `ui-shared:css`, `ui-shared:js` and `ui-shared:steps` marker comments (the last one is HTML made from `ui-shared/relay-steps.txt`); `--check` verifies. |
| `documentation/tools/check_docs.py` | The documentation checker (tree, config keys, links, ADR index). |
| `documentation/tools/docs_todo.py` | Prints which pages to update for the code that changed (checklist only, edits nothing). |
