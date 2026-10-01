# Spec P9g2: install safety for a sideloaded Vox (Android)

Status: Implemented on branch `feat/p9g-notes` (task G2), **not run on a phone**. Date: 2026-10-01. Source: the research note on Android install trust (kept outside the repo, primary sources only), and the "Additions agreed" list of the part 3 plan. Decision record: [0035](../decisions/0035-sideload-warnings-are-explained-not-engineered-away.md).

## Goal
Vox is installed from a file (GitHub Releases) and types for you through an accessibility service. Two things then get in the way, and neither can be removed by code:

1. **"Restricted setting"** (Android 13 and newer): the accessibility switch is greyed out until the user opens Settings, Apps, Vox, the three dots, **Allow restricted settings**. The menu entry only appears after one failed attempt to turn the service on. Android decides this from how the package was installed (a local or downloaded file), not from anything in the app.
2. **Play Protect "App blocked" or "unknown app"** while installing an app that declares accessibility and comes from a browser or file manager. Google says such apps should be distributed through Google Play; the warning is not fixable by app changes.

What code can do: tell the user exactly what to tap, keep the manifest small and honest, and not give Play Protect extra reasons. That is all this task does.

## What was built
- **Install help card** (Android Settings, System, above Clear history; `id="install-help"`, a collapsible card in the same style as "How to set up the relay"). Three steps: the Restricted setting steps with an **Open App info** button (`V.openAppInfo()`, which opens `Settings.ACTION_APPLICATION_DETAILS_SETTINGS` for the package; the bridge method already existed), the Play Protect "More details, Install anyway" note, and the adb way (`adb install -r Vox.apk`, plus `adb shell cmd appops set com.minhaj.vox ACCESS_RESTRICTED_SETTINGS allow` marked **Optional**), each command with a Copy button. A closing line says plainly that a sideloaded app cannot fully avoid the warnings and that Google Play (closed testing) or an app store such as F-Droid are the real fixes.
- **Home setup step "Turn on the Vox bubble"** keeps its existing App info hint and now links to the card ("Blocked while installing? Install help"), which opens Settings with the card expanded.
- **Android only.** The Windows page has no install step like this, so nothing went into `ui-shared/`, and no golden rows are needed (no rule shared by both apps).
- **`VIBRATE` removed.** Nothing in `android/src` uses `Vibrator` or `VibrationEffect`; the only haptics are `View.performHapticFeedback` calls on the bubble, which needs no permission. The manifest now declares `RECORD_AUDIO`, `INTERNET`, `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MICROPHONE` and `POST_NOTIFICATIONS`, each with a use in the code (checked by `tests/test_android_install_safety.py`). `<queries>` stays (the Styles page lists launcher apps).
- **`isAccessibilityTool` is not set** (it defaults to false). Play Protect blocks an app that sets it without helping people with disabilities; Vox is a dictation tool and that claim is not ours to make. `accessibility_config.xml` was reviewed and left as it is: all four event types are handled in `onAccessibilityEvent`, `canRetrieveWindowContent` is needed by `findFocus` and `getSource`, and the description already says what is read and where it is sent.
- **Signing unchanged.** The same release key (the `ANDROID_KEYSTORE_B64` secret) must sign every build, because Android updates only same-key apps and any future developer-verification registration is tied to the package name plus that key. Back the key up. Without the secret every CI APK gets a new key and cannot update an installed Vox (already in the known issues).

## targetSdk: kept at 34, on purpose
The research recommends 35 or 36 (Play Protect warns when the target is more than two API levels below the phone: a target of 34 is fine on Android 15 and 16 but would warn on Android 17, API 37; Play's own requirement is 36). The sources compile against `platforms/android-34/android.jar` (the local SDK, `build.sh`, CI), so the code cannot be type-checked against newer APIs. The task rule was: do not raise it if a newer `android.jar` is needed. Raising it also changes behaviour that nobody has reviewed for Vox (for target 35 and up: edge-to-edge enforcement for the WebView page, foreground-service rules for the microphone service; the research did not verify each change against Vox's code).

