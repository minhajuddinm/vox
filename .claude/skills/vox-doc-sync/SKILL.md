---
name: vox-doc-sync
description: Sync the Vox documentation (the documentation/ folder, CHANGELOG.md, devlog, decision records) with the code after any work on the Vox repo. Use this at the end of every Vox session and before every commit or pull request that touches windows/, android/, spec/, tests/ or the workflow, and whenever the user says "sync the docs", "update the documentation", "wrap up", "end of session", "did you update the docs" or "document what we did". Also use it when a task changed behaviour, settings, files, tests or CI even if the user does not mention documentation, because the documentation is only trustworthy if it moves with the code and CI checks it.
---

# Vox documentation sync

The `documentation/` folder is how the next agent (or person) understands Vox without re-reading every file. It only helps if it is true. This skill is the closing routine: after code changes, make the docs say what the code now does, prove it with the checker, and record what happened.

Do this **at the end of every session that touched the repo**, and again before opening or updating a pull request. Skipping it is how docs rot; the CI check (`documentation/tools/check_docs.py`) only catches missing files, settings and broken links, not wrong descriptions, so the reading and judgement below are the real work.

## 1. Find what changed

```
python documentation/tools/docs_todo.py            # since origin/main, plus uncommitted work
python documentation/tools/docs_todo.py <git-ref>  # or since another ref
```

It prints a checklist: new/removed files for the tree page, the pages that describe each touched area, and the pages to always consider. It edits nothing. If the session's work is already merged, compare against the "Verified against" commit in `documentation/README.md` instead: `python documentation/tools/docs_todo.py <that-commit>`.

## 2. Read the code, then write

For every page on the checklist, open the changed code and the page side by side. Change only what is now wrong or missing. Reasons matter here: an invented sentence in a doc is worse than a gap, because agents will trust it.

- **State only what you verified** by reading code, running it, or reading its output. If you are inferring intent (for example why the original author chose something), write "inferred".
- **Numbers rot fastest.** Recount with `python -m pytest --collect-only -q -p no:cacheprovider | tail -1` and update the test counts in `documentation/README.md`, `documentation/10-build-test-release.md` and (if present) `documentation/devlog.md`. Do not add line counts or line numbers.
- **Settings and data:** every Windows setting (`cfg.get("...")`, `DEFAULT_CONFIG`) and Android preference key must appear in `documentation/07-config-and-data.md` with type, default and meaning. New files go in `documentation/03-repo-tree.md` with a one-line purpose.
- **Behaviour and features:** update `documentation/08-features.md` (what, where in code, which tests) and the platform page (`04-windows-app.md`, `05-android-app.md`) or `06-pipeline.md`.
- **Security and privacy:** if data that leaves the device, or what is stored, changed, update `documentation/09-security-privacy.md` and check `docs/privacy.html` still tells the truth.
- **Known issues and roadmap** (`documentation/12-known-issues-and-roadmap.md`): remove what you fixed, add what you found but did not fix.
- **Decisions:** if you chose between real alternatives, add `documentation/decisions/NNNN-title.md` (next number, template in `decisions/README.md`) and add it to the index table and the tree page. Never rewrite an accepted record; add a new one that supersedes it and change the old status to `Superseded by NNNN`.
- **Changelog:** one line per user-visible change under `## [Unreleased]` in `CHANGELOG.md` (Added / Changed / Fixed / Removed). Internal-only changes do not belong there.
- **Devlog:** append a dated entry to `documentation/devlog.md` (3 to 10 lines): what changed, how it was verified, what surprised you (environment traps, misleading errors). This is the "logs" trail for future sessions.
- **Verified-against line:** set the commit in `documentation/README.md` to the commit the docs now describe (use the current `git rev-parse --short HEAD` after the code commits, or the merge commit once merged).
- **Never write secrets or personal data**: no API keys, tokens, config contents, personal file paths, or private account names beyond what already appears in the docs.

## 3. Prove it

```
python documentation/tools/check_docs.py
python -m pytest -q -p no:cacheprovider
```

Fix every reported problem. If the checker fails because of a rule that is genuinely wrong (not because you skipped a doc), fix the checker in the same change and say so.

## 4. Ship it with the code

Documentation changes go in the same branch and pull request as the code they describe (or a follow-up PR straight after, if the code is already merged). Mention in the PR description which pages changed. CI runs the checker in the `tests` job on every pull request (the Windows build only runs on tags or by hand: `gh workflow run build.yml --ref <branch>`).

## 5. Report

End the session summary with two lines: which documentation pages were updated, and the checker result (or that nothing needed syncing and why). If you had to skip anything, say what and why.
