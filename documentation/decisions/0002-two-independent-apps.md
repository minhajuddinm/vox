# 0002. Two independent apps (Python and Java)

Status: Accepted
Date: 2026-09-27 (original design; recorded 2026-09-30)

## Context

Vox targets Windows (hotkey, paste, tray, meeting notes) and Android (bubble, accessibility). The platforms share almost no system APIs.

## Decision

Build a Python app for Windows and a Java app for Android with no shared runtime code. The screens are HTML in both (pywebview on Windows, a WebView on Android). (Reasons **inferred**: each platform needs native integration; a cross-platform framework would still need native code for hotkeys and accessibility.)

## Consequences

- The dictation pipeline and prompts exist twice and can drift. [0007](0007-shared-golden-file.md) adds a shared test file to catch that.
- Each app can be built and released without the other, but `build.yml` builds both on every tag.
- Features do not have to exist on both sides (meeting notes are Windows only).

## Alternatives considered

- A cross-platform UI framework (Flutter, Electron, Kotlin Multiplatform): not chosen by the author; would add build weight and still need native glue.
- Shared logic through a compiled library: rejected for this size of project.
