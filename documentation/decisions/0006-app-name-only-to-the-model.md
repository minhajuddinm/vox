# 0006. Send the app name, never the window title

Status: Accepted
Date: 2026-09-29

## Context

The original Windows engine read the focused window's title and put it in the cleanup prompt ("the text will be typed into the app: <title>") and in history. Window titles often contain private text (document names, email subjects, chat partners). The model only needs the app to choose a tone.

## Decision

`engine.foreground_app` returns only the executable name (for example `slack.exe`). That name is the app label in the prompt and the `app` field in history. The `title` field is no longer saved. Android already sent only the app's display name.

## Consequences

- Less private data leaves the PC and less is stored.
- The model has slightly less context about what is being typed (for example the document name); per-app styles still work because they are keyed by exe name.
- The privacy page states that the app name is sent for tone.

## Alternatives considered

- Keep the title but let the user opt out: more surface and settings for little benefit.
- Send nothing about the app: would lose tone matching.
