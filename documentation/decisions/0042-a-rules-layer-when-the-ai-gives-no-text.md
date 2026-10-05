# 0042. A rules layer when the AI gives no text; short phrases skip the AI

Status: Accepted (tested with golden rows and fakes, not on real speech)
Date: 2026-10-05

## Context

[0013](0013-raw-fallback-when-cleanup-fails.md) types the raw words when the cleanup fails, and the v2 review (M1 section 6.7) found that text rough: "Um" stays, a spoken "comma" or "period" stays a word, and a skipped short phrase or a very casual chat gets no style at all. The cleanup also has a read cap now (`cleanup_read_ms`), so a slow answer ends in that same rough text. Its alternative "convert all spoken punctuation deterministically" was turned down because "period" and "comma" are also ordinary words.

## Decision

- A deterministic rules layer (`windows/rules_layer.py`, `RulesLayer.java`, golden kinds `rulelayer` and `fallback`) gives the text whenever the cleanup was wanted (on, style not `raw`) but gave none: a phrase under `cleanup_min_words`, an error or timeout, or an answer the fidelity guard rejected. `fallback_text(raw, style, strength)` keeps its old call (`fallback_text(raw)` is the neutral style in Light).
- It never adds a word. In Light it drops only pure noises (um, uh, er, erm, ah, hmm) and spoken punctuation commands; the commands are taken only where they cannot be the noun (not after a, the, trial, notice..., `period` only at the end or before a mark or line break). Standard also takes a typed-value self-correction (`thursday no wait friday`: both values of the same kind and different). Then capitals and the final mark for the style; very casual adds no capitals and no final period.
- Cleanup off and the `raw` style keep the old text (spoken line breaks only): the user asked for the words as spoken. Code mode keeps its own path.
- `cleanup_min_words` default 3 -> 4, so three-word phrases skip the network and get the rules layer. No list-cue exception: a list needs more than three words anyway.
- Design and prototype from the v2 review D1 section 4; only the part that cannot change meaning was taken. Numbers, times, emails, contractions, stutters and the sounds-like dictionary pass of the prototype are left to the AI.

## Consequences

- A failed, slow, rejected or skipped cleanup now reads like typed text: "um so i was uh thinking comma we should go tomorrow period" -> "So I was thinking, we should go tomorrow."
- The fallback text now ends with a full stop (casual: only after 12 words) and spoken punctuation names are converted; golden rows and tests that pinned the old text were updated (listed in the stream-4 report).
- Some command uses are missed on purpose (`period` mid-sentence, `colon`, a bare `no` as a correction cue), and `I was, um, thinking` keeps one comma.
- Rules are ASCII only, so Hindi and Hinglish words are never changed; a Devanagari sentence gets no final mark.

## Alternatives considered

- The whole D1 prototype (numbers, times, emails, contractions, stutter removal, sounds-like spelling): each can change or drop a word that was meant; left to the AI.
- Applying the rules layer when cleanup is off: that setting is how people get the words as spoken.
- A bare "no" as a correction cue: "one no one knows" would lose words.
