# Development log

Chronological notes on how the improvement series was made, what was found, and what was checked. Newest entries at the bottom. Facts only; decisions have their own records in [decisions/](decisions/README.md).

## 2026-09-29

**Review.** The repository (Windows Python app, Android Java app) was reviewed as a whole with two parallel reviews, "standards" (smells, correctness) and "spec" (goal, README and privacy promises), plus a design pass. Main findings: Android state and cancel bugs; no retry so dictations could be lost; window title sent to the model; no password-field check; no silence gate; cleanup rules duplicated in Python and Java and already drifted; `engine.py` and `meeting.py` overgrown with thread-safety gaps; unpinned dependencies and Actions.

**Phase 0 to 6 (branch stack, one CI run per branch):** tests and CI (`chore/ci-tests`) -> Android state and retry (`fix/android-state`) -> configurable server address (`feat/endpoint-ui`) -> privacy (`feat/privacy`) -> robustness (`fix/robustness`) -> learning from corrections (`feat/learn-corrections`) -> pins and hygiene (`chore/hygiene`) -> shared golden file (`test/parity`) -> spoken commands (`feat/spoken-commands`) -> microphone chooser (`feat/mic-select`). Every branch was pushed to the owner's fork and built by CI (`tests`, `windows`, `android` jobs) before the next was started.

**Verification facts.**
- Java code has no local compiler on the development PC, so it was compiled and unit-tested only in CI; the Java test programs (`GroqClientTest`, `EndpointTest`, `PcmTest`, `CorrectionsTest`, `ParityTest`) printed their pass counts in the CI log.
- Windows behaviour was checked with pytest (188 tests at the end), a stub OpenAI-compatible server on localhost (keyless, plain http: dictation pipeline returned the cleaned text), and real-device checks below.
- The two HTML pages were exercised in a browser with mocked bridges: the "Fix a word" flow and the server-address validation worked on both.

**Environment findings worth remembering.**
- GUI programs started from inside the coding agent's shell were invisible to the user: the window failed with WebView2 "Invalid window handle" (also with pywebview 5.4 and pythonnet 3.0.5; a Qt backend opened a window that was hidden). Started from the user's own terminal, the window and tray worked with the stock pywebview 6.2.1 setup. Conclusion: launch GUI checks from the user's own terminal.
- `pytest` could fail while cleaning its temp folder on Windows; `-p no:cacheprovider --basetemp=<dir>` avoids it.
- A bare `python -` heredoc through a shell shim without stdin hangs; use script files.

## 2026-09-30

**Device tests.** Windows: Ctrl+Win dictation did not work at first. Log analysis showed the hotkey fired ("recording started") and every recording was rejected by the new silence gate: the default microphone (a headset) delivered pure silence (peak level 1), while the Realtek microphone input read a normal noise floor (about 25 to 30). A Microphone setting was added; after choosing the Realtek microphone dictation worked. The missing recording pill in that run was explained by a `KeyboardInterrupt` in the Tk main loop (a Ctrl+C in the launching terminal) that stopped the pill while the rest of the engine kept running; the engine now quits cleanly on Ctrl+C. Driving the real engine from a script showed the pill state and visibility working. Android: the APK built by CI from the head branch was installed and tested by the user, who reported that it works (the individual checks were not itemised).

**Merge.** The 17 commits were fast-forwarded into the fork's `main` (old `main` tagged `pre-improvements`), then opened and merged upstream as PR 1 (`cb3f679`) by the user (a collaborator on the repository), with the original author's agreement. The fork was archived afterwards.

**Documentation.** This folder was written by reading every source file, then checked by `documentation/tools/check_docs.py` (see [decisions/0014-documentation-checked-in-ci.md](decisions/0014-documentation-checked-in-ci.md)).

Later the same day: PR 2 (the documentation folder) was merged upstream as `de839c8`. Added `documentation/tools/docs_todo.py` (maps changed files to the pages that describe them, tested in `tests/test_docs_todo.py`) and the project skill `vox-doc-sync`, so the documentation is synced at the end of every session ([decisions/0015-sync-docs-every-session.md](decisions/0015-sync-docs-every-session.md)). The tool was tried on its own branch: it listed the three new files and the pages to update.

## 2026-09-30, P7c built (Windows sync client)
PRs 10 to 13 were merged first (main `becb3d2`). `windows/sync.py` plus `dirty`/`seq` columns and merge functions in `notes.py`, an engine worker thread, control endpoints, Settings block and sync status on the Voice notes page. 10 new tests in `tests/test_sync.py` run two devices (two data folders) against a real relay on localhost: push and pull, edit and delete propagation, a newer-edit-wins conflict, an edit made while sending stays dirty, offline and bad-token and wrong-owner messages losing nothing, upgrade of an old database, the merge rules, the worker. One test was wrong (it searched by word for a note inserted without a search index entry). Not seen on a screen; not tried against the Pi or over Tailscale.

