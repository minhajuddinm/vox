# Design specs

One design spec per planned sub-project, written **before** the code. A spec says what will be built, the interface, the decisions, the steps and how to know it is done. When the work ships, the spec stays as history and the pages in `documentation/` (01-13) describe the real behaviour. Decisions that a reader would question get an ADR in [../decisions/](../decisions/README.md).

The roadmap these come from (v2: any provider, lighter, faster, personal context, live level, voice notes, relay) is in the maintainer's notes; the order is:

| Spec | Sub-project | Status |
|---|---|---|
| [p1-providers-and-models.md](p1-providers-and-models.md) | Any provider, per-role server, model list and Test button | Implemented (PR 5) |
| [p2a-keydown-warmup.md](p2a-keydown-warmup.md) | Warm connections at key-down | Implemented (PR 6) |
| [p3-about-you-context.md](p3-about-you-context.md) | "About you" context in cleanup | Implemented (PR 7) |
| [p4-live-voice-level.md](p4-live-voice-level.md) | Live voice level on the pill and bubble | Implemented (PR 8) |
| [p5-voice-notes-windows.md](p5-voice-notes-windows.md) | Voice notes on Windows: store, search, recording | Implemented (PR 11) |
| [p7a-relay-server.md](p7a-relay-server.md) | The relay server (no clients yet) | Implemented (PR 12) |
| [p7b-relay-portable-and-web-page.md](p7b-relay-portable-and-web-page.md) | Relay on a Raspberry Pi, with a management web page | Implemented (PR 13) |
| [p7c-windows-sync-client.md](p7c-windows-sync-client.md) | Windows sync client for voice notes | Implemented (PR 14) |
| [p7d-profile-sync.md](p7d-profile-sync.md) | Profile sync (About you, dictionary, people, optional keys) | Implemented (PR 15) |
| [p7f-relay-proxy.md](p7f-relay-proxy.md) | The relay as the AI server (proxy mode, both apps) | Implemented (tested against stand-in servers only) |
| [p2b-stream-long-dictations.md](p2b-stream-long-dictations.md) | Send long recordings in pieces while speaking | Implemented |
| [p8c-quick-wins.md](p8c-quick-wins.md) | Quick wins: one Java test runner and compile check, `GroqClient` renamed `ApiClient`, `cleanup_min_words`, the relay run from the Windows app | Implemented (not run as a built exe or on a device) |
| [p6-android-note-mode.md](p6-android-note-mode.md) | Android note mode: faster start, note bubble, notification, quick settings tile, notes store | Implemented (not run on a device) |
| [p7e-android-sync.md](p7e-android-sync.md) | Android relay sync and profile merge | Implemented (not run on a device) |
| [p8b-design-refresh.md](p8b-design-refresh.md) | Design and privacy refresh: shared UI parts, regrouped settings and Status card, result flash on the pill and bubble, safer paste, privacy text | Implemented (not seen on a screen, phone or packaged build) |
| [p9b-measure-and-speed-up.md](p9b-measure-and-speed-up.md) | Timings of every dictation and the Speed card on both apps; faster Android (warm connections, pieces while recording, m4a upload, bounded cleanup, shorter timeouts) | Implemented (not run on a phone, a real server or the built exe) |

Later specs (write each just before its work starts): lightweight build, cleanup fidelity and structure (see the roadmap in [../12-known-issues-and-roadmap.md](../12-known-issues-and-roadmap.md)).
