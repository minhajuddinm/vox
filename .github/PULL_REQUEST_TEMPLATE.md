## What and why

<!-- One or two sentences. Link the issue or spec if there is one. -->

## How it was tested

<!-- Commands you ran and their result (pytest count, android/run-tests.sh, check_docs.py).
     Say what ran on a real PC, phone or relay, and what did not. -->

## Checklist

- [ ] A test was written first for the fix or new rule, and it failed before the change.
- [ ] `python -m pytest -q` passes; `bash android/run-tests.sh` passes if Java changed.
- [ ] If a rule shared by both apps changed: Python, Java and `spec/golden.txt` changed together.
- [ ] If `ui-shared/` changed: `python tools/sync_ui.py` was run and both pages are committed.
- [ ] `python documentation/tools/check_docs.py` says OK; the pages listed by `documentation/tools/docs_todo.py` are updated.
- [ ] A user-visible change has a line under `Unreleased` in `CHANGELOG.md`.
- [ ] If what leaves the device or what is stored changed: `documentation/09-security-privacy.md` and `docs/privacy.html` are updated.
- [ ] No secrets, tokens, real host names, personal paths or personal data in the diff.

Contributions are accepted under the project's MIT licence (see `LICENSE` and `CONTRIBUTING.md`).
