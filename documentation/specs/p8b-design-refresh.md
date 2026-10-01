# Spec P8b: design and privacy refresh

Status: Implemented in group D of the v2 part 2 plan (commits `71f0825..5a0f98f`), **not seen on a real screen, phone or packaged build**. Date: 2026-09-30. Decisions: [0028](../decisions/0028-shared-ui-parts-are-generated-into-both-pages.md) (shared UI parts) and [0029](../decisions/0029-paste-checks-the-window-clipboard-default-off.md) (paste). Follows [p8c](p8c-quick-wins.md); the roadmap item it closes is "design and privacy refresh" in [12](../12-known-issues-and-roadmap.md).

## Goal
Make both apps easier to read and honest about where data goes:
1. Both HTML pages share one palette, one set of component styles and one set of helper functions, and a test guards the pages against broken ids and bridge calls.
2. Settings are grouped by what they are about; Home shows what Vox is doing, not vanity numbers; first-run asks for an AI provider, not a Groq key.
3. The pill (Windows) and the bubble (Android) show whether a dictation landed or failed.
4. Pasting never lands in the wrong window and does not leave the user's own clipboard behind by default.
5. Every statement about what leaves the device, in the app and on the public pages, matches the code.

## Design

### 1. Static checks and shared parts
- `tests/test_ui_static.py` reads both pages as text: every id the script looks up exists, no id is defined twice in the static markup, every `api().NAME` call on the Windows page is a public method of `class Api` (parsed with `ast`), every `V.NAME(` / `Vox.NAME(` call on the Android page is a `@JavascriptInterface` method of `MainActivity`. Lookups built at run time (`$(role + "-status")`) are listed by hand in `DYNAMIC_LOOKUPS`, and an unlisted computed lookup fails the test. It found one real bug: the Android page had two elements with `id="about"`, so the Settings footer line was written into the About-you box and never shown; the footer is now `about-vox`.
- `ui-shared/tokens.css` (palette), `ui-shared/components.css` (declarations identical in both pages for `.card .btn .chip .switch .status .srow .hint .day .entry`) and `ui-shared/common.js` (`STYLES`, `ABOUT_MAX`, `$`, `esc`, `toast`, `dictRepls`, `dictLines`, `aboutCount`, `agoText`, `combineTests`, `statusRows`, `statusHtml`) are written into both pages by `python tools/sync_ui.py` between marker comments. Each page keeps its own sizes, spacing and bridge code. `--check` and `tests/test_ui_shared.py` fail when a generated block was edited by hand. Output stays inline, so PyInstaller and the APK are packaged as before.

### 2. Settings, Home and onboarding (both pages)
- **Settings** has four sections in this order: AI providers, Voice & audio, Privacy, System. On Windows the relay switch is the last row of the providers card. Each page says in one line what leaves the device.
- **Home**: the word counts, words per minute and "time saved" are gone. A **Status** card shows the provider (or "My relay"), the speech and cleanup model, the last connection Test (kept in the page's memory; the Test button runs both roles only when pressed, so Home makes no network call by itself), the sync state, the voice note count and the last dictation. Windows builds the data in `providers.status_summary` (returned by `Api.get_state` as `status`); Android's `state()` adds `status {notes, unsent}`. A failed dictation shows "Not sent" first, even when older history entries exist.
- **Onboarding**: Windows shows "Choose your AI provider" (preset grid, Test and Save key; local servers say "No key needed") and hides it only when a key is set, or when the relay switch is on and `proxy_problem()` is empty. Android's setup list has three steps (microphone, accessibility, AI provider); the battery setting moved to System. Android gained switches for the note bubble and the note notification.
- **Styles (Windows)**: the app for a style is chosen from a list of recent apps plus "Other..." for typing an exe name (lower-cased, `.exe` added as before).

### 3. Result signal
- Windows: `Engine.flash("sent" | "error")` sets `flash_kind` and `flash_until` (0.7 s green check, 1.8 s red !); `Engine.state` is unchanged. `overlay_mode(...)` (pure, tested) chooses what the pill draws: recording and sending always win; while idle a running flash replaces the meeting timer. `_process` flashes once at the end: sent for a paste or a saved note, error for a copy only, a missing key, a microphone error, nothing heard and every send failure. A cancelled or too-short recording, an empty result and informational notices do not flash. Tray balloon texts are unchanged.
- Android: `BubbleView.flash(SENT | ERROR)` (same durations, timed from `SystemClock.uptimeMillis()`, no animator, drawn only while idle). `VoxAccessibilityService.onResult` flashes SENT when the text was typed and ERROR when it only reached the clipboard, `onError` flashes ERROR, and `onNoteSaved` flashes SENT on the note bubble. `tests/test_flash_constants.py` keeps the two duration constants equal.

### 4. Paste (Windows)
`windows/paste.py` `paste_text(text, target_exe, keep_clipboard)` returns `"pasted"` or `"copied"`. Order: wait up to 2 s for Shift, Ctrl, Alt and Win to be up; compare the focused window's exe name with the one remembered when recording started (case-insensitive, never the title); if it differs, copy only; otherwise copy, Ctrl+V, wait 0.4 s, and restore the old clipboard only when `keep_clipboard` is off, it was readable, and the clipboard still holds our text. When the target is unknown, the window cannot be named or the lookup fails, it pastes anyway (losing the text is worse). `keep_clipboard` is now a real default: **false**. See ADR 0029.

### 5. Privacy text
`docs/privacy.html`, `docs/index.html`, README section 10, the Android accessibility description, `documentation/09` and the in-app lines were rewritten sentence by sentence against the code. Corrected, because the code contradicted them: settings and notes can leave the device through the relay; the dictionary and people also go to the speech server (as a spelling hint) and the cleanup server; the app name is the exe name on Windows and the app label on Android; voice notes send no app name; meeting transcripts go to the cleanup server; a model-list request goes to each server on both apps; "Test" next to the relay address contacts the relay; no analytics and no server of Vox's own.

## Not in this step
A browser or device run of the new pages; styling the tables on the privacy page (`docs/style.css` has no table rules); recording failed dictations in history (so with history off Windows Home says "History is off"); a Windows UI test beyond the static checks; encrypting the Android key.

## Verification done
At `5a0f98f`, on Windows, 2026-09-30: see the devlog entry for the numbers. Checked in a browser against mock bridges (Playwright, desktop and phone width, by the implementers): section order, Status card, Test button, welcome card show and hide, no horizontal overflow; computed styles of the shared classes identical before and after the move (light and dark). Static checks mutated on the real pages fire on the mistake they exist for. `compile-check` and the Java tests pass. **Not verified:** any of it in the real Windows window, the pill on a real screen (the Tk canvas items were inspected and the shapes previewed from the same coordinates), the bubble flash and note-saved flash on a phone, the Android Status card and new switches, the real Win32 calls under a live Ctrl+Win dictation (the foreground-exe lookup was probed on the development PC and agrees with psutil apart from case), the privacy page's table markup rendered, and a packaged build.
