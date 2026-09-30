# 0003. Android: accessibility overlay bubble and text insertion

Status: Accepted
Date: 2026-09-27 (original design; recorded 2026-09-30)

## Context

Android has no global hotkey and no system-wide "type into any app" API for a normal app. Vox must dictate into any text field, mostly on the phone.

## Decision

Use an accessibility service that (a) draws a floating bubble as an accessibility overlay (`TYPE_ACCESSIBILITY_OVERLAY`, so no "draw over other apps" permission), (b) tracks the focused editable field, and (c) inserts text with `ACTION_SET_TEXT`, falling back to clipboard paste. Recording runs in a separate microphone foreground service because Android only lets that start from a visible activity; an invisible `TrampolineActivity` bridges the gap.

## Consequences

- Works in almost every app, with no per-app code.
- Users must enable the accessibility service manually and read a system warning; the service description explains what is read.
- `ACTION_SET_TEXT` replaces the whole field, which can lose rich text or race with typing (open issue, see [../12-known-issues-and-roadmap.md](../12-known-issues-and-roadmap.md)).
- Some apps mark fields as password fields or block accessibility input; Vox refuses password fields and falls back to the clipboard.
- Tap-to-toggle is used instead of hold-to-talk (**inferred**: a floating bubble cannot reliably receive a long hold while a text field keeps focus).

## Alternatives considered

- A custom keyboard (IME): a different install and switching burden; not chosen by the author.
- Share-sheet or intent-based input: cannot type into arbitrary fields.
