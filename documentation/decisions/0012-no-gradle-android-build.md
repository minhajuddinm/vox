# 0012. Build the APK with plain SDK tools

Status: Accepted
Date: 2026-09-27 (original design; recorded 2026-09-30)

## Context

The Android app is small (a handful of Java files, one HTML screen, no libraries). It is built on GitHub Actions; there is no Android SDK on the maintainers' PCs.

## Decision

`android/build.sh` calls the SDK tools directly: `aapt2` (compile and link resources), `javac` (source 8, against `android.jar`), `d8`, `zip`, `zipalign`, `apksigner`. No Gradle, no AndroidX, no Kotlin. (Reason **inferred**: nothing needs a dependency manager; it keeps the build one readable script.)

## Consequences

- Tiny APK (tens of KB) and a build that takes seconds after the SDK is installed.
- No dependency resolution, no Android Studio project files, no instrumentation tests. Pure logic is therefore kept in plain Java classes that a normal JDK can test (see [0007](0007-shared-golden-file.md) and [../10-build-test-release.md](../10-build-test-release.md)).
- Library-heavy features (Jetpack, Kotlin, Compose) are unavailable without changing the build.
- The signing key is a keystore file (`android/vox.keystore`, git-ignored) fed from a CI secret; a missing keystore makes the script generate a throwaway one.

## Alternatives considered

- Gradle with Android Studio: heavier than the project needs.
