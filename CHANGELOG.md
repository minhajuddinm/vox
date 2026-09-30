# Changelog

All notable changes to Vox. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Dates are commit dates. Add a line under **Unreleased** in the same commit as any user-visible change (see [documentation/README.md](documentation/README.md)).

## [Unreleased]

Documentation: added the `documentation/` folder, `AGENTS.md` and this changelog; a checker (`documentation/tools/check_docs.py`) runs in CI. `.venv/` is now git-ignored. Added a documentation-sync routine for the end of every session: `documentation/tools/docs_todo.py` (checklist from `git diff`) and the project skill `.claude/skills/vox-doc-sync/`. Added `documentation/specs/` (design specs written before the code; first: providers and model list) and proposed ADR 0016. CI now also runs the tests and the Android build on pull requests. Providers: any OpenAI-compatible server works for speech and cleanup separately (own address and key per role), Settings shows a model list from the server's `/models` with free-text fallback and a Test button per role, and reasoning parameters are sent only where accepted (new `windows/providers.py`, `Providers.java`, `models` rows in `spec/golden.txt`). Speed: the server connections are opened when the hotkey goes down (Windows) or recording starts (Android) and reused for the upload; Android now checks the address of each role before recording. About you: a text box on the Dictionary page (both apps) whose text is added to every cleanup request as fenced background, capped at 8,000 characters; golden rows `context` and `promptctx` keep Python and Java the same. Live level: one shared meter curve on both platforms (golden `level` rows); the Windows pill draws a scrolling history of the real voice at 30 frames a second, and the Android bubble updates about 25 times a second. Privacy: the public privacy page and the accessibility description now describe any provider, separate speech and cleanup servers, the About you text and the connection warm-up. Lighter Windows build: the unused Pillow image codecs (AVIF, WebP) are left out, 85 MB to 77 MB. Voice notes (Windows): a New voice note button (Voice notes page) and tray item record a note that is written down, tidied up and saved to a searchable list (search, time filter, edit, delete) instead of being pasted. Relay (server only): `relay/relay.py`, an optional self-hosted server for notes and a profile with token auth, a change cursor and delete markers; it runs on Linux (including a Raspberry Pi), macOS and Windows and has a web page to manage and monitor it (status, notes, devices and activity, profile with secrets hidden, backup, compact, purge, new token); Windows can sync voice notes with it (Settings, Sync between devices; works offline, newer edit wins, deletes travel too); Android does not yet. Profile sync (Windows): the About you text, dictionary, people, default style, cleanup switch and language follow you between devices through the relay, merged field by field; provider settings and API keys travel only if you switch on "Also share my provider settings and API keys". Speed (Windows): long recordings are sent to speech-to-text in pieces while you are still talking, so less is left to wait for when you stop (Settings switch; short recordings are unchanged). Fixed: in Settings, the status messages of the model, key, address and relay rows no longer squeeze the setting's name into a narrow column; they now sit under its description.

## Improvement series (PR 1, merged 2026-09-29 as `cb3f679`; not yet released as a tag)

17 commits by Yuvraj Singh with Claude Code, from a full review of both apps.

### Added
- **Server address** setting on Windows and Android: use your own OpenAI-compatible Whisper/LLM server instead of Groq. The API key is optional for such servers. Plain `http://` is accepted only for this device, the local network and Tailscale; other servers need `https://`. README has a self-hosting section. (`8788f43`, `20fc90b`, `0ef4331`, `510ac58`)
- **Windows:** Microphone chooser in Settings; "Vox did not hear anything" now shows how loud the recording was. (`d1a7b86`)
- **Windows:** tray item **Retry last dictation** after a failed send. **Android:** **Retry** button on the Vox notification; the recording is kept until sent. (`3ba28ed`, `a21f00a`)
- **Silence gate** on both platforms: a recording that never gets louder than about -34 dBFS is not uploaded. (`3ba28ed`)
- **Keep dictation history** switch on both platforms. (`82a0118`, `6f3c91a`)
- **Fix a word** in History suggests `wrong => right` dictionary entries on both platforms. (`2ed4d97`)
- Spoken **"new line" / "new paragraph"** become line breaks when AI cleanup did not run (raw style, cleanup off, failure); Vox says when cleanup failed instead of silently using the raw words. (`924438e`)
- Shared test file `spec/golden.txt` and parity tests in Python and Java; pytest and Java helper tests run in CI. (`a1b2db3`, `007c699`, `578ed8a`, `859dc9c`)

### Changed
- **Privacy (Windows):** only the app's exe name is sent to the model and saved; the window title is no longer read. The API key in `config.json` is protected with the Windows login (DPAPI). `engine.json` is deleted when Vox quits. (`82a0118`)
- **Privacy (Android):** narrower accessibility configuration (dropped two unused flags); WebView file access off. (`6f3c91a`)
- **Android:** text is typed only into the app the dictation started in (otherwise copied to the clipboard); Vox never starts in, or types into, password fields. (`a21f00a`)
- **Windows robustness:** HTTP 500/502/503/504 are retried; stop/cancel are atomic; meeting start/stop are serialised and the final pass swaps entries under a lock; quitting waits for meeting notes to finish saving; Ctrl+C in the terminal quits cleanly. (`3ba28ed`, `70697e5`)
- Style names in the cleanup prompt are case-insensitive on both platforms; Android replacements treat `_` as part of a word and match Unicode case-insensitively; Android dictionary terms are de-duplicated like Python. (`a1b2db3`)
- Internal API: `GroqError` is now `ApiError`; the request helpers in `windows/vox_core.py` are public (`post_with_retry`, `auth_headers`, `check_response`). (`8788f43`)
- CI: dependencies pinned, Actions pinned by commit SHA, workflow permissions read-only except the release job, keystore password from an optional secret. (`e3c6791`)

### Fixed
- **Android:** the bubble no longer sticks after a recorder error; cancelling and starting again can no longer insert the old result or overwrite the new state. (`a21f00a`)
- **Android:** a negative `AudioRecord.read` no longer spins the recording loop. (`a21f00a`)
- **Windows:** two simultaneous stop requests could process one recording twice. (`3ba28ed`)

### Removed
- Unused `dot()` helper in `windows/engine.py`. (`e3c6791`)

## [1.2.0] - 2026-09-27 (tag `v1.2.0`, `32a5bfc`)

- Meeting notes: live "ask" during a meeting, an accurate final pass with Whisper large-v3, and detailed summaries. (`32a5bfc`)
- Later the same day, without a tag: new logo and further meeting-notes upgrade (`9aa4d28`, the base of the improvement series).

## [1.1.0] - 2026-09-27 (tag `v1.1.0`, `3b59c71`)

- Android app added (accessibility bubble, dictation service, screens), README for both platforms.

## [1.0.0] - 2026-09-27 (tag `v1.0.0`, `3dbc3c9`)

- First release: Windows app (hold-to-talk dictation, cleanup, dictionary, per-app styles, history window, meeting notes beta, Google Calendar and iCal link), installer and CI build, landing page and privacy policy.

Note: `android/AndroidManifest.xml` at the base commit already said version 1.3 (`versionCode` 4); no `v1.3` tag exists.
