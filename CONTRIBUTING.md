# Contributing to Vox

Thank you for helping. Bug reports, device test results, documentation fixes and pull requests are all welcome.

## Before you start

- **Licence.** Vox is under the [MIT License](LICENSE). By opening a pull request you agree that your contribution is accepted under that licence.
- **Small fixes:** open a pull request directly. **Larger changes** (a new feature, a new setting, anything that touches both apps): open an issue first, and write a short design in `documentation/specs/` before the code.
- **Security problems:** do not open a public issue. See [SECURITY.md](SECURITY.md).
- Be kind and constructive: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Setup

You need Python 3.13 and, for the Java tests, a JDK 17 and Android's `android.jar` for platform 34 (from the Android SDK's `platforms/android-34` folder). No Gradle, no Android Studio and no device are needed for the tests.

```
python -m venv .venv
.venv\Scripts\pip install -r windows\requirements.txt -r tests\requirements.txt
```

(On Linux or macOS use `.venv/bin/pip`. The Windows app itself only runs on Windows; the tests that need it are skipped elsewhere.)

The documentation in [documentation/](documentation/README.md) describes every file, setting and feature. Read [documentation/README.md](documentation/README.md) and the page for the area you are changing before you change it.

## Run the tests

| What | Command (from the repository root) |
|---|---|
| Python tests | `python -m pytest -q -p no:cacheprovider --basetemp=<a folder outside the repo>` |
| Java tests (Android logic, no device) | `bash android/run-tests.sh` with `ANDROID_JAR` set and a JDK 17 on `PATH` |
| Java tests against the real relay | `bash android/run-tests.sh --integration` (needs Python 3.9+) |
| Type-check every Android source | `bash android/compile-check.sh` |
| Shared UI parts are in sync | `python tools/sync_ui.py --check` |
| Documentation matches the code | `python documentation/tools/check_docs.py` |

The Python tests write settings to `%APPDATA%` (Windows) and the home folder. To keep your own Vox settings out of it, point `APPDATA`, `HOME` and `USERPROFILE` at a temporary folder for the test run.

CI ([.github/workflows/build.yml](.github/workflows/build.yml)) runs the Python tests and the documentation checker on Linux, the relay tests on Python 3.9 and 3.13 (x86 and arm64), and the Java tests and the APK build on every pull request. So:

- A test that imports Windows-only modules (`engine`, `ui_app`, anything that pulls in `pynput`, `pystray`, `sounddevice`, `pyperclip` or `psutil`) must stub them (the pattern is at the top of `tests/test_engine_notes.py`) or use `pytest.importorskip`.
- Do not rely on Windows paths or line endings in tests.

## Rules that keep the two apps honest

1. **Write the test first** for a bug fix or a new rule: see it fail, then make it pass.
2. **Python and Java share behaviour through golden rows.** The cleanup rules, prompts, dictionary, note rules, profile merge, timing and several bubble rules exist in both `windows/` and `android/`. `spec/golden.txt` holds one case per line and is run by both `tests/test_parity.py` and `android/test/com/minhaj/vox/ParityTest.java`. If you change one of these behaviours, change **both** implementations and the affected rows, compute the expected value from the Python implementation and check it by hand. Never edit a row just to make one side pass. Format and kinds: [documentation/06-pipeline.md](documentation/06-pipeline.md).
3. **Shared UI parts are generated.** Edit `ui-shared/`, run `python tools/sync_ui.py`, commit both pages. A test fails if a generated block was edited by hand.
4. **Documentation moves with the code, in the same pull request.** Run `python documentation/tools/docs_todo.py` for the list of pages your change affects, update them, and run `python documentation/tools/check_docs.py` until it says OK (CI runs it too). In short: a new or moved file goes in `documentation/03-repo-tree.md`; a new setting in `documentation/07-config-and-data.md`; a user-visible change in `documentation/08-features.md` and one line under `Unreleased` in [CHANGELOG.md](CHANGELOG.md); a choice between real alternatives gets a decision record in `documentation/decisions/`. Write only what you verified, and say what you did not verify. The full routine is in `.claude/skills/vox-doc-sync/SKILL.md`.
5. **Privacy first.** Do not send more to a server than the feature needs (only the app name goes with a transcript, never a window title). If what leaves the device or what is stored changes, update `documentation/09-security-privacy.md` and `docs/privacy.html`.
6. **No secrets and no personal data.** Never commit `config.json`, `google_client.json`, `relay.json`, keystores, API keys, tokens, real host names or personal paths. Test fixtures use obviously fake values (`example.com`, `https://your-pi.your-tailnet.ts.net`, keys such as `test-key`).
7. **Screenshots are rendered, not taken by hand.** If you change a page that is in `docs/screenshots/`, run `python tools/render_screenshots.py` (needs Microsoft Edge or Google Chrome; `--list` shows the images) and commit the new PNGs. It opens copies of the apps' own pages with a stand-in bridge and made-up sample data (Sam, Acme), so no real profile, key or dictation can end up in an image. Keep each image under about 250 KB. Parts drawn outside the pages (the recording pill, the Android bubble, notifications) are not covered and still need a real device.

## Pull request checklist

- [ ] The change is small and does one thing, on its own branch.
- [ ] New behaviour or a fix has a test that failed before the change.
- [ ] `python -m pytest -q` passes; `bash android/run-tests.sh` passes if Java changed.
- [ ] If a shared rule changed: both apps and `spec/golden.txt` changed together.
- [ ] `python documentation/tools/check_docs.py` says OK, and the pages from `docs_todo.py` are updated.
- [ ] `CHANGELOG.md` has a line under `Unreleased` for a user-visible change.
- [ ] The description says what was tested on a real device or PC, and what was not.
- [ ] No secrets, personal data or generated files are in the diff.

## Testing on a device

Much of the newest work has only run in unit tests. Running a device checklist and reporting the result is one of the most useful contributions. The checklists are in the specs (for example [p9d](documentation/specs/p9d-android-bubble.md), [p9g](documentation/specs/p9g-note-bubble.md), [p9g2](documentation/specs/p9g2-install-safety.md), [p9e](documentation/specs/p9e-keep-listening.md)). Use the **Device test report** issue form, and leave out personal text, keys and tokens.
