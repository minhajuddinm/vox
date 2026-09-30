# 5. Android app

Plain Java (source level 8, no Kotlin, no Gradle, no AndroidX), package `com.minhaj.vox`, minimum Android 8.0 (API 26), target API 34. Version in `android/AndroidManifest.xml` (`versionCode` 4, `versionName` 1.3 at the time of writing). Built with `android/build.sh` (see [decisions/0012-no-gradle-android-build.md](decisions/0012-no-gradle-android-build.md)).

## Manifest (`android/AndroidManifest.xml`)

| Item | Value / purpose |
|---|---|
| Permissions | `RECORD_AUDIO`, `INTERNET`, `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MICROPHONE`, `POST_NOTIFICATIONS`, `VIBRATE` |
| `<queries>` | Lets the Styles page list installed launcher apps |
| Application | `allowBackup="false"`, `networkSecurityConfig="@xml/network_security_config"` |
| `MainActivity` | Exported launcher activity (the screens) |
| `TrampolineActivity` | Not exported, translucent, no history; closes when `DictationService` calls `TrampolineActivity.finishNow()`, or after a 1500 ms safety timeout |
| `DictationService` | Not exported, `foregroundServiceType="microphone"` |
| `VoxAccessibilityService` | Bound with `BIND_ACCESSIBILITY_SERVICE`, config `@xml/accessibility_config` |

Accessibility config: events `typeViewFocused | typeWindowStateChanged | typeViewTextSelectionChanged | typeViewClicked`, `canRetrieveWindowContent="true"`, 100 ms notification timeout. No flags.

Network security config: `cleartextTrafficPermitted="true"` for the whole app because Android cannot express address ranges. The app itself refuses plain http unless the host is private (`Endpoint.error`, checked in `ApiClient.open`). See [decisions/0005-configurable-endpoint-private-http.md](decisions/0005-configurable-endpoint-private-http.md).

## `DictationService`

Foreground service (notification "Vox is ready", low importance, with a "Turn off" action; "Retry" appears while a recording is pending). Started only from a visible activity, because Android blocks background microphone access.

**Start intent.** `onStartCommand` calls `startForeground`, sets `instance`, and, when the intent has `EXTRA_START`, calls `startRecording` itself, then `TrampolineActivity.finishNow()` (also when nothing was asked to start, and even if `startRecording` throws). `TrampolineActivity` passes its own extras through, so a bubble tap with the service not running needs no further hop and no fixed delay. Extras: `EXTRA_START` (boolean), `EXTRA_PKG` and `EXTRA_LABEL` (the app being typed into), `EXTRA_DEST` (`dictation`, the default, or `note`; any other value means `dictation`), `EXTRA_TAP_AT` (`SystemClock.elapsedRealtime()` of the tap, only used by the log). A start without `EXTRA_START` (the settings switch, `MainActivity`) just starts the service.

| Method | Behaviour |
|---|---|
| `startRecording(pkg, label, dest, tapAtMs)` | Refuses (toast) on a bad server address or a missing key (`Prefs.keyMissing`). Creates an `AudioRecord` (16 kHz mono PCM16, `VOICE_RECOGNITION` source) and a recording thread. Increments `jobId`. Limit 360 s. Remembers `dest` for the recording (`dictation` or `note`). When `tapAtMs` is not 0, the recording thread logs `tap->recording ms=N` (tag `vox`, debug level) at the first audio frame. `startRecording(pkg, label, dest)` and `startRecording(pkg, label)` (dest `dictation`) call it with no tap time. |
| `stopRecording()` | Ends recording, then on the worker thread: drops clips under 0.4 s, rejects silent clips (`Pcm.isSilent`, toast "Vox did not hear anything"), writes the WAV to `cache/vox_pending.wav`, marks pending, calls `send`. |
| `send(job, pkg, label)` | Up to 3 attempts to transcribe (`ApiClient.isRetryable`: network errors, 5xx, 429, 408; waits 0.8 s, 1.6 s between); silence-phrase filter; cleanup only when `ApiClient.needsCleanup` says so (style is not `raw`, cleanup is on and the text has at least `Prefs.cleanupMinWords` words, default 3); falls back to the raw text (with `applySpokenCommands`) and toasts if cleanup fails; dictionary replacements; history; result delivered to the `Listener`. The WAV is deleted only on success. |
| `retryLast()` | Sends the pending WAV again (notification button). Needs the service to be alive; the WAV is deleted when the service is destroyed or the user cancels. |
| `cancel()` | Bumps `jobId`, deletes the pending WAV, goes idle. |

State machine: `IDLE (0)` -> `RECORDING (1)` -> `PROCESSING (2)` -> `IDLE`. Every change made by a background job is guarded by `isCurrent(job)`; `failRecording` and `finish` are synchronized. This fixed a stuck-bubble bug and a cancel race (see [decisions/0010-job-token-for-android-dictation-state.md](decisions/0010-job-token-for-android-dictation-state.md)).

