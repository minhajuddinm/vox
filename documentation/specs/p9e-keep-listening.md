# Spec P9e: keep listening (Windows)

Status: Implemented on branch `feat/p9e-listen` (tasks E1, E2 and E5, then the branch E review fixes), merged into `feat/part3`. **Windows only. Android keep-listening is out of scope for this round** (the phone already has note mode, [p6-android-note-mode.md](p6-android-note-mode.md), and the note bubble, [p9g-note-bubble.md](p9g-note-bubble.md)); nothing here was written for Java and there are no golden rows. Date: 2026-10-01. **Never run with a real microphone, a real speech server or on a real desktop** (only pure logic, fakes and a hidden-Tk drawing check were run). The reasons are in [decision 0032](../decisions/0032-keep-listening-pieces-two-targets-crash-safe-buffer.md). Behaviour as built: [04-windows-app.md](../04-windows-app.md), [08-features.md](../08-features.md); settings: [07-config-and-data.md](../07-config-and-data.md); privacy: [09-security-privacy.md](../09-security-privacy.md).

## Goal
Request R7 of the v2 part 3 plan: the double-press mode should keep listening through long speech, fast, in the background, for long voice notes. It is **not** an AI assistant: the requester's answer to "what should the text bot do?" was "no ai chat only listening". So the mode has two targets:

- **Note** (default): one long continuous voice note. Everything said is turned into text while you speak; when you stop, the text is cleaned once (in chunks) and saved as one note.
- **Type**: each piece is cleaned and typed into the app you started in, as you pause.

