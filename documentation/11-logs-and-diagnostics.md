# 11. Logs and diagnostics

## Windows logs

Each process writes to `%APPDATA%\Vox` with a `RotatingFileHandler` (1,000,000 bytes per file, 2 backups: `vox.log`, `vox.log.1`, `vox.log.2`; same for `window.log` and `relay.log`). Format: `YYYY-MM-DD HH:MM:SS,mmm LEVEL logger: message`. Level is INFO. Uncaught exceptions in the main thread and in worker threads are logged (`uncaught`, `thread crashed`) with tracebacks. The code does not log the API key; the logs do contain app (exe) names and error text from the server.

| File | Written by | Logger names |
|---|---|---|
| `vox.log` | engine process (`Vox.exe`) | `vox`, `vox.overlay`, `vox.meeting`, `vox.calendar`, `vox.gcal`, `vox.secret`, `vox.sync` |
| `window.log` | window process (`Vox.exe --window`) | `vox.ui` and pywebview's own logger (WebView2 errors land here) |
| `relay.log` | relay process (`Vox.exe --relay`, started by the tray item "Run relay on this PC") | `vox`; only uncaught errors (the relay prints to its console, which a windowed exe does not have) |

Messages worth knowing:

| Message | Meaning |
|---|---|
| `engine started, hotkey=[...]` | Engine is up and listening for the shortcut |
| `overlay ready WxH scale=... geometry=...` | The pill window was created (it appears only while recording, sending or showing a result signal) |
| `overlay shown (rec)` | The pill became visible for a recording (the word in brackets is the state drawn: `rec`, `busy`, `meet`, or `sent` / `error` for the short result signal) |
| `recording started (app=Code.exe)` | Hotkey accepted; the exe that will receive the text |
| `keep listening started (target=note, app=...)` | A keep-listening session started (double-tap, tray or note shortcut) |
| `keep listening: piece N is text X ms after it ended` | One piece of a session became text; X is the wait from the end of the speech |
| `keep listening: ...` (warning or traceback) | A piece could not be sent, the audio buffer could not be written or removed, typing or the cleanup failed; the audio file under `%APPDATA%\Vox\listen` is kept after a failure |
| `note shortcut '...' is off: ...` | The note shortcut setting is not usable (for example it contains the dictation shortcut) and is ignored |
| `notify: ...` | A tray notification was shown (text included), e.g. "Vox did not hear anything (loudest sound N of 32768)..." |
| `api error: ...` | The server answered with an error status |
| `settings reloaded, hotkey=...` | `config.json` changed and was re-read |
| `uncaught` / `thread crashed` | A bug; read the traceback that follows |
| `relay started on port N (pid P)` / `relay stopped` in `vox.log` | The engine started or stopped the relay child process (tray item "Run relay on this PC") |
| `relay ended right after starting (exit code N)` in `vox.log`, with a tray notification | The relay process exited within 10 s; `relay.log` has the traceback (for example an unwritable data folder) |
| Tray notification "Port N is already in use ... Vox did not start its own" | Something already answers on the relay port (often a relay started by hand); stop it, or change `relay_port`, or untick "Run relay on this PC" |
| `KeyboardInterrupt` traceback in `overlay.run` | Ctrl+C in the launching terminal (the engine now quits cleanly on it) |
| `WebView2 initialization failed ... Invalid window handle` in `window.log` | The window was started somewhere WebView2 cannot attach (for example a hidden desktop session); start Vox from the user's own terminal |

## Android

The Android app writes one small file: the bubble diagnostics, `files/overlay_diag.log` in the app's private folder (the last 50 events that can make the floating bubble appear or vanish: service connected or unbound, bubble added or removed with the reason, a bubble put back by the watchdog, screen on or off, unlock, rotation, an app being installed or removed). Read it in the app: Settings, System, **Bubble diagnostics** (service state, battery optimisation state, the last 20 events, Copy report). The same events go to logcat as `bubble: kind detail` under the tag `vox`, together with the debug line `tap->recording ms=N` (the time from a bubble tap to the first audio frame): `adb logcat -s vox`. Problems are shown as toasts. Otherwise use `adb logcat` only for crashes of the process itself.

## Diagnosing common problems

| Symptom | Check |
|---|---|
| Shortcut does nothing | `vox.log` for `engine started` and `recording started`. No `recording started`: the engine is not running or the key names in `hotkey` are wrong. |
| "Vox did not hear anything (loudest sound 1 ...)" | The selected microphone delivers silence (muted, dead, or a wireless headset whose mic is off). Pick another in Settings > Microphone. A live microphone in a quiet room usually shows a small but non-trivial value (25 to 30 was seen on one laptop). |
| Nothing pasted, no error | The target app blocked Ctrl+V, or the engine was busy (`busy` state) |
| Notification "Copied; the window changed" | The focused program was not the one the dictation started in, so Vox did not paste (it would have gone to the wrong app). The text is on the clipboard: press Ctrl+V where you want it. `vox.log` shows a warning `could not read the focused window` when that check itself failed (the paste then goes ahead). |
| Notification "The server rejected the API key" | Key wrong or expired; Settings > Test |
| Notification "Rate limit reached" | Groq free limit; wait, then tray > Retry last dictation |
| Recording pill missing but dictation works | Vox was started from a session the user cannot see, or the overlay failed (`overlay failed to start` in `vox.log`) |
| Window will not open | `window.log`; see the WebView2 line above |
| Android bubble never appears or keeps disappearing | Settings, System, Bubble diagnostics: "Switched on but not running" means Android stopped the service (switch it off and on in Accessibility settings); "Bubble hidden by only-typing" means no text field was focused; "Bubble could not be added" names the exception; a screen off or unlock line just before a removal points at the screen; a battery line saying Vox may be stopped points at battery optimisation (the card then shows an Open battery settings button); "Bubble put back by the watchdog" means Android had dropped the window and Vox added it again; "Always show the bubble" in Settings, System keeps the bubble up without a focused text field |
| Android "Vox did not hear anything" | Silent recording (mic covered, wrong source) |
| Android dictation fails with a network message | The recording is kept; tap Retry in the Vox notification |

## Quick level check (Windows microphone)

A one-off script that prints the loudest sample per second from the default input device is enough to tell a dead microphone from a live one: with `sounddevice`, open an `InputStream(samplerate=16000, channels=1, dtype="int16")` and print `abs(indata).max()` per callback. A live microphone in a quiet room shows a small noise value (25 to 30 was seen on one laptop); a value of 1 means digital silence.

## Sync and the relay

Sync problems do not appear as errors in the app: the Voice notes page shows "Not synced: <reason>" (wrong token, cannot reach the relay, another Tailscale user) and Settings has a Test button. Only unexpected failures are written to `vox.log` under `vox.sync`. The relay writes no access log on purpose; its management page (Devices and activity) shows the last 100 requests, refused tokens and errors from memory only, and the service manager's own log holds anything the process prints (on a Raspberry Pi: `journalctl -u vox-relay`).