`DictationService.Listener` (implemented by `VoxAccessibilityService`, set with `DictationService.setListener`): `onState(int)`, `onLevel(float)`, `onResult(String text, String targetPkg)`, `onError(String)`. Results and errors are posted to the main thread.

## `VoxAccessibilityService`

- **Bubble:** `BubbleView` added with `TYPE_ACCESSIBILITY_OVERLAY` (60 dp). Position saved as `bubble_x` / `bubble_y`; it snaps to the nearest screen edge after a drag. Shown only while a text field is focused unless `only_typing` is off (always shown while busy).
- **Gestures:** tap = start (or stop) recording; long press = cancel when busy, otherwise open the settings screen; drag = move.
- **Focus tracking:** on focus, selection and click events it remembers the focused editable node and its package (`editNode`, `editPkg`); it releases held nodes on API < 33.
- **Password fields:** Vox does not start recording when the remembered field is a password field, and refuses to type into one.
- **Result flash:** `onResult` calls `BubbleView.flash(SENT)` when `insertText` returned true and `flash(ERROR)` when the text only reached the clipboard or was refused; `onError` calls `flash(ERROR)` (see [Result flash on the bubble](#result-flash-on-the-bubble)).
- **Insertion (`insertText(text, targetPkg)`, returns true when the text was typed into the field, false when it was copied to the clipboard instead or refused):**
  1. Find the currently focused input node (fall back to the remembered one). None: copy to clipboard and toast.
  2. Refuse password fields.
  3. If the focused node's package differs from the package the dictation started in ("you switched apps"), copy to the clipboard instead of typing.
  4. Compute the new text: current text (ignoring a hint shown as placeholder), selection, add a leading space when needed, then `ACTION_SET_TEXT` and move the caret. Known limitation: `SET_TEXT` replaces the whole field ([12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md)).
  5. If `SET_TEXT` is refused, fall back to a clipboard paste and restore the old clip after 800 ms.
- **Lifecycle:** removes the bubble and clears the static listener on unbind and destroy. If `DictationService` is not running when the bubble is tapped (and the remembered field is not a password field) it starts `TrampolineActivity` with `EXTRA_START`, the package and label of the focused app and the tap time; the service then starts recording by itself (there is no `onDictationServiceReady` and no 350 ms wait any more). The haptic tick is given at the tap on both paths.

## `MainActivity` and the `Vox` bridge

A `WebView` (JavaScript on, DOM storage on, file access off) loads `file:///android_asset/index.html`. Back presses go to the page first (`window.voxBack`). `onResume` calls `window.voxRefresh`.

`Bridge` methods (exposed to JavaScript as `window.Vox`): `state`, `save(json)`, `requestMic`, `openAccessibility`, `openAppInfo`, `openBattery`, `setService(on)`, `testKey(key, baseUrl, callback)`, `suggestCorrections(original, edited)`, `endpointProblem(baseUrl)`, `copy`, `deleteHistory(t)`, `clearHistory`, `openUrl(https only)`, `toast`, `apps()`, `appLabel(pkg)`.

`save` writes only keys present in the JSON; `base_url` is saved only if `Endpoint.error` says it is acceptable.

## Screens (`android/assets/index.html`)

One HTML file, works in a normal browser too (a mock `Vox` object is used when the bridge is missing, which is how UI changes can be tried without a phone).

| Page | Content |
|---|---|
| Home | Setup checklist (key, microphone, accessibility bubble, dictation service), try-it box, stats, searchable history (copy, delete, "Fix a word") |
| Dictionary | Words, People, Replacements |
| Styles | Default style and a style per installed app |
| Settings | API key + test, Server address, AI cleanup, skip cleanup below N words, keep history, bubble only while typing, language, dictation service switch, battery, speech and cleanup model, clear history |

## `Prefs` (SharedPreferences file `vox`)

Keys, defaults and formats: [07-config-and-data.md](07-config-and-data.md). History is a JSON array of up to 500 entries, newest first. `Prefs.dictionaryTerms()` and `replacements()` delegate to `Terms`.

## Helper classes (pure Java, unit-tested)

`ApiClient` static helpers, `Terms`, `Endpoint`, `Pcm`, `Corrections`, `NoteLogic` (the voice-note rules: title, search words and string, sync merge, tag clean-up), `Note` (plain value class for one note), `SyncStore` (interface for the sync; see the next section) and `ProfileMerge` (the three-way merge of the profile that will follow the user between devices, and the lists of fields that travel; the same rule as `sync.merge3` on Windows). Nothing in the app calls the note classes yet; they are the groundwork for notes on the phone. They avoid Android APIs on purpose so CI can test them with plain `javac` and `java`. See [10-build-test-release.md](10-build-test-release.md).

## Voice notes store (`NotesStore`)

`NotesStore` keeps voice notes in a SQLite database, `notes.db`, in the app's private `databases` folder (write-ahead logging on). It is a literal port of `windows/notes.py`: the same tables (`notes`, `sync_meta`, and `notes_fts` when this phone's SQLite has FTS5; otherwise search matches each word with `LIKE`), the same columns including `dirty` and `seq`, and the same operations: `add` (returns the new id), `get`, `update` (a `null` argument keeps the current value), `delete` (keeps a marker row with `deleted = 1` and the text fields emptied), `search` (query words, source, created-after, created-before, tag, limit; newest first), `count`, and for the sync `dirtyNotes`, `markSynced` (clears `dirty` only when `updated_at` is still the value that was sent), `applyRemote` (`NoteLogic.remoteWins` decides), `getMeta` and `setMeta`. The decisions (title, search words, which side of a sync wins, tags) live in `NoteLogic`, so this class only runs SQL. `Note` is the plain value class it returns; `SyncStore` is the interface the relay sync uses (`NotesStore` implements it, tests can use an in-memory one). `NotesStore.get(context)` returns the shared instance. Table and column formats are in [07-config-and-data.md](07-config-and-data.md).

Nothing in the app calls it yet. Android's SQLite cannot run in the local Java tests, so `NotesStore` is only type-checked (`android/compile-check.sh`); it has not been run on a device (whether the phone's SQLite has FTS5, and that the `LIKE` fallback works, are still to be checked).

