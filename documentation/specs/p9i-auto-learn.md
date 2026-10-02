# Spec P9i: learn from my corrections

Status: Implemented on branch `feat/auto-learn`, **not run on a real desktop or phone**. Date: 2026-10-02. Source: Yuvraj's request "I dictate something with Vox; as soon as the dictation is over, if I immediately correct a word in that same text, the app notes the correction and applies it in future. Both Android and Windows" (Wispr Flow does this), amended the same day: watch for up to 3 minutes and stop earlier when the text is clearly done (sent, gone, another app, a new dictation), with a final check on the last text seen so a fix made just before Send counts.

## Goal
"Fix a word" in History already turns a fix into a dictionary entry, but only when the user opens History and asks. Here the app notices the fix where the user makes it: in the field Vox just typed into.

## What was built
- **Shared rules** (`windows/autolearn.py`, `AutoLearn.java`; golden kinds `autocorrect` 48 rows and `autolearn` 7 rows): `detect(inserted, current)` finds the typed text again in the field (anchors: its first and last words) and keeps the swaps of `suggest_corrections` that look like corrections (`looks_like_fix`); `learn` decides what goes into the dictionary (capped at 1,000 lines); `apply_learned` / `remove_learned` keep the `learned_log`. The exact rules are in [../06-pipeline.md](../06-pipeline.md), "Dictionary".
- **The watch** (`autolearn.Watch`, `AutoLearnWatch.java`, injectable clock): armed when Vox typed; ends after `AUTO_LEARN_WINDOW_S` = 180 s, when the app changes, the field is emptied or shrinks to under half the typed text, the typed text cannot be found any more, or Vox types again (re-armed with the new text). A text is analysed after it stayed the same for 1.5 s; when the watch ends, the last text that still held the typed text is analysed once more and then dropped (memory only). Each pair is reported once per watch.
- **Windows** (`windows/correction_watch.py`; two lines in `engine.py`): after a real paste, a daemon thread reads the focused control every 2 s through UI Automation, only while the window pasted into is in front and only while the watch runs; password controls are never read; a control without text ends the watch ("auto-learn: control exposes no text", logged once). Learned fixes are saved to `config.json` and announced by a private tray balloon "Learned: wrong -> right". UI Automation is reached through the .NET `UIAutomationClient` with pythonnet, which pywebview already installs: no new dependency, no build change ([decision 0037](../decisions/0037-auto-learn-reads-the-field-through-ui-automation-with-pythonnet.md)).
- **Android** (`VoxAccessibilityService`, `accessibility_config.xml` gets `typeViewTextChanged`): after `insertText` succeeded the watch is armed with the target package; text-changed events are ignored while no watch runs; from the watched package the changed node's text is read (never a password field, nothing over 20,000 characters) and checked again 1.6 s later; another package ends it. New fixes go through `Prefs.learnCorrections` with a toast and a sync kick.
- **Settings and visibility (both apps):** `auto_learn` (on by default) in Settings, Privacy, text from `ui-shared/common.js` (`AUTO_LEARN_TEXT`): "For up to 3 minutes after Vox types, or until you send it, Vox notices when you fix a word in that text and adds the fix to your dictionary. Only the changed words are kept. Password fields are never read." The Dictionary page has **Recently learned** (last 20, Remove deletes the dictionary lines that entry made). `learned_log` and `auto_learn` stay on the device; the dictionary lines sync like any dictionary line.

## What works where (expected, not verified)
- Windows: Notepad-class edit fields, Word, text fields in browsers (UI Automation TextPattern or ValuePattern).
- Android: text fields that report their text to accessibility (most native apps: Messages, WhatsApp, Notes, Gmail).
- Not: terminals (no text exposed); Electron apps (Slack, Discord, VS Code, Teams) only when their accessibility mode is on; a field whose app empties and refills it in another way.

## Honest limits
- Never run on a real desktop or phone: UI Automation, the accessibility events, the timings and the toasts are tested only with fakes.
- A fix in text someone else wrote in the same field (a shared document) is learned like your own, as long as it is inside the span Vox typed.
- A very short dictation (one or two words) whose words are all edited cannot be found again and teaches nothing.
- The Python and Java word diffs are different algorithms (`difflib` and an LCS); the golden rows agree, odd inputs might not.
- `TextPattern` on a large Word document reads at most 20,001 characters; a longer document ends the watch.

## Device checklist (for a tester; none of this has been run)
1. **Windows, Notepad:** dictate "please send the jason file to minhaj today". Within a minute click into the text, change `jason` to `JSON`, wait 3 s. A tray balloon says "Learned: jason -> JSON"; Dictionary, Replacements shows `jason => JSON`, Words shows `JSON`, Recently learned lists it with "just now". `%APPDATA%\Vox\vox.log` says "auto-learn: learned 1 corrections" and does not contain the text.
2. **Windows, before Send:** in a browser text box (for example a web mail draft), dictate, fix a word and within one second clear the box (select all, delete): the fix is still learned.
3. **Windows, other app:** dictate into Notepad, switch to another window, then fix a word in Notepad: nothing is learned (the watch ended when the window changed).
4. **Windows, terminal:** dictate into Windows Terminal: nothing happens; the log says "auto-learn: control exposes no text" once.
5. **Windows, 3 minutes:** dictate, wait 3 minutes and 10 s, fix a word: nothing is learned.
6. **Windows, switch off:** Settings, Privacy, turn off Learn from my corrections; dictate and fix: nothing is learned. Turn it back on.
7. **Windows, Remove:** Dictionary, Recently learned, Remove on the entry from step 1: `jason => JSON` and the word `JSON` leave the dictionary.
8. **Android, WhatsApp:** dictate "ask you vrag about it" into a chat, fix it to "ask Yuvraj about it", press Send within a second. A toast says "Learned: you vrag -> Yuvraj"; Dictionary shows the replacement and the word; Recently learned lists it.
9. **Android, Notes (Google Keep or Samsung Notes):** dictate a sentence, fix one word, wait 2 s without typing: the toast appears without pressing anything.
10. **Android, password:** dictate into a normal field, then move to a password field in the same app and type: nothing is read or learned (logcat has no "auto-learn" line).
11. **Android, other app:** dictate into Messages, switch to another app and type there: the watch ends, nothing from the other app is learned.
12. **Both, rewrites:** dictate "I will send it today", change it to "I will ship it tomorrow": nothing is learned. Change "yuvraj" to "Yuvraj" (capitals only): nothing is learned.
13. **Both, sync:** with relay sync on, a learned replacement appears on the other device's Dictionary page after a sync; its Recently learned list does not show it.
