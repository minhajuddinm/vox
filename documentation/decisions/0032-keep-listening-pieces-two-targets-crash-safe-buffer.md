# 0032. Keep listening: pieces, two targets (Note and Type), a crash-safe audio file

Status: Accepted (built on Windows, unit tested with fakes; never run with a real microphone or server, see [p9e](../specs/p9e-keep-listening.md)). Android keep-listening is out of scope for this round. The Esc rule of point 6 is superseded by [0037](0037-esc-cancels-and-the-hook-only-queues-keys.md): Esc now cancels.
Date: 2026-10-01

## Context

Request R7 of the v2 part 3 plan: the double-press mode of Ctrl+Win, which was a hands-free dictation of at most 360 s sent as one recording, should "keep listening" through long speech, fast, in the background, for long voice notes and a voice "text bot". Asked what the bot should do, the requester answered "wait what ??" and then "no ai chat only listening". The risks named in the plan: a session that outlives the app's focus, the PC sleeping, the microphone taken by another app, the typing target changing between sentences, and lost audio.

## Decision

1. **Keep listening is not an assistant.** There is no AI chat and no speech output. The double press starts a listening session with two targets, chosen by the setting `listen_target` (default `note`, a tray radio item): **Note** (everything said becomes one note) and **Type** (each piece is typed into the app the session started in).
2. **The audio is cut at pauses and sent in pieces while the person speaks.** The pure `ListenSession` uses the existing `Segmenter` with a 3 s minimum and a 20 s maximum (shorter than the 12 s and 28 s of long dictations, so the text comes sooner). Pieces are transcribed one at a time, in order, by `streaming.piece_text` (the same call as `StreamingStt`, with the end of the previous text as context). One request per piece. Pure-silence pieces are not sent.
3. **Note: clean once at the end, in chunks.** The whole text is cut at paragraph breaks, else sentence ends, else every 350 words, each chunk goes through the normal `process_text`, so the fidelity guard of [0030](0030-cleanup-keeps-the-spoken-words.md) protects every chunk separately and a failed chunk falls back to its spoken words. One note is saved the same way as a voice note. No app name is sent.
4. **Type: clean and type each piece, only in the chosen window.** `same_target` compares the exe name at the start with the exe name now and **refuses an unknown name**, unlike a single paste ([0029](0029-paste-checks-the-window-clipboard-default-off.md)) which lets an unknown window through, because a session types many times. In another window typing pauses, the pill says so, one balloon is shown, and the text is kept and saved as a note at the end. Type is started only by the double press (from the tray the foreground window is the taskbar).
5. **The audio is written to disk while it arrives** (`%APPDATA%\Vox\listen\*.pcm`, unbuffered appends) and removed only after a clean end. After a crash, a failed send or a failed save the file stays and the tray offers "Recover listening session", which turns it into a note. A microphone that sends nothing for 5 s ends the session through the normal path.
6. **Ending needs a deliberate act.** A double press (one press could belong to another shortcut), the phrase "stop listening" at the end of a piece, Esc (which **saves**, never discards), the tray, the note shortcut, or the 60 minute limit (a warning from 55 minutes).
7. **A second shortcut for notes** (`note_hotkey`, default Ctrl+Alt+N, empty = off) toggles a Note session whatever `listen_target` says. It is rejected when it contains the dictation shortcut, matches the key by its virtual-key code and does not block the keys.
8. **Android is out of scope.** The phone has note mode and the note bubble; no Java was changed and there are no golden rows.

## Consequences

- A long note is possible and little is lost: audio is on disk, the pieces are small, and a piece that cannot be sent is reported and the audio kept.
- **The audio of a session is on the PC as plain raw PCM until the session ends cleanly**, and indefinitely after a failure. Nothing deletes a left-over file except a successful recovery (or the user); the startup balloon only offers recovery. This is listed in [09-security-privacy.md](../09-security-privacy.md) and [12-known-issues-and-roadmap.md](../12-known-issues-and-roadmap.md).
- Each piece is one request: a 60 minute session is up to 1,200 requests (3 to 20 s pieces), which could hit a free tier's rate limit. Not measured.
- The Note cleanup runs only at the end, so a note appears some seconds after stopping, longer for a long note (one cleanup request per 350 words, one after the other).
- The stop phrase is matched on the speech server's text; a mis-heard or differently punctuated phrase will not stop the session (it ends with the other ways). A person who really says "stop listening" at the end of a sentence in a note ends it.
- The hands-free dictation by double press is gone; the flag remains for the tray's "New voice note".
- The pure part (`session.py`) has no hardware and is tested without a microphone; the running part (`listen.py`) is tested with fake speech and window calls. Real audio, real focus changes and real servers are not covered.

## Alternatives considered

- **An AI chat or "text bot".** Not wanted ("no ai chat only listening"); it would also need speech output and a different privacy story.
- **One long recording sent at the end.** Simple and what the old mode did, but the wait grows with the length, a provider's file size limit applies, and a failure loses everything. Pieces give text while speaking and a recoverable middle.
- **Streaming speech recognition (a socket).** Not available on the OpenAI-compatible servers Vox uses; would make a server-specific path.
- **Cleaning each piece for the Note target.** Cheaper at the end but a cleanup that sees only one piece cannot decide paragraphs or lists, and the guard works better on larger chunks; one cleanup per chunk at the end also leaves the typed text path for Type.
- **Reusing `paste._window_changed` unchanged for Type.** It lets an unknown window through; for many typing actions that could type a meeting into the wrong app.
- **Esc discards the session.** Matches Esc cancelling a dictation, but an hour of speech would be lost by a stray key.
- **Keeping the audio in memory only** (as failed dictations on Windows are). A crash or a failed save would lose a long note; a file costs privacy exposure but keeps the words.
