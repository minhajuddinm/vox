# Vox documentation

This folder is the accurate, current description of Vox for humans and for coding agents. If you are an agent starting work on this repo, read this page, then the pages that match your task. Do not guess from file names.

**Vox** is a voice-dictation tool: hold a shortcut (Windows) or tap a floating bubble (Android), speak, and cleaned-up text is typed into whatever app you are using. It also records meeting notes on Windows. Speech-to-text and text cleanup run on Groq by default, or on any OpenAI-compatible server you point it at.

Documentation verified against commit: see the `Verified against` line at the bottom of this page. The checker `documentation/tools/check_docs.py` runs in CI and fails when the tree, config keys or links drift from the code.

## Read this first

| If you need... | Read |
|---|---|
| What Vox is, who it is for, what it does not do | [01-overview.md](01-overview.md) |
| How the pieces fit: processes, threads, data flow | [02-architecture.md](02-architecture.md) |
| Where a file lives and what it does (every tracked file) | [03-repo-tree.md](03-repo-tree.md) |
| The Windows app in depth | [04-windows-app.md](04-windows-app.md) |
| The Android app in depth | [05-android-app.md](05-android-app.md) |
| Speech and cleanup pipeline, prompts, error handling, the shared golden file | [06-pipeline.md](06-pipeline.md) |
| Every setting, file on disk and data format | [07-config-and-data.md](07-config-and-data.md) |
| Every feature, where it lives, how it is tested | [08-features.md](08-features.md) |
| What data leaves the device, what is protected, known gaps | [09-security-privacy.md](09-security-privacy.md) |
| Build, test, CI, release, environment traps | [10-build-test-release.md](10-build-test-release.md) |
| Where logs are and how to diagnose problems | [11-logs-and-diagnostics.md](11-logs-and-diagnostics.md) |
| Open problems and the roadmap | [12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md) |
| Terms used in the code and docs | [13-glossary.md](13-glossary.md) |
| The optional relay server: protocol, management page, rules, what is missing (set-up guide: [../relay/README.md](../relay/README.md)) | [14-relay.md](14-relay.md) |
| Code mode on Windows: spoken formatters and the whole spoken symbol table | [15-code-mode.md](15-code-mode.md) |
| Why things are the way they are | [decisions/README.md](decisions/README.md) (architecture decision records) |
| Designs written before the code (one per sub-project) | [specs/README.md](specs/README.md) |
| What changed and when | [../CHANGELOG.md](../CHANGELOG.md) and [devlog.md](devlog.md) |
| How to contribute, report a vulnerability, or the project's conduct rules (for people outside the project) | [../CONTRIBUTING.md](../CONTRIBUTING.md), [../SECURITY.md](../SECURITY.md), [../CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) |

## Rules for keeping these docs true

1. **Change code, change docs, in the same commit.** New or moved file: update [03-repo-tree.md](03-repo-tree.md). New setting: update [07-config-and-data.md](07-config-and-data.md). New user-visible behaviour: update [08-features.md](08-features.md) and add a line under `Unreleased` in [../CHANGELOG.md](../CHANGELOG.md). A decision that a future reader would question: add an ADR in [decisions/](decisions/README.md).
2. **Run the checker before committing docs or code:** `python documentation/tools/check_docs.py`. It needs only Python and `requests` (the same as the unit tests). CI runs it.
2b. **Sync at the end of every session.** `python documentation/tools/docs_todo.py` lists the pages the changed code affects; the project skill `.claude/skills/vox-doc-sync/SKILL.md` describes the whole routine ([decisions/0015-sync-docs-every-session.md](decisions/0015-sync-docs-every-session.md)).
3. **State only what you verified.** If something is inferred (for example the reason the original author made a choice), say "inferred". Do not copy claims from old notes without checking the code.
4. **No secrets, no personal data.** Never write API keys, tokens or the contents of a user's `config.json` here.
5. **Keep pages short and factual.** Tables and file paths beat prose. Link instead of repeating.

## Conventions used in these docs

- Paths are relative to the repository root unless they start with `%APPDATA%` (a folder on the user's PC) or say "on the phone".
- `file.py:function` names a function; line numbers are avoided because they rot.
- "Windows app" means everything under `windows/`; "Android app" means everything under `android/`.
- "Groq" means the default speech and language API at `https://api.groq.com/openai/v1`. "Server" means whatever the Server address setting points to (Groq by default).

## Verified against

Branch `feat/cleanup-quality` on top of `main` at `8ea15e1`: the cleanup-quality round (guard v2, prompt v3 and term selection, Whisper prompt v2, the rules layer, edge-silence trim and the Whisper segment filter, the benchmark tools; ADR 0042 and 0043). Tests run locally on Windows on 2026-10-05 (`APPDATA`, `LOCALAPPDATA`, `HOME` and `USERPROFILE` pointed at a temporary folder): 4532 pytest tests collected (4522 pass, 8 skipped, 2 fail only because `bash` is WSL on this machine); 38 Java test programs pass; `javatest compile` is OK (55 files); `tools/sync_ui.py --check` and `documentation/tools/check_docs.py` report OK. **Not run:** real speech, a real model or speech server, a phone or the installed Windows app; the thresholds are first guesses until the benchmark runs on the user's own recordings. The longer per-branch verification notes that used to be here are in this page's git history (up to `aeb9be3`); the story of the work is in [devlog.md](devlog.md).
