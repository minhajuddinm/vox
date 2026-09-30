# 0018. "About you" context: constant text, fenced, capped

Status: Accepted
Date: 2026-09-30

## Context

Users want to give the cleanup model standing background (who they are, projects, style, terms). That text is sent with every cleanup request, so it must be cheap (prompt caching works on an exact, constant prefix), it must not let the user's own text break the prompt, and it must not be treated as instructions to follow or text to output.

## Decision

- One free-text setting (`user_context` on Windows, the `user_context` preference on Android), edited on the Dictionary page under "About you".
- It goes into the system prompt after the fixed rules and the dictionary terms and before the style and app lines, inside `<about_speaker>` tags, introduced as reference material that is never output and never instructions. The constant parts come first so a provider's automatic prefix caching can reuse them; the variable parts (style, app, transcript) come last.
- `clean_context` / `cleanContext`: normalise line endings, remove our own `<about_speaker>` tags, trim, cap at 8,000 characters (about 2,000 tokens; Groq's free tier allows about 8,000 tokens a minute). Both platforms are held to the same rules by the `context` and `promptctx` rows of `spec/golden.txt`.
- The text goes only to the cleanup server; it is not sent to speech-to-text.

## Consequences

- Cleanup requests are longer by up to the cap; with caching the extra cost is small on providers that cache.
- Editing the text invalidates the cache once.
- The cap counts characters; Java counts UTF-16 units, Python code points, so text with emoji at the cut could differ by a character (not covered by the golden file).

## Alternatives considered

- Putting the context into the Whisper prompt: limited to about 224 tokens and it cannot remove fillers or apply corrections.
- Per-app contexts: later, as part of per-app modes.
