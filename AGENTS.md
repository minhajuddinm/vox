# Guide for coding agents

Vox is a voice-dictation tool with two independent apps: a Windows app in Python (`windows/`) and an Android app in Java (`android/`). Both send audio to an OpenAI-compatible speech and chat server (Groq by default) and type the cleaned-up text into the focused app.

**Start with [documentation/README.md](documentation/README.md).** It indexes an accurate description of the architecture, every file, every setting, every feature, the security model, the build, the logs, the decisions and the known issues. Do not rely on file names or old notes.

## Ground rules

1. **Docs move with code, and are synced at the end of every session.** Run `python documentation/tools/docs_todo.py` for the checklist and follow the skill `.claude/skills/vox-doc-sync/SKILL.md`. New or moved file: update `documentation/03-repo-tree.md`. New setting: `documentation/07-config-and-data.md`. User-visible change: `documentation/08-features.md` and a line under `Unreleased` in `CHANGELOG.md`. A decision worth explaining later: a new record in `documentation/decisions/`. Run `python documentation/tools/check_docs.py` (CI does too).
2. **Python and Java share behaviour.** The cleanup rules exist in both languages. If you change one, change the other and `spec/golden.txt`, then run both suites (details: `documentation/06-pipeline.md`).
3. **Run the tests:** `python -m pytest -q` from the repo root. The Java helpers are tested in CI (`documentation/10-build-test-release.md`). There is no local Android SDK requirement.
4. **Never handle secrets.** Do not read, print or commit `config.json`, `google_client.json`, keystores or API keys. Tests must use a temporary `APPDATA`.
5. **Do not send more than needed to the model.** Only the app name goes with the transcript; never a window title or other screen content.
6. **Small, reviewable changes** on a branch, with a PR. CI runs only on tags or by hand (`gh workflow run build.yml --ref <branch>`), so run it yourself for branches that touch builds.
7. **GUI checks need a real desktop.** A GUI started from an agent shell may be invisible to the user (WebView2 can fail with "Invalid window handle"). Ask the user to start Vox from their own terminal to check anything visual; logs (`%APPDATA%\Vox\vox.log`) still work.

## Where things are, in one screen

| Want to change | Look at |
|---|---|
| Hotkey, recording, paste, tray, retry (Windows) | `windows/engine.py` |
| Prompts, server calls, config, dictionary, silence gate | `windows/vox_core.py` (Java twin: `android/src/com/minhaj/vox/GroqClient.java`) |
| Windows window and its Python side | `windows/ui/index.html`, `windows/ui_app.py` |
| Meeting notes and calendar | `windows/meeting.py`, `windows/gcal.py`, `windows/vcalendar.py` |
| Android recording, retry, state | `android/src/com/minhaj/vox/DictationService.java` |
| Android bubble and text insertion | `android/src/com/minhaj/vox/VoxAccessibilityService.java` |
| Android screens and settings bridge | `android/assets/index.html`, `android/src/com/minhaj/vox/MainActivity.java`, `Prefs.java` |
| CI and release | `.github/workflows/build.yml` |
