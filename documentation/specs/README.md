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
| [p7a-relay-server.md](p7a-relay-server.md) | The relay server (no clients yet) | Implemented |

Later specs (write each just before its work starts): key-down speed, "About you" context, live voice level, lightweight build, notes store, Android note mode, relay, design and privacy refresh.
