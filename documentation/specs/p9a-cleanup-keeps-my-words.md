# Spec P9a: cleanup keeps my words (fidelity guard, prompt, strength, benchmark)

Status: **Partly implemented.** Branch `feat/p9a-fidelity` of the v2 part 3 plan (`J:\Projects\.notes\PLAN-vox-v2-part3-2026-10-01.md`, branch A). Date: 2026-10-01. Decision: [0030](../decisions/0030-cleanup-keeps-the-spoken-words.md). Follows [p3](p3-about-you-context.md) (the About you text) and [0013](../decisions/0013-raw-fallback-when-cleanup-fails.md) (the raw fallback). Requests R1, R2, R3 and R6 of the part 3 notes.

| Task | What | State on this branch |
|---|---|---|
| A1 | Fidelity guard in both apps | **Built and tested** (three commits; see "Verification done") |
| A2 | Prompt rewrite, About you first, structure rules | **Not built** |
| A3 | `cleanup_strength` setting row, "Use raw" in history, guard wiring in the services | **Not built** (the guard itself is wired in; the setting has no row and no default of Light yet) |
| A4 | Dictionary really applied (fuzzy term pass) | **Not built** |
| A5 | Benchmark harness `tools/bench_cleanup.py` | **Not built** |
| A6 | This page, ADR 0030, the doc updates | Done in this commit |

Everything below under "Design" for A2 to A5 is the plan, not behaviour you can find in the code today. The sections marked **Built** describe code that exists.

## Goal
1. A cleanup that shortened, summarised or rewrote what was said is never typed as if it were the user's words (R1).
2. The cleanup prompt reads as a formatter's brief, puts About you first, and states structure rules (paragraphs and lists only where the speaker enumerates) (R1, R2, R3).
3. Two strengths: **Light** (keep every word) is the default, **Standard** also removes fillers and false starts. The raw transcript stays available (R1).
4. The dictionary's terms are really applied, even when the model or speech-to-text spells them slightly wrong (R3).
5. A repeatable, local benchmark says which cleanup model keeps the words and how fast it is (R6, and the "fastest cleanup model" ask).

## Design

### A1. The fidelity guard (Built)
Pure rules in `windows/vox_core.py` (Java twin: `android/src/com/minhaj/vox/Fidelity.java`), called from `looks_valid(raw, cleaned, strength="light")` / `ApiClient.looksValid(raw, cleaned, strength)`.

- **Tokens.** `word_tokens(text)`: lowercase; letters, digits and combining marks are word characters (Hinglish and Devanagari work); everything else separates words, so punctuation, newlines and bullet markers do not matter; an apostrophe inside a word stays (a curly one counts as straight).
- **Allowances applied to the raw words before comparing**: spoken commands (`new line`, `new paragraph`, `question mark`, `comma`, `period`, `colon`) are not expected; `dollars`, `euros`, `pounds`, `rupees`, `percent`, `degrees` (and the singular) count as kept when cleaned has the symbol; a spoken `at` is kept when cleaned has an `@` and a spoken `dot` when cleaned has a dot between two word characters (one allowance per symbol, so an ordinary `at` still counts); spoken numbers equal digits (`twenty five` = `25`, `one hundred and five` = `105`, `a thousand` = `1000`, `two thousand twenty six` = `2026`, `three point five` = `3.5`, `p m` = `pm`), and runs of digit groups join (`five five five one two` = `555-12`). Millions and billions are not understood.
- **Light** (every strength value except `standard`): pure noises (`um`, `uh`, `er`, `erm`, `ah`, `hmm`) may go. At least 97% of the other raw words must be in cleaned, at most 12 of them (`LIGHT_MAX_MISSING`) may be missing in total, and cleaned must have at least 90% of the raw word count minus one.
- **Standard**: fillers (`like`, `you know`, `i mean`, `sort of`, `kind of`) and immediate repeats may go too; at least 85% of the rest must be in cleaned and cleaned must be at least 60% as long. No absolute cap (known trade-off, ADR 0030).
- Under four words the length rule is skipped. An empty answer fails. The existing length ceiling (`1.6 x len(raw) + 40` characters) still applies in `looks_valid`.
- Comparison is a multiset (order ignored, repeats counted). Integer arithmetic only, so Python and Java agree. `word_recall(raw, cleaned)` gives the same measure as a number and is what the benchmark (A5) reuses.
- **On failure** `process_text` records `cleanup_error = "the cleanup answer looked wrong"` and uses the words as spoken: `apply_spoken_commands(raw)` then the dictionary replacements, the same as a failed call ([0013](../decisions/0013-raw-fallback-when-cleanup-fails.md)); the existing notice tells the user.
- **Strength today.** `process_text` reads `cleanup_strength` and treats an unset value as `standard`; `DictationService.send` passes `"standard"`. Both are deliberate until A2 and A3 land: the current prompt removes fillers, and a Light guard would reject its answers. There is no Settings row for the key, so only a hand-edited `config.json` can switch Windows to Light.
- Tests: golden kinds `fidelity` (strength, raw, cleaned, expected; 72 rows), `tokens` (14) and `recall` (21), run by `tests/test_parity.py` and `ParityTest.java`; `tests/test_cleanup_fidelity.py` (94 tests: tokens, recall, numbers, symbols, Light and Standard, property checks that adding only whitespace or bullet markers never lowers recall, a 1,500-word dictation, `looks_valid`, `process_text` fallback); `FidelityTest.java` (101 checks).

