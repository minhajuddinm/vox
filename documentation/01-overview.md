# 1. Overview

## What Vox is

A free, open-source replacement for paid voice-typing tools. The user speaks; Vox turns the audio into text, cleans it up, and types it into the focused text field of any app.

| | Windows | Android |
|---|---|---|
| Trigger | Hold a shortcut (default Ctrl+Win); double-tap for hands-free | Tap the floating mic bubble; tap again to send |
| Output | Pasted with Ctrl+V into the focused app | Inserted into the focused text field through the accessibility service |
| Extra | Meeting notes (mic + PC audio), calendar link, history, stats, voice notes, "About you" context, optional sync through your own relay | History, dictionary, per-app styles, "About you" context |
| Language | Python 3.13 | Java (no Gradle, no Kotlin), HTML for the screens |

Both apps talk to an OpenAI-compatible HTTP API for two jobs: speech-to-text (Whisper) and text cleanup (a chat model). By default that is Groq with the user's own free API key. The user can instead enter their own server address.

## Who made it

Original author: Muhammad Minhajuddin (repository `minhajuddinm/vox`, commits up to `9aa4d28`). An improvement series (17 commits, merged as PR 1) was added afterwards by Yuvraj Singh with Claude Code assistance. See [../CHANGELOG.md](../CHANGELOG.md). The v2 series followed (PRs 4 to 16: providers and model pickers, connection warm-up, About you, live level, voice notes, the relay and its sync, streaming of long recordings, lighter build).

## Design goals (as built)

1. **Free to run.** No Vox server, no subscription. Each user brings their own key (or their own server).
2. **Nothing is lost.** A dictation that fails to send is kept and can be retried (Windows tray menu, Android notification).
3. **Private by default.** Only the app name (never a window title) goes to the model; no accounts; history can be turned off; the Windows API key is protected by the Windows login.
4. **Works with any app.** Text goes in through paste (Windows) or accessibility (Android), so no per-app integration exists.
5. **Same behaviour on both platforms.** The cleanup rules exist twice (Python and Java) and are kept identical by a shared test file (see [06-pipeline.md](06-pipeline.md)).

## Non-goals and limits

- No iOS or macOS or Linux app. The Windows code has some non-Windows branches only so tests can run on Linux CI.
- No Vox cloud. The only sync is optional and goes through a relay the user runs (see [14-relay.md](14-relay.md)); today only the Windows app uses it (voice notes and profile), the phone does not sync yet, and dictation history and meetings never sync.
- Android meeting notes do not exist.
- Speech is not processed on the device: audio always goes to the configured server.
- No Vox-run service of any kind. The maintainer receives no data.

## Where to go next

- How it works: [02-architecture.md](02-architecture.md)
- What exists: [08-features.md](08-features.md)
- What is broken or missing: [12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md)
