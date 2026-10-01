# 5. Android app

Plain Java (source level 8, no Kotlin, no Gradle, no AndroidX), package `com.minhaj.vox`, minimum Android 8.0 (API 26), target API 34. Version in `android/AndroidManifest.xml` (`versionCode` 4, `versionName` 1.3 at the time of writing). Built with `android/build.sh` (see [decisions/0012-no-gradle-android-build.md](decisions/0012-no-gradle-android-build.md)).

## Manifest (`android/AndroidManifest.xml`)

| Item | Value / purpose |
|---|---|
| Permissions | `RECORD_AUDIO`, `INTERNET`, `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MICROPHONE`, `POST_NOTIFICATIONS`, `VIBRATE` |
| `<queries>` | Lets the Styles page list installed launcher apps |
| Application | `allowBackup="false"`, `networkSecurityConfig="@xml/network_security_config"` |
| `MainActivity` | Exported launcher activity (the screens) |
| `TrampolineActivity` | Not exported, translucent, no history; closes when `DictationService` calls `TrampolineActivity.finishNow()`, or after a 1500 ms safety timeout. It keeps every open instance (a weak set), so `finishNow()` closes all of them: a fast double tap on a cold start no longer leaves the first one up until the timeout |
| `DictationService` | Not exported, `foregroundServiceType="microphone"` |
| `NoteTileService` | The quick settings tile "Voice note". Exported (Android binds to tiles), `BIND_QUICK_SETTINGS_TILE`, action `android.service.quicksettings.action.QS_TILE`, icon `@drawable/ic_note`, label `@string/tile_note_label` |
| `VoxAccessibilityService` | Bound with `BIND_ACCESSIBILITY_SERVICE`, config `@xml/accessibility_config` |

Accessibility config: events `typeViewFocused | typeWindowStateChanged | typeViewTextSelectionChanged | typeViewClicked`, `canRetrieveWindowContent="true"`, 100 ms notification timeout. No flags.

Network security config: `cleartextTrafficPermitted="true"` for the whole app because Android cannot express address ranges. The app itself refuses plain http unless the host is private (`Endpoint.error`, checked in `ApiClient.open`). See [decisions/0005-configurable-endpoint-private-http.md](decisions/0005-configurable-endpoint-private-http.md).

## `DictationService`

Foreground service (notification "Vox is ready", low importance, with a "Turn off" action; "Retry" appears while a recording is pending; while a voice note is being recorded the title is "Recording a voice note" and a "Stop" action is added). Started only from a visible activity, because Android blocks background microphone access.

**Start intent.** `onStartCommand` calls `startForeground`, sets `instance`, and, when the intent has `EXTRA_START`, calls `startRecording` itself, then `TrampolineActivity.finishNow()` (also when nothing was asked to start, and even if `startRecording` throws). `TrampolineActivity` passes its own extras through, so a bubble tap, the "Record note" notification or the tile with the service not running needs no further hop and no fixed delay. Extras: `EXTRA_START` (boolean), `EXTRA_PKG` and `EXTRA_LABEL` (the app being typed into), `EXTRA_DEST` (`dictation`, the default, or `note`; any other value means `dictation`), `EXTRA_TAP_AT` (`SystemClock.elapsedRealtime()` of the tap, only used by the log). A start without `EXTRA_START` (the settings switch, `MainActivity`) just starts the service. Every start that goes into the foreground (not "Turn off", "Retry" or "Stop recording") ends with `SyncWorker.kick(this)`: a sync run when relay sync is on, nothing when it is off. Microphone access comes from the while-in-use grant the microphone foreground service receives at `startForeground` while the trampoline is visible; the `AudioRecord` is created inside `startRecording` and starts capturing a moment later on the `vox-rec` thread, after the trampoline was asked to close.

**Other actions** (intents with an action, handled before any of the above): `ACTION_STOP` ("Turn off": `stopSelf`), `ACTION_RETRY` (`retryLast`) and `ACTION_STOP_RECORDING` (`stopRecording`; the service keeps running, and a stop that reaches a service that is not in the foreground stops it again so nothing is left behind). `ACTION_STOP_RECORDING` is what the note notification's "Stop" button and the tile send.

