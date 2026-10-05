# Changelog

All notable changes to Vox. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Dates are commit dates. Add a line under **Unreleased** in the same commit as any user-visible change (see [documentation/README.md](documentation/README.md)).

## [Unreleased]

### Fixed
From the full v2 review of 2026-10-05 (summary in documentation/12-known-issues-and-roadmap.md). Tested by unit, golden and integration tests only; nothing below has run on a real device.
- **Both apps:** a dictation with no usable text (a lone "Thank you.") is announced, not dropped. Learn from my corrections no longer learns grammar edits (complete → completed, है → हैं) but still learns misheard names, including two-word ones. Replacements, snippets and spellings no longer match inside Hindi words or inside emails, URLs, paths and code. The cleanup check accepts dates with a year, money with cents and a spoken "oh". Everyday sentences no longer become lists; real spoken lists still do. Profile sync merges dictionary, people and snippets item by item.
- **Windows dictation:** failed recordings kept for Retry survive later dictations (up to 5, tray shows the count). Learn from my corrections no longer freezes Vox, and a keyboard hook Windows dropped is reinstalled. No line breaks are typed into terminals. The next dictation can start as soon as text is pasted. AltGr typing, Vox's own paste keys and other Ctrl+Shift shortcuts no longer trigger the shortcut (program-sent keys such as PowerToys remaps still do). Long recordings keep text already transcribed after a failed piece and wait out rate limits. Code mode converts real code but leaves prose in terminals alone. Cleanup: temperature 0, a cut-off answer falls back to your words, a slow cleanup gives up after 20-60 s. Quit waits for a dictation still being sent. Stuck-busy after a slow microphone stop is gone.
- **Windows data:** config.json has one writer at a time (no more reset to defaults; a failed save says so). Note edits survive a PC clock that is behind. Sync recovers from a wiped relay. With key sharing on, keys are no longer stored in plain text in notes.db; deleted note text is erased. Meetings and Improve my cleanup use your provider's models. Calendar e-mail addresses are never sent; the iCal address is fetched over https only. Deleting a meeting, a note or all history asks first. Copy buttons keep dictations out of the cloud clipboard.
- **Android:** voice notes run 18 minutes (warning 30 s before), long notes upload safely without running out of memory; a send error no longer crashes the app; a successful send no longer deletes an older kept recording; the clipboard fallback cleans up and marks text sensitive; sync recovers from a wiped relay; typed text that looks like a placeholder is kept; keys and history are excluded from phone-to-phone transfer.
- **Relay and security:** the apps send the relay token only after the relay proves it holds it (re-proved every 10 s and after errors). Plain http to a name is allowed only when it resolves to this PC, the LAN or Tailscale. A device token can no longer change the relay's token or AI servers. NaN profiles and huge cursors are refused. Connection cap and header timeout. No redirects followed with audio, text or keys.
- **Builds:** only tag builds use the release signing key (a missing `ANDROID_KEYSTORE_PASS` warns); dependencies are locked; checksums cover every release file; only tags on main publish.
- Windows: a dictation that yields no usable text now shows the error mark and a balloon instead of vanishing silently (PR 62).

### Changed
- README, project site, release notes and GitHub issues condensed. The detailed 2.0 entries previously in this file are in git history (`aeb9be3`).

## [2.0.0] - 2026-10-05 (tag `v2.0.0`)

Everything below is covered by unit and parity tests and CI builds; unless stated, not yet run on a real device.

### Added
- **Providers:** any OpenAI-compatible endpoint, separate speech and cleanup servers and keys, model list and Test button, presets (Groq, OpenAI, OpenRouter, Together, Mistral, Ollama, LM Studio, Speaches). Plain `http://` only for loopback, LAN and Tailscale.
- **Cleanup:** word-keeping prompt, Light (default) / Standard strength, fidelity guard with fallback to raw words, **Use raw** in History, skip cleanup under N words, fuzzy dictionary, About you first in the prompt, learned cleanup rules.
- **Text:** spoken lists (both apps), snippets (inserted after cleanup), Windows code mode (formatters, symbols, verbatim in code apps), paragraph breaks at pauses (Windows).
- **Learn from my corrections** (both apps, on by default): 3 min or until sent, changed words only, Recently learned list with undo.
- **Windows input:** tap-or-hold, hands-free chord (off), paste last (Shift+Alt+Z), copy last, Esc cancel, edit by voice (experimental), keep the mic ready (`warm_mic`, off), terminal paste (Ctrl+Shift+V; Shift+Insert for PuTTY/mintty), Win+V clipboard history (`clipboard_history`), upload while speaking, FLAC upload.
- **Notes:** voice notes on both apps; Windows keep listening (double-tap, up to 60 min, Note or Type, crash recovery) and note shortcut Ctrl+Alt+N; Android note bubble, notification and Quick Settings tile; Improve my cleanup (Windows).
- **Android:** bubble watchdog, saved position and diagnostics card, mic choice, Install help card, faster start and upload (m4a, pieces, warm-up).
- **Relay:** notes and profile sync, Devices list, relay as AI server, set-up help and Test connection on both apps, relay from the Windows tray (`Vox.exe --relay`), standalone Windows x64, Linux x64 and arm64 programs with SHA256SUMS.
- **Diagnostics:** Speed card (local timings), Home status card, result marks on pill and bubble, `tools/bench_cleanup.py`.
- **Open source:** README, CONTRIBUTING, SECURITY, Code of Conduct, issue forms, MIT licence, CI on every PR, project site.

### Changed
- Settings regrouped: AI providers, Voice & audio, Privacy, System. Privacy text rewritten to match the code.
- Safer paste: if the window changed while Vox worked, the text is copied, not pasted. Long pause-free speech is cut at the quietest moment. Double-tap now starts keep listening, not hands-free.
- Android no longer declares `VIBRATE`; Android typed-text events are requested only while learning is armed.

### Fixed
- Overlay pill vanishing after minutes (re-assert, repair, rebuild, watchdog; unverified on the user's PC).
- "Message" prefix typed into WhatsApp/Telegram (placeholder check).
- Final-review fixes: transient config open no longer renames a good `config.json`; meeting recovery no longer deletes audio with an empty transcript; relay device PUT/DELETE no longer lose to a future-stamped note (clock bound); auto-learn ignores half words, numbers and ordinary-word swaps; paragraph breaks kept on the pieces path; edit by voice respects `clipboard_history`; cancel stops pieces uploads and no longer reports a cleanup failure on Android; snippet byte limits; Windows Learn lock no longer held while querying the app.
- Calendar tokens protected by DPAPI; a damaged config no longer blocks startup.

## [1.2.0] - 2026-09-27 (tag `v1.2.0`, `32a5bfc`)

- Meeting notes: live "ask" during a meeting, an accurate final pass with Whisper large-v3, and detailed summaries. (`32a5bfc`)
- Later the same day, without a tag: new logo and further meeting-notes upgrade (`9aa4d28`, the base of the improvement series).

## [1.1.0] - 2026-09-27 (tag `v1.1.0`, `3b59c71`)

- Android app added (accessibility bubble, dictation service, screens), README for both platforms.

## [1.0.0] - 2026-09-27 (tag `v1.0.0`, `3dbc3c9`)

- First release: Windows app (hold-to-talk dictation, cleanup, dictionary, per-app styles, history window, meeting notes beta, Google Calendar and iCal link), installer and CI build, landing page and privacy policy.

Note: `android/AndroidManifest.xml` at the base commit already said version 1.3 (`versionCode` 4); no `v1.3` tag exists.
