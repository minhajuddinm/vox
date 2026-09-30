# 0004. Windows: engine process plus window process

Status: Accepted
Date: 2026-09-27 (original design; recorded 2026-09-30)

## Context

The Windows app needs something that is always listening for the shortcut (cheap, no visible window) and a rich window for history, dictionary and meetings. Tk (used for the recording pill) must run on the main thread of its process (stated in `windows/overlay.py`), and pywebview's `webview.start()` also runs on the main thread of its process (**inferred** to be why the window is separate).

## Decision

Run two processes from the same program. `Vox.exe` is the engine: tray icon, hotkey listener, recording, the Tk pill on the main thread, and a localhost control server. `Vox.exe --window` shows the pywebview window. They share state through files in `%APPDATA%\Vox` (the engine reloads `config.json` when its modified time changes) and through the engine's control server (`127.0.0.1`, random port, per-run token in `engine.json`) for live meeting operations. Named mutexes keep each process to one instance.

## Consequences

- The window can crash or be closed without affecting dictation.
- Settings changes need no restart (about one second).
- There is no in-process state sharing; anything the window needs from the engine must be a file or a control route.
- The two processes must never both hold the same in-memory truth: for example meeting status lives only in the engine and the window polls `/meeting/status`.
- Launching the window from an environment with no visible desktop makes WebView2 fail ("Invalid window handle"); see [../11-logs-and-diagnostics.md](../11-logs-and-diagnostics.md).

## Alternatives considered

- One process with the window in a thread: blocked by both toolkits wanting the main thread (**inferred**).
- A native (C# / WPF) app: not chosen by the author.
