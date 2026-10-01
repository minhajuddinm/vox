# 10. Build, test, CI and release

## Environment

| Tool | Version / note |
|---|---|
| Python | 3.13 (CI uses 3.13; create a local venv with `python -m venv .venv` (it is git-ignored)) |
| Windows app dependencies | `windows/requirements.txt` (pinned) |
| Test dependencies | `tests/requirements.txt` (pinned `requests`, `pytest`) |
| Android build | JDK 17+, Android platform 34, build-tools 36 (CI installs them). No Gradle. No local Android SDK is needed for day-to-day work |
| Windows installer | Inno Setup 6 (used if present on the CI runner; the workflow installs it with Chocolatey if missing) |

## Run from source (Windows)

```
python -m venv .venv
.venv\Scripts\pip install -r windows\requirements.txt
.venv\Scripts\python windows\vox_app.py            # engine (tray, hotkey)
.venv\Scripts\python windows\vox_app.py --window   # the window only
```

Put the API key in the window's Settings, or in `%APPDATA%\Vox\config.json` (it is protected on first load).

**Agent note:** GUI programs started from inside a coding agent's shell can run on a desktop the human cannot see (the window may fail with WebView2 "Invalid window handle", and no window or pill appears). Have the human start Vox from their own terminal when checking anything visual. Logs still work either way ([11-logs-and-diagnostics.md](11-logs-and-diagnostics.md)).

## Tests

| Suite | Command | Covers |
|---|---|---|
| Python | `python -m pytest -q` (from the repo root) | 1041 tests collected at the time of writing (`python -m pytest --collect-only -q`): `tests/test_*.py`. On Windows 1039 pass and 2 are skipped (the two POSIX file-permission tests in `test_relay_admin.py`, which run in CI on Linux); 270 of the 1041 are the shared golden cases in `test_parity.py`; the total includes `tests/test_ui_static.py`, the text-only checks of the two HTML pages |
| Java | `bash android/run-tests.sh` (needs a JDK and `ANDROID_JAR`; see below) | 20 programs in `android/test/com/minhaj/vox/` (`ApiClientTest` 52 checks, `CorrectionsTest` 16, `EndpointTest` 39, `NoteEventsTest` 13, `NoteLogicTest` 181, `NoteTest` 29, `MultipartTest` 43, `ParityTest` 270 golden cases, `DevicesViewTest` 26, `PcmTest` 12, `PlainJsonTest` 88, `ProfileMapTest` 85, `ProfileMergeTest` 40, `PendingQueueTest`, `ProvidersTest`, `RelayClientTest` 254, `SyncEngineTest` 373, `RelayIntegrationTest`, `ProxyUploadIntegrationTest`), no device, no JUnit; the two integration tests run only with `--integration` (see below), so a normal run runs 18 and skips those |
| Parity | part of both suites | `spec/golden.txt` |
| Docs | `python documentation/tools/check_docs.py` | tree, config keys, links, ADR index |
| Docs checklist | `python documentation/tools/docs_todo.py` | not a test: lists the pages to update for the code you changed (see [decisions/0015-sync-docs-every-session.md](decisions/0015-sync-docs-every-session.md)) |

Local pytest tip on Windows: if pytest fails while cleaning its temp folder, run `python -m pytest -q -p no:cacheprovider --basetemp=%TEMP%\vox-pt`.

CI and local runs share one script, `android/run-tests.sh`. With a JDK (17) on `PATH` and `ANDROID_JAR` pointing at `platforms/android-34/android.jar`, run `bash android/run-tests.sh` from the repo root: it compiles the sources in `android/testsrc.list` plus `android/test/**/*.java` (`javac -source 8 -target 8`) into a temporary folder, runs every `*Test` class (`ParityTest` gets `spec/golden.txt`), prints one line per test and a final `N tests run` line, and stops with a non-zero exit at the first failure. A new pure Java class needs one line in `android/testsrc.list`. `android.jar` is only needed to satisfy `org.json` imports; the tests never call it. `RelayClientTest` starts the JDK's own HTTP server (`com.sun.net.httpserver`) on `127.0.0.1` with a free port, so the relay client is tested over real HTTP with no network. A local wrapper script kept outside the repo sets `JAVA_HOME` and `ANDROID_JAR` and runs the script (a JDK 17 and `platforms/android-34` are enough; without build-tools no APK can be built locally).

