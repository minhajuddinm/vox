# Spec P3: "About you" context

Status: Implemented on branch `feat/about-you`. Date: 2026-09-30. Decision record: [0018](../decisions/0018-about-you-context-in-the-prompt.md).

## Goal
The user writes one block of background about themselves; cleanup uses it on every dictation.

## Design
`user_context` setting; "About you" card on the Dictionary page of both apps (text box, character count against 8,000); `system_prompt(..., context)` / `systemPrompt(..., context)` add the fenced block; `GroqClient.cleanup` takes the context; `DictationService` passes `Prefs.userContext()`. Golden rows `context` and `promptctx`. Unit tests in `tests/test_user_context.py`.

## Not in this step
Local fuzzy dictionary matching before the LLM, the `EMPTY` sentinel and stricter output checks, per-app contexts and modes, a token estimate in the UI.

## Done when
Tests and CI pass; on a device the text box saves and a dictation that depends on the context (a project name, spelling preference) comes out accordingly. Not verified on a device yet.