| Method | Behaviour |
|---|---|
| `startRecording(pkg, label, dest, tapAtMs)` | Refuses (toast) on a bad server address or a missing key (`Prefs.keyMissing`). Creates an `AudioRecord` (16 kHz mono PCM16, `VOICE_RECOGNITION` source) and a recording thread. Increments `jobId`. Limit 360 s. Remembers `dest` for the recording (`dictation` or `note`); for a note it forgets the app (`pkg` null, `label` empty), as Windows note mode does, so the default style is used and no app name goes to the cleanup model. When `tapAtMs` is not 0, the recording thread logs `tap->recording ms=N` (tag `vox`, debug level) at the first audio frame. `startRecording(pkg, label, dest)` and `startRecording(pkg, label)` (dest `dictation`) call it with no tap time. |
| `stopRecording()` | Ends recording, then on the worker thread: drops clips under 0.4 s, rejects silent clips (`Pcm.isSilent`, toast "Vox did not hear anything"), writes the WAV to `cache/vox_pending.wav`, marks pending (and keeps the recording's `pkg`, `label` and `dest` with it), calls `send`. `pkg`, `label` and `dest` are copied when recording stops, so a later recording cannot change where this one goes. |
| `send(job, pkg, label, dest)` | Up to 3 attempts to transcribe (`ApiClient.isRetryable`: network errors, 5xx, 429, 408; waits 0.8 s, 1.6 s between); silence-phrase filter; cleanup only when `ApiClient.needsCleanup` says so (style is not `raw`, cleanup is on and the text has at least `Prefs.cleanupMinWords` words, default 3); falls back to the raw text (with `applySpokenCommands`) and toasts if cleanup fails; dictionary replacements; then by `dest`: `dictation` writes the history and delivers the result to the `Listener`, `note` saves a voice note (see "Voice note mode"). The WAV is deleted only on success. |
| `retryLast()` | Sends the pending WAV again (notification button), to the destination that recording was made for (the `pkg`, `label` and `dest` kept with the file), not the latest recording's: a short or silent recording started in between cannot turn an old voice note into typed text. Needs the service to be alive; the WAV is deleted when the service is destroyed or the user cancels. |
| `cancel()` | Bumps `jobId`, deletes the pending WAV, goes idle. |
| `isNoteJob()`, `isNoteRecording()` | Whether the job in progress is a note (the accessibility service uses it to choose which bubble shows the state), and whether a note is being recorded right now. |

State machine: `IDLE (0)` -> `RECORDING (1)` -> `PROCESSING (2)` -> `IDLE`. Every change made by a background job is guarded by `isCurrent(job)`; `failRecording` and `finish` are synchronized. This fixed a stuck-bubble bug and a cancel race (see [decisions/0010-job-token-for-android-dictation-state.md](decisions/0010-job-token-for-android-dictation-state.md)).

`DictationService.Listener` (implemented by `VoxAccessibilityService`, set with `DictationService.setListener`): `onState(int)`, `onLevel(float)`, `onResult(String text, String targetPkg)`, `onError(String)`. Results and errors are posted to the main thread. When no listener is set (no accessibility service, for example a note started from the tile) an error is shown as a toast by the service itself. A voice note never reaches `onResult`: it has its own `DictationService.NoteListener` (`setNoteListener`, `onNoteSaved(String id, String title)`, main thread), independent of the accessibility service.

## `VoxAccessibilityService`

- **Bubble:** `BubbleView` added with `TYPE_ACCESSIBILITY_OVERLAY` (60 dp). Position saved as `bubble_x` / `bubble_y`; it snaps to the nearest screen edge after a drag. Shown only while a text field is focused unless `only_typing` is off (always shown while busy with a dictation).
- **Note bubble:** a second `BubbleView` (blue when idle, a note page instead of the mic) that is on screen whenever `note_bubble` is on, whatever is focused and whatever `only_typing` says. Position saved as `note_bubble_x` / `note_bubble_y` (default: the right edge, lower than the mic bubble); same snap to the edge. Both bubbles are one `Floating` object each (view, window layout, shown flag) sharing the touch handler. `refreshVisibility` decides both: the mic bubble as above (a note in progress does not bring it up), the note bubble from `note_bubble` alone. The state and level go to the bubble of the job in progress (`DictationService.isNoteJob`); the other one stays idle.
- **Gestures:** tap = start (or stop) recording; long press = cancel when busy, otherwise open the settings screen; drag = move. On the note bubble a tap starts a note, or finishes the note being recorded (while a dictation is in progress it says "Finish the dictation first"); on the mic bubble a tap during a note says "Vox is busy with a voice note". A long press cancels only the job that bubble shows; on the other bubble it opens the settings. The note bubble ignores password fields: nothing is typed.
- **Focus tracking:** on focus, selection and click events it remembers the focused editable node and its package (`editNode`, `editPkg`); it releases held nodes on API < 33.
- **Password fields:** Vox does not start recording when the remembered field is a password field, and refuses to type into one.
- **Result flash:** `onResult` calls `BubbleView.flash(SENT)` on the mic bubble when `insertText` returned true and `flash(ERROR)` when the text only reached the clipboard or was refused; `onError` calls `flash(ERROR)` on the bubble of the job in progress (the note bubble for a voice note, otherwise the mic bubble) (see [Result flash on the bubble](#result-flash-on-the-bubble)).
- **Insertion (`insertText(text, targetPkg)`, returns true when the text was typed into the field, false when it was copied to the clipboard instead or refused):**
  1. Find the currently focused input node (fall back to the remembered one). None: copy to clipboard and toast.
  2. Refuse password fields.
  3. If the focused node's package differs from the package the dictation started in ("you switched apps"), copy to the clipboard instead of typing.
  4. Compute the new text: current text (ignoring a hint shown as placeholder), selection, add a leading space when needed, then `ACTION_SET_TEXT` and move the caret. Known limitation: `SET_TEXT` replaces the whole field ([12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md)).
  5. If `SET_TEXT` is refused, fall back to a clipboard paste and restore the old clip after 800 ms.
- **Lifecycle:** removes both bubbles and clears the static listener on unbind and destroy. If `DictationService` is not running when the bubble is tapped (and the remembered field is not a password field) it starts `TrampolineActivity` with `EXTRA_START`, the package and label of the focused app and the tap time; the service then starts recording by itself (there is no `onDictationServiceReady` and no 350 ms wait any more). The note bubble does the same with `NoteEntry.startIntent` (destination `note`, no app). The haptic tick is given at the tap on both paths. When the service connects (also after a reboot) it calls `NoteEntry.applySettings`, which puts the "Record note" notification back if `note_notification` is on.

## `MainActivity` and the `Vox` bridge

A `WebView` (JavaScript on, DOM storage on, file access off) loads `file:///android_asset/index.html`. Back presses go to the page first (`window.voxBack`). `onResume` calls `window.voxRefresh` and `SyncWorker.kick`, and while the activity is resumed it registers a listener that calls `voxRefresh` again after a sync run that changed the settings of this phone (so a stale page cannot save over them); `onPause` removes it. `save` also calls `SyncWorker.kick` after it stored the settings.

`Bridge` methods (exposed to JavaScript as `window.Vox`): `state`, `save(json)`, `requestMic`, `openAccessibility`, `openAppInfo`, `openBattery`, `setService(on)`, `testKey(key, baseUrl, callback)`, `suggestCorrections(original, edited)`, `endpointProblem(baseUrl)`, `copy`, `deleteHistory(t)`, `clearHistory`, `openUrl(https only)`, `toast`, `apps()`, `appLabel(pkg)`; for voice notes `notesList(query, period, tag)`, `noteEdit(id, title, text, tagsJson)`, `noteDelete(id)`, `noteToggle()`, `noteStatus()`; for the relay `syncStatus()`, `syncNow(callback)`, `syncTest(callback)` (see "Relay sync").

`save` writes only keys present in the JSON; `base_url`, `stt_base_url`, `llm_base_url` and `relay_url` are saved only if `Endpoint.error` says they are acceptable. The relay keys (`relay_sync`, `relay_url`, `relay_token`, `relay_sync_keys`) and `device_name` go through `save` like the others, and `state()` returns them in `config` (the token too, as it does the API key, for the password box), plus `device_default` (the name used while none is typed). `state` reports and `save` accepts the two note settings `note_bubble` and `note_notification`; after a save the bubbles are refreshed and `NoteEntry.applySettings` posts or removes the "Record note" notification (`onCreate` calls it too). The Settings page has no rows for these two yet, so for now they can only be changed through the bridge.

## Screens (`android/assets/index.html`)

One HTML file, works in a normal browser too (a mock `Vox` object is used when the bridge is missing, which is how UI changes can be tried without a phone).

| Page | Content |
|---|---|
| Home | Setup checklist (key, microphone, accessibility bubble, dictation service), try-it box, stats, searchable history (copy, delete, "Fix a word") |
| Notes | Voice notes: New voice note / Finish note button, search, period filter (any time, today, last 7 days, last 30 days), a tag filter (tap a tag), notes grouped by day (title, text, tags, time, length, device; tap the text to expand), copy, edit sheet (title, text, tags), delete (two taps), the sync line and Sync now. See "Voice notes page". |
| Dictionary | Words, People, Replacements |
| Styles | Default style and a style per installed app |
| Settings | API key + test, Server address, AI cleanup, skip cleanup below N words, keep history, bubble only while typing, language, dictation service switch, battery, speech and cleanup model, **Sync between devices** (relay on/off, address, token, Test connection, "also share my provider settings and API keys" (off), this phone's name), clear history (no rows yet for the note bubble and the "Record note" notification) |

## Voice note mode

A voice note is recorded like a dictation but goes to the notes store instead of a text field. It can be started from three places and stopped from each of them; all end up in `DictationService` with `dest = note`.

| Entry point | Start | Stop |
|---|---|---|
| Note bubble (`note_bubble` on, needs the accessibility service) | tap: `startRecording(null, "", note)` when the service is running, otherwise `TrampolineActivity` | tap |
| "Record note" notification (`note_notification` on; `NoteEntry.ensureNotification`) | action button: a `PendingIntent.getActivity` (immutable) to `TrampolineActivity` with `EXTRA_START` and `EXTRA_DEST=note` | the "Stop" action that the service's own notification gets while a note is recorded: `ACTION_STOP_RECORDING` |
| Quick settings tile "Voice note" (`NoteTileService`) | tap: `startActivityAndCollapse(PendingIntent)` on Android 14 and later, `startActivityAndCollapse(Intent)` before; after the phone is unlocked | tap while a note is recorded: `startService` with `ACTION_STOP_RECORDING` (the tile says "busy" while a dictation is in progress) |

`NoteEntry` holds the intent that starts a note (`startIntent`, `startPendingIntent`, the same for every entry point), the ongoing "Record note" notification (channel "Voice notes", low importance, id 2; `ensureNotification`, `cancelNotification`, and `applySettings`, which does one or the other from `note_notification`) and the "Note saved" notification. On Android 14 a user can swipe the ongoing notification away; it comes back when the app or the accessibility service starts. There is no boot receiver, so after a reboot it stays away until one of those starts.

**What is saved.** The note branch of `send` mirrors `Engine._process` on Windows: the recording is transcribed, the same silence and cleanup gates run (`needsCleanup`, default style because a note has no app, `looksValid`, spoken commands when cleanup did not run, dictionary replacements), and `NotesStore.add(text, raw, secs, "voice note", device name, no tags, no title)` stores the cleaned text, the transcript before cleanup, the length and `Prefs.deviceName()`; the title is the first words of the text (`NoteLogic.autoTitle`). Nothing is typed, nothing goes into the dictation history, and `insertText` is never called. An empty result saves nothing and says so. If cleanup failed the words are saved as spoken and a toast says so ("Cleanup did not work, so Vox saved your words as spoken"). If the database cannot be written the recording stays on disk for Retry.

**After the save.** `NoteEvents.fireSaved()` tells the registered listeners (the relay sync will register one; the class only keeps a thread-safe list of `Runnable`s, see below), then on the main thread a "Note saved: title" notification is posted (heads-up channel "Voice note saved", replaces the previous one, goes away after 20 s; a toast when notifications are off for the app) and `DictationService.NoteListener.onNoteSaved(id, title)` is called.

**Not checked on a device.** None of this has run on a phone (the local tests cannot run the framework classes). The device checklist: start from the notification, the tile and the bubble; stop from each; the microphone service starts on Android 14 and 15 from each path (the tile and notification go through the trampoline, the bubble through the trampoline or the running service); the note appears in the store with the right text, raw text, length and device; the "Stop" action appears in the service notification only while recording; the tile shows active when opened during a recording; a double tap on a cold start leaves no trampoline on screen.

## `Prefs` (SharedPreferences file `vox`)

Keys, defaults and formats: [07-config-and-data.md](07-config-and-data.md). History is a JSON array of up to 500 entries, newest first. `Prefs.dictionaryTerms()` and `replacements()` delegate to `Terms`.

## Helper classes (pure Java, unit-tested)

`ApiClient` static helpers, `Terms`, `Endpoint`, `Pcm`, `Corrections`, `NoteLogic` (the voice-note rules: title, search words and string, sync merge, tag clean-up), `Note` (plain value class for one note), `SyncStore` (interface for the sync; see the next section), `ProfileMerge` (the three-way merge of the profile that follows the user between devices, and the lists of fields that travel; the same rule as `sync.merge3` on Windows), the relay sync classes `SyncEngine`, `RelayApi`, `RelayClient`, `SyncConfig`, `SyncResult`, `ProfileMap` and `PlainJson` (see "Relay sync") and `NoteEvents` (the "a note was saved" hook: a static, thread-safe list of `Runnable` listeners with `addSavedListener` (adding the same one twice has no effect), `removeSavedListener` and `fireSaved`, which runs every listener on the calling thread and lets one that throws not stop the others; nothing registers a listener yet, see "Relay sync"). `NoteLogic` also holds `deviceName` (the name the phone shows on the relay and on its notes) and `periodStart` (the "created since" bound of the period filter). `DictationService` (note mode, above), `MainActivity.Bridge` (the Notes page) and the relay sync use these classes. They avoid Android APIs on purpose so CI can test them with plain `javac` and `java`. See [10-build-test-release.md](10-build-test-release.md).

## Voice notes store (`NotesStore`)

`NotesStore` keeps voice notes in a SQLite database, `notes.db`, in the app's private `databases` folder (write-ahead logging on). It is a literal port of `windows/notes.py`: the same tables (`notes`, `sync_meta`, and `notes_fts` when this phone's SQLite has FTS5; otherwise search matches each word with `LIKE`), the same columns including `dirty` and `seq`, and the same operations: `add` (returns the new id), `get`, `update` (a `null` argument keeps the current value; returns the changed note, or `null` when there is no such note), `delete` (keeps a marker row with `deleted = 1` and the text fields emptied; returns whether a note was deleted), `search` (query words, source, created-after, created-before, tag, limit; newest first), `count`, and for the sync `dirtyNotes`, `markSynced` (clears `dirty` only when `updated_at` is still the value that was sent), `applyRemote` (`NoteLogic.remoteWins` decides), `getMeta` and `setMeta`. The decisions (title, search words, which side of a sync wins, tags) live in `NoteLogic`, so this class only runs SQL. `Note` is the plain value class it returns; `SyncStore` is the interface the relay sync uses (`NotesStore` implements it, tests can use an in-memory one). `NotesStore.get(context)` returns the shared instance. Table and column formats are in [07-config-and-data.md](07-config-and-data.md).

Note mode calls `add` (then `get` for the title); the Notes page and the relay sync use the rest. A null or empty id or key never reaches a query (`rawQuery` throws on a null argument): `get(null)` is `null`, `update(null, ...)` is `null`, `delete(null)` is `false`, `getMeta(null, d)` is `d`, `applyRemote` of a note without an id is `false`, `setMeta(k, null)` stores an empty string (the column is `NOT NULL`) and a null key is ignored. The Notes page reads and changes the notes through `Bridge` (see "Voice notes page"). Android's SQLite cannot run in the local Java tests, so `NotesStore` is only type-checked (`android/compile-check.sh`); it has not been run on a device (whether the phone's SQLite has FTS5, and that the `LIKE` fallback works, are still to be checked).

## Voice notes page

The **Notes** page (fifth item of the bottom bar, `<section id="notes">`) is the phone's counterpart of the Windows Voice notes page. It calls these `Bridge` methods; every string argument may be `null` from JavaScript and is treated as `""`.

| Method | Answer |
|---|---|
| `notesList(query, period, tag)` | JSON array, newest first, at most 200: `{id, title, text, created_at, updated_at, secs, device, tags}`. `period` is `all`, `today` (since local midnight), `week` or `month` (`NoteLogic.periodStart`); a blank `tag` filters nothing. Searches with `NotesStore.search` and source `voice note` (dictation history and meetings are not in it). `{"error": ...}` when the notes cannot be read (the page shows it instead of "No voice notes yet"). |
| `noteEdit(id, title, text, tagsJson)` | The note as it is now, or `{"error": ...}`. `tagsJson` is a JSON array of strings; a blank value keeps the current tags. The tags go through `NoteLogic.cleanTags`. |
| `noteDelete(id)` | `{"ok": true}` or `{"error": ...}`. The page asks for a second tap first, because a deleted note cannot be brought back and the delete is sent to the other devices. |
| `noteToggle()` | `{"ok": true, "action": "start" or "stop"}` or `{"error": ...}`. Finishes the recording when the service is recording; otherwise refuses while the last recording is still being written down, without the microphone permission, with a bad server address or without a key, and otherwise starts a recording with `EXTRA_DEST=note` (through `startForegroundService` with `EXTRA_START` when the service is not running, else `startRecording` on it). What happens to the text is the service's job, not the page's. |
| `noteStatus()` | `{"recording": bool, "busy": bool}` from `DictationService.getState()` (`busy` while it writes the recording down). This is the state of the service as a whole, so a dictation started from the bubble shows as a recording too. The page polls it while a note is recording, every 800 ms, and for up to 4 s after a start request (the service takes a moment); if no recording shows up it says so. |
| `syncStatus()`, `syncNow(callback)`, `syncTest(callback)` | The relay sync (see "Relay sync"). `syncStatus` answers the fields of the Windows `/sync/status`; `syncNow` and `syncTest` answer `callback("{ok, message}")` when they are done. The page shows "Synced ..." or "Not synced. <reason>" under the search box and the test result under Test connection. |

The page follows `windows/ui/index.html`: search, period filter, a card per note, edit sheet, delete, the sync line with a Sync now button (shown when sync is on), and in Settings the block **Sync between devices** with the same wording (on/off, relay address, token, "also share my provider settings and API keys" off by default, this phone's name, Test). The address is checked with `endpointProblem` before it is saved; Test saves the typed address and token first, because `syncTest` reads the saved settings. `index.html` also works in a desktop browser: the preview mock has five sample notes and a fake recording (`previewNotes`), which is how the page was checked at 390, 360 and 320 px wide.

## Relay sync

Optional, off by default (Settings, Sync between devices). It sends the voice notes and the shared profile settings to a relay the user runs (see [14-relay.md](14-relay.md) for the protocol) and is a port of `windows/sync.py`. The notes always work locally; a sync failure is only a message.

**When it runs.** `SyncWorker` owns a thread called `vox-sync` (a `HandlerThread`). It runs one pass when the app comes to the front (`MainActivity.onResume`), when the dictation service starts, after settings are saved (`Bridge.save`), when the user taps Sync now or switches sync on, and every 90 seconds after the last pass while the process lives. A note saved on the phone is not hooked in yet: `SyncWorker.kick(Context)` is the call to make from the note-saved event (the note-saving code is a separate change). There is no background scheduler on purpose: a phone that never opens the app syncs the next time it is opened. Asking while a pass is waiting changes nothing; asking while one is going makes exactly one more start after it. Passes run one at a time on that one thread. Nothing runs when sync is off or the address or token is empty.

**One pass (`SyncEngine.syncOnce`, never throws).**
1. Push: `dirtyNotes(100)` at a time, oldest change first; each note goes out with `PUT /notes/{id}`. `applied` true: `markSynced` (clears `dirty` only when `updated_at` is still the sent one, so a note edited while it was being sent is sent again in the same pass). `applied` false: the relay has a newer version, which `applyRemote` takes (counted as received), or the very same version, which is just marked as sent.
2. Pull: `GET /changes?since=<cursor>&limit=200` page by page, `applyRemote` for each note and delete marker, and the cursor (`sync_meta` key `relay_cursor`) saved after every page, so a failure keeps the pages already done. A relay that says `more` without moving the cursor ends the pass.
3. Profile: see below.

**A note the relay refuses for good** (a 4xx other than 401, 403 and 429; `RelayError.permanent()`) is skipped for this pass and counted; it stays `dirty` and is tried once more in the next pass. It does not stop the other notes, the pull or the profile, and a pass never tries the same version twice. The result then says "1 note could not be sent: The relay answered HTTP 400 (...)." (or "N notes ..."), which the page shows as "Not synced. ...". Everything else that goes wrong stops the pass and keeps what was done: no network or a timeout (the words mention Tailscale), 401 ("The relay refused the token."), 403 ("The relay belongs to another Tailscale user."), 429, 5xx, an answer that is not a relay's. A note saved offline or with the app killed stays `dirty` and is sent once by a later pass (it is in `notes.db`, not in memory). The wording is the wording of `windows/sync.py`; a relay's own error text (`{"error": "text"}`, or the OpenAI shape `{"error": {"message": "text"}}`) is added in brackets, cleaned and cut at 200 characters.

**`RelayClient`** is `HttpURLConnection` with `PlainJson` (no org.json: its classes cannot run in the off-device tests, and this way the client is tested against a real HTTP server). Every request has `Authorization: Bearer <token>` and `X-Vox-Device: <device name>` (printable ASCII only, anything else becomes `?`); 15 s to connect and to answer; redirects are never followed (a `Location` could send the token to another server); the token and the address are never in a message or the log. The body is sent with its `Content-Length` (not streamed: in streaming mode a 401 makes `HttpURLConnection` throw instead of reporting the status). `RelayClient.problem(url, token)` says why an address or token cannot be used (`Endpoint.error`, the address is blank, the address has a `?` or `#`, the token is blank or has a space or a non-ASCII character); a path in the address is allowed (a relay published under one). `RelayClient.check` is Test connection (`GET /health`: "Connected. The relay holds N notes.").

**Profile** (`SyncEngine.syncProfile`, the port of `sync_profile`): read `GET /profile`; if the relay has no profile (version 0) or has not changed since this phone last synced it, the phone's settings win; otherwise each field is merged three ways against the snapshot of the last sync (`ProfileMerge.mergeProfile`: the side that changed wins, the relay wins a clash). The fields that differ are written to the preferences; when the merge differs from the relay it is sent with `PUT /profile` and `If-Match: <version>`, keeping fields that other devices added, and a 412 (someone wrote in between) is read again, up to 3 times ("The profile keeps changing on the relay; it will be tried again later."). Version and snapshot are kept in `sync_meta` (`profile_version`, `profile_snapshot`, JSON). Provider settings and keys travel only while "also share my provider settings and API keys" is on; when it is off they are taken off the relay's document (and any device with it off does that, as on Windows). A damaged snapshot counts as none.

**How a setting is shared (`ProfileMap`).** The phone keeps each setting in SharedPreferences under the relay's field name; Windows keeps them in `config.json`. Every value is cleaned the same way in both directions, so a setting that goes to the relay and comes back is unchanged, and a value that this phone cannot use (wrong type, an address `Endpoint.error` refuses, an unknown style) is ignored as if the relay did not have it.

| Field | Phone (`Prefs`) | Relay (and Windows `config.json`) | Conversion |
|---|---|---|---|
| `user_context` | text | text | as typed, not trimmed; empty is sent as `""` (present), so clearing About you reaches the relay |
| `dictionary`, `people` | text, one entry per line (a `#` line is a comment) | list of text | phone to relay: one entry per line, trimmed, blank and `#` lines dropped; relay to phone: each entry and a line break; an entry with line breaks becomes several; a list with a non-text entry is ignored whole |
| `default_style` | text (`neutral` when unset) | text | one of `formal`, `casual`, `very_casual`, `neutral`, `raw`, otherwise ignored |
| `cleanup` | boolean (`true` when unset) | boolean | as is |
| `language` | text | text | trimmed |
| `provider`, `stt_model`, `llm_model` | text (defaults `groq`, the default models) | text | trimmed (only with keys on) |
| `base_url`, `stt_base_url`, `llm_base_url` | text (the Groq address when `base_url` is unset; `""` for the role addresses) | text | no trailing slash, and only an address `Endpoint.error` accepts; `""` is fine (means the main server) (only with keys on) |
| `api_key`, `stt_api_key`, `llm_api_key` | text | text | trimmed (only with keys on) |
| `llm_reasoning` | none (the phone always behaves as `auto`) | text | not read and not written; what another device put there stays in the document |

Not shared on either side: per-app styles, the hotkey, the microphone, the relay settings, the device name. Comment lines in a dictionary are not shared (the phone's default header is a comment, and the page never shows comments). A Windows server address that means "this PC" (`http://localhost:...`) is taken as it is and will not work on a phone.

**Status for the page.** `Bridge.syncStatus()` answers `{enabled, running, last_run, last_ok, error, pushed, pulled}` (Unix seconds, per process; `enabled` means the switch is on with an address and a token); `last_ok` only moves after a pass without an error. `syncNow(callback)` answers `{ok, message}` after the next pass (at once with "Sync is off or not set up." when sync is off); `syncTest(callback)` runs `RelayClient.check` on its own thread. The log gets only counts and "ok" or "failed" (tag `vox`, debug).

**Not checked yet.** `SyncWorker`, `Prefs.profileStored`/`applyProfile` and the bridge need a device and are only compile-checked; `SyncEngine`, `ProfileMap`, `RelayClient` and `PlainJson` are tested off-device (`SyncEngineTest`, `ProfileMapTest`, `RelayClientTest`, `PlainJsonTest`), and `SyncEngine` over `RelayClient` is also run against the real `relay/relay.py` by `RelayIntegrationTest` (`bash android/run-tests.sh --integration`; see [10-build-test-release.md](10-build-test-release.md)). The device checklist is in [12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md).

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
- **Who calls it:** `VoxAccessibilityService.onResult` (on the mic bubble: SENT when the text was typed, ERROR when it only reached the clipboard) and `onError` (ERROR, on the note bubble when the job in progress is a voice note, otherwise on the mic bubble). A voice note that is saved does not flash SENT: nothing is typed, and the "Note saved" notification says it. `noteJob()` reads the destination of the latest job, so an error that is raised before a job has set it (a refused start: bad server address, no key) can flash the other bubble. A warning that is followed by a result (cleanup fell back to the raw words) calls ERROR and then SENT in the same pass, so the check wins.
- **Not covered:** if "bubble only while typing" hides the bubble when the field loses focus, the flash is not seen (the toast still is). Only compiled (`android/compile-check.sh`), not run on a device.
