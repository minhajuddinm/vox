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
| Meeting hallucination list differs from dictation's | `meeting.HALLUCINATIONS` is wider than `vox_core.SILENCE`; both exist separately. |
| `Api.meeting_catchup` and `/meeting/catchup` exist but the window never calls them | `.catchup` CSS in `ui/index.html` is also unused. |
| Windows history is unbounded | `history.jsonl` only grows; the window shows the newest 300. Android keeps 500. |
| New screens and meters are unchecked on devices | The providers settings, the About you box, the recording pill history and the phone bubble level were verified only by tests, script checks and CI; nobody has looked at them on a Windows screen or a phone yet. |

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

1. Lighter builds (P8a): replace `numpy`, `pyperclip` and `psutil` in dictation with the standard library and ctypes, add PyInstaller excludes, create the WebView window on demand; on Android drop the manual "start service" step. Measure size and idle memory before and after.
2. Notes store with search and filters (P5): SQLite (FTS5 with a `LIKE` fallback) unifying dictations, meetings and voice notes.
3. Android note mode (P6): a persistent bubble without a text box, a notification action and a Quick Settings tile. Test on Android 14 and 15 first: starting the microphone service from an overlay tap is not guaranteed.
4. Relay over Tailscale (P7): profile and notes sync; keys served to devices or proxied by the relay.
5. More speed (P2b, P2c): shorten the Android start delay (400 ms + 350 ms, needs a device test), skip cleanup for short phrases, stream the cleanup, chunk speech-to-text for long dictation. The connection warm-up saving is an estimate, not measured.
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
16. Rename the Java `GroqClient` to `ApiClient`; check the model dropdown, About you box and meters on real devices.

Not planned: Android meeting notes, iOS, on-device speech recognition.
