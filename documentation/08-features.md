# 8. Features

Everything Vox does today. "Origin" is `orig` for the original author's work (up to commit `9aa4d28`) and `PR1` for the improvement series merged as PR 1. Tests point to pytest files in `tests/` (`py`) or Java programs in `android/test/com/minhaj/vox/` (`java`).

## Dictation

| Feature | Win | And | Origin | Where | Tests |
|---|---|---|---|---|---|
| Speech to text with Whisper | yes | yes | orig | `vox_core.transcribe` / `GroqClient.transcribe` | py `test_endpoint_config` |
| AI cleanup (fillers, self-corrections, punctuation, lists, numbers) | yes | yes | orig | `vox_core.cleanup` / `GroqClient.cleanup` | py `test_vox_core`, `test_parity`; java `GroqClientTest`, `ParityTest` |
| Tone per app (formal / neutral / casual / very casual / raw) | yes | yes | orig | `vox_core.style_for` / `Prefs.styleFor` | py `test_vox_core` |
| Personal dictionary: terms, people, `wrong => right` replacements | yes | yes | orig | `vox_core.dictionary_terms`, `apply_replacements` / `Terms`, `GroqClient.applyReplacements` | py + java parity |
| Hold-to-talk shortcut, five choices | yes | - | orig | `Engine.on_combo_down/up` | - |
| Hands-free mode (double-tap, press to finish, Esc cancels) | yes | - | orig | `Engine` | - |
| Floating mic bubble (tap to start/stop, drag, long press) | - | yes | orig | `VoxAccessibilityService`, `BubbleView` | - |
| Recording pill (waveform, dots) | yes | - | orig | `overlay.py` | - |
| Paste into the focused app; optional clipboard restore | yes | - | orig | `Engine.paste` | - |
| Insert into the focused field via accessibility | - | yes | orig | `VoxAccessibilityService.insertText` | - |
| Language lock (Whisper language code) | yes | yes | orig | `language` setting | - |
| History with search, copy, delete, clear; stats (Windows) | yes | yes | orig | `core.history_*`, window; `Prefs.history` | - |
| Start with Windows | yes | - | orig | `ui_app.set_autostart` | - |

## Added by PR 1

