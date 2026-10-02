# 0037. Learn from my corrections: a short watch of the typed field, UI Automation through pythonnet, a final check before the text goes

Status: Accepted (not run on a real desktop or phone)
Date: 2026-10-02

## Context

The user asked that a fix made right after a dictation, in the same text, be learned without opening History ([specs/p9i-auto-learn.md](../specs/p9i-auto-learn.md)). That means reading the text of another app's field after Vox typed into it. Android already has an accessibility service that holds the field. Windows had no way to read another app's text: the engine only pastes. The text the user corrects is often sent (Enter in a chat) seconds after the fix, so the field is empty by the time a debounced check would run.

## Decision

- **Watch only the field Vox typed into, only for a short time.** A watch starts after a successful insert and lasts at most 3 minutes (`AUTO_LEARN_WINDOW_S`, one constant in both languages, checked by a test); it ends earlier when the field is emptied or shrinks to under half the typed text, the typed text is gone, the app or window changes, or Vox types again. Nothing is read while no watch runs.
- **Keep the last good snapshot in memory and check it when the watch ends.** The last text that still held the typed text is analysed once more at the end, then dropped; so a fix followed at once by Send is learned. It is never written or logged; logs carry counts only.
- **Analyse a text only once it has been quiet for 1.5 s**, and a pair once per watch, so a word typed letter by letter is not learned half-way.
- **Windows reads through the .NET `UIAutomationClient` with pythonnet.** pythonnet is already installed and bundled because pywebview needs it, and the UI Automation assemblies are part of Windows, so there is no new dependency and no PyInstaller change. The CLR is loaded on the first read in the watcher's thread, not in the paste path. Everything UI-Automation-specific sits behind `correction_watch.Provider`; tests use a fake and `tests/conftest.py` keeps every test away from the real desktop.
- **Same rules in both apps through golden rows** (`autocorrect`, `autolearn`), as for the other shared helpers.

## Consequences

- Fixes are learned with no extra step, in fields that expose their text; terminals and Electron apps without their accessibility mode teach nothing.
- The app now reads more of another app's field than before (the whole field, for up to 3 minutes); the privacy page, the security page and Android's accessibility description say so, and a switch turns it off.
- The managed UI Automation client is the older wrapper (UIA through `System.Windows.Automation`); some providers expose less through it than through the COM API. Not measured.

## Alternatives considered

- **comtypes and the COM `IUIAutomation` interface:** the usual Python route and closer to the native API, but a new dependency whose generated wrapper module (`comtypes.gen`) needs extra PyInstaller handling. Kept as the fallback if the managed client proves too weak on a real desktop.
- **pywinauto or uiautomation packages:** larger dependencies for the two calls needed.
- **Send WM_GETTEXT to the focused control:** works only for classic Win32 edit controls, not Word or browsers.
- **Read the clipboard or simulate Ctrl+A, Ctrl+C:** changes the user's selection and clipboard; not acceptable.
- **Analyse at the first change after a quiet period, as first asked:** it would learn half-typed words (`Minhaj -> Minhaju`); analysing a text once it has been quiet, plus the final check on the last snapshot, covers the "fix right before Send" case the request was about.
- **A fixed 60 s watch (the first version of the request):** too short for a longer message, and it kept watching after the text was sent.
