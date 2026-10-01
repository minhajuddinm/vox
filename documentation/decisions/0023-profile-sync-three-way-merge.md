# 0023. Profile sync: a fixed set of fields, merged field by field, keys only by choice

Status: Accepted
Date: 2026-09-30

## Context

The user wants their profile (who they are, dictionary, people, and the provider settings and API keys) to follow them between devices through the relay. The relay stores one JSON document with a version and refuses stale writes ([0020](0020-relay-design.md)). Some settings must never travel (hotkey, microphone, per-app styles that name Windows programs or Android packages, the relay's own address and token). API keys are the most sensitive thing Vox holds.

## Decision

- Shared fields: `user_context`, `dictionary`, `people`, `default_style`, `cleanup`, `language`. Only with the switch `relay_sync_keys` (default off): `provider`, `base_url`, `stt_base_url`, `llm_base_url`, `stt_model`, `llm_model`, `llm_reasoning`, `api_key`, `stt_api_key`, `llm_api_key`.
- Each sync (after the notes) reads the relay profile and merges field by field against the snapshot from the last sync: a field changed only here keeps this device's value, a field changed only on the relay takes the relay's, and a field changed on both takes the relay's. With no snapshot yet (a device's first sync) a blank relay value (an empty text or list) is only the other device's default and never replaces a value that is set here. Merged values that differ locally are saved to `config.json` (the engine reloads it); if the merge differs from the relay, the document is written back with `If-Match`; a refused write (someone else wrote in between) is retried up to three times.
- Fields other devices added to the document (for example Android's own per-app styles) are kept when this device writes.
- Switching keys off removes them from the relay on the next sync and keeps them on this device.
- Settings changes trigger a sync (the engine's settings watcher), and a sync with nothing new writes nothing, so this cannot loop.

## Consequences

- One person's devices converge without merge dialogs; an edit made on two devices to the same field at the same time loses the older one.
- Anyone with the relay token can read the keys while the switch is on; the relay page hides them but the API returns them. The relay stores them unencrypted.
- A device that never opted in to keys ignores them even if the relay holds them.
- Per-app styles do not sync yet (they differ by platform).

## Alternatives considered

- Whole-document last-writer-wins: simpler, but a device that changed one field would overwrite another device's change to a different field.
- Syncing everything: would copy the hotkey, microphone and relay settings to devices where they make no sense.
- Keys always on: too risky as a default.
