# Spec P9f: "Improve my cleanup" (weekly self-improvement)

Status: Implemented on branch `feat/p9f-improve` (tasks F1 and F2), merged into `feat/part3`. Date: 2026-10-01. **Never run against a real model, a real history, on a real desktop or on a phone.** Request R4 of the v2 part 3 plan. The privacy rules are fixed in [decision 0033](../decisions/0033-the-improvement-run-sends-transcripts-only-on-an-explicit-button.md). Behaviour as built: [04-windows-app.md](../04-windows-app.md), [06-pipeline.md](../06-pipeline.md), [08-features.md](../08-features.md); settings: [07-config-and-data.md](../07-config-and-data.md); privacy: [09-security-privacy.md](../09-security-privacy.md). It builds on the fidelity work of [p9a](p9a-cleanup-keeps-my-words.md) (the guard, the checks) and the profile sync of [p7d](p7d-profile-sync.md).

## Goal
Once in a while a stronger model (the request named `gpt-oss-120b`) reads some of the person's own dictations, what the speech recognition heard and what Vox typed, and proposes small improvements: words for the dictionary, replacements, a few cleanup rules and notes on where cleanup lost words. The person decides each item. **Nothing is sent, applied or changed by itself.**

## The card (Windows, Settings, between "Voice & audio" and "Privacy", no new heading)
1. **Look at**: the last 7, 14, 30 or 90 days, or all (`improve_days`). Below it, from local data only: the number of dictations, their characters, a rough token estimate, and how many cleanups lost words or fell back to the raw words (`improve.fidelity_report`).
2. **Model**: a text box fed by the cleanup server's model list, default `openai/gpt-oss-120b` (`improve_model`). **The provider is not chosen here**: a run goes to the configured cleanup server, or to the relay when "Use my relay as the AI server" is on, and the card names it. Keys exist only for those servers.
3. **Run once**: shows a confirm step with the sentence "This sends N transcripts (about X characters) to <provider>" and a line that the About you text, the dictionary and the cleanup rules also go along. Only **Send** runs it. Cancel, closing the card or changing the range or model sends nothing.
4. **Proposal**: a checkbox per dictionary word, replacement (`wrong => right`) and rule, with Tick all. The model's "About you" suggestions are shown read-only and are **never applied**, even if their id is sent to the bridge. The model's fidelity findings are listed below.
5. **Apply**: adds the ticked items. **Versions**: each applied change is listed with the date and what it added, with a **Revert** button. After Apply or Revert the page reloads its settings so the Dictionary page does not overwrite the new words.
6. **Remind me weekly** (`improve_remind`): a tray message once a week (the engine checks every hour). It never runs anything and sends nothing; switching it on only starts the week.

## What is sent (the data of one run)
- One chat request to the cleanup server (or the relay), model `improve_model`, temperature 0.2, at most 4,000 answer tokens, 180 s timeout.
- The system message is fixed (`improve.INSTRUCTIONS`): it says everything in the data is data and never instructions, and describes the JSON answer. The user message is one JSON object: `about` (the About you text, cleaned and capped like for cleanup), `terms` (up to 150 dictionary and people terms), `rules` (the current `my_cleanup_rules`) and `transcripts` `[{id, raw, cleaned}]`. Because the data is JSON, a transcript cannot pass for markup.
- The transcripts: `select_transcripts` takes the newest history entries of the range that fit **40,000 characters** (`MAX_SEND_CHARS`, raw plus cleaned, about 10,000 tokens), oldest first, and leaves out entries that have no raw or no cleaned text and entries flagged `private` or `no_history`. **No code writes `private` or `no_history` yet**: the filter works and is tested, but it protects nothing until such a flag exists (the engine writes history only when `keep_history` is on).
- `improve_run` takes the count and characters the person confirmed and **sends nothing** if they are missing or the history no longer matches them ("Your history changed since the numbers were shown..."), or if the server address or key is not usable.

