# Spec P8c: quick wins (Java test runner, ApiClient rename, cleanup threshold, relay from the app)

Status: Implemented in the quick-wins group (first group of the v2 part 2 plan). Date: 2026-09-30. No decision record: none of the five items chose between alternatives that a reader would question later. Follows [p7d](p7d-profile-sync.md) and [p2b](p2b-stream-long-dictations.md); the two leftovers it closes are "rename `GroqClient`" (see [p1](p1-providers-and-models.md)) and "skip cleanup for short phrases" ([p2a](p2a-keydown-warmup.md), [p2b](p2b-stream-long-dictations.md)).

## Goal
Five small, separate changes that remove friction before the larger Android notes and relay work:
1. The Java tests run the same way in CI and on a laptop, with a local JDK.
2. All Android code can be type-checked locally, not only the pure helpers.
3. The Java class `GroqClient` is called `ApiClient`, because it talks to any OpenAI-compatible server.
4. Very short dictations skip the cleanup call, and the limit is a setting.
5. The Windows app can run the relay for the user (`Vox.exe --relay` and a tray item), so a PC does not need Python for it.

## Design

### 1. One Java test runner (`android/run-tests.sh`, `android/testsrc.list`)
- `run-tests.sh` compiles the files listed in `android/testsrc.list` plus every `android/test/**/*.java` (`javac -source 8 -target 8`, output in a temporary folder), runs every `*Test` class in alphabetical order (`ParityTest` gets `spec/golden.txt`), prints one `PASS`/`FAIL` line per test and `N tests run`, and stops with a non-zero exit at the first failure. Needs a JDK and `ANDROID_JAR` (platform 34). Works from Git Bash on Windows (`cygpath` for the Windows JDK, `;` as the class-path separator there).
- The CI `android` job calls the script instead of its own `javac` loop, so CI and local runs cannot drift. A new pure class needs one line in `testsrc.list`.
- `.gitattributes` keeps `*.sh` and `*.list` at LF on Windows checkouts (bash fails on a trailing CR).
- A local wrapper outside the repo sets `JAVA_HOME` (Temurin 17 from mise) and `ANDROID_JAR` and calls the script. Only `android.jar` of platform 34 is needed, no build-tools.

### 1b. Local compile check (`android/compile-check.sh`)
Type-checks every file in `android/src` with `javac --release 8` against `android.jar`. aapt2 is not installed locally, so the script writes a stub `R.java` from the `R.<type>.<name>` uses it finds in the sources; a misspelled resource name therefore cannot fail it (only a non-fatal warning when no file under `android/res` matches), and `android/build.sh` and CI stay the authority. Prints `compile-check: OK (N files)`. Not used by CI.

### 2. Rename `GroqClient` to `ApiClient`
Class, file, test (`ApiClientTest`), the CI step, `testsrc.list`, `Prefs`, `MainActivity`, `DictationService`, the golden-file header, the `docs_todo.py` rule and the documentation. No behaviour changed: inside the class only the class name and the constructor name differ (the cleanup gate of item 3 is a separate addition to the same file).

### 3. Cleanup threshold (`cleanup_min_words`)
- Setting `cleanup_min_words` (Windows `config.json` int, default 3; Android preference stored as a string, default "3"), shown in Settings on both apps as "Skip AI cleanup for phrases shorter than N words" (a number box, 1 to 20).
- `vox_core.clean_min_words(value)` / `ApiClient.cleanMinWords(String)`: a whole number clamped to 1..20; anything that is not a whole number (`None`, empty, `banana`, `2.5`) gives 3; a huge integer clamps to 20 (Java uses `BigInteger` so it does not overflow).
- `vox_core.needs_cleanup(raw, style, enabled, min_words)` / `ApiClient.needsCleanup`: false when cleanup is off or the style is exactly `raw`; otherwise true when the transcript has at least that many words (runs of non-space characters). `process_text` and `DictationService.send` call it, replacing the hard-coded `>= 3`. A skipped phrase is handled like a failed cleanup without the warning (spoken commands, then replacements).
- Python and Java agree through ten `gate` rows in `spec/golden.txt`, run by `tests/test_parity.py` and `ParityTest.java`. Pure-function tests: `test_clean_min_words_clamps_and_falls_back_to_three`, `test_process_text_uses_the_cleanup_min_words_setting`.
- Default 3 keeps the behaviour of every earlier version. The setting is not in `PROFILE_FIELDS`, so it does not sync between devices.

