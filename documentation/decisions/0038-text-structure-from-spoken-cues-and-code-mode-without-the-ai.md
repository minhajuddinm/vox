# 0038. Lists come only from spoken cues, after the cleanup; code apps get rules, not the AI cleanup

Status: Accepted (built and tested on text; never run on real speech, a real editor or a phone)
Date: 2026-10-02

## Context

Yuvraj: "formatting of the text is not being done in bullet points, and code recognition, variable recognition is not being done." Until now a list appeared only when the cleanup model decided to make one, and then only in some styles; with the cleanup off, failed or rejected by the fidelity guard ([decision 0030](0030-cleanup-keeps-the-spoken-words.md)) there was never a list. Code dictated into an editor went through the prose cleanup, which may rewrite identifiers. The research for this pass (open-source dictation tools, Talon, whisper-local, Superwhisper S1-mini) found four ways to make lists: a prompt rule, a deterministic ordinal pass, an explicit structure control, and select-then-instruct; only Talon does code dictation properly, with spoken formatters and a symbol table.

## Decision

- **Lists are made by a deterministic pass from spoken cues** (`windows/structure.py`, Java twin `Structure.java`, golden kind `structure`): ordinals in sequence, point/item/step/number one then two, Hindi and Hinglish ordinals, and bullet cues, each at a clause start (a few unambiguous cues anywhere). **Commas alone never make a list.** At least two items; a single "first" stays prose. The cue words go; every other word stays, in order. Text that already has list markers (the model's own list) is left alone, so the pass is idempotent.
- **The pass runs after the cleanup and its fidelity guard**, on the final text of every path (cleaned, guard fallback, cleanup off or failed). The guard keeps judging the model's answer against the spoken words, and removing cue words afterwards cannot make a good answer look bad.
- A setting, **"Lists and paragraphs"** (`structure`: Off / Auto / Lists only, Auto by default, per device, not synced), switches it, and the cleanup prompt follows it (golden kind `promptstructure`), so the prompt never asks for lists the setting turned off. Casual and very casual styles take only the explicit cues.
- **Paragraph breaks at pauses are Windows only**: they need the speech server's segment times (`verbose_json`), which the Windows app asks Whisper models for in Auto; Android does not parse them.
- **Code mode (Windows only) is a rule layer, and by default code is not sent to the AI cleanup** (`code_cleanup` = `rules`): in the apps of `code_apps` (editors and terminals) or with the per-app style `code`, Talon-style formatters and a symbol table turn spoken code into code ([15-code-mode.md](../15-code-mode.md)). Choosing `llm` sends it with a code-aware style line.
- **Snippets are applied after the cleanup** (and before the lists), so a saved text (an address, a signature) never goes to the cleanup server; the Improve run sends the trigger phrase instead.

## Consequences

- A list or a code identifier no longer depends on a model's mood or on the cleanup running at all, and the rules are the same on both apps (golden rows), but only for the cues in the table: a list spoken without cues stays prose unless the model makes one, and a cue that Whisper spells differently ("No. 1", "open-paren") is missed. Real speech has not been tried yet.
- In code apps nothing reaches the cleanup server by default, which is faster and more private, but there is no punctuation fixing either.
- Every new cue or symbol needs a change in both languages (lists, snippets) or in the table, the help box and the docs page together (code mode; tests check they agree).

## Alternatives considered

- Leave lists to the cleanup prompt only: does nothing when the cleanup is off, failed or rejected, and models over-format short dictations (one reason OpenWhispr forbids it).
- Make lists from commas or from "and" enumerations: turns ordinary sentences ("I need milk, eggs and bread") into lists; the brief and the research both reject it.
- Run the list pass before the cleanup: the guard would then compare the model's answer with a text whose cue words were already removed, and the model would see list markers it might rewrite.
- Let the AI cleanup handle code with a stronger prompt: it still rewrites identifiers and cannot decide where an identifier ends; Talon's experience is that a constrained grammar is what makes code dictation accurate.
- Read variable names from the open file (UI Automation): out of scope; it would read screen content, which Vox does not send or read ([decision 0006](0006-app-name-only-to-the-model.md)).
