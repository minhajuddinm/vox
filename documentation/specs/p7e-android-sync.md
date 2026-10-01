# Spec P7e: Android relay sync and profile merge

Status: Implemented in the Android notes group (group B of the v2 part 2 plan), **not run on a phone**. Date: 2026-09-30. Decision record: [0026](../decisions/0026-android-notes-and-sync-are-ports-with-shared-golden-rows.md). Ports [p7c](p7c-windows-sync-client.md) and [p7d](p7d-profile-sync.md) to Android; the notes it syncs are from [p6](p6-android-note-mode.md). Relay protocol: [14-relay.md](../14-relay.md). Behaviour as built: [05-android-app.md](../05-android-app.md), "Relay sync".

## Goal
Voice notes and the shared profile settings made on the phone reach the relay and the PC, and the PC's reach the phone, with the same rules as the Windows client, and with the notes never depending on the relay being reachable.

## Design

### Pieces (all under `android/src/com/minhaj/vox/`)
- `ProfileMerge` (task 11): three-way merge per field, the port of `sync.merge3` and `sync.merge_profile`. The side that changed since the snapshot wins; both changed differently: the relay's value wins; an absent result drops the field. Shared with Windows by golden rows `merge3` and `profilefields`.
- `SyncEngine` (task 12, pure): `syncOnce()` is the port of `sync_once` and `sync_profile`. It never throws (a `RelayError` gives its message, any other `RuntimeException` gives "Sync failed: <ClassName>" and nothing else). Push in batches of 100 (`dirtyNotes`), `markSynced` or `applyRemote`; pull `GET /changes` pages of 200 and save the cursor after each page; profile with `If-Match` and up to three retries on 412. Written against two interfaces, `RelayApi` and `SyncStore`, plus `SyncConfig` for the settings.
- `RelayClient`: `HttpURLConnection` and `PlainJson`. Bearer token and `X-Vox-Device` on every request, 15 s timeouts, redirects never followed, the body sent with a length, answers capped at 32 MB, messages worded like `windows/sync.py` plus the relay's own error text (cut at 200 characters). `problem(url, token)` checks the address and token; `check` is the Test connection button.
- `PlainJson`: a strict JSON reader and writer (depth limit 64). It replaced `org.json`: the engine must store the profile snapshot as JSON, and `org.json` from `android.jar` throws in the off-device tests, so with `PlainJson` the whole client is tested over real HTTP.
- `ProfileMap`: phone settings to relay fields and back, one clean-up per field kind in both directions (table in [05](../05-android-app.md)); the phone keeps the dictionary and people as text, one entry per line, and the relay holds lists.
- `SyncWorker`: a `HandlerThread` called `vox-sync`. `kick(Context)` is cheap and safe from any thread; one run at a time; a request during a run queues exactly one more; a tick every 90 s after the last pass while sync is on. Runs on app resume, service start, settings save, Sync now, switching sync on, and (task 12b) a saved note: `SyncWorker.start` registers one `NoteEvents` listener per process, and every `kick` calls `start` first.
- Bridge: `syncStatus`, `syncNow(callback)`, `syncTest(callback)`; the Notes page shows the status and `voxRefresh` after a pass that changed this phone's settings.
- Settings (task 10): Sync between devices (switch, address, token, phone name, "also share my provider settings and API keys"); keys in [07-config-and-data.md](../07-config-and-data.md).

### Rules shared with Windows
A note the relay refuses for good (a 4xx other than 401, 403 and 429; golden kind `permanent`) is skipped for the run, the other notes, the pull and the profile still go, and it is reported each pass until edited or deleted. 401, 403, 429, 5xx and network failures stop the run. The Windows client had the one-bad-note-blocks-everything flaw and was fixed first (`windows/sync.py`, `SyncError.permanent`, four tests). Review follow-ups (task F2): "received" is kept across profile retries on both sides, and the push limit is the batch plus the notes parked in this run.

### Tests
`PlainJsonTest` 88 checks, `ProfileMapTest` 85, `ProfileMergeTest` 40, `RelayClientTest` 228 (real `com.sun.net.httpserver`), `SyncEngineTest` 317 (an in-memory store and a fake relay that follows `relay.py`), `ParityTest` 184 golden cases. `RelayIntegrationTest` (task 13) runs two or three in-memory phones through the real `relay/relay.py` as a child process: add, edit, conflicting edits, delete markers, a bad id that does not block good notes, a profile clash, a real 412, keys staying off the relay, paging over 201 notes, a wrong token. It runs only with `bash android/run-tests.sh --integration`, which CI does in the `android` job.

