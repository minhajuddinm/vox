# 5. Android app

Plain Java (source level 8, no Kotlin, no Gradle, no AndroidX), package `com.minhaj.vox`, minimum Android 8.0 (API 26), target API 34. Version in `android/AndroidManifest.xml` (`versionCode` 4, `versionName` 1.3 at the time of writing). Built with `android/build.sh` (see [decisions/0012-no-gradle-android-build.md](decisions/0012-no-gradle-android-build.md)).

## Manifest (`android/AndroidManifest.xml`)

| Item | Value / purpose |
|---|---|
| Permissions | `RECORD_AUDIO`, `INTERNET`, `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MICROPHONE`, `POST_NOTIFICATIONS`, `VIBRATE` |
| `<queries>` | Lets the Styles page list installed launcher apps |
| Application | `allowBackup="false"`, `networkSecurityConfig="@xml/network_security_config"` |
| `MainActivity` | Exported launcher activity (the screens) |
| `TrampolineActivity` | Not exported, translucent, no history |
| `DictationService` | Not exported, `foregroundServiceType="microphone"` |
| `VoxAccessibilityService` | Bound with `BIND_ACCESSIBILITY_SERVICE`, config `@xml/accessibility_config` |

Accessibility config: events `typeViewFocused | typeWindowStateChanged | typeViewTextSelectionChanged | typeViewClicked`, `canRetrieveWindowContent="true"`, 100 ms notification timeout. No flags.

Network security config: `cleartextTrafficPermitted="true"` for the whole app because Android cannot express address ranges. The app itself refuses plain http unless the host is private (`Endpoint.error`, checked in `GroqClient.open`). See [decisions/0005-configurable-endpoint-private-http.md](decisions/0005-configurable-endpoint-private-http.md).

## `DictationService`

Foreground service (notification "Vox is ready", low importance, with a "Turn off" action; "Retry" appears while a recording is pending). Started only from a visible activity, because Android blocks background microphone access.

| Method | Behaviour |
|---|---|
| `startRecording(pkg, label)` | Refuses (toast) on a bad server address or a missing key (`Prefs.keyMissing`). Creates an `AudioRecord` (16 kHz mono PCM16, `VOICE_RECOGNITION` source) and a recording thread. Increments `jobId`. Limit 360 s. |
| `stopRecording()` | Ends recording, then on the worker thread: drops clips under 0.4 s, rejects silent clips (`Pcm.isSilent`, toast "Vox did not hear anything"), writes the WAV to `cache/vox_pending.wav`, marks pending, calls `send`. |
| `send(job, pkg, label)` | Up to 3 attempts to transcribe (`GroqClient.isRetryable`: network errors, 5xx, 429, 408; waits 0.8 s, 1.6 s between); silence-phrase filter; cleanup unless style is `raw`, cleanup is off or the text has fewer than 3 words; falls back to the raw text (with `applySpokenCommands`) and toasts if cleanup fails; dictionary replacements; history; result delivered to the `Listener`. The WAV is deleted only on success. |
| `retryLast()` | Sends the pending WAV again (notification button). Needs the service to be alive; the WAV is deleted when the service is destroyed or the user cancels. |
| `cancel()` | Bumps `jobId`, deletes the pending WAV, goes idle. |

State machine: `IDLE (0)` -> `RECORDING (1)` -> `PROCESSING (2)` -> `IDLE`. Every change made by a background job is guarded by `isCurrent(job)`; `failRecording` and `finish` are synchronized. This fixed a stuck-bubble bug and a cancel race (see [decisions/0010-job-token-for-android-dictation-state.md](decisions/0010-job-token-for-android-dictation-state.md)).

`DictationService.Listener` (implemented by `VoxAccessibilityService`, set with `DictationService.setListener`): `onState(int)`, `onLevel(float)`, `onResult(String text, String targetPkg)`, `onError(String)`. Results and errors are posted to the main thread.

## `VoxAccessibilityService`

