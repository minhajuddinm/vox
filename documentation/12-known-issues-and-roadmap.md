# 12. Known issues and roadmap

State of the code, honestly. Update this page when you fix or discover something. Items are grouped by kind, roughly most important first.

## Known issues

### Behaviour and correctness

| Issue | Detail |
|---|---|
| `ACTION_SET_TEXT` rewrites the whole field (Android) | `VoxAccessibilityService.insertText` sets the complete new text, so rich text formatting can be lost and typing during insertion can race. The clipboard-paste path is only a fallback. |
| Microphone setting is ignored by meeting notes (Windows) | `meeting.py` records the default microphone through `soundcard`; only dictation uses `input_device`. |
| Failed Windows dictation audio lives in memory only | `Engine.pending` is lost when Vox quits (Android keeps a file until sent or cancelled). |
| Spoken "new line" false positives | When cleanup did not run, a phrase like "a new line of code" becomes a line break. This is a deliberate trade-off. |
| Private-host rule differs slightly between platforms | Python's `ipaddress.is_private` also accepts a few reserved ranges (for example documentation and benchmarking blocks) that the Java `Endpoint.isPrivateHost` rejects. `spec/golden.txt` does not cover address rules. |
| Note rules differ slightly between platforms | `notes._tags` (Windows) also removes double quotes from a tag and has no limit, while `NoteLogic.cleanTags` (Android) keeps quotes and keeps at most 20 tags (the relay's limit); tag clean-up has no golden rows. "Word" and "space" in `auto_title` and `fts_query` follow each runtime's Unicode tables (Python 3.13: Unicode 15.1; Android's Java: whatever the phone's version has), so letters added in a newer Unicode version can count as words on one side only. The whitespace set is written out in `NoteLogic` and matches Python's `str.isspace()` exactly. |
| Android note mode is unchecked on a phone | The note bubble, the "Record note" notification, the quick settings tile and the save path in `DictationService` only compile and have read-through review; the microphone service starting from each entry point on Android 14 and 15, the tile and notification actions, the Stop button, and a note landing in the store have not been tried on a device. The ongoing notification is not restored after a reboot until the app or the accessibility service starts (no boot receiver), and on Android 14 a user can swipe it away. |
| Android notes database is unchecked on a phone | `NotesStore` needs Android's SQLite, which the local Java tests cannot run, so it is only compile-checked and read against `windows/notes.py`. Whether the phone's SQLite has FTS5 (otherwise search falls back to `LIKE`) and that both search paths, edit, delete and the sync calls work has to be tried on a device. |
| Meeting hallucination list differs from dictation's | `meeting.HALLUCINATIONS` is wider than `vox_core.SILENCE`; both exist separately. |
| `Api.meeting_catchup` and `/meeting/catchup` exist but the window never calls them | `.catchup` CSS in `ui/index.html` is also unused. |
| Windows history is unbounded | `history.jsonl` only grows; the window shows the newest 300. Android keeps 500. |
| New screens are unchecked on a phone and a Pi | The Windows screens were looked at by Yuvraj on his laptop (2026-09-30, "looks good", details not recorded). The Android screens (providers, About you, meters, the "skip cleanup below N words" box) and everything that needs a phone, a Raspberry Pi or Tailscale have only been verified by tests, script checks, a browser preview and CI. The Android code changed by the `ApiClient` rename and the cleanup threshold (`DictationService`, `MainActivity`) is type-checked by `android/compile-check.sh` and the pure helpers are unit tested, but it has never run on a device; the new Settings box on Windows has not been seen in a window. |
| Voice notes exist on Windows only and are a separate list | The Voice notes page lists notes only; dictation history and meetings are not in it, and Android can record notes (note mode) but has no page that lists them yet. `Voice notes` was verified by tests (store and engine logic) but not seen on a screen. |
| The relay is only used by the Windows app, is not installed as a service, and is untested on a real Pi | The PC can run it from Vox (`Vox.exe --relay`, tray item "Run relay on this PC"), but only while Vox runs. Only the Windows app syncs (voice notes and the profile, keys only if switched on); Android, dictation history and meetings do not. The systemd unit, the Raspberry Pi steps and `tailscale serve` from a phone have not been tried (CI runs the relay tests on arm64 Linux). The `/admin` endpoints share the data token. Notes sync is last-writer-wins by device clocks; the profile merges field by field and the relay's value wins a clash. |
| `Vox.exe --relay` has never been run as a built exe | The relay from the tray (`relay_host.py`, `vox_app.py --relay`) was tested from source on Windows: a real child process, a real relay answering `/health`, stop, and the child dying with its parent. The PyInstaller flags that bundle `relay.py` (`--paths ../relay --hidden-import relay`) are in `build_app.bat` and the workflow and a test checks the text, but no frozen build with them has been made or started, so whether the exe finds `relay` is unproven. |
| Two relays can share one port on Windows | `relay.py` inherits `allow_reuse_address = 1` from `http.server.HTTPServer`, so it binds with `SO_REUSEADDR`; on Windows a second relay then binds a taken port without an error (checked 2026-09-30). The app checks the port itself (`relay_host.port_busy`) before it starts its relay; `python relay.py` has no such check, so starting it by hand after Vox's relay is running gives two servers on one port. |
| The tray item "Run relay on this PC" shows the saved setting, not the live state | The tick follows `relay_run` in `config.json`. It stays ticked when the relay did not start (port in use, launcher error) or ended by itself. When `config.json` is edited while Vox runs, the tick follows the file (the engine re-reads it) but nothing starts or stops until the next Vox start or tray click. The only signs of the real state are the tray notifications and `vox.log`. |
| `cleanup_min_words` does not sync between devices | Profile sync shares only `PROFILE_FIELDS` in `windows/sync.py` (About you, dictionary, people, default style, `cleanup`, language); the threshold is not one of them, so each device keeps its own value (Android keeps its own `cleanup_min_words` preference and has no relay client at all). |

### Security and privacy (see [09-security-privacy.md](09-security-privacy.md))

- Android API key and history are not encrypted inside the app's private storage.
- Windows history, meeting data and `google_token.json` are plain files.
- `keep_clipboard` defaults to true (dictated text stays on the clipboard).

### Structure and quality

- `windows/engine.py` mixes hotkey, audio, tray, paste, control server and calendar in one class; `windows/meeting.py` mixes recording, speech, prompts, storage and search. Several flags (`Engine.busy`, `Engine.pending`, `Meeting.active`) are plain attributes read from other threads.
- `Meeting.attribute_speakers` updates `entries` without holding `lock`.
- `Engine.quit` waits at most 180 s for meeting notes, then exits anyway.
- `windows/ui/index.html` is one large script with global state; `android/assets/index.html` likewise.
- Almost no automated coverage outside the pure helpers (see [10-build-test-release.md](10-build-test-release.md)).
- The cleanup rules are implemented twice; `spec/golden.txt` guards them, but new rules must be added on both sides by hand.
- `Prefs` is created in many places; `DictationService.listener` is a static reference.

### Build and release

- CI runs the tests and the Android build on pull requests; the Windows build runs only on tags or by hand.
- Without the `ANDROID_KEYSTORE_B64` secret, every CI APK has a new signing key and cannot update an installed app.
- `windows/build_app.bat` installs an unpinned PyInstaller (CI pins one) and its failure text tells the user to "send it to Claude" (original wording).
- Uploaded audio is uncompressed WAV (about 32 KB per second of speech).

## Roadmap (not started unless marked)

Ordered by how much they would help (the v2 plan; P1 to P4 are done):

1. Lighter builds, rest of P8a (the Pillow codec excludes are done: 85 MB to 77 MB, see [10-build-test-release.md](10-build-test-release.md)): numpy is 28 MB and needed by the meeting audio capture; Pillow's `_imagingft` (2 MB) and Tcl's `tzdata` (3 MB) could go; create the WebView window on demand; on Android drop the manual "start service" step (needs a device test; the APK is already 56 KB). Idle memory has not been measured.
2. Notes, rest of P5: the Windows voice notes store and page are done; still to do are Android notes, one list for dictations and meetings, tags in the UI, and a hotkey for notes.
3. Android note mode (P6): built (a persistent bubble without a text box, a notification action and a Quick Settings tile) but not tried on a device; still to do are the Settings rows for the two switches and a screen that lists the notes. Test on Android 14 and 15 first: starting the microphone service from an overlay tap is not guaranteed.
4. Relay, rest of P7: the server, Windows voice-note sync and Windows profile sync are done (see [14-relay.md](14-relay.md)); still to do are the Android client (outbox, profile), dictation history and meetings, per-app styles per platform, audio blobs, proxy mode, and trying it over real Tailscale.
5. More speed: the Android start delay is shortened (the 400 ms + 350 ms fixed waits are replaced by the service recording as soon as it is in the foreground) but not yet measured or checked on Android 14 and 15 (read `tap->recording ms` with `adb logcat -s vox`), streaming pieces on Android. The connection warm-up, the Windows piece-by-piece transcription of long recordings ([decisions/0024-stream-long-dictations-in-pieces.md](decisions/0024-stream-long-dictations-in-pieces.md)) and the skipped cleanup for short phrases (`cleanup_min_words`) are built but their savings are estimates, not measured on a real network.
6. Design and privacy refresh (P8b): regrouped settings, shorter onboarding, error states on the pill, pickers instead of typed app names.
7. Cleanup quality: fuzzy dictionary matching before the LLM, an `EMPTY` sentinel and stricter output checks, per-app modes.
8. Set the signing-key secrets so phone updates work without uninstalling.
9. Split `meeting.py` (store, prompts, recording/STT) and `engine.py` (hotkey, recorder, paste, control server, tray) behind tests first.
10. Encrypt the Android API key with the Android Keystore; restrict `google_token.json` permissions.
11. Tests for `engine.py` state logic, `meeting.py` text helpers and `Prefs`; a Windows UI smoke test with a stubbed `pywebview.api`.
12. Compress uploads (FLAC or Opus) when the server accepts it.
13. Make the Microphone setting apply to meeting notes.
14. Put address rules (`is_private_host`) into `spec/golden.txt` so both platforms agree exactly.
15. Merge the two hallucination lists.
16. Check the model dropdown, About you box and meters on real devices.

Not planned: Android meeting notes, iOS, on-device speech recognition.