## Deviations from the plan
- `RelayClient` uses `PlainJson`, not `org.json`. The integration test was first wired to download a pinned `org.json` jar; task F2 removed that download, the pin and the classpath entry from `run-tests.sh`, `build.yml` and the docs, because nothing needs the jar.
- The relay does not answer 400 for text over 100,000 characters (the plan assumed it did). `relay.py` cuts it silently (`_text`, `tests/test_relay.py::test_the_id_in_the_path_wins_and_text_is_capped`), so a longer note reaches other devices shortened while the phone keeps all of it. The real permanent refusals are 400 (bad id, non-numeric times, `tags` not a list, not JSON), 413 (body over 1 MB) and 411.
- `SyncStore` is the five calls the sync makes (`dirtyNotes`, `markSynced`, `applyRemote`, `getMeta`, `setMeta`), not four.
- The note-saved trigger went in as a `NoteEvents` listener registered by `SyncWorker.start`, not as a call inside `DictationService.send`.
- `RelayClient.problem` rejects `?` and `#` in the address and a blank or non-ASCII token; `windows/sync.problem` was left alone. A relay's own error text is added in brackets on the phone only.

## Not in this step
A background scheduler (`WorkManager` or an alarm), a boot receiver, syncing dictation history, meetings, per-app styles or audio, encrypting the relay token and keys on the phone, a fix for the keys ping-pong described below.

## Known limits
- The phone syncs only while the app's process is alive: the first kick starts the 90 s tick, and it runs for as long as the process does (the accessibility service shares the process, so it may go on after the app is closed). When the system ends the process nothing syncs until the next kick. There is no boot receiver.
- The saved-note trigger exists only after the first kick in the process.
- The relay token and the API keys are stored unencrypted in SharedPreferences.
- A note the relay refuses for good shows "Not synced. 1 note could not be sent: ..." on every pass until it is edited or deleted (a delete marker for an invalid id is refused too).
- First sync against a relay that already has a profile: there is no snapshot, so a setting both sides have comes from the relay (the same on Windows, [0023](../decisions/0023-profile-sync-three-way-merge.md)).
- Keys on one device and off on another ping-pong: the device with the switch off removes them from the relay each pass and the other puts them back. Windows behaves the same.
- Comment lines in the dictionary are not shared; a Windows address like `http://localhost:11434` arrives as it is and does not work on a phone; `llm_reasoning` is not shared with the phone.
- Two writes to one note in the same millisecond get the same `updated_at` on Android; not reachable through the UI.
- `RelayClientTest` needs `com.sun.net.httpserver` (in a JDK 17, not in a bare JRE). The CI step for `--integration` has never run on Linux; the local `javatest.cmd` wrapper does not pass the flag.

## Done when
Tests pass (`bash android/run-tests.sh`, and with `--integration`), CI is green, and the device checklist below passes with a relay reachable over Tailscale. **The checklist has not been run.**

## Device checklist (nothing here has run on a phone, a Raspberry Pi or a Tailscale link)
1. Settings, Sync between devices: address and token, Test connection says "Connected. The relay holds N notes."; a wrong token says "The relay refused the token."; the relay stopped or Tailscale off says "Cannot reach the relay (is Tailscale running?)...". `http://example.com` is refused; `http://100.x.y.z:8765` and an https address are accepted; the token is masked; clearing the phone name falls back to the model; a 70-character name is cut at 60.
2. Switch sync on: Notes shows "Synced just now"; a note made on the PC appears within a minute or after Sync now; the phone's notes show on the relay's management page.
3. Airplane mode, make or edit a note, leave the app, network back, open the app: the note is on the relay once, no duplicate.
4. Edit or delete a note on the PC, Sync now on the phone: it follows.
5. About you, dictionary and people: PC to phone (the Dictionary page refreshes by itself after the pass) and phone to PC. `api_key` is absent from the relay's profile while "also share my provider settings and API keys" is off, present when on, removed when off again.
6. `adb logcat -s vox` during all of this: counts and ok or failed only, no token, no address, no note text. After "Clear app data", `relay_token` is gone.
7. App open for 3 minutes, a change made on the PC: it arrives without a tap (the 90 s tick).
8. Save a voice note with sync on: it reaches the relay within seconds (log "sync: 1 sent").
9. Arm Delete on note A, then on note B within 3 s: both reset after about 3 s.
10. Type in About you, then change a setting on the PC and wait for a sync: the typed text stays; the page redraws after leaving the box. Deleting a dictionary word or person removes that entry even after a refresh.
11. Turn off relay sync and toggle "share keys": no sync run starts.
12. Start a bubble dictation and open Notes: the button shows busy, and Finish note does nothing to the dictation.

## Not verified
Everything that needs Android or a real network: `SyncWorker` and its timing, `Prefs.profileStored` and `applyProfile`, the bridge methods, the page, and `HttpURLConnection` on Android. The `--integration` step in CI. The page edits of task F2 were only syntax-checked (`node --check`); no browser or `tests/test_ui_static.py` run (that file does not exist at this base). Engine, client and the real relay were run together on a Windows PC (`RelayIntegrationTest`, 116 checks, task F2), and the Android classes were run against the real `relay.py` and `windows/sync.py` by scratch programs in task 12; none of that was on a phone.
