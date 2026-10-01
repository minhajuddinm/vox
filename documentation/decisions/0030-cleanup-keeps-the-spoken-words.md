# 0030. Cleanup keeps the spoken words: a fidelity guard, a Light default, the raw text always kept

Status: Accepted (the guard and the prompt rewrite are built; the Light default and "Use raw" are decided but not built yet, see [p9a](../specs/p9a-cleanup-keeps-my-words.md))
Date: 2026-10-01

## Context

Yuvraj's report (round 3 of the v2 plan, 2026-09-30): the AI cleanup sometimes shortens or summarises what he said, so his own words are lost. The cause is in two places. The prompt asks the model to "rewrite it as the text the speaker intended to type", which invites paraphrase, and `looks_valid` ([0013](0013-raw-fallback-when-cleanup-fails.md)) only rejected an empty answer or one longer than `1.6 x raw + 40` characters, so a short summary of a long dictation was accepted and typed. Nothing compared the answer with what was said. Small chat models (`gpt-oss-20b` was named) tend to compress.

## Decision

1. **The cleanup contract.** The cleanup may change punctuation, capitalisation, spelling, obvious grammar slips, paragraph breaks and list formatting. In **Light** strength it keeps every spoken word except pure noises (`um`, `uh`, `er`, `erm`, `ah`, `hmm`). **Standard** strength also drops fillers (`like`, `you know`, `i mean`, `sort of`, `kind of`), immediate repeats and false starts. Neither strength summarises, merges, reorders or paraphrases.
2. **A guard enforces it, in code, in both apps.** `fidelity_ok(raw, cleaned, strength)` (`windows/vox_core.py`) and `Fidelity.ok` (`android/src/com/minhaj/vox/Fidelity.java`) compare the two texts as multisets of word tokens and are tied together by the golden kinds `fidelity`, `tokens` and `recall`. `looks_valid` calls it, so a cleanup that lost words is handled like a failed cleanup: the words as spoken are used, with spoken commands applied and the dictionary replacements. The thresholds are in [06-pipeline.md](../06-pipeline.md) and [p9a](../specs/p9a-cleanup-keeps-my-words.md). Integer arithmetic only, so both languages agree.
3. **Light is the default strength** (setting `cleanup_strength`, `light` or `standard`, per device, not synced). The new prompt (task A2) takes the strength and the code passes the same value to the prompt and to the guard. Until the setting row exists (task A3) it is `standard` when the key is unset, on both apps, so nobody gets Light by accident before the setting, the history flag and "Use raw" are there.
4. **The raw transcript is always kept** next to the cleaned text in the history, with a one-tap "Use raw" (planned, task A3). Cleanup is a convenience layered on text the user can always get back.
5. **Nothing is measured by feel.** A local benchmark (`tools/bench_cleanup.py`, planned, task A5) scores a provider and model on the same metrics (word recall, added-word rate, length ratio, structure, About-you term accuracy, latency) with the user's own key; no default model changes without its numbers.

## Consequences

- A summary or rewrite can no longer be typed as if it were the user's words; the worst case is the raw words with spoken commands applied (no model punctuation), plus the existing "cleanup did not work" notice.
- A legitimate cleanup can be rejected too: a list conversion that drops `first`, `second`, `third`, and a translation all count as lost words. The guard understands spoken numbers up to billions (thousand, lakh, million, crore, billion), ordinals up to thirty-first, `half past three`, decimals, times (`three thirty p m`), spoken emails (`john at gmail dot com`) and currency and percent words; anything else (fractions, `out of`, `quarter past`) is conservative on purpose and falls back to the raw words.
- **Known trade-off in Standard:** it has only a percentage (at least 85% of the words after fillers and repeats are removed, cleaned at least 60% as long) and no absolute cap, because it exists to drop false starts and self-corrections and a cap would reject legitimate cleanups. In a long dictation (1,000+ words) Standard can therefore lose a few percent unseen. **Light** has an absolute cap of 12 missing words as well as the 97% rule, because 97% of a 1,000-word dictation is a whole paragraph.
- A long dictation that is cleaned in chunks is checked as one text; a whole chunk dropped by the chunking code would be caught only by the totals above (chunked cleanup does not exist yet; branch E).
- The guard is a rule on words, not on meaning: in a text of about 100 words a cleanup that turns `can come` into `can't come` still passes (checked by hand with `fidelity_ok`: one lost word is inside the allowed share), so a changed meaning in one word is not caught. It cannot see added words either, except through the length floor and the existing 1.6x cap.
- The prompt, About you and strength changes (tasks A2 and A3) are separate commits and can be tuned without touching the guard. The prompt asks for "- " bullets that keep the words first, second, third, never a numbered list, because the guard counts a spoken ordinal that became a digit as a lost word.

## Alternatives considered

- **Only fix the prompt.** Cheap, but a model that ignores the prompt once still gets typed. The guard is the safety net, the prompt is the first line.
- **A similarity score (edit distance or embeddings).** Heavier, not identical between Python and Java, and an embedding needs a network call or a model on the phone. A multiset word count is stable, explainable and the same in both languages.
- **Reject only when the output is shorter than N% of the input.** A summary can pass; a rewrite that keeps the length but changes the words can pass. Recall of the spoken words catches both.
- **Cleanup off by default.** Loses capitalisation, punctuation and list structure that he wants; the guard plus the Light default keep those.
- **Ask the model to grade itself.** An extra network call, adds latency and trusts the model that failed.