- **Bubble:** `BubbleView` added with `TYPE_ACCESSIBILITY_OVERLAY` (60 dp). Position saved as `bubble_x` / `bubble_y`; it snaps to the nearest screen edge after a drag. Shown only while a text field is focused unless `only_typing` is off (always shown while busy).
- **Gestures:** tap = start (or stop) recording; long press = cancel when busy, otherwise open the settings screen; drag = move.
- **Focus tracking:** on focus, selection and click events it remembers the focused editable node and its package (`editNode`, `editPkg`); it releases held nodes on API < 33.
- **Password fields:** Vox does not start recording when the remembered field is a password field, and refuses to type into one.
- **Insertion (`insertText(text, targetPkg)`):**
  1. Find the currently focused input node (fall back to the remembered one). None: copy to clipboard and toast.
  2. Refuse password fields.
  3. If the focused node's package differs from the package the dictation started in ("you switched apps"), copy to the clipboard instead of typing.
  4. Compute the new text: current text (ignoring a hint shown as placeholder), selection, add a leading space when needed, then `ACTION_SET_TEXT` and move the caret. Known limitation: `SET_TEXT` replaces the whole field ([12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md)).
  5. If `SET_TEXT` is refused, fall back to a clipboard paste and restore the old clip after 800 ms.
- **Lifecycle:** removes the bubble and clears the static listener on unbind and destroy. If `DictationService` is not running when the bubble is tapped it starts `TrampolineActivity`; `onDictationServiceReady` then starts recording after 350 ms.

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
| Settings | API key + test, Server address, AI cleanup, keep history, bubble only while typing, language, dictation service switch, battery, speech and cleanup model, clear history |

## `Prefs` (SharedPreferences file `vox`)

Keys, defaults and formats: [07-config-and-data.md](07-config-and-data.md). History is a JSON array of up to 500 entries, newest first. `Prefs.dictionaryTerms()` and `replacements()` delegate to `Terms`.

## Helper classes (pure Java, unit-tested)

`GroqClient` static helpers, `Terms`, `Endpoint`, `Pcm`, `Corrections`. They avoid Android APIs on purpose so CI can test them with plain `javac` and `java`. See [10-build-test-release.md](10-build-test-release.md).

## Not present on Android

Meeting notes, calendar, hotkeys, overlay pill, DPAPI-style key protection (the key is in app-private SharedPreferences; see [09-security-privacy.md](09-security-privacy.md)), file logs (the app does not use `android.util.Log`; problems appear as toasts).

## AI provider settings

Settings starts with an **AI provider** card: preset list (`Providers.PRESETS`, sent in `Bridge.state` as `presets`; a phone cannot use `localhost`, so servers of your own are the single "custom" preset), address, key, Voice model and Cleanup model boxes (text fields with a `<datalist>`), Refresh, and Test buttons. `Bridge.listModels(role, form, callback)` and `Bridge.testRole(...)` run on a background thread and answer the named JavaScript callback with a JSON string; `form` holds the settings as typed. A switch reveals a separate server and key for voice or cleanup. `DictationService.send` builds one `GroqClient` per role from `Prefs.role(...)`. `GroqClient` keeps its name for now (a rename is deferred to keep the diff small). The datalist dropdown has not been checked on a device.

## Connection warm-up

`DictationService.startRecording` checks the address of each role (speech and cleanup), then starts a `vox-warm` thread that calls `GroqClient.warm()` for each distinct server: a small `GET /models` read to the end, so the connection returns to the pool. `GroqClient.readJson` no longer calls `disconnect()`, so the upload reuses it. The start delay of the bubble (400 ms trampoline activity plus 350 ms in `VoxAccessibilityService.onDictationServiceReady`) is unchanged.

## About you

The Dictionary page starts with an **About you** card bound to the `user_context` preference (`Prefs.userContext`, `Bridge.state`/`save`). `DictationService.send` passes it to `GroqClient.cleanup`, which adds it to the prompt.

## Recording meter

The record loop in `DictationService.startRecording` reads 40 ms buffers and posts `Pcm.levelFromRms(rms)` to the listener (about 25 updates a second); `BubbleView.setLevel` rises fast (65% new value) and falls slowly (20%). Same curve as Windows.
