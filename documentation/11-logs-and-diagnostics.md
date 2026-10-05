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
| `overlay shown (rec)` | The pill became visible for a recording (the word in brackets is the state drawn: `rec`, `busy`, `meet`, `listen`, or `sent` / `error` for the short result signal). Logged only when it was hidden before: a recording that starts while a check, a ! or the meeting timer is still up has no line of its own |
| `overlay hwnd=N exstyle before=0x... after=0x...` | The pill window was set up (no focus, click-through, on top); logged at start and again for every rebuilt or newly made frame window |
| `overlay repair: <found>; after: <left>; state=... hwnd=... visible=... iconic=... cloaked=... tk_mapped=... rect=... work_area=... monitors=[...] works=[...] exstyle=0x... (N like this held back)` | The half-second re-assert of the visible pill found something wrong and tried to fix it (put back on top, re-placed, shown without focus). `<found>`: `hidden` (Windows says the window is not visible), `minimised`, `cloaked` (Windows keeps it off the screen; `cloaked=2` means the shell did it, typically because the window is on another virtual desktop), `off-screen` (less than half of it on any monitor), `not topmost`, `style lost` (the no-focus or click-through style is gone), `tk unmapped` (Tk thinks the window is unmapped and does not draw it: the window is up but fully transparent), `new window` (Tk made a new frame window). `after: fixed` means the repair worked. The facts are from before the repair; `monitors` and `works` are every monitor's rectangle and work area, `work_area` the main monitor's work area the pill is placed in. The same problem list is logged once a minute at most; N counts the ones held back. **This is the line that names the cause of a missing pill** |
| `overlay rebuilt (<why>): hwnd A -> B; now: ok; ...` | A repair did not bring the pill back (`<why>`: hidden, minimised, cloaked or off-screen), so a new pill window was made (it lands on the current virtual desktop); `now:` lists what is still wrong with the new one (`ok` when nothing). At most one rebuild every 10 s and 3 while the pill stays up |
| `overlay rebuild took the focus; gave it back` (warning) | The new pill window became the foreground window, and the focus was handed back to the app you were in |
| `overlay: SetWindowPos failed (Windows error N; M more since the last warning)` (warning) | A Win32 call for the pill failed (also `GetWindowRect`, `EnumDisplayMonitors`, `SystemParametersInfoW`, `SetWindowLongW`); N is `GetLastError` (1400: invalid window handle, 5: access denied). Once a minute per call at most |
| `overlay tick has not run for N s (state rec); threads: ...` (warning) | The pill's 33 ms Tk tick stopped while not idle (the main thread is blocked); followed by every thread's stack. Logged once per stall by the engine's `overlay-watch` thread |
| `state 'rec' stuck for N s (recording=False busy=False listening=False): ending it` (warning) | `Engine.state` did not match the engine's flags for 10 s (`rec` with no recording, `busy` with nothing being sent, `listen` with no session, or a recording past its longest time limit plus 60 s because the microphone stopped calling back); the watchdog set it to idle (or ended the recording as its time limit would) |
| `recording started (app=Code.exe)` | Hotkey accepted; the exe that will receive the text |
| `keep listening started (target=note, app=...)` | A keep-listening session started (double-tap, tray or note shortcut) |
| `keep listening: piece N is text X ms after it ended` | One piece of a session became text; X is the wait from the end of the speech |
| `keep listening: ...` (warning or traceback) | A piece could not be sent, the audio buffer could not be written or removed, typing or the cleanup failed; the audio file under `%APPDATA%\Vox\listen` is kept after a failure |
| `note shortcut '...' is off: ...` | The note shortcut setting is not usable (for example it contains the dictation shortcut) and is ignored |
| `hands_free_hotkey '...' is off: ...` (also `paste_last_hotkey`, `copy_last_hotkey`, `command_hotkey`) | That shortcut clashes with another one or cannot be read (`hotkeys.check`) and is ignored; Settings shows the same reason under its row |
| `hands-free dictation (tap)` / `hands-free dictation (shortcut)` | A dictation was latched hands-free by a tap (`hotkey_style` hold_or_tap) or by the hands-free shortcut |
| `recording cancelled (Esc)` / `keep listening: cancelled after N s[, audio kept]` | Esc ended a recording or a session without sending anything more |
| `hotkey: N key(s) were not really held after a pause, forgotten` | After more than 2 s without key events some keys the engine thought were held were up (a lost key-up, for example after Win+L); they were dropped |
| `modifier keys still held after 2.0 s, going on` | The paste waited for Shift/Ctrl/Alt/Win to come up and gave up; the paste was sent anyway |
| `fidelity guard: <reason>` (info) and `fidelity guard: the cleanup answer lost the spoken words, used the rules layer's text (N words)` (warning; not when nothing was left to type) | The guard rejected the cleanup answer (`missing 3 > 1`, `numbers changed`, `added sentence`, `scaffold echo: rules:`, ...); the reason never holds a dictated word. The phone writes `fidelity guard: <reason>, used the rules layer's text` to logcat (tag `vox`). The rules layer's text is the spoken words with noises and spoken punctuation commands handled, capitals and a final mark |
| `streaming: N pieces, M sent while speaking` | A dictation went to speech-to-text in N pieces, M of them before the key was released |
| `FLAC encoding failed (...), sending WAV` | The FLAC upload could not be made; the dictation went as WAV |
| `clipboard format N (B bytes) is too big to keep, it is not put back` | One clipboard format was over 16 MB and was left out of the copy taken before a paste |
| `edit by voice: applied (A -> B characters)` / `edit by voice: refused (reason)` / `edit by voice: api error N` | Edit by voice; sizes and reasons only, never the selected text or the instruction |
| `meeting ID saved, exported (N lines[; audio kept: not fully transcribed])` / `... not exported` / `could not export the notes of meeting ID (ErrorType)` | A meeting was finished; its title and export path are not logged |
| `notify: ...` | A tray notification was shown (text included, except note and meeting titles, which log `(private text not logged)`), e.g. "Vox did not hear anything (loudest sound N of 32768)..." |
| `api error: ...` | The server answered with an error status |
| `settings reloaded, hotkey=...` | `config.json` changed and was re-read |
| `uncaught` / `thread crashed` | A bug; read the traceback that follows |
| `relay started on port N (pid P)` / `relay stopped` in `vox.log` | The engine started or stopped the relay child process (tray item "Run relay on this PC") |
| `relay ended right after starting (exit code N)` in `vox.log`, with a tray notification | The relay process exited within 10 s; `relay.log` has the traceback (for example an unwritable data folder) |
| Tray notification "Port N is already in use by another program, so Vox did not start its relay ..." | Something already answers on the relay port: often a relay started by hand, but if you did not start one, something may be waiting for your devices' relay token (the apps check the relay's proof first, so they do not send it). Find it with `netstat -ano | findstr :N`, stop it, or change `relay_port`, or untick "Run relay on this PC" |
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
| Notification "The window in front runs as administrator..." | That program runs elevated and Vox does not, so Windows drops the keys Vox sends; the text is on the clipboard (press Ctrl+V), or run that program without administrator rights |
| Hands-free starts when you tap the shortcut | Settings, "A quick tap of the dictation shortcut" is Hold or tap (`hotkey_style`); Classic restores the old behaviour |
| Notification "Copied; the window changed" | The focused program was not the one the dictation started in, so Vox did not paste (it would have gone to the wrong app). The text is on the clipboard: press Ctrl+V where you want it. `vox.log` shows a warning `could not read the focused window` when that check itself failed (the paste then goes ahead). |
| Notification "The server rejected the API key" | Key wrong or expired; Settings > Test |
| Notification "Rate limit reached" | Groq free limit; wait, then tray > Retry last dictation |
| Recording pill missing but dictation works | Vox was started from a session the user cannot see, or the overlay failed (`overlay failed to start` in `vox.log`). Otherwise look for `overlay repair` and `overlay rebuilt` lines near the recording: `cloaked=2` points at a virtual desktop switch (the dictation shortcut Ctrl+Win plus an arrow key or D switches or creates desktops), `off-screen` with the `monitors` list at a monitor change, `hidden` or `minimised` at something that hid the window, `tk unmapped` at Tk not drawing it. `overlay shown` lines with no repair lines while the pill was still invisible mean Windows reported the window visible, on screen, on top and not cloaked: report it with the lines around it |
| Window will not open | `window.log`; see the WebView2 line above |
| Android bubble never appears or keeps disappearing | Settings, System, Bubble diagnostics: "Switched on but not running" means Android stopped the service (switch it off and on in Accessibility settings); "Bubble hidden by only-typing" means no text field was focused; "Bubble could not be added" names the exception; a screen off or unlock line just before a removal points at the screen; a battery line saying Vox may be stopped points at battery optimisation (the card then shows an Open battery settings button); "Bubble put back by the watchdog" means Android had dropped the window and Vox added it again; "Always show the bubble" in Settings, System keeps the bubble up without a focused text field |
| "Vox heard no usable words in that recording" | The text came back empty: a lone "Thank you.", "Bye." or "You" is filtered as a Whisper silence phrase, or the server returned nothing. Speak a little longer; if it is a real "Thank you.", type it |
| "Vox heard only filler sounds (um, uh) in that recording, so nothing was typed." (both apps) | Words came back, but only noises or fillers (the cleanup answered EMPTY, or the rules layer left no word). Nothing is wrong with the microphone |
| Android "Vox did not hear anything" | Silent recording (mic covered, wrong source) |
| Android dictation fails with a network message | The recording is kept; tap Retry in the Vox notification |

## Quick level check (Windows microphone)

A one-off script that prints the loudest sample per second from the default input device is enough to tell a dead microphone from a live one: with `sounddevice`, open an `InputStream(samplerate=16000, channels=1, dtype="int16")` and print `abs(indata).max()` per callback. A live microphone in a quiet room shows a small noise value (25 to 30 was seen on one laptop); a value of 1 means digital silence.

## Sync and the relay

Sync problems do not appear as errors in the app: the Voice notes page shows "Not synced: <reason>" (wrong token, cannot reach the relay, another Tailscale user) and Settings has a Test button. Only unexpected failures are written to `vox.log` under `vox.sync`. The relay writes no access log on purpose; its management page (Devices and activity) shows the last 100 requests, refused tokens and errors from memory only, and the service manager's own log holds anything the process prints (on a Raspberry Pi: `journalctl -u vox-relay`).