### 4. The relay from the Windows app
- `vox_app.py --relay [relay.py options]` runs `relay.main(argv)` and nothing else: it is handled first in `main`, before the GUI, audio and keyboard modules are imported, so it works without a display; logs to `relay.log`. `relay.py` is bundled in the exe with `--paths ../relay --hidden-import relay` (`build_app.bat` and the workflow); from source the script adds `..\relay` to the import path.
- `windows/relay_host.py` `RelayHost(data_dir, port, notify)` runs it as a child process of the engine: `start()` (once; checks `port_busy` first), `stop()` (terminate, wait 5 s, kill), `running()`. The launcher hides the console window and puts the child in a Windows job object with kill-on-close, so it cannot outlive Vox. A child that ends within 10 s of starting is reported.
- Settings `relay_run` (bool, default false) and `relay_port` (int, default 8765). The tray item "Run relay on this PC" (`Engine.toggle_relay`) flips `relay_run`, saves it (loading the file first so the window's changes are kept) and starts or stops; `Engine.run` starts it at launch when `relay_run` is on; `Engine.quit` stops it.
- The relay's data folder is `%APPDATA%\VoxRelay`, the relay's own default, so `python relay.py` and Vox use the same relay. The first start shows `tailscale serve --bg PORT` in a tray notification; Vox never runs `tailscale` itself. The relay stays bound to `127.0.0.1` and is never published with Funnel.
- Why the port check exists: `relay.py`'s server class inherits `allow_reuse_address = 1` from `http.server.HTTPServer`, so on Windows a second relay binds a port that is already taken and raises no error.

## Not in this step
A Settings-page switch for the relay (only the tray item); pointing the PC's own sync at its own relay automatically; showing the live relay state in the tray; making `cleanup_min_words` part of the synced profile; a Windows service for the relay; any Android relay client (Group B of the plan); running the relay from Linux or macOS through the app.

## Verification done
On the merged base `6dff2c7`, from Windows, 2026-09-30:
- `python -m pytest -q`: 387 collected, 386 passed, 1 skipped (POSIX file permissions). That includes the 26 tests of `tests/test_relay_cli.py`, which start `vox_app.py --relay` as a real subprocess (with import blockers for `pystray`, `webview`, `pynput`, `sounddevice`, `tkinter` and `pyperclip`, so no GUI module is loaded), start and stop a real relay through `RelayHost` and read `/health` with the token, and kill a parent process to see its child die (job object; during the work, replacing the job-object call with `pass` made that test fail, so it does test the guarantee).
- `javatest` (runs `android/run-tests.sh`): 6 programs passed (`ApiClientTest` 43 checks, `CorrectionsTest` 16, `EndpointTest` 39, `ParityTest` 101 golden cases, `PcmTest` 12, `ProvidersTest`). A wrong `gate` row added to a temporary copy of `spec/golden.txt` made `ParityTest` fail (`line 107 (gate): expected <true> but got <false>`) and the script exit 1, so the Java side does run the new rows.
- `javatest compile` (runs `android/compile-check.sh`): `compile-check: OK (12 files)`.
- `python documentation/tools/check_docs.py`: `documentation OK`.
- A second `relay.make_server` on the port of a listening one succeeded without an error on this PC (the `SO_REUSEADDR` behaviour above). `vox_app.py --relay --data-dir <unwritable path>` exited with code 1 and wrote an `uncaught` traceback to `relay.log`.

## NOT verified
- **A frozen `Vox.exe`.** No PyInstaller build with `--paths ../relay --hidden-import relay` was made or run, so `Vox.exe --relay` is unproven; a test only checks that both build files contain those flags.
- **Anything on a device.** The Android changes (`DictationService` using `needsCleanup`, the Settings box, the `Prefs` and `MainActivity` changes) are type-checked and the pure helpers are unit tested, but nothing ran on a phone; the number box was not seen in the Android WebView.
- **The new Settings box on both pages** was not seen, neither in the Windows window nor in a browser: only the syntax of the inline page scripts was checked (`node --check`) and the change was read in the diff.
- **A real tray click.** `Engine.toggle_relay`, the tray menu item and `quit` were tested with a fake host and stubbed tray objects, not by clicking in a running Vox.
- **Over Tailscale.** Nothing reached the PC's relay from another device.
- **CI.** None of these changes had run in CI when this was written: the `android/run-tests.sh` script on the CI runner, and `tests/test_relay_cli.py` on Ubuntu, are unproven until the pull request runs.
- **The time saved** by skipping cleanup for short phrases: not measured (an estimate of one round trip).
- **Killing the relay mid-write** (hard stop with `TerminateProcess`): SQLite's atomic commits make a torn note unlikely; this was not tested.

## Known limits (also on the known-issues page)
`cleanup_min_words` does not sync between devices; the tray tick shows the saved setting, not whether the relay runs; two relays can share a port on Windows when one is started by hand after Vox's; the frozen exe is unverified. See [12-known-issues-and-roadmap.md](../12-known-issues-and-roadmap.md).
