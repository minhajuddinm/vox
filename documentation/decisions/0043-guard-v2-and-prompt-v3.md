# 0043. Guard v2 and prompt v3: judge the cleanup by what it may change, send only the terms it needs

Status: Accepted (tested with golden rows, a labelled set and fakes; not yet on real speech or a real model: the tuning round uses `tools/bench_*` on the user's own clips)
Date: 2026-10-05

## Context

The v2 review measured the word-keeping guard ([0030](0030-cleanup-keeps-the-spoken-words.md)) on 189 labelled cleanups. It was right on about 66% in Light and 58% in Standard:
- It rejected good answers: self-corrections ("thursday no wait friday"), apostrophes, some dates and money.
- It accepted bad answers: an appended reply ("... France? Paris."), padding words, a dropped "not".

The prompt put About you, up to 150 dictionary terms and the style line before the fixed rules, so providers could cache almost none of it. Research (W2 in the review notes) found that sending the whole dictionary made name errors worse.

## Decision

- **Guard v2** (`vox_core` fidelity functions, `Fidelity.java`, golden kinds `guard`, `lcs`, `pkey`):
  - Keeps the number, money and date merging.
  - Adds a self-correction cue window (Standard only) and apostrophe-insensitive comparison. Contraction changes are still rejected.
  - Adds an insertion budget for unexplained new words, protected words (a dropped negation or number always rejects, whatever cue is near) and prompt-echo detection.
  - Accepts `EMPTY` for filler-only input.
  - One shared LCS replaces difflib, so Python and Java agree.
- **Prompt v3** (`system_prompt`, `ApiClient.systemPrompt`):
  - The static part comes first (role, rules, 8 targeted examples), then About you, the matched terms, learned rules, the style line and the app.
  - The prompt says plainly that the speaker is never talking to the model, and that Hinglish stays in its script.
  - Filler-only input is answered `EMPTY`, which becomes "".
- **Term selection** (`select_terms`, `Terms.select`): at most 20 dictionary terms whose sound key or spelling is close to a 1-3 word window of the transcript.
- **Whisper prompt v2**: a short sentence of people and recently learned terms, budgeted at about 160 estimated tokens (one named setting), trimmed by whole terms.
- **Not changed:** model, temperature 0, reasoning effort. All thresholds are named constants, so the tuning round can move them.

## Consequences

- **Labelled set:** guard v2 is right on 98.9% (Light) and 97.9% (Standard), with no bad cleanup accepted on the held-out part. This set was written by hand, not taken from real model output.
- **Changed expectations:** the old golden rows whose expectation changed are listed in the stream reports (padding now rejected, multi-word drops rejected).
- **Prompt size:** about 785-840 static tokens. A large dictionary sends fewer input tokens; an empty one sends slightly more (the examples).
- **Benchmark baseline:** the old prompt and guard stay measurable in `tools/bench/legacy.py`.

## Alternatives considered

- **Asking the model for a list of edits (JSON) and checking each one:** a stronger guarantee on long texts, but more work and no structured output with streaming. Revisit if guard v2 is not enough on real clips.
- **Sending the whole dictionary but cached:** this still puts unrelated names in front of the model, which research found harmful.
- **A larger Whisper prompt (about 220 tokens):** Whisper reads only the last 224 real tokens and the estimate undercounts rare names.