## Not present on Android

Meeting notes, calendar, hotkeys, overlay pill, DPAPI-style key protection (the key is in app-private SharedPreferences; see [09-security-privacy.md](09-security-privacy.md)), file logs (the only `android.util.Log` call is the debug line `tap->recording ms=` described under `DictationService`; problems appear as toasts).

## AI provider settings

Settings starts with an **AI provider** card: preset list (`Providers.PRESETS`, sent in `Bridge.state` as `presets`; a phone cannot use `localhost`, so servers of your own are the single "custom" preset), address, key, Voice model and Cleanup model boxes (text fields with a `<datalist>`), Refresh, and Test buttons. `Bridge.listModels(role, form, callback)` and `Bridge.testRole(...)` run on a background thread and answer the named JavaScript callback with a JSON string; `form` holds the settings as typed. A switch reveals a separate server and key for voice or cleanup. `DictationService.send` builds one `ApiClient` per role from `Prefs.role(...)` (renamed from `GroqClient`). The datalist dropdown has not been checked on a device.

## Connection warm-up

`DictationService.startRecording` checks the address of each role (speech and cleanup), then starts a `vox-warm` thread that calls `ApiClient.warm()` for each distinct server: a small `GET /models` read to the end, so the connection returns to the pool. `ApiClient.readJson` no longer calls `disconnect()`, so the upload reuses it. The fixed start delays of the bubble (a 400 ms trampoline activity plus 350 ms in `VoxAccessibilityService.onDictationServiceReady`) are gone: see "Start intent" above. The new tap-to-first-audio time has not been measured on a device yet (read it from `adb logcat -s vox`).

## About you

The Dictionary page starts with an **About you** card bound to the `user_context` preference (`Prefs.userContext`, `Bridge.state`/`save`). `DictationService.send` passes it to `ApiClient.cleanup`, which adds it to the prompt.

## Recording meter

The record loop in `DictationService.startRecording` reads 40 ms buffers and posts `Pcm.levelFromRms(rms)` to the listener (about 25 updates a second); `BubbleView.setLevel` rises fast (65% new value) and falls slowly (20%). Same curve as Windows.

## Result flash on the bubble

`BubbleView.flash(kind)` with `BubbleView.SENT` or `BubbleView.ERROR` shows, for a moment, the same signal as the pill on Windows ([04-windows-app.md](04-windows-app.md#result-signal-on-the-pill)): a green circle with a check that draws itself (0.7 s), or a red circle that shakes once, with a "!" (1.8 s), and then the normal idle bubble. The service's states are unchanged and the toast keeps the words.

- **Only while idle:** the flash is drawn only when the bubble's state is `IDLE`. `DictationService` posts `onResult` / `onError` before it goes back to idle, and the bubble's `setState(IDLE)` does not cancel the flash; `setState(RECORDING)` and `setState(PROCESSING)` do, so a new recording or a retry replaces it at once.
- **No animator:** the flash is timed from `SystemClock.uptimeMillis()` and redrawn with `postInvalidateOnAnimation()` while it lasts, so it does not depend on the system's animation-speed setting, and an old flash can never reappear (once its time is up it is forgotten).
- **Who calls it:** `VoxAccessibilityService.onResult` (SENT when the text was typed, ERROR when it only reached the clipboard) and `onError` (ERROR). A warning that is followed by a result (cleanup fell back to the raw words) calls ERROR and then SENT in the same pass, so the check wins.
- **Not covered:** if "bubble only while typing" hides the bubble when the field loses focus, the flash is not seen (the toast still is). Only compiled (`android/compile-check.sh`), not run on a device.
