# 16. Developer guide: the fast loop

How to change Vox quickly without breaking it. The rest of the documentation describes *what* Vox is; this page is *how to work on it*. For "where is the code for X", use the table in [../AGENTS.md](../AGENTS.md) and the full file list in [03-repo-tree.md](03-repo-tree.md).

## One command

Everything goes through `tools/dev.py` (plain Python, no extra install):

| Command | What it does |
|---|---|
| `python tools/dev.py setup` | creates `.venv` (Python 3.13 from mise) and installs `windows/requirements.lock` and `tests/requirements.txt` (pytest, ruff) |
| `python tools/dev.py test` | the whole pytest suite (`-k name`, a file, or any pytest option can follow) |
| `python tools/dev.py test --changed` | only the test files whose name matches a module changed against `main`: the inner loop while you edit. A change to `tests/conftest.py`, `spec/` or `ui-shared/` runs everything |
| `python tools/dev.py java` | the Android Java tests (`android/run-tests.sh`); needs a JDK 17 on PATH and `ANDROID_JAR` (see [10-build-test-release.md](10-build-test-release.md)) |
| `python tools/dev.py lint` | ruff with the CI rules in `pyproject.toml` (undefined and unused names, syntax errors) |
| `python tools/dev.py lint --wide` | a wider bug-finding set (late-bound loop variables in closures, mutable defaults, ...); not a gate, fix hits in small PRs |
| `python tools/dev.py docs` | the docs checklist for your change, then the docs checker |
| `python tools/dev.py check` | what CI's tests job checks: lint, pytest, the shared UI parts, the docs. Run it before every push |
| `python tools/dev.py run` / `selfcheck` | start the Windows app from source / import every module it loads |

`VOX_PYTHON` picks another interpreter (useful in a git worktree, which has no `.venv` of its own); `VOX_BASH` names Git Bash when the `bash` on PATH is WSL.

## The loop

1. **Branch** from `main` (`git switch -c feat/<thing>`, or a worktree beside the repo for parallel work).
2. **Write the test first** where the change is logic (most of Vox's logic is pure functions in `windows/*.py` and their Java twins). Tests never touch the real profile: `tests/conftest.py` gives each test empty profile folders and fails a test that reaches the real one.
3. **Edit, then `dev.py test --changed`** until green. Prototype text behaviour in a Python shell against `vox_core` before wiring it into the app.
4. **`dev.py check`**, then the docs sync ([decisions/0015-sync-docs-every-session.md](decisions/0015-sync-docs-every-session.md), skill `.claude/skills/vox-doc-sync/SKILL.md`).
5. **PR** with the template; CI runs lint, pytest, the docs check, the relay tests, the Java tests and the APK build. The Windows exe and release run on tags or by hand (`gh workflow run build.yml --ref <branch>`).

Bigger changes start with a short design in [specs/](specs/README.md) and, when a future reader would ask "why", a decision record in [decisions/](decisions/README.md).

## Recipes

| Change | Touch, in this order |
|---|---|
| A new Windows setting | `DEFAULT_CONFIG` in `windows/vox_core.py`; read it with `cfg.get(...)`; a control in `windows/ui/index.html` and its handler in `windows/ui_app.py`; a test; a row in [07-config-and-data.md](07-config-and-data.md) (the checker fails without it) |
| A new Android setting | `Prefs.java` (key and default); `android/assets/index.html` and the bridge in `MainActivity.java`; a Java test; a row in [07-config-and-data.md](07-config-and-data.md). If both apps have it and it syncs, the profile map too ([decisions/0023-profile-sync-three-way-merge.md](decisions/0023-profile-sync-three-way-merge.md)) |
| A cleanup or text rule | the Python function (mostly `windows/vox_core.py`, `structure.py`, `codemode.py`, `rules_layer.py`) **and** its Java twin (`ApiClient.java`, `Fidelity.java`, ...), plus rows in `spec/golden.txt` so `test_parity.py` and `ParityTest` hold both to the same output ([decisions/0007-shared-golden-file.md](decisions/0007-shared-golden-file.md)); then `dev.py test` and `dev.py java` |
| A prompt change | the prompt in `windows/vox_core.py` (and Android's copy); measure it with `tools/bench_cleanup.py --compare-prompt` before and after; keep the old version selectable until the numbers say otherwise |
| Shared look or shared UI text | edit `ui-shared/` only, then `python tools/sync_ui.py` ([decisions/0028-shared-ui-parts-are-generated-into-both-pages.md](decisions/0028-shared-ui-parts-are-generated-into-both-pages.md)) |
| A new file | a row in [03-repo-tree.md](03-repo-tree.md) (the checker fails without it) |
| Anything user-visible | a line under `Unreleased` in [../CHANGELOG.md](../CHANGELOG.md) and the row in [08-features.md](08-features.md) |

## Trying things out

| Tool | Use it to |
|---|---|
| `tools/bench_cleanup.py` | run the real cleanup over made-up or recorded transcripts and compare prompts, guards and models with numbers |
| `tools/bench_stt.py`, `bench_record.py`, `bench_wizard.cmd` | record your own clips and compare speech models and audio settings |
| `tools/bench_public.py` | the same on public speech sets |
| `tools/relay_smoke.py` | a quick end-to-end check of a relay |
| `windows/vox_app.py --selfcheck` | prove a packaged or fresh install can import everything |

GUI checks need a person at the desktop: a window started from an agent's shell can be invisible ([10-build-test-release.md](10-build-test-release.md)).

## Code conventions

- **Every source file starts with a header** (a Python module docstring, a Java class comment) that says what the file is for and its main entry points. Keep it true when the file changes.
- **Comment the why, not the what.** A comment explains a constraint, a trap or a decision (link the ADR); the code says what it does. Delete a comment that has gone stale rather than leave it.
- **Pure logic apart from I/O.** Text rules, merges and decisions are plain functions with tests; hooks, threads, network and UI call them. This is what makes the fast loop fast.
- **Python and Java behave the same** where both have a feature; `spec/golden.txt` is the contract.
- **Never send more than needed to a model** (the app name only, never a window title), never log a key or a transcript at info level, never read or commit a real `config.json` ([09-security-privacy.md](09-security-privacy.md)).
- **Lint:** the gate is `ruff check` with the rules in `pyproject.toml`. Widen it one rule at a time, with the fixes in the same PR; never run a mass reformat (it buries real changes in review).
