# 0014. This documentation is machine-checked in CI

Status: Accepted
Date: 2026-09-30

## Context

The documentation exists so that coding agents and humans can trust it without re-reading the code. Documentation of this kind rots: files move, settings are added, links break.

## Decision

`documentation/tools/check_docs.py` (standard library plus `vox_core` from the repo) runs in the `tests` job of `.github/workflows/build.yml` and locally. It fails when: a tracked file is missing from [../03-repo-tree.md](../03-repo-tree.md) or a listed file does not exist; a Windows setting key (`DEFAULT_CONFIG` or any `cfg.get("...")` in `windows/*.py`) or an Android preference key is not named on [../07-config-and-data.md](../07-config-and-data.md); a relative Markdown link points nowhere; the ADR index and the files in `documentation/decisions/` disagree.

## Consequences

- Adding a file or a setting without documenting it breaks CI, which is the intent.
- It checks structure, not meaning: a wrong description passes. Human and agent review still matter, and page headers say what was verified against which commit.
- Cheap to run (no network, well under a second).

## Alternatives considered

- Generate the tree automatically: loses the human descriptions that make it useful.
- Trust reviewers to notice: does not scale and fails silently.
