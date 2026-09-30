# 0015. Sync the documentation at the end of every session

Status: Accepted
Date: 2026-09-30

## Context

CI ([0014](0014-documentation-checked-in-ci.md)) catches missing files, undocumented settings and broken links, but not wrong descriptions, stale counts, a missing changelog line or a missing decision record. Those only stay right if someone re-reads the changed code and updates the pages, and agents that work in short sessions forget to.

## Decision

Every session that touches the repository ends with a documentation sync:

- `documentation/tools/docs_todo.py` lists, from `git diff`, the pages that describe the changed code plus the pages to always consider (changelog, devlog, features, known issues, decision records, the "Verified against" line).
- The project skill `.claude/skills/vox-doc-sync/SKILL.md` describes the routine (find changes, read code and write, run the checker and tests, ship with the code, report) and triggers on end-of-session wording and on any Vox code change.
- The rule is also written in `AGENTS.md` (for any agent) and in the maintainer's own agent instructions and memory.
- Docs go in the same branch and PR as the code.

## Consequences

- Documentation drift shows up as a routine step, not a surprise.
- The checklist tool only maps file paths to pages; deciding what to write still needs reading the code.
- A small amount of overhead at the end of each session.

## Alternatives considered

- Rely on CI alone: misses meaning and counts.
- A git hook that blocks commits: too intrusive for a project with many small commits, and agents can bypass it.
