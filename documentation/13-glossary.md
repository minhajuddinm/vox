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
| Cleanup | The chat-model pass that removes fillers, fixes punctuation, and so on. Skipped for the `raw` style, when switched off, and for texts under 3 words. |
| Raw transcript | The Whisper text before cleanup. Used as the fallback when cleanup fails. |
| Style | Tone for cleanup: `formal`, `casual`, `very_casual`, `neutral`, or `raw` (no cleanup). Chosen per app. |
| App label | The name given to the cleanup model: exe name on Windows, app display name on Android. Never a window title. |
| Term | A word or name in the dictionary that helps spelling (a plain dictionary line, or the right side of a replacement). |
| Replacement | A `wrong => right` dictionary line applied to the final text. |
| People | Extra dictionary terms for names. |
| Hands-free | Windows recording mode started by double-tapping the shortcut; finish with one more press. |
| Silence gate | The check that skips upload when the loudest sample is below 655 (of 32768). |
| Silence hallucination | Whisper inventing "thank you", "bye" and similar on silence. Filtered out. |
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
| Voice note | A note recorded by voice in note mode and saved to `notes.db` instead of being pasted (`windows/notes.py`). |
| Relay | The optional self-hosted server (`relay/relay.py`, runs on a Raspberry Pi, Linux, macOS, Windows) that stores voice notes and a profile so devices can share them over Tailscale, with a management web page. |
| Dirty note | A local voice note changed on this device and not yet accepted by the relay (`dirty = 1` in `notes.db`). |
