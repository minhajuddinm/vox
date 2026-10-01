# 0026. Android notes store and sync are ports of the Windows code, with pure logic shared through golden rows

Status: Accepted
Date: 2026-09-30

## Context

The phone must store voice notes and sync them and the profile through the same relay as Windows, and the two clients must treat a note the same way, or notes would change shape every time they cross a device. The Windows logic is in Python (`notes.py`, `sync.py`); the phone is Java without Gradle, and its Android classes cannot run off a device ([0012](0012-no-gradle-android-build.md)).

## Decision

- Each Windows module is ported function by function: `notes.py` to `NotesStore`, `sync.py` to `SyncEngine`, `merge3` and `merge_profile` to `ProfileMerge`. Names and order follow the Python.
- The pure rules (`NoteLogic`: title, search string, remote-wins, device name, tag clean-up, push batch; `ProfileMerge`; `RelayError.permanent`; the profile field lists) have no Android imports and are held equal by rows in `spec/golden.txt` (`title`, `ftsq`, `remotewins`, `devname`, `merge3`, `profilefields`, `permanent`), run by `tests/test_parity.py` and `ParityTest.java`, as in [0007](0007-shared-golden-file.md). Expected values are produced by running the Python.
- The sync engine is written against interfaces (`RelayApi`, `SyncStore`, `SyncConfig`) so it runs off-device with an in-memory store and a fake relay. The relay client reads and writes JSON with a small pure class, `PlainJson`, not `org.json`, so it also runs off-device, over a real local HTTP server.
- `RelayIntegrationTest` runs the engine and client against the real `relay/relay.py`, only with `--integration`.
- When the port finds a flaw in the Windows code, the Windows code is fixed first and the golden file gets the new rule (a note the relay refuses for good no longer blocks the others, on both sides).

## Consequences

- The only code that cannot be tested off-device is the thin Android layer (`NotesStore` on a real SQLite, `SyncWorker`, `HttpURLConnection` on Android, the bridge, the services); it is type-checked by `compile-check.sh` and still needs a device.
- A rule can still differ where no golden row covers it: tag clean-up (Android caps at 20 tags, Windows has no cap), and the Unicode version behind "word" in titles and search ([12](../12-known-issues-and-roadmap.md)).
- Two codebases follow one behaviour; a rule changed on one side without a golden row will drift.

## Alternatives considered

- Share one implementation (for example Python on the phone): not possible without a runtime on the phone.
- A looser port with a shared protocol only: simpler, but sync bugs (a lost edit, a duplicate) would then be found on the phone first.
- `org.json` in `RelayClient`: smaller, but its classes in `android.jar` throw in plain Java, and the profile snapshot needed a JSON writer in the pure engine anyway.