| Feature | Win | And | Where | Tests |
|---|---|---|---|---|
| Server address (own Whisper/LLM server), key optional | yes | yes | `vox_core.api_base`, `endpoint_error`, `key_missing` / `Endpoint`, `Prefs.baseUrl` | py `test_endpoint_config`, `test_endpoint_safety`; java `EndpointTest` |
| Plain http only for this device, LAN and Tailscale; https otherwise | yes | yes | `vox_core.is_private_host` / `Endpoint.isPrivateHost` | py `test_endpoint_safety`; java `EndpointTest` |
| Separate server and key for speech and for cleanup | yes | yes | `providers.role_settings` / `Providers.roleSettings`, `Prefs.role` | py `test_providers`; java `ProvidersTest` |
| Model list and picker from the server's `/models`, with free-text fallback | yes | yes | `providers.list_models`, `classify` / `GroqClient.listModels`, `Providers.classify` | py `test_providers`, `test_parity` (`models` rows); java `ParityTest` |
| Test button per role (real call, plain-language failure reasons) | yes | yes | `providers.test` / `GroqClient.test` | py `test_providers` |
| Reasoning fields only where accepted; `<think>` stripped | yes | yes | `providers.reasoning_params`, `strip_think` / `Providers.sendReasoning`, `stripThink` | py `test_providers`; java `ProvidersTest` |
| "About you" context added to every cleanup request (fenced, capped) | yes | yes | `vox_core.clean_context`, `system_prompt` / `GroqClient.cleanContext`, `systemPrompt` | py `test_user_context`, `test_parity` (`context`, `promptctx` rows); java `ParityTest` |
| Live voice level on the pill and bubble (same curve, real history on Windows) | yes | yes | `vox_core.level_from_rms`, `LevelHistory`, `overlay._draw_recording` / `Pcm.levelFromRms`, `BubbleView.setLevel` | py `test_level`, `test_parity` (`level` rows); java `ParityTest` |
| Voice notes: record from the tray or the window, saved (not pasted), searchable with time filter, editable, deletable | yes | no | `notes.py`, `Engine.toggle_note`, `Engine._process`, `Api.notes_list` | py `test_notes`, `test_engine_notes` |
| Relay server: notes with a change cursor, delete markers, search, versioned profile, token auth (server only; no client uses it yet) | server | no | `relay.py` | py `test_relay` |
| Only the app name is sent to the model (no window title) | yes | (already) | `engine.foreground_app` | - |
| API key protected by the Windows login (DPAPI) | yes | - | `secret.py`, `vox_core.load_config/save_config` | py `test_secret` |
| "Keep dictation history" switch | yes | yes | `keep_history` | - |
| Keep a failed recording and retry (tray / notification) | yes | yes | `Engine.pending`, `retry_last` / `DictationService.retryLast` | py `test_robustness` (policy) |
| Retry on temporary server errors | yes | yes | `vox_core.post_with_retry` / `GroqClient.isRetryable` | py `test_robustness`; java `GroqClientTest` |
| Silence gate: nothing is uploaded for a silent recording | yes | yes | `vox_core.is_silent` / `Pcm.isSilent` | py `test_robustness`; java `PcmTest` |
| Type only into the app you started in; refuse password fields | - | yes | `VoxAccessibilityService.insertText` | - |
| Dictation state machine that survives cancel and errors | - | yes | `DictationService` job ids | - |
| Choose the microphone | yes | - | `audio_devices.py`, `input_device` | py `test_audio_devices` |
| "Vox did not hear anything (loudest sound N)" diagnostics | yes | - | `Engine.stop`, `vox_core.peak_level` | py `test_robustness` |
| Fix a word in History -> suggested dictionary entries | yes | yes | `vox_core.suggest_corrections` / `Corrections` | py `test_suggest_corrections`; java `CorrectionsTest` |
| Spoken "new line"/"new paragraph" when cleanup did not run | yes | yes | `vox_core.apply_spoken_commands` / `GroqClient.applySpokenCommands` | py `test_spoken_commands`, parity; java parity |
| Tell the user when cleanup failed | yes | yes | `Engine._process` / `DictationService.send` | py `test_spoken_commands` |
| Ctrl+C quits the engine cleanly; quit waits for meeting notes | yes | - | `Engine.run`, `Engine.quit` | - |
| Safe concurrent meeting start/stop, locked entries | yes | - | `Meeting.ctl`, `Meeting.lock` | - |
| Shared golden test file for the cleanup rules | yes | yes | `spec/golden.txt` | py `test_parity`; java `ParityTest` |

## Meeting notes (Windows only, beta)

| Feature | Where |
|---|---|
| Record your mic ("You") and the PC's audio ("Others") separately | `meeting._Source` |
| Live transcript, about 10 to 15 s behind | `Meeting._transcribe_loop` |
| Ask a question about the meeting so far | `Meeting.ask_live` (route `/meeting/ask`) |
| Accurate final pass with `whisper-large-v3` | `Meeting._final_pass` |
| Speaker naming from context and the attendee list | `Meeting.attribute_speakers` |
| Structured notes (summary, discussion, decisions, action items, open questions, next steps, who said what) | `Meeting._notes`, `NOTES_PROMPT` |
| Saved meetings: open, rename, your own notes, tick action items, delete, search across meetings | `meeting.detail`, `save_my_notes`, `set_done`, `rename`, `ask` |
| Calendar: Google sign-in (read-only) or private iCal link; reminders or automatic start | `gcal.py`, `vcalendar.py`, `Engine._watch_calendar` |

No automated tests cover meetings, calendar or the UI pages (see [12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md)).

## Distribution

| Feature | Where |
|---|---|
| Windows installer (per-user, optional autostart) | `windows/installer.iss`, `build.yml` |
| Local Windows build and install | `windows/build_app.bat` |
| Android APK built with plain SDK tools | `android/build.sh` |
| GitHub Release on a `v*` tag with both files | `build.yml` job `release` |
| Public landing page and privacy policy | `docs/` |
