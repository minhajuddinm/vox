# Spec P7d: profile sync (Windows)

Status: Implemented on branch `feat/sync-profile`. Date: 2026-09-30. Decision record: [0023](../decisions/0023-profile-sync-three-way-merge.md). Relay protocol: [14-relay.md](../14-relay.md).

## Goal
The "About you" text, dictionary and people (and, if the user chooses, the provider settings and API keys) are the same on every device.

## Design
`windows/sync.py`: `PROFILE_FIELDS`, `PROFILE_KEY_FIELDS`, `shared_fields`, `merge3`, `sync_profile` (called from `sync_once` after the notes; retries a refused write up to three times). Local snapshot and version in `sync_meta` (`profile_snapshot`, `profile_version`). New setting `relay_sync_keys`. Settings switch "Also share my provider settings and API keys". `Engine.reload_if_changed` triggers a sync.

## Not in this step
The Android side, per-app styles (they differ by platform), a UI showing what changed, encryption of the profile on the relay.

## Done when
`tests/test_sync_profile.py` passes (two devices through a real relay: travel, edits follow, merge and clash, keys off by default and removal on switch-off, fields from other devices kept, a raced write retried); Yuvraj sees the About you text arrive on a second device. Not seen on a screen yet.