What is needed to raise it, as a separate task:
1. Install `platforms;android-35` (or 36) next to android-34 locally and in the CI step "Install SDK parts"; point `ANDROID_JAR`, `android/build.sh` (`JAR` and `--target-sdk-version`), `run-tests.sh` and `compile-check.sh` at it. `build-tools;36.0.0` is already installed in CI.
2. Change `android:targetSdkVersion` in the manifest (the test `test_target_sdk_matches_the_build_script_and_the_installed_platform` then needs the new platform name).
3. Read the behaviour-change pages for the new level and run the device checklist of the bubble, the microphone service, the notification and the tile on a phone running it.

## Honest limits
- A sideloaded APK cannot fully avoid either warning. Real fixes: Google Play (internal or closed testing; needs a developer account, an Android App Bundle, target API 36 and an accessibility declaration) or F-Droid (own repo or the main one). Installing with adb avoids the browser and file-manager path that Play Protect's blocking check targets, and it is exempt from Google's developer verification (global rollout from 2027); that does not make it risk-free on every phone.
- Google's developer verification started on 2026-09-30 for installs from seven named app stores in four countries; a direct APK sideload is not covered yet, and the 2027 global scope should be re-checked before relying on it. The free "limited distribution" registration (20 devices, no ID) is the path for a personal app; it is not done and not needed today.
- Obtainium or the F-Droid client install through a session and mark the package source as a store, which can avoid the Restricted setting step; whether it works depends on the phone's allowlist and was not tested.

## Tests
`tests/test_android_install_safety.py` (6 tests): declared permissions equal the expected five and each has a use in the sources; `VIBRATE` absent and no `Vibrator` use; `targetSdkVersion` equals `--target-sdk-version` in `build.sh` and the platform it builds against; no `isAccessibilityTool` anywhere in the manifest or `res/xml`; the card exists with the key texts, the App info button, the Optional adb command and Copy buttons; the Home step links to it. `tests/test_ui_static.py` (ids, bridge calls) and `tests/test_ui_shared.py` still pass.

## Device checklist (for a tester; none of this has been run)
1. Settings, System, **Install help**: opens and closes; the text reads well in light and dark; Copy command puts `adb install -r Vox.apk` on the clipboard.
2. **Open App info** opens Vox's App info screen.
3. On a fresh install (or after clearing the restriction) with the service off: Home shows the setup steps; the link "Install help" jumps to Settings with the card open.
4. Install an APK downloaded in a browser on Android 13 or newer: the accessibility switch shows "Restricted setting"; after the three-dots step it can be turned on. Note the exact wording on your phone and whether the entry appears only after the first try.
5. Install the same APK with `adb install -r`: is the switch restricted? Does `appops ... ACCESS_RESTRICTED_SETTINGS allow` change anything?
6. Check haptic feedback on the bubble still works with `VIBRATE` gone (tap, long press, snap).
7. Check what Play Protect says on your phone for the same APK from the browser; note whether "Install anyway" is offered.

## Not verified
- Nothing ran on a phone: the card, the App info button, the haptics without `VIBRATE`, and every Android and Play Protect behaviour above come from the research note and the AOSP and Google pages it cites, which were read through a summarising tool; quote them only after opening the page.
- Unconfirmed in the research: whether the restriction returns on an update installed the same way, whether adb installs show a Play Protect scan prompt, whether the "App blocked" check applies in the user's country or to installs by Obtainium and the F-Droid client, whether the ECM trusted-installer list on the user's phone names any installer, whether registering under limited distribution removes any warning, and the exact Play requirements for internal testing.
- The haptics claim (no permission for `View.performHapticFeedback`) is from the Android documentation as remembered when the change was made, not re-fetched; confirm on the phone (device checklist step 6).
