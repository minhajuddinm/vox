# 0035. Sideload warnings are explained in the app, not engineered away

Status: Accepted
Date: 2026-10-01

## Context

A sideloaded Vox meets "Restricted setting" (Android 13 and newer) on the accessibility switch and Play Protect's "App blocked" or "unknown app" prompt. Both come from how the APK was installed, not from the manifest (research note on Android install trust, primary sources). Two tempting app-side moves exist: declare the service an accessibility tool (`isAccessibilityTool="true"`) and raise `targetSdkVersion` to the current level.

## Decision

- **Explain, do not evade.** The Settings page has an Install help card with the exact taps for Allow restricted settings (and a button to the App info screen), the Play Protect "Install anyway" step and the adb alternative, and the docs say that a sideloaded APK cannot fully avoid the warnings.
- **No `isAccessibilityTool`.** Play Protect blocks apps that set it without assisting users with disabilities. Vox is a dictation tool; the claim is not ours to make. Cost: on Android 16 and newer, views that an app marks `accessibilityDataSensitive` are hidden from non-tool services, so Vox may not see such fields.
- **targetSdk stays 34 until the build platform moves.** The sources compile against `platforms/android-34` only. Raising the number without compiling against the newer platform and reviewing its behaviour changes would change behaviour blind. The steps to raise it are in [the spec](../specs/p9g2-install-safety.md).
- **Permissions are the minimum.** `VIBRATE` was removed (only `View.performHapticFeedback` is used). A test fails when a declared permission has no use in the code.
- **The signing key never changes** (update continuity and any later developer-verification registration depend on it).

## Consequences

- Users still see the warnings and must tap through them; the card shortens the search, it does not remove the step.
- On Android 17 (API 37) Play Protect may show "built for an older version" for a target of 34 until the target is raised.
- Real fixes stay open as later work: Google Play closed testing, an F-Droid repo, or Obtainium on the phone.

## Alternatives considered

- Set `isAccessibilityTool="true"`: removes the hidden-views limit but risks a block and a false claim.
- Raise targetSdk to 36 now: matches Play's requirement but needs a new android.jar, a CI change and a device pass, none of which fit this task.
- Tell users to turn Play Protect off: weakens their phone and does not touch the Restricted setting block.