## 2026-09-30, P7b built (relay on a Raspberry Pi, with a management web page)
Asked for: run the relay on a Pi 5 and manage it from a web page. Moved it to `relay/`, made the data folder per-platform and private on POSIX, added SIGTERM handling, a systemd unit, a Pi guide, the management page and `/admin/*` endpoints, device names (`X-Vox-Device`) and an in-memory activity list. 19 new tests (313 total; one is skipped on Windows and runs in CI on Linux) and a CI job that runs the relay tests on Python 3.9 and 3.13 on x86 Linux and 3.13 on arm64 Linux. The page was checked in a real browser (sign-in, five tabs, phone width, a `<script>` note shown as text, no console errors). CI on the PR caught a real bug that Windows never showed: an oversized upload got its 413 while the client was still sending, so on Linux the client sometimes saw a broken pipe; the server now reads and drops the upload (up to 5 MB) before answering, and connections that stall for 30 s are dropped. The first screenshots landed in the session's working folder and were deleted. A stray bare `python -` heredoc hung the shell once (known trap). Not tried: a real Pi, the systemd unit, `tailscale serve` from a phone.

## 2026-09-30, P7a built (relay server)
`windows/relay.py` with 17 tests over real HTTP (auth, owner header, cursor paging, last-writer-wins, tombstones, search with and without FTS5, profile If-Match, limits, restart), and a smoke run as a real process (health with token 200, without 401, put and search worked). Tailscale itself was not involved: nothing was published to a tailnet and no phone connected. New page 14-relay.md, ADR 0020.

## 2026-09-30, P5 built on Windows (voice notes)
`windows/notes.py` (SQLite, FTS5 with LIKE fallback, delete markers), engine note mode (`toggle_note`, `pending` now a triple), tray item, control endpoints, Voice notes page. 15 new tests (277 total): 9 for the store, 6 that drive the engine's note logic with stubs (they skip where pynput and friends are missing, so CI's test job does not run them). The first store test failed only because I miscounted the words in an expected title. Not seen on a screen. Decision 0019.

## 2026-09-30, lighter Windows build (P8a, measured)
Built Vox three ways in a throwaway environment: CI flags 85 MB (1,694 files); with Pillow's AVIF and WebP codecs excluded 77 MB. The biggest item is numpy (28 MB), needed by `soundcard` for meeting notes, so dropping `psutil` and `pyperclip` (small) was not worth the risk and numpy stays. The excluded build started with a temporary `APPDATA` (engine and overlay up, no errors). Android's CI artifact is 56 KB. Not measured: installer size, idle memory. First attempt failed because PyInstaller resolves a relative `--icon` against the spec folder.

## 2026-09-30, documentation sync after P1 to P4
Ran the vox-doc-sync routine by hand (the skill is not listed in a session started outside `vox\`, and `/reload-skills` does not change that). Found stale: the privacy page (still said Groq only), the accessibility description string, the roadmap (item "run CI on pull requests" was done, numbering broken), the "CI only on tags" sentences in `AGENTS.md` and the skill itself, the verified-against line and test counts, missing glossary and architecture rows. Fixed all; no code behaviour changed except the accessibility description text.

## 2026-09-30, P4 built (live voice level)
Shared curve `level_from_rms` / `Pcm.levelFromRms` with golden rows; `LevelHistory` for the Windows pill; 40 ms reads and faster-rise smoothing on Android. 13 new tests (262 total). The code map had shown that both meters already existed, so this is an upgrade. Not verified: how it looks on a Windows screen or on a phone.

## 2026-09-30, P3 built ("About you" context)
`user_context` on both apps, the fenced block in `system_prompt`/`systemPrompt`, `clean_context`/`cleanContext`, golden rows and 11 new tests (249 total). CI on PR 6 (P2a) was green before this. Not verified: the Java code before CI, the text box on a device, whether the extra context measurably changes results (no real-provider test).

## 2026-09-30, P2a built (warm connections at key-down)
PRs 4 and 5 merged first. Added `vox_core.warm` and a shared HTTP session (Windows) and `GroqClient.warm` plus connection reuse (Android); `startRecording` now validates each role address. 5 new tests (238 total); `tests/conftest.py` routes the shared session back through `requests.post` for older tests. Verified: pytest. Not verified: real time saved (an estimate of one to two round trips), the Java code before CI. Decision 0017: no always-open microphone.

## 2026-09-30, P1 built (providers and model list)
Implemented spec P1 on `feat/providers`: `windows/providers.py` and `Providers.java` (per-role address/key/model, model discovery and classification, Test, reasoning retry), Windows and Android settings screens, golden `models` rows, 37 new Python tests (233 total) and `ProvidersTest`. Verified: pytest, page scripts parse, every element id used exists, Playwright not used. Not verified: the Java code (compiled only in CI), the settings screens in a real window and on a phone, the datalist dropdown in the Android WebView, and any real provider other than the stub tests. Decision: keep the Java class name `GroqClient` (rename deferred); the model list drops hidden models instead of returning them.

## 2026-09-30, v2 planning
Research (open-source dictation tools, providers and models, relay and Android limits, a code map) produced an 8-part roadmap: providers and model list first, then key-down speed, "About you" context, live level, lightweight build, notes store, Android note mode, relay, design refresh. Decisions: server-only (no bundled local models), relay serves or proxies keys (chosen in settings), Android keeps the WebView UI, notes keep transcripts only by default. Added `documentation/specs/` (spec P1) and ADR 0016 (proposed). CI now runs the `tests` and `android` jobs on pull requests; the Windows build still runs only on tags or by hand.

## How to add an entry

Add a dated heading and 3 to 10 lines: what changed, what was verified and how, what surprised you. Put lasting reasons into an ADR and user-visible changes into `CHANGELOG.md`.
