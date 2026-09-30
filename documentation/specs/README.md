# Design specs

One design spec per planned sub-project, written **before** the code. A spec says what will be built, the interface, the decisions, the steps and how to know it is done. When the work ships, the spec stays as history and the pages in `documentation/` (01-13) describe the real behaviour. Decisions that a reader would question get an ADR in [../decisions/](../decisions/README.md).

The roadmap these come from (v2: any provider, lighter, faster, personal context, live level, voice notes, relay) is in the maintainer's notes; the order is:

| Spec | Sub-project | Status |
|---|---|---|
| [p1-providers-and-models.md](p1-providers-and-models.md) | Any provider, per-role server, model list and Test button | Implemented (PR 5) |
| [p2a-keydown-warmup.md](p2a-keydown-warmup.md) | Warm connections at key-down | Implemented |

Later specs (write each just before its work starts): key-down speed, "About you" context, live voice level, lightweight build, notes store, Android note mode, relay, design and privacy refresh.
