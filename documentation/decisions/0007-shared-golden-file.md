# 0007. Keep Python and Java in step with a shared golden file

Status: Accepted
Date: 2026-09-29

## Context

The cleanup rules (prompt, sanitize, validity guard, replacements, dictionary terms, silence phrases) exist in Python and Java. A review found they had already drifted: the Java dictionary de-duplicated only the people list, the replacement word-boundary treated `_` differently, style names were case-sensitive in one language only.

## Decision

Do not share code (there is no shared runtime). Instead keep one text file, `spec/golden.txt`, of cases with expected outputs. `tests/test_parity.py` runs it against Python and `android/test/com/minhaj/vox/ParityTest.java` runs it against Java; both run in CI. Android's dictionary parsing was moved into a pure `Terms` class so it could be tested. The format is a plain tab-separated text file so the Java test needs no JSON library (`org.json` in `android.jar` is a stub that cannot run off-device).

## Consequences

- Drift in covered behaviours fails CI.
- New or changed rules must be edited in both languages and in the file, by hand; expected values are computed from the Python implementation and reviewed.
- Behaviours not in the file (address rules, correction suggestions, silence gate) rely on mirrored unit tests instead.

## Alternatives considered

- Load one JSON spec at run time in both apps: needs bundling on Windows and asset loading on Android, with no way to test the Android loading path off-device.
- Generate Java constants from a spec at build time: adds a build step and a Python dependency to the Android build.
