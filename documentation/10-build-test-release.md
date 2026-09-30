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
| Python | `python -m pytest -q` (from the repo root) | 262 tests at the time of writing: `tests/test_*.py` |
| Java | compiled and run by CI (see below) | 5 programs in `android/test/com/minhaj/vox/`, no device, no JUnit |
| Parity | part of both suites | `spec/golden.txt` |
| Docs | `python documentation/tools/check_docs.py` | tree, config keys, links, ADR index |
| Docs checklist | `python documentation/tools/docs_todo.py` | not a test: lists the pages to update for the code you changed (see [decisions/0015-sync-docs-every-session.md](decisions/0015-sync-docs-every-session.md)) |

Local pytest tip on Windows: if pytest fails while cleaning its temp folder, run `python -m pytest -q -p no:cacheprovider --basetemp=%TEMP%\vox-pt`.

There is no local JDK requirement: CI compiles the Java helpers and tests. To run them yourself: `javac -cp <android.jar> -d out android/src/com/minhaj/vox/{GroqClient,Endpoint,Pcm,Corrections,Terms}.java android/test/com/minhaj/vox/*.java`, then `java -cp out:<android.jar> com.minhaj.vox.<TestName>` (`ParityTest` takes the path `spec/golden.txt`). `android.jar` is only needed to satisfy `org.json` imports; the tests never call it.

Not covered by tests: `engine.py`, `meeting.py`, `overlay.py`, `gcal.py`, `vcalendar.py`, `ui_app.py`, both HTML pages, `DictationService`, `VoxAccessibilityService`, `MainActivity`, `BubbleView`, `Prefs`. Verify those by hand or add tests when you touch them.

UI pages can be checked in a browser without the apps: Android's `index.html` runs with a built-in mock bridge; the Windows page needs a stub `window.pywebview.api` before it loads.

## CI (`.github/workflows/build.yml`)

Triggers: push of a tag `v*`, manual run (`workflow_dispatch`), or a pull request (the `tests` and `android` jobs only; the Windows build and release are skipped). It does **not** run on ordinary pushes; run it by hand on a branch: `gh workflow run build.yml --ref <branch>`.

| Job | Runner | Steps |
|---|---|---|
| `tests` | ubuntu | install `tests/requirements.txt`; `pytest -q`; documentation checker |
| `windows` (needs `tests`) | windows | optional Google client from secret; `pip install -r windows/requirements.txt pyinstaller==6.22.3`; PyInstaller `--onedir --windowed`; Inno Setup; upload `VoxSetup` artifact |
| `android` (needs `tests`) | ubuntu | install SDK parts; optional keystore from secret; compile and run the Java tests (`GroqClientTest`, `EndpointTest`, `PcmTest`, `CorrectionsTest`, `ProvidersTest`, `ParityTest spec/golden.txt`); `android/build.sh`; upload `Vox-android` artifact (`Vox.apk`) |
| `release` (tags only) | ubuntu | download artifacts, publish a GitHub Release with `VoxSetup.exe` and `Vox.apk` |

Workflow permissions are `contents: read`; only `release` has `contents: write`. All third-party Actions are pinned by commit SHA (comments give the version).

Secrets: `GOOGLE_CLIENT_JSON` (Windows Google sign-in), `ANDROID_KEYSTORE_B64` (stable signing key), `ANDROID_KEYSTORE_PASS` (keystore password; the script falls back to its old default so existing keys keep working).

## Building locally

- **Windows installer flow:** `windows\build_app.bat` (Python 3.10+): makes a venv in `%LOCALAPPDATA%\Vox\venv`, installs requirements and an unpinned PyInstaller, builds, and installs to `%LOCALAPPDATA%\Programs\Vox` with a Start-menu shortcut and autostart entry.
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
| Android APK | 56 KB (the CI artifact zip) | | no libraries, no fonts; nothing left to shrink |

The rebuilt Windows app was started with a temporary settings folder: the engine and overlay came up and stayed running for 12 seconds with no errors in `vox.log`. What is left is mostly numpy, which `soundcard` (PC audio for meeting notes) and the dictation meter need; removing it would mean dropping or rewriting the meeting audio capture. Pillow's `_imagingft` (2 MB) and Tcl's `tzdata` (3 MB) could also go but were not tried.