`bash android/run-tests.sh --integration` adds `ProxyUploadIntegrationTest` (the speech upload of `ApiClient` through the real relay's proxy to a stub AI server) and `RelayIntegrationTest`, which checks the phone's sync client against the real relay instead of a fake: the test starts `relay/relay.py` as a child process (`--data-dir` in a temp folder, a free port on `127.0.0.1`), reads the token from the `relay.json` the relay makes there (it is never printed), syncs two or three in-memory phones through it and stops the relay at the end. It needs Python 3.9 or newer (`VOX_PYTHON`, else `python3`, else `python`) and the repository root as the working directory. Nothing is downloaded: `RelayClient` reads JSON with `PlainJson`, so the test needs no `org.json` jar and CI has no Maven Central dependency. The test counts the `/changes` pages the paging scenario asks for (two, derived from `SyncEngine.PULL_LIMIT`), removes its temp folder even when the relay cannot be started, and reports any exception, or a relay that cannot be stopped, as a failure. The local wrapper script does not pass the flag; run `bash android/run-tests.sh --integration` yourself with `JAVA_HOME` and `ANDROID_JAR` set. Without the flag the test is skipped (`SKIP RelayIntegrationTest` in the output) and `N tests run` counts one less.

To type-check the Android code that the tests do not reach (`DictationService`, the services, `MainActivity`) without build-tools, run `bash android/compile-check.sh` (same `ANDROID_JAR`, JDK 17 on `PATH`): it compiles every file under `android/src` with `javac --release 8` against `android.jar` and prints `compile-check: OK (N files)`, or javac's errors and a non-zero exit. The `R.java` that aapt2 would generate is replaced by a stub built from the `R.<type>.<name>` uses in the sources, so a misspelled resource name is only a warning there (no match under `android/res`); only `android/build.sh` and CI fail on it. It is a local aid; CI does not run it.

Not covered by tests (except that `engine.py` has tests for voice notes and the relay tray toggle, which need the Windows runtime packages and are skipped in CI's `tests` job): `engine.py`, `meeting.py`, `overlay.py`, `gcal.py`, `vcalendar.py`, `ui_app.py`, what both HTML pages do, `DictationService`, `VoxAccessibilityService`, `MainActivity`, `BubbleView`, `Prefs`, `SyncWorker`. Verify those by hand or add tests when you touch them.

The palette, component CSS and helper JS the two pages share live in `ui-shared/` and are generated into both pages by `python tools/sync_ui.py` (inline, between `ui-shared:css` and `ui-shared:js` marker comments, so packaging is unchanged: PyInstaller still adds `windows/ui`, the APK still bundles `android/assets`). Edit `ui-shared/`, run the tool, commit the pages; `tests/test_ui_shared.py` fails if a block was edited by hand. The two HTML pages get static checks, in `tests/test_ui_static.py` (no browser, plain text and `ast`): every id a page looks up (`$("x")`, `getElementById`, `querySelector("#x")`, `setVal("x", ...)`) exists as `id="x"`; no id is used twice in the markup; every `pywebview.api.NAME` / `api().NAME` in the Windows page is a public method of `Api` in `ui_app.py` (parsed, not imported); every `V.NAME(` / `Vox.NAME(` in the Android page is a `@JavascriptInterface` method in `MainActivity.java`. Ids looked up through a computed name (`$(role + "-status")`) are listed by hand in `DYNAMIC_LOOKUPS` in that file, and a new computed lookup fails the test until it is added there with the ids it can resolve to. The checks do not see callbacks that Java calls in the page (`window.keyResult`, `window.modelsStt`, ...), `label for=` targets, or calls made through a variable.

UI pages can be checked in a browser without the apps: Android's `index.html` runs with a built-in mock bridge; the Windows page needs a stub `window.pywebview.api` before it loads.

## CI (`.github/workflows/build.yml`)

Triggers: push of a tag `v*`, manual run (`workflow_dispatch`), or a pull request (the `tests` and `android` jobs only; the Windows build and release are skipped). It does **not** run on ordinary pushes; run it by hand on a branch: `gh workflow run build.yml --ref <branch>`.

| Job | Runner | Steps |
|---|---|---|
| `tests` | ubuntu | install `tests/requirements.txt`; `pytest -q`; documentation checker |
| `windows` (needs `tests`) | windows | optional Google client from secret; `pip install -r windows/requirements.txt pyinstaller==6.22.3`; PyInstaller `--onedir --windowed` (with `--paths ../relay --hidden-import relay` so `Vox.exe --relay` can import `relay/relay.py`); Inno Setup; upload `VoxSetup` artifact |
| `android` (needs `tests`) | ubuntu | install SDK parts; optional keystore from secret; compile and run the Java tests with `bash android/run-tests.sh` (`ApiClientTest`, `CorrectionsTest`, `EndpointTest`, `NoteEventsTest`, `NoteLogicTest`, `NoteTest`, `DevicesViewTest`, `ParityTest spec/golden.txt`, `PcmTest`, `PlainJsonTest`, `ProfileMapTest`, `ProfileMergeTest`, `ProvidersTest`, `RelayClientTest`, `SyncEngineTest`); the same script with `--integration` (the sync client against the real relay, using the runner's `python3`; no downloads); `android/build.sh`; upload `Vox-android` artifact (`Vox.apk`) |
| `release` (tags only) | ubuntu | download artifacts, publish a GitHub Release with `VoxSetup.exe` and `Vox.apk` |

Workflow permissions are `contents: read`; only `release` has `contents: write`. All third-party Actions are pinned by commit SHA (comments give the version).

Secrets: `GOOGLE_CLIENT_JSON` (Windows Google sign-in), `ANDROID_KEYSTORE_B64` (stable signing key), `ANDROID_KEYSTORE_PASS` (keystore password; the script falls back to its old default so existing keys keep working).

## Building locally

- **Windows installer flow:** `windows\build_app.bat` (Python 3.10+): makes a venv in `%LOCALAPPDATA%\Vox\venv`, installs requirements and an unpinned PyInstaller, builds, and installs to `%LOCALAPPDATA%\Programs\Vox` with a Start-menu shortcut and autostart entry. It passes the same `--paths "%~dp0..\relay" --hidden-import relay` as the workflow; `tests/test_relay_cli.py` checks that both build files keep those two flags, but no PyInstaller build with them has been run yet.
- **Android:** `ANDROID_HOME=... ./android/build.sh` produces `android/build/Vox.apk`. Steps: aapt2 compile/link, javac (source 8), d8, zip, zipalign, apksigner. If `android/vox.keystore` is missing it generates one.

## Releasing

1. Raise `versionCode` and `versionName` in `android/AndroidManifest.xml` for each Android release.
2. Update `CHANGELOG.md` (move `Unreleased` under the new version).
3. Tag and push: `git tag v1.x.y && git push origin v1.x.y`. CI builds both files (about 10 minutes) and attaches them to the release.

## Signing and updating the phone app

A phone only accepts an APK update signed with the same key as the installed one. CI builds without the `ANDROID_KEYSTORE_B64` secret create a new throwaway key each time, so such APKs need the old app uninstalled first (this clears its settings). Set the secret to make updates work.

## Windows-specific traps for contributors

- Line endings: files may show CRLF in a Windows working copy; the repository stores LF.
- Files locked by a running Vox (for example WebView2 DLLs in a venv) make `pip install` fail with "access denied"; quit Vox first.
- Never run a bare `python -` with a heredoc through a shell shim that has no stdin; write a script file instead.

## Size of the builds

Measured on 2026-09-30 by building with the CI flags (PyInstaller 6.22.3, `--onedir`) on the developer's PC; the Inno Setup installer was not built, so installer sizes are unmeasured.

| Build | Size | Files | Notes |
|---|---|---|---|
| Windows folder before | 85 MB | 1,694 | numpy 28 MB (`numpy.libs` 21 MB is OpenBLAS), Pillow 13 MB, `libcrypto` 8 MB, `python313.dll` 6 MB, Tcl/Tk about 9 MB |
| Windows folder now | 77 MB | 1,692 | the build excludes Pillow's AVIF and WebP codecs (`PIL._avif` alone was 7.5 MB), which Vox never uses (the tray icon only needs basic drawing and ICO/BMP saving) |
| Windows folder after the v2 series | 79 MB | 1,694 | same excludes; the extra 2 MB is SQLite (voice notes), measured by building `main` at `50094d0` |
| Android APK | 56 KB (the CI artifact zip) | | no libraries, no fonts; nothing left to shrink |

The rebuilt Windows app was started with a temporary settings folder: the engine and overlay came up and stayed running for 12 seconds with no errors in `vox.log`. What is left is mostly numpy, which `soundcard` (PC audio for meeting notes) and the dictation meter need; removing it would mean dropping or rewriting the meeting audio capture. Pillow's `_imagingft` (2 MB) and Tcl's `tzdata` (3 MB) could also go but were not tried.

## Relay tests in CI

Job `relay` (in `.github/workflows/build.yml`) runs `tests/test_relay.py`, `tests/test_relay_admin.py` and `tests/test_relay_proxy.py` (not `test_relay_cli.py`: it imports the Windows app's modules, so it runs in the `tests` job on Ubuntu with everything else (the Windows-only tests are skipped there; it has only been run on Windows so far); do not use a `test_relay*.py` glob here) with only pytest installed (the relay is standard library only) on Python 3.9 and 3.13 on x86 Linux and on Python 3.13 on arm64 Linux (`ubuntu-24.04-arm`, free for public repositories). This is what checks the relay on Linux, on the oldest supported Python and on the Raspberry Pi's processor family. `tests/conftest.py` puts `relay/` on the import path and skips its `vox_core` routing fixture when the Windows packages are missing. Two tests in `tests/test_relay_proxy.py` use `requests` (the Windows app's real upload through the proxy, and the chunked-upload 411 check); they call `pytest.importorskip("requests")`, so the stdlib-only relay job skips them and the `tests` job (which installs `requests`) runs them. The `tests` job installs only `requests` and `pytest` (`tests/requirements.txt`), not `numpy`: the one test that imports `windows/meeting.py` (`test_the_meeting_notes_call_through_the_relay_carries_the_hint`) puts an empty stand-in `numpy` in `sys.modules` for that import when the real one is missing (the code path it checks, `meeting._llm`, does not use `numpy`) and removes `meeting` afterwards.
