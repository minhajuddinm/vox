# 0033. The improvement run sends transcripts only on an explicit button, and changes nothing by itself

Status: Accepted (built on Windows, unit tested with a fake provider; never run against a real model, see [p9f](../specs/p9f-improve-my-cleanup.md))
Date: 2026-10-01

## Context

Request R4 of the v2 part 3 plan: a "weekly self-improvement" where a stronger model (Yuvraj named `gpt-oss-120b`) reads stored transcripts and proposes dictionary words, About you changes, cleanup rules and a fidelity report. This is the first feature in Vox that sends **stored** dictation history (not a dictation being made now) to a server, in bulk, and the history is plain text on the PC. Until now what was sent was the dictation just spoken. The plan fixed the rule: nothing is sent without an explicit button press that shows counts and cost, proposals are accepted per item, nothing auto-applies.

## Decision

1. **Only the button sends.** A run starts only from the confirm step of **Run once** ("This sends N transcripts (about X characters) to <provider>", plus a line that About you, the dictionary and the rules go along) and its **Send** button. Previewing (counts, characters, a token estimate) uses local data only. The bridge `improve_run` takes the count and characters the person confirmed and sends nothing without them or when the history no longer matches them.
2. **The weekly reminder is a tray message.** `improve_remind` (default off) shows one message a week. It does not run, send or prepare anything; turning it on only starts the week.
3. **What goes, and where.** The newest history entries of the chosen range that fit 40,000 characters (raw and cleaned text), the About you text, the dictionary terms (up to 150) and the current rules, as one JSON object, to the **cleanup server the person already uses**, or to the relay when it is the AI server. No new server, no new key, no copy kept by Vox. Entries flagged `private` or `no_history` are left out (no code sets those flags yet; see Consequences). The model is `improve_model`, default `openai/gpt-oss-120b`; the provider cannot be picked separately.
4. **The data is data.** The system message says so, the transcripts travel as JSON, and About you and the rules are cleaned like they are for cleanup. A hostile transcript cannot add an instruction through markup, but a model can still be misled by what it reads: that is why the next point exists.
5. **Nothing applies by itself.** The proposal is kept in memory in the bridge and **Apply** uses only that proposal and only the ticked ids. Dictionary words, replacements and rules are added; **About you suggestions are display-only and never applied**. Rules are capped (12 items, 200 characters each, 2,000 in all), every change is a version, and **Revert** undoes it (and later ones) while keeping the person's own edits.
6. **Learned rules are prompt text, not instructions.** They go into the cleanup prompt inside a tagged block that says they are the speaker's habits and never override the fixed rules; the tag is stripped from rules and from About you. The fidelity guard still checks every cleanup, so a bad rule can cause fallbacks to the spoken words but cannot make the model's rewrite be typed.
7. **Nothing new is stored** except the settings (`improve_model`, `improve_days`, `improve_remind`, `improve_remind_last`, `improve_last_run`) and the rule versions (`my_cleanup_rules_versions`, this device only). The rules themselves sync to the phone as part of the profile (`my_cleanup_rules`).

## Consequences

- A person who presses Send hands up to 40,000 characters of their own dictations to their cleanup provider in one request. The confirm sentence says how many; whether the provider keeps or trains on it is that provider's policy ([09-security-privacy.md](../09-security-privacy.md)).
- **The private-entry filter protects nothing yet.** `select_transcripts` skips entries with `private` or `no_history`, but nothing writes them; the only way to keep a dictation out is to have **Keep dictation history** off when it is made, or to delete it from History. A real flag is future work.
- **Known weaknesses of the escaping (from the branch review, not fixed):** our tags are removed in one pass, so a nested tag can rebuild a live `<my_cleanup_rules>` (about the same weakness already existed for About you). The rule text reaches the prompt only after the person ticks it and presses Apply.
- A run is one request with a 180 s timeout and the normal retry policy, so a stalled server can receive the confirmed payload up to three times before the card gives up. Nothing unconfirmed is sent.
- The proposal is lost when the window closes and a new run costs a request; that keeps nothing on disk but costs money.
- The model's quality is unknown: no real model has answered. The benchmark ([0030](0030-cleanup-keeps-the-spoken-words.md)) does not measure the learned rules.
- Android has no card: it receives the rules through profile sync and uses them in its prompt, so a phone dictation is affected by rules approved on the PC without any screen on the phone showing them.

## Alternatives considered

- **A scheduled run by itself.** Convenient, but it would send history without a person looking at the count; the plan ruled it out. The reminder is the compromise.
- **Learning on the device.** There is no local model in Vox (server-only, [0001](0001-openai-compatible-api-groq-default.md)); a rule-based miner could find repeated corrections but not propose rules or find lost words.
- **Applying everything the model proposes, with Revert.** Less clicking, but a bad item would change every later dictation before the person saw it; per-item ticking keeps the person in charge.
- **Sending only the entries where cleanup fell back or lost words.** Smaller, but it hides the entries where cleanup was right, which the model needs to avoid proposing rules that break them.
- **Letting the model edit About you directly.** About you is personal text the person wrote and is sent with every dictation; edits to it are too sensitive to apply, so they are suggestions only.
- **Choosing a different provider for the run.** A key for a second server would have to be stored and shown; using the cleanup server keeps the set of recipients the person already chose.
