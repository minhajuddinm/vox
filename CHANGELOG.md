# Changelog

All notable changes to Vox. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Dates are commit dates. Add a line under **Unreleased** in the same commit as any user-visible change (see [documentation/README.md](documentation/README.md)).

## [Unreleased]

Documentation: added the `documentation/` folder, `AGENTS.md` and this changelog; a checker (`documentation/tools/check_docs.py`) runs in CI. `.venv/` is now git-ignored. Added a documentation-sync routine for the end of every session: `documentation/tools/docs_todo.py` (checklist from `git diff`) and the project skill `.claude/skills/vox-doc-sync/`. Added `documentation/specs/` (design specs written before the code; first: providers and model list) and proposed ADR 0016. CI now also runs the tests and the Android build on pull requests. Providers: any OpenAI-compatible server works for speech and cleanup separately (own address and key per role), Settings shows a model list from the server's `/models` with free-text fallback and a Test button per role, and reasoning parameters are sent only where accepted (new `windows/providers.py`, `Providers.java`, `models` rows in `spec/golden.txt`). Speed: the server connections are opened when the hotkey goes down (Windows) or recording starts (Android) and reused for the upload; Android now checks the address of each role before recording. About you: a text box on the Dictionary page (both apps) whose text is added to every cleanup request as fenced background, capped at 8,000 characters; golden rows `context` and `promptctx` keep Python and Java the same. Live level: one shared meter curve on both platforms (golden `level` rows); the Windows pill draws a scrolling history of the real voice at 30 frames a second, and the Android bubble updates about 25 times a second. Privacy: the public privacy page and the accessibility description now describe any provider, separate speech and cleanup servers, the About you text and the connection warm-up. Lighter Windows build: the unused Pillow image codecs (AVIF, WebP) are left out, 85 MB to 77 MB. Voice notes (Windows): a New voice note button (Voice notes page) and tray item record a note that is written down, tidied up and saved to a searchable list (search, time filter, edit, delete) instead of being pasted. Relay (server only): `relay/relay.py`, an optional self-hosted server for notes and a profile with token auth, a change cursor and delete markers; it runs on Linux (including a Raspberry Pi), macOS and Windows and has a web page to manage and monitor it (status, notes, devices and activity, profile with secrets hidden, backup, compact, purge, new token); Windows can sync voice notes with it (Settings, Sync between devices; works offline, newer edit wins, deletes travel too); Android does too (see Added below). Profile sync (Windows): the About you text, dictionary, people, default style, cleanup switch and language follow you between devices through the relay, merged field by field; provider settings and API keys travel only if you switch on "Also share my provider settings and API keys". Speed (Windows): long recordings are sent to speech-to-text in pieces while you are still talking, so less is left to wait for when you stop (Settings switch; short recordings are unchanged). Fixed: in Settings, the status messages of the model, key, address and relay rows no longer squeeze the setting's name into a narrow column; they now sit under its description.