### A2. Prompt rewrite (Not built)
`system_prompt(style, terms, app_label, context="", strength="light")` in both apps, same text:
1. Opens with the formatter role: copy the transcript word for word; change only punctuation, capitalisation, spelling, obvious grammar slips, paragraph breaks and list formatting; never summarise, shorten, merge, reorder, paraphrase or drop anything.
2. The About you block (`<about_speaker>`, still cleaned by `clean_context`, never echoed) is the first block after the role line, so the stable prefix caches; the dictionary line follows.
3. Strength text (Light: keep every spoken word except um, uh, er; Standard: remove fillers, stutters and false starts and apply self-corrections), then structure rules (a paragraph break at clear topic shifts and about every five sentences in long text; `- ` bullets or numbered lists only when the speaker enumerates; chat styles stay flat; never reorder), then three short few-shot examples whose output has the same word count as the input.
4. `STRUCTURE_BY_STYLE`: casual and very casual flat; neutral paragraphs allowed; formal and email paragraphs and lists; notes paragraphs and bullets.
5. The prompt for identical inputs is byte-identical (no timestamps).
Golden `prompt` rows or substring asserts keep both apps equal, as part 1 did for `<about_speaker>`.

### A3. Strength setting, "Use raw", fallback marker (Not built)
`cleanup_strength` in `DEFAULT_CONFIG` as `light` (and as an Android preference), a "Cleanup strength" row in Settings on both pages (shared UI block, then `python tools/sync_ui.py`), `process()` passing the strength, a history flag `fidelity_fallback` and a one-tap "Use raw" in history that copies or pastes `raw`. The setting is per device and not part of the synced profile. When this lands, ADR 0030 point 3 becomes true and the "unset counts as standard" sentence above goes.

### A4. Dictionary applied (Not built)
Inspect `apply_replacements` and any fuzzy pass; if none exists add `fuzzy_dictionary(text, terms)` (case-insensitive exact word, or a one-edit match for single-word terms of five letters or more; never a common English word; idempotent) and `Terms.fuzzy`, with golden kind `fuzzydict`. Examples: `Kubernetis` becomes `Kubernetes`; `Vox` is not applied to `box`; a multi-word spelling such as `u v raj` is out of scope.

### A5. Benchmark (Not built)
`tools/bench_cleanup.py --provider P --model M --strength light [--corpus FILE] [--out FILE] [--compare A,B]` runs `system_prompt` and the provider call over `tools/bench/corpus.jsonl` (at least 30 synthetic transcripts: chat, long rambling, filler-heavy, enumerations, Hinglish, names from a sample About you, numbers, commands) and prints a table: median and p95 latency, word recall, added-word rate, length ratio, guard pass rate, About-you term accuracy, structure-only changes. The metrics are pure functions in `tools/bench_metrics.py`. The key is read from the user's normal Vox config and never printed; results go to `%APPDATA%\Vox\bench\` and are never uploaded; CI uses a fake provider.

## Not in this step
Chunked cleanup of very long dictations (branch E, task E2) and its chunk-level guard; the weekly self-improvement run (branch F); Android UI for the strength row beyond A3; any change to the default cleanup model (needs benchmark numbers first); a semantic check of meaning (the guard compares words, not meaning).

## Verification done
Worktree `J:\Projects\vox-wt\w1-a-fidelity`, Windows, 2026-10-01, before the A6 documentation commit:
- `python -m pytest -q` (the venv of the main checkout, with `--basetemp` outside the repo because the default pytest temp folder gave `PermissionError` on this PC): 1059 passed, 2 skipped (the POSIX permission tests), 1061 collected.
- `J:\Projects\.bin\javatest.cmd`: 18 programs run and pass (`FidelityTest` 101 checks, `ParityTest` 316 golden cases, `ApiClientTest` 52, `SyncEngineTest` 373), 2 integration programs skipped.
- `javatest compile`: `compile-check: OK (34 files)`. `python tools/sync_ui.py --check`: `ui-shared OK`.
- Each guard rule was written test first (red, then green) in the A1 commits and its review fixes; see the devlog entries of 2026-10-01.

## NOT verified
- **Tasks A2 to A5 do not exist yet**, so the Light default, the new prompt, the strength row, "Use raw", the fuzzy dictionary and the benchmark have no behaviour to check.
- **Real model output.** The thresholds (97%, 85%, 60%, 90%, 12 words) were tuned on written examples and a synthetic 1,000-word text, not on transcripts that real cleanup models returned. Whether Standard rejects too many legitimate cleanups, or Light too few, is for the benchmark (A5) to show.
- **A phone.** `Fidelity.java` is covered by unit tests and golden rows and type-checks; `DictationService` calling it was not run on a device.
- **CI.** None of this has run in CI.
- **Speed.** Not measured; the guard is a few list passes over the words and adds no network call.