Before this, a double press started a hands-free dictation limited to `MAX_SECONDS` (360 s) and sent as one recording. That mode is replaced (the hands-free flag is still used by the tray's "New voice note").

## How it behaves

### Start and stop
- **Start:** double-press the dictation shortcut (second press within 0.5 s, `DOUBLE_TAP_GAP`), or the tray items "Start listening" and, with a note shortcut set, "Start a note (Ctrl + Alt + N)", which shows the shortcut as set,, or the **note shortcut** below. Starting needs a key and a good server address like a dictation (`Engine._ready`).
- **End:** a double press again (a single press is ignored while listening, because one press can belong to another shortcut such as Ctrl+Win+arrows), the stop phrase, **Esc**, the tray item "Stop listening", the note shortcut (it ends whichever session is running), 60 minutes, or a dead microphone. Esc **ends and saves**; it does not discard (audio is never thrown away in this mode). *Changed on 2026-10-02 by [decision 0037](../decisions/0037-esc-cancels-and-the-hook-only-queues-keys.md): Esc now cancels the session (nothing more sent, no note; a session of 30 s or more keeps its audio for Recover).*
- **Stop phrase:** "stop listening" at the very end of a piece, any case, with an optional comma before it and punctuation after it. The phrase is cut from the text. The same words inside a sentence do not stop it. It is matched on the speech server's text, which was only tested with typed examples.
- **Quit** while a session runs ends it and waits up to 3 minutes for the note to be saved.
- **Note and Retry** (tray) are ignored while a session runs; a dictation cannot start while one runs.

### The two targets (setting `listen_target`, tray radio items "Keep listening: Note / Type")
- **Note:** the microphone audio is cut into pieces and each piece is transcribed in the background. At the end the whole text goes through `session.chunk_text` (chunks of about 350 words, cut at paragraph breaks, else sentence ends, else after 350 words; every word is in exactly one chunk) and each chunk through `core.process_text`, so the fidelity guard (decision 0030) falls back to the spoken words **per chunk**. One note is saved with `Engine.save_note` (same path as a voice note: `notes.add`, sync trigger, "Note saved" balloon). No app name is sent to the cleanup server.
- **Type:** each piece is cleaned like a dictation (`cleanup_min_words` applies, and the app's exe name is the app label, as for any dictation) and typed with `Engine.paste` **only while the focused window is the app the session started in** (`session.same_target`, exe names compared case-insensitively; an unknown or empty name on either side is a refusal, stricter than a single paste). Otherwise typing pauses, the pill says "Paused: wrong window", one balloon is shown, and the text is kept; it is saved as a note at the end ("What was not typed is saved as a note."). Typing resumes when the app is in front again. A Type session can be started only with the double press, because from the tray the foreground window is the taskbar; the tray item refuses with a message (the note shortcut always starts a Note session).

### Pieces and order (`windows/session.py`, pure, no hardware)
- `ListenSession(target)` cuts with `core.Segmenter(min 3 s, max 20 s)` (pauses of 0.6 s as for long dictations, shorter pieces than the 12 and 28 s of `stream_stt` so the text comes sooner). Pure-silence pieces are not sent. A silent gap of 3 s or more puts a blank line before the next piece in the note text; otherwise pieces are joined by a space.
- Pieces are transcribed one after the other (so the "previous text" hint, 150 characters, is in order) by `streaming.piece_text`, the helper `StreamingStt` now shares. Answers may arrive in any order in the pure class; `on_text` returns the texts that are complete in order.
- Contract: every piece sent is answered with `on_text`, with "" on failure. `stop()` flushes the rest of the audio, so nothing is lost at the end.
- From 55 minutes the pill says "Listening ends in N min."; at 60 minutes (`MAX_SECONDS`) the session ends itself and ignores later audio.
- Four threads, none of them the audio callback (which only queues bytes): feed (cuts audio and writes the buffer), stt, type (Type target only) and the caller of `stop`.

### Pill and tray
- A new pill state `listen`: a stop square and "Listening 5:07" (`overlay_mode.pill_clock`, also used by the meeting timer), or the message ("Paused: wrong window", the time warning). The pill is wider while listening (230 px against 132). The tray icon is the recording icon. The green check or the red ! flashes at the end, as for a dictation.
- Tray: "Start/Stop listening", "Start a note (<shortcut>)" (only with a note shortcut), "Keep listening: Note", "Keep listening: Type" (radio), "Recover listening session" (visible only when there is one).

### Note shortcut (task E5, setting `note_hotkey`, default `ctrl+alt+n`, empty = off)
- Press once: a session with the target **Note** starts, whatever `listen_target` says. Press again: it stops and saves. A press while a note is being saved does nothing.
- Valid: at least one of Ctrl, Alt or Win (Shift may join) and exactly one letter, digit or F1 to F12. The text is normalised (`Win + Alt + M` is saved as `alt+cmd+m`). Rejected: no modifier or Shift only, no main key or two, a repeated key, space, and **any combination that contains the dictation shortcut** (dictation would start as soon as those keys were down; a missing or invalid dictation setting counts as the default Ctrl+Win). The default Ctrl+Alt+N works with the default dictation shortcut; with the dictation shortcut set to the Ctrl + Alt preset the engine ignores the note shortcut and logs why, and Settings shows the reason.
- The main key is matched by its virtual-key code (pynput changes the key's char between press and release while Ctrl is held) and a key-down flag stops key repeat from toggling again.
- The keys are **not blocked**: the app in front still receives Ctrl+Alt+N. Settings says so. Settings row: Voice & audio, "Note shortcut" (bridge `set_note_hotkey`, `note_hotkey_problem`).

### Crash-safe audio buffer and recovery
- While a session runs its audio is appended to `%APPDATA%\Vox\listen\listen-<date>-<time>-<microseconds>-<target>.pcm` (raw 16 kHz, 16-bit, mono), unbuffered, so a killed process loses nothing already appended (no fsync: a power cut can still lose the last moments).
- The file is removed only after a clean end (the note saved, or the Type text typed or saved). If a piece failed to send, the note could not be saved or the process died, the file stays.
- A balloon at startup says a session did not finish; tray "Recover listening session" plays the newest file back into a Note session (even when it was a Type session), saves a note and then removes the file. A half sample left by a crash is trimmed.
- Failure handling: a piece that cannot be transcribed is reported once and the audio kept; a bug in the cleanup still saves the spoken words; a microphone that sends no block for 5 s ends the session through the normal path ("The microphone stopped sending sound..."), saving what was heard.

### Timing
`seg_end` and `seg_text` are two new marks in `windows/timing.py` (`MARKS`; Python only, not one of the six stages, the Java twin is untouched). The wait of each piece (end of speech to its text) is logged (`keep listening: piece N is text X ms after it ended`) and kept in `Listening.latency_ms`. It is not shown on the Speed card and not written to the history.

## Files
`windows/session.py` (pure: `ListenSession`, `same_target`, `chunk_text`, `parse_note_hotkey`, `SessionBuffer`, `recoverable`, `load_pcm`), `windows/listen.py` (`Listening`), `windows/engine.py` (hotkeys, tray, `start_listening`, `stop_listening`, `save_note`, `_ready`, `_open_mic`, `_save_setting`, recovery), `windows/streaming.py` (`piece_text`), `windows/overlay.py`, `windows/overlay_mode.py`, `windows/timing.py`, `windows/ui_app.py`, `windows/ui/index.html`, `windows/vox_core.py` (two settings). Tests: `tests/test_listen_session.py`, `tests/test_listen.py`, `tests/test_engine_listen.py`, `tests/test_note_hotkey.py`, plus additions to `tests/test_streaming.py`, `tests/test_timing.py`, `tests/test_overlay_mode.py` and `tests/test_ui_static.py`.

## Checklist without a phone (for a tester, on the PC; none of this has been run)
Start Vox from your own terminal (a GUI started from the coding agent's shell is invisible), key set, the right microphone chosen. Keep `%APPDATA%\Vox\vox.log` open to check the log lines.

1. **Start (Note).** Tap Ctrl+Win twice quickly. The pill widens and shows a stop square and `Listening 0:00` counting up; the tray icon turns red. The log says `keep listening started (target=note, ...)`.
2. **Speak and pause.** Say two or three sentences with a 1 s pause between them, then a 4 s silence, then one more sentence. The log shows `piece N is text X ms after it ended` for each piece (the number is the wait).
3. **End with a double press.** Tap Ctrl+Win twice again. The pill shows the sending dots, then the green check, and a balloon says `Note saved: ...`. Open Vox > Notes: one note with every word you said, a blank line where the 4 s silence was. Check that `%APPDATA%\Vox\listen\` has no `.pcm` file left.
4. **Single press is ignored.** Start again, press Ctrl+Win once: nothing changes. End it.
5. **Stop phrase.** Start, say a sentence and then "stop listening". It ends and saves; the phrase is not in the note. Start again and say "I said stop listening to him yesterday, and then carried on": it must not stop.
6. **Esc.** Start, speak, press Esc: it ends and the note **is** saved.
7. **Tray.** Tray icon > Start listening, speak, Stop listening: a note. Tray > "Keep listening: Type" ticked, then Start listening: refused with a message (it needs the app you type into).
8. **Type target.** With "Keep listening: Type" ticked, click into Notepad and double-press. Say a sentence and pause: the text appears in Notepad within a few seconds, with a space before the next piece. Switch to another window and speak: the pill says `Paused: wrong window` and one balloon appears (not one per piece). Switch back to Notepad: typing resumes (what you said while away is **not** typed). End the session: a balloon says what was not typed is saved as a note; open Notes and check it.
9. **Note shortcut.** Ctrl+Alt+N starts a note session (even with Type ticked); Ctrl+Alt+N again ends and saves it. Holding the keys does not toggle repeatedly. In Settings > Voice & audio > Note shortcut: type `m` (rejected, needs a modifier), `ctrl+win+n` (rejected, contains the dictation shortcut), `alt+m` (accepted, works after the toast); clear the box (off). Set the dictation shortcut to Ctrl + Alt: the note shortcut shows a reason and does nothing.
10. **Microphone lost.** During a session unplug the USB microphone or switch the device off in Windows sound settings. Within about 5 s a balloon says the microphone stopped sending sound, the session ends and the note with what you said before is saved.
11. **Server unreachable.** Start a session, turn the Wi-Fi off, speak, end it. A balloon says part of what you said could not be sent and the audio is kept. Turn the Wi-Fi on, tray > Recover listening session: a note is made and the `.pcm` file is gone.
12. **Crash.** Start a session, speak, end the Vox engine process in Task Manager, start Vox again. A balloon says a session did not finish; tray > Recover listening session makes the note.
13. **Quit.** Start a session, speak, then tray > Quit Vox: Vox waits and the note is saved before it closes.
14. **Long session.** Let one run for 10 minutes while you read something aloud: the pill keeps counting, no piece is lost, the log shows the pieces' waits. (The 55 and 60 minute limits are only unit tested.)

## Deviations and choices
- **Ending takes a double press.** A single press could belong to another shortcut, so one press is ignored while listening (plan: "second double-press").
- **Esc saves.** The plan only said Esc ends the session; for a long note a discard would lose an hour of speech, so it ends and saves.
- **Recovery and the startup balloon** are in the plan only as "offered for recovery after a crash"; the tray item and the file format are choices of this branch.
- **Recovery always makes a note**, also for a Type session (the typed target window is gone).
- **`same_target` is stricter than `paste._window_changed`** (an unknown window is refused, not allowed).
- **Latency (task E4):** only the two marks and the log line were built. No timing is shown in a card and nothing was tuned against a real server; the 3 s and 20 s piece lengths are the plan's numbers, not measured.

## Not verified
- Everything in the checklist above: no real microphone, no real Whisper server (the stop phrase was tested on typed text; real transcripts may add other punctuation or mis-hear it), no real desktop (typing into another app, the focus check, Task Manager kill, unplugging a microphone), the pill's look (only a hidden-Tk drawing check) and the tray items.
- The Segmenter's pacing was tested on synthetic tone and silence only. The buffer was tested by reading the file before `close()`, not with a real process kill, a power cut or a full disk.
- Whether the 3 s minimum piece gives text fast enough and whether many small requests hit a provider's rate limit on the free tier (each piece is one request, a 60 minute session at 3 to 20 s pieces is 180 to 1,200 requests).
- The Java side (nothing was changed); Android keep-listening (out of scope).