### Added
- **Android voice notes** (not run on a phone yet): record a note without a text box and it is cleaned up and saved to a searchable list on the phone, not typed. Start it from a second, blue note bubble (Settings key `note_bubble`, drawn by the accessibility service, shows without a focused text field), from an ongoing "Record note" notification (`note_notification`) or from a Quick Settings tile "Voice note"; stop from the same place or the "Stop" action of the Vox notification. A "Note saved: title" notification follows. The Notes page lists, searches, edits and deletes notes. The two switches have no Settings row yet.
- **Android relay sync** (not run on a phone yet): Settings, Sync between devices sends the phone's voice notes and the shared profile settings (About you, dictionary, people, and by choice provider settings and API keys) to your relay and takes the PC's, with the same rules as Windows. It runs when the app opens, when the dictation service starts, when settings are saved, when a note is saved and every 90 seconds while the app's process lives; there is no background scheduler. Test connection and Sync now are in Settings.
- **Skip AI cleanup for short phrases** (both apps, Settings): phrases shorter than N words are typed as spoken, which saves a round trip to the cleanup model. The default is 3 words (what earlier versions always did); the box accepts 1 to 20, and 1 cleans up everything. Python and Java use the same rule (`gate` rows in `spec/golden.txt`). The setting does not sync between devices.
- **Run the relay from the Windows app:** the tray item "Run relay on this PC" (and `Vox.exe --relay`) starts the relay as a hidden child process of Vox, stops it when you untick the item or quit, and cannot outlive Vox. The first start shows the `tailscale serve --bg 8765` command that makes it reachable from your phone. New settings `relay_run` and `relay_port` (default 8765). Tested from source only: the built exe was not run.
- **The relay as the AI server (both apps):** a new Settings switch, "Use my relay as the AI server", sends dictation, model lists, warm-up and the Test buttons to the relay (`/proxy/stt` and `/proxy/llm`) with the relay token, so the AI provider key can live on the relay only. The relay's management page has a new tab, "AI server (proxy)", with an address and a write-only key for speech and for cleanup. Audio and text pass through the relay while this is on, and the relay keeps the keys in plain text in `relay.json` (see `documentation/09-security-privacy.md`). Tested against stand-in servers only: not yet tried with a real speech or chat server, a phone, a Raspberry Pi or Tailscale.
- **Hints through the relay:** a 401 or 403 while the relay is the AI server says to check the relay token and the AI server key set on the relay page (the relay passes an AI server's 401 and 403 on unchanged); a 404 names the relay address or the relay's AI server settings.
- **The pill and the bubble show how a dictation ended:** a green check for 0.7 s when the text landed (or a voice note was saved) and a red ! for 1.8 s when it did not (copied only, missing key, nothing heard, any send error). The tray and toast messages are unchanged. Not seen on a real screen or phone yet.
- **Home status card** (both apps): provider (or your relay), speech and cleanup model, the last connection Test, sync state, voice note count and the last dictation ("Not sent" on Android after a failed one). It replaces the word counts and "time saved".
- **Windows Styles:** pick the app for a style from a list of recent apps, or "Other..." to type an exe name.
- **Android:** switches for the note bubble and the note notification in Settings; a hint for the "Voice note" Quick Settings tile.

### Changed
- **A cleanup that drops your words is rejected (both apps):** the AI cleanup answer must keep the words you said (in Light strength 97% of them, in Standard 85% after fillers and repeats are ignored; spoken numbers, currency words and spoken commands are allowed for); a summary or a rewrite that loses words now falls back to your words as spoken, as a failed cleanup does. The setting `cleanup_strength` (Light or Standard) has no Settings row yet; until it does, the guard runs in Standard strength.
- **Android start is faster:** the dictation service starts recording as soon as it is in the foreground; the fixed 400 ms and 350 ms waits are gone. A cold bubble tap in a password field now refuses, like a warm tap. The speed gain has not been measured on a phone (`adb logcat -s vox` shows `tap->recording ms=N`).
- For contributors: the Java class `GroqClient` is now `ApiClient` (no behaviour change); the Java tests run through one script, `android/run-tests.sh`, in CI and on a laptop with a local JDK; `android/compile-check.sh` type-checks every Android source against `android.jar` without the Android build tools. CI fix: the two relay-proxy tests that use `requests` skip in the stdlib-only `relay` job, and the meeting-notes relay-hint test runs without `numpy` (stand-in module), so the `relay` and `tests` jobs pass.
- **Settings are regrouped** on both apps: AI providers, Voice & audio, Privacy, System, each with a plain line on what leaves the device. First run on Windows asks you to choose an AI provider (Groq, OpenAI, OpenRouter, Together, Mistral, or a local server) instead of asking for a Groq key; Android's setup list is three steps (microphone, accessibility, AI provider).
- **Paste is safer (Windows):** if you switch to another window while Vox is working, the text is copied and not pasted, with the notice "Copied; the window changed". The old clipboard is put back only if it still holds the dictation. **Behaviour change:** "Keep dictation on the clipboard" is now off unless you turned it on; before, a config that never set it behaved as on.
- **Privacy text rewritten** to match the code (privacy page, home page, README, the Android accessibility description, the in-app lines): audio goes to your speech server; text, About you, app name and style go to your cleanup server; the dictionary and people also go to the speech server as a spelling hint; the relay gets notes, the profile and, only if you switch it on, keys or your dictations; there is no analytics and no server of Vox's own.
- For contributors: both HTML pages share their palette, component styles and helper functions from `ui-shared/`, generated into the pages by `python tools/sync_ui.py` (a test fails if a generated block is edited by hand); static tests check every id and bridge call of both pages; `Engine.paste` moved to `windows/paste.py`.

### Fixed
- **Windows sync:** one voice note that the relay refuses for good (for example a bad id) no longer stops every other note, the pull and the profile sync; it is skipped, counted and reported as "N note(s) could not be sent". Network failures and 401, 403, 429 and 5xx still stop the run. A "received" profile result is no longer lost when the write is retried.
- **Android:** the Settings footer line (version and where audio goes) was written into the About-you box and never shown, because two elements shared one id. It now shows under Clear history, and the About-you box keeps only what you typed.
- **Final review fixes:** the Windows meeting recorder no longer sends a speech piece again after a read timeout when the relay is the AI server; on Android a stale Retry button sends nothing, a failed Retry moves that recording behind the others and parks it after three tries (the notification says how many are stuck); keys that this device put on the relay are still taken off when switched off after an upgrade or an address rewrite; the relay README states the right cleanup limit (240 s) and that the apps use the proxy routes.

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
