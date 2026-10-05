# 13. Glossary

| Term | Meaning in Vox |
|---|---|
| Engine | The Windows background process: tray icon, hotkey, recording, paste (`windows/engine.py`). |
| Window | The Windows main window process (`Vox.exe --window`). |
| Overlay / pill | The small recording indicator at the bottom of the Windows screen (`windows/overlay.py`). |
| Bubble | The floating mic button on Android (`BubbleView`, drawn by the accessibility service). |
| Server / server address | The OpenAI-compatible API Vox talks to. `base_url` setting; Groq unless changed. |
| Groq | The default provider of Whisper speech-to-text and chat models. |
| STT | Speech to text (Whisper). |
| Cleanup | The chat-model pass that removes fillers, fixes punctuation, and so on. Skipped for the `raw` style, when switched off, and for texts shorter than the `cleanup_min_words` setting (4 words unless changed). |
| Fidelity guard | The check that the cleanup answer still holds the words that were said, in order, and adds nothing (`fidelity_check`, `Fidelity.check`; guard v2); an answer that fails it is dropped and the raw words go through the rules layer instead. See [specs/p9a-cleanup-keeps-my-words.md](specs/p9a-cleanup-keeps-my-words.md) and [decisions/0030-cleanup-keeps-the-spoken-words.md](decisions/0030-cleanup-keeps-the-spoken-words.md). |
| Cleanup strength | `light` (keep every spoken word except pure noises such as um and uh) or `standard` (also drop fillers, repeats and false starts); the setting `cleanup_strength` (Settings, "Cleanup strength"; `light` unless set) chooses the prompt's rule and how strict the fidelity guard is. |
| Use raw | The History button that copies the words as spoken (the entry's `raw`) when they differ from the typed text. An entry whose cleanup the fidelity guard rejected says "cleanup rejected, your words as spoken". |
| Fallback text | What is typed when the AI cleanup was wanted but gave no text (a phrase under `cleanup_min_words`, an error or timeout, or a rejected answer): the rules layer's text (`fallback_text`, `ApiClient.fallbackText`), then the dictionary. |
| Rules layer | The deterministic cleanup without the AI (`windows/rules_layer.py`, `RulesLayer.java`): pure noises and spoken punctuation commands out, capitals and the final mark for the style; Standard also takes a typed-value self-correction. Never adds a word. |
| Fuzzy dictionary pass | The step after `apply_replacements` that puts a single-word term of 5+ letters right when the text has it in another case, and a term of 7+ letters also when the text has it one letter off (`fuzzy_dictionary`, `Terms.fuzzy`); words on the `COMMON_WORDS` stoplist are never changed. |
| Benchmark | `tools/bench_cleanup.py`: a hand-run developer tool that scores a cleanup model on a 45-row synthetic corpus (latency, word recall, added words, guard pass, term accuracy); local only, not in CI. |
| Raw transcript | The Whisper text before cleanup. Used as the fallback when cleanup fails. |
| Style | Tone for cleanup: `formal`, `casual`, `very_casual`, `neutral`, or `raw` (no cleanup). Chosen per app. On Windows a per-app style can also be `code` (code mode in that app). |
| Cue (list cue) | A spoken word that starts a list item: an ordinal in sequence (first, second; pehla, doosra), point/item/step/number one, or a bullet word (bullet point, next point). The list pass (`structure.py`, `Structure.java`) removes it and writes the item on its own line. |
| Code mode | Windows only: in an editor or terminal (`code_apps`) or an app with the style `code`, spoken formatters (camel case, snake case, ...) and symbols (open paren, equals equals, ...) become code (`codemode.py`, [15-code-mode.md](15-code-mode.md)). |
| Snippet | A trigger phrase and the text it types (`snippets` setting, `snippets.py`, `Snippets.java`); put in after the cleanup. |
| App label | The name given to the cleanup model: exe name on Windows, app display name on Android. Never a window title. |
| Term | A word or name in the dictionary that helps spelling (a plain dictionary line, or the right side of a replacement). |
| Replacement | A `wrong => right` dictionary line applied to the final text. |
| People | Extra dictionary terms for names. |
| Keep listening | Windows session started by double-tapping the shortcut (or the note shortcut or the tray): audio cut at pauses into pieces and turned into text while you speak; target Note (one cleaned note at the end) or Type (each piece typed into the app it started in); a double press, the stop phrase, the tray, the note shortcut or the 60 minute limit ends it and saves; Esc cancels it (no note). Android has none. |
| Hands-free dictation | Windows: a dictation that keeps recording after the keys are let go, until the next press (the pill shows a stop square): from the hands-free shortcut (`hands_free_hotkey`, off by default), a tap with `hotkey_style` Hold or tap, or a voice note. |
| Edit by voice | Windows, experimental (`command_hotkey`): select text, hold the shortcut, say what to change; the selection and the instruction go to the cleanup server and the answer replaces the selection after a guard. |
| Listening target | `listen_target`: `note` or `type`, what keep listening does with the text. |
| Note shortcut | `note_hotkey` (default Ctrl+Alt+N): a second shortcut that starts and stops a keep-listening session with the target Note. |
| Piece | One utterance of a keep-listening session (3 to 20 s) or of a long dictation; each is one speech-to-text request. |
| Listening buffer | The raw audio of a running keep-listening session on disk (`%APPDATA%\Vox\listen`), kept after a failure so "Recover listening session" can make a note of it. |
| Improve my cleanup | The Settings card that sends a confirmed set of saved dictations to a stronger model and shows proposals (dictionary words, replacements, rules) to accept per item. |
| Learned rules (`my_cleanup_rules`) | Up to 2,000 characters of short cleanup rules, one per line, added by Improve my cleanup; part of the cleanup prompt and the synced profile. |
| Silence gate | The check that skips upload when the loudest sample is below 655 (of 32768). |
| Silence hallucination | Whisper inventing "thank you", "bye" and similar on silence. Filtered out; in a recording sent in pieces only while no text has been heard yet (a closing "Thank you." after real speech is kept). |
| Pending recording | A dictation that failed to send and is kept for retry. |
| Job (Android) | One dictation, identified by an increasing `jobId`; stale jobs cannot change state. |
| Golden file | `spec/golden.txt`: expected outputs that the Python and Java cleanup helpers must both produce. |
| Parity | The requirement that Python and Java implementations behave identically. |
| Control server | The engine's localhost HTTP API used by the window for meeting features. |
| DPAPI | Windows Data Protection API; protects the API key in `config.json`. |
| Private host | An address where plain http is allowed: this device, LAN, Tailscale. |
| Tailscale | A private network; its addresses are `100.64.0.0/10` and `*.ts.net`. |
| Trampoline | `TrampolineActivity`: an invisible activity that lets the microphone service start from the foreground. |
| Meeting notes | Windows feature that records "You" (mic) and "Others" (PC audio) and writes structured notes. |
| Final pass | The accurate re-transcription of a meeting after it ends. |
| ADR | Architecture decision record, in `documentation/decisions/`. |
| Role | Which job a server does: `stt` (speech to text) or `llm` (cleanup). Each role can have its own address, key and model (`windows/providers.py`). |
| Preset | A provider entry in Settings that fills in the address (Groq, OpenAI, and so on). Only a convenience: the address decides behaviour. |
| About you | Free text about the user added to every cleanup request (`user_context`). |
| Meter level | 0 to 1 loudness value of the voice, the same curve on both platforms (`level_from_rms`, `Pcm.levelFromRms`). |
| Warm-up | Opening the server connections when recording starts, so the upload does not wait for the TLS handshake (`vox_core.warm`, `ApiClient.warm`). |
| Timing | The marks of one dictation (`key_down`, `rec_start`, `key_up`, `stt_start`, `stt_done`, `llm_start`, `llm_done`, `inserted`) and the stage times made from them (`start`, `rec`, `stt`, `llm`, `insert`, `total`). Saved as `timing` in the history entry and shown on the Speed card; never leaves the device (`windows/timing.py`, `Timing.java`). |
| Voice note | A note recorded by voice in note mode and saved to `notes.db` instead of being pasted (`windows/notes.py`). |
| Relay | The optional self-hosted server (`relay/relay.py`, runs on a Raspberry Pi, Linux, macOS, Windows) that stores voice notes and a profile so devices can share them over Tailscale, with a management web page. |
| Dirty note | A local voice note changed on this device and not yet accepted by the relay (`dirty = 1` in `notes.db`). |