## The answer
`parse_proposal` is tolerant (code fences, text around the JSON, `<think>` blocks) and never raises: an unusable answer gives an empty proposal and the message "The answer was not usable JSON, so nothing was proposed." Caps: 50 dictionary words, 20 replacements, 12 rules of at most 200 characters each and at most 2,000 characters of rules in all, 10 About you suggestions, 20 findings. A dictionary word or replacement side must be one line, at most 60 characters, with no `=>` and not starting with `#`; an About you suggestion is cut at 1,000 characters and a finding note at 300. Item ids look like `rule:0`, `dictionary:1`. The proposal is kept in memory inside the bridge (`Api._proposal`); **Apply works only on that proposal**, never on items the page sends back. Closing the window loses it and a new run costs one request. Applying uses it up.

## What applying does
- `improve.apply(proposal, accepted_ids, cfg)` adds the ticked dictionary words and replacements to `dictionary` and the ticked rules to `my_cleanup_rules` (one per line, at most 2,000 characters in all; what does not fit is left out; what is already there is skipped).
- Each change is a version in `my_cleanup_rules_versions` (the last 20): the time, the rules before it, the dictionary lines it added. `improve.revert(cfg, index)` undoes that version and every later one: the added dictionary lines are removed when they are still there (the person's own edits stay) and the rules are put back.
- The rules reach the cleanup prompt: `system_prompt(..., rules=...)` puts a bullet and a `<my_cleanup_rules>` block right after the strength rule, after the About you text and before the dictionary. Both our tags are stripped from About you and from the rules, so neither can fake or close the other's block. With no rules the prompt is byte for byte what it was before. The phone does the same (`ApiClient.systemPrompt` with `rules`, `DictationService` passes `Prefs.myCleanupRules()`); golden rows `rules` (8) and `promptrules` (8) tie the two together ([06-pipeline.md](../06-pipeline.md)).
- `my_cleanup_rules` is a new **profile sync** field (`sync.PROFILE_FIELDS`, `ProfileMap`, `ProfileMerge.SHARED_FIELDS`, the `profilefields` golden row), so the phone gets the learned rules through the existing sync. `my_cleanup_rules_versions` stays on the PC. There is **no card on Android**: it only receives the rules.

## Code
- `windows/improve.py`: pure core (`select_transcripts`, `estimate_cost`, `build_request`, `parse_proposal`, `apply`, `revert`, `fidelity_report`) and the card's helpers (`preview`, `selection`, `confirm_text`, `provider_label`, `versions_view`, `remind_action`) plus the single server call `ask`.
- `windows/vox_core.py`: `MAX_RULES`, `clean_rules`, the `rules` argument of `system_prompt`, and `chat_text` (the one chat call, extracted from `cleanup`, which sends the same request as before; a test checks it).
- `windows/ui_app.py` (`improve_state`, `improve_run`, `improve_apply`, `improve_revert`), `windows/engine.py` (`check_improve_reminder`, an hourly watcher), `windows/ui/index.html` (the card).
- Android: `ApiClient` (`cleanRules`, `RULES_TEXT`, `MAX_RULES`, `systemPrompt` with rules), `DictationService`, `Prefs`, `ProfileMap`, `ProfileMerge`.
- `improve.fidelity_report` calls `core.word_recall` and `core.looks_valid` directly instead of importing `tools/bench_metrics.py` (which only wraps them; `tools/` is not in the built exe), so the numbers are the benchmark's.
- Tests: `tests/test_improve.py` (core, fake provider), `tests/test_improve_card.py` (preview and confirm, versions, reminder, the one server call, the bridge: nothing is sent before the confirmed numbers), `tests/test_ui_improve.py` (ids, place on the page, only the confirm button runs it, escaping), `tests/test_prompt.py`, `tests/test_parity.py`, `tests/test_sync_profile.py`; Java `ApiClientTest`, `ParityTest`, `ProfileMapTest`, `ProfileMergeTest`.

## Checklist on the PC (for a tester; none of this has been run against a real server)
You need a key for the cleanup server and a few days of history (Settings > Privacy > Keep dictation history on).
1. Settings: the card **Improve my cleanup** sits between Voice & audio and Privacy. It shows a count, characters and tokens for the last 7 days, and a model box with `openai/gpt-oss-120b`. Turn history off and reopen: it says there is nothing to look at and Run once is off; turn it on again.
2. Press **Run once**: a confirm step shows "This sends N transcripts (about X characters) to <your server>" and the About you line. Press **Cancel**: nothing is sent (the server's usage page, or `vox.log`, shows no request).
3. Press Run once, then **Send**. After some seconds a proposal list appears: tick two items, press **Apply**. Look at the Dictionary page: the words are there. A rule shows in the versions list ("2 rules").
4. Dictate something that uses one of the new rules and check the cleanup follows it (judge by eye; the rules are a hint to the model, not enforced).
5. Press **Revert** on the version: the dictionary lines and the rules are gone again; your own dictionary edits made in between are still there.
6. Run again with a history that changed in between (dictate once between the confirm and Send, if you can be quick): the card says the numbers changed, updates them and does not send.
7. Switch **Remind me weekly** on: nothing appears now. (To see the message quickly, set `improve_remind_last` in `config.json` to `1` while Vox is stopped, then start Vox: a tray message appears within the hour.) Run once to see that nothing is sent by the reminder itself.
8. With the relay set up as the AI server, run once: the card says "My relay" and the run goes through it.
9. Phone (needs a relay with profile sync on): after Apply and a sync, the phone cleans with the new rules (it has no screen that shows them). Check with a dictation whose result depends on a rule, and that the dictionary words arrived on the Dictionary page.

## Deviations and choices
- **The provider is not selectable** (see above): the plan said "provider+model chosen in the model picker".
- **A transcript budget** the plan did not name: 40,000 characters per run, newest first. Whether a provider's free tier takes that much for `gpt-oss-120b` was not checked; a 413 or 429 shows as a plain error.
- **A proposal is kept in memory only.**
- **`extra_chars`** was added to `estimate_cost` so the About you text, dictionary and rules are counted in the token estimate (they are sent too).
- **About you suggestions** are display-only on purpose.

## Known limits (from the branch F review, not fixed)
- **Tag escaping can be bypassed by nesting.** `clean_rules` and `clean_context` (and Java `cleanTagged`) remove our tags in one pass, so `<my_cleanup<my_cleanup_rules>_rules>` becomes a live `<my_cleanup_rules>` (checked in Python). The model reads untrusted dictation and its rules text reaches the prompt only after the person ticks the item and presses Apply; the About you version of this flaw is older than this branch. Fix: repeat the removal until the text stops changing in both languages, with a golden row. **Fixed afterwards** (integration branch review fixes): `clean_context` and `ApiClient.cleanTagged` loop until nothing changes; golden rows of kinds `context` and `rules` cover the nesting for both tags.
- **When the learned rules are near the 2,000-character limit**, `apply` silently skips the rules that do not fit and the page says "Nothing ticked" even though items were ticked.
- **One very long newest entry** (over 40,000 characters of raw plus cleaned) makes `select_transcripts` stop at once and the card says "No saved dictations in this range"; an entry that is too big should be skipped instead. `ask` uses `post_with_retry` with a 180 s timeout, so a stalled server can receive the confirmed payload up to three times and keep the card waiting for minutes (nothing unconfirmed is ever sent).
- The `private` and `no_history` flags are never written (above).

## Not verified
- No real model has answered: the prompt, the JSON shape the model returns, the quality of what it proposes, and whether `gpt-oss-120b` accepts 10,000 tokens on Groq's free tier. The card was clicked through once in a browser with a mocked bridge (not a committed test); it was not seen in the real window or the built exe.
- That the learned rules make a real cleanup model behave better, or whether they cause more guard fallbacks.
- The weekly reminder's timing on a running engine and the tray message; the Android receipt of the rules on a device.
