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
| Why things are the way they are | [decisions/README.md](decisions/README.md) (architecture decision records) |
| Designs written before the code (one per sub-project) | [specs/README.md](specs/README.md) |
| What changed and when | [../CHANGELOG.md](../CHANGELOG.md) and [devlog.md](devlog.md) |

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

Commit `71f0825` (the relay proxy group of the v2 part 2 plan: the relay's per-role AI server settings and write-only keys, the four proxy routes, and the "Use my relay as the AI server" switch on both apps, on top of the notes group `be39d5c`). Tests run locally on Windows on 2026-09-30 at the worktree of that commit plus the documentation changes: 718 pytest tests collected (716 pass, 2 skipped on Windows and run in CI on Linux), including 189 shared golden cases, and 14 plain-Java test programs via `android/run-tests.sh` (`RelayIntegrationTest` is skipped without `--integration`; `ParityTest` ran 189 golden cases); the docs checker passed. **Not yet seen in CI** for this group. **Never seen on a real phone, Raspberry Pi or Tailscale link, and never built or run as a frozen exe:** the proxy routes against a real speech or chat server or an `https` upstream with a real certificate, Python 3.9 at runtime, Linux, the systemd unit with `AF_INET6` on a Pi, a phone through `tailscale serve`, either app dictating through a relay on a device, and everything listed in [12-known-issues-and-roadmap.md](12-known-issues-and-roadmap.md). Checked by a person on a real PC (2026-09-30): Yuvraj ran the Windows app from `main` on his laptop, long before this group, and reported that it looks good; which features he exercised is not recorded.
