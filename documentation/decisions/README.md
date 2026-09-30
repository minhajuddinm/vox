# Architecture decision records

Short records of decisions that a future reader would otherwise question. One file per decision, numbered, never rewritten: if a decision changes, add a new record that supersedes the old one and set the old one's status to `Superseded by NNNN`.

Records 0001 to 0004 describe choices made by the original author; their reasons are marked **inferred** where the code does not state them. Records 0005 onward were made during the improvement series merged as PR 1 (see [../../CHANGELOG.md](../../CHANGELOG.md)).

| No. | Title | Status |
|---|---|---|
| [0001](0001-openai-compatible-api-groq-default.md) | OpenAI-compatible API with Groq as the default | Accepted |
| [0002](0002-two-independent-apps.md) | Two independent apps (Python and Java) | Accepted |
| [0003](0003-android-accessibility-bubble.md) | Android: accessibility overlay bubble and text insertion | Accepted |
| [0004](0004-windows-two-process-model.md) | Windows: engine process plus window process | Accepted |
| [0005](0005-configurable-endpoint-private-http.md) | Configurable server address; plain http only for private hosts | Accepted |
| [0006](0006-app-name-only-to-the-model.md) | Send the app name, never the window title | Accepted |
| [0007](0007-shared-golden-file.md) | Keep Python and Java in step with a shared golden file | Accepted |
| [0008](0008-dpapi-for-the-windows-api-key.md) | Protect the Windows API key with DPAPI | Accepted |
| [0009](0009-keep-failed-recordings-and-retry.md) | Keep failed recordings and offer retry | Accepted |
| [0010](0010-job-token-for-android-dictation-state.md) | Job ids for Android dictation state | Accepted |
| [0011](0011-silence-gate-before-upload.md) | Silence gate before upload | Accepted |
| [0012](0012-no-gradle-android-build.md) | Build the APK with plain SDK tools | Accepted |
| [0013](0013-raw-fallback-when-cleanup-fails.md) | Raw transcript is the fallback when cleanup fails | Accepted |
| [0014](0014-documentation-checked-in-ci.md) | This documentation is machine-checked in CI | Accepted |
| [0015](0015-sync-docs-every-session.md) | Sync the documentation at the end of every session | Accepted |
| [0016](0016-per-role-server-and-model-discovery.md) | Per-role server and model discovery by id | Accepted |
| [0017](0017-warm-connections-not-an-open-microphone.md) | Warm the connections at key-down; never keep the microphone open | Accepted |
| [0018](0018-about-you-context-in-the-prompt.md) | "About you" context: constant, fenced, capped | Accepted |
| [0019](0019-voice-notes-in-sqlite.md) | Voice notes in SQLite, separate from history and meetings for now | Accepted |
| [0020](0020-relay-design.md) | Relay: loopback server, tailnet transport, token auth, sequence cursor | Accepted |
| [0021](0021-relay-portable-with-a-web-page.md) | The relay is portable and manages itself through a web page | Accepted |
| [0022](0022-sync-client-dirty-flag-and-cursor.md) | Sync client: a dirty flag per note and the relay's cursor | Accepted |
| [0023](0023-profile-sync-three-way-merge.md) | Profile sync: fixed fields, field-by-field merge, keys only by choice | Accepted |
| [0024](0024-stream-long-dictations-in-pieces.md) | Long dictations are transcribed in pieces while the user speaks | Accepted |

## Template

```
# NNNN. Title

Status: Accepted | Superseded by NNNN | Deprecated
Date: YYYY-MM-DD

## Context
What forced a decision.

## Decision
What we do.

## Consequences
What gets easier, what gets harder, what to watch.

## Alternatives considered
Short list with why not.
```
