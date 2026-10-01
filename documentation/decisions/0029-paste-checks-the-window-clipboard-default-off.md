# 0029. Paste checks the window; the clipboard default is off

Status: Accepted
Date: 2026-09-30

## Context

Windows dictation pastes with Ctrl+V into whatever has focus when the text is ready. Cleanup can take a second or more, so the user may have switched windows by then, and the text would land in the wrong app. Two other problems: the old clipboard was restored after a fixed wait, which overwrote something the user had copied in the meantime; and `keep_clipboard` defaulted to true when the key was absent, so every dictation stayed on the clipboard where clipboard history and other apps can read it. Android already types only into the app the dictation started in ([0003](0003-android-accessibility-bubble.md)).

## Decision

- `windows/paste.py` `paste_text` compares the focused window's exe name with the one remembered at record start (case-insensitive; the title is never read, [0006](0006-app-name-only-to-the-model.md)). If it differs it copies the text, sends no Ctrl+V and returns `"copied"`; the engine says "Copied; the window changed" and flashes the red !.
- When it cannot tell (no target remembered, no foreground window name, the lookup raised) it pastes anyway: a text typed into a window that may be wrong is better than a text lost.
- It waits for Shift, Ctrl, Alt and Win to be up (reading the real key state, 2 s cap) before Ctrl+V.
- The old clipboard text is put back only when `keep_clipboard` is off, it could be read, and the clipboard still holds our text after the wait.
- `keep_clipboard` is a real `DEFAULT_CONFIG` key, default **false** (the settings page used `!== false`, which encoded true).

## Consequences

- Text never goes to a window the dictation did not start in; in that case it stays on the clipboard for the user to paste.
- A user who never touched the setting changes from "dictation stays on the clipboard" to "old clipboard comes back". A saved `true` is kept. This is a behaviour change for existing configs and is in the changelog and doc 07.
- "Retry last dictation" compares against the window of the last recording started, which is normally the failed dictation's window but not guaranteed.
- If the clipboard is locked and setting it raises, the generic error path handles it and the text is lost (not changed here).
- Not tried with a live dictation on the user's PC; the exe lookup was probed once and agrees with psutil apart from case.

## Alternatives considered

- Always paste: fast, but puts text into the wrong app.
- Refuse and keep the text only in Vox: the user would have to find it; the clipboard is where they look.
- Compare window handles instead of exe names: stricter, but a program that opens a new window for the same task would count as changed. Exe names are what the rest of Vox already uses.
- Keep `keep_clipboard` on by default: simpler to explain, but the dictated text stays readable in clipboard history.
