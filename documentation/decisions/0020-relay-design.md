# 0020. Relay: loopback server, tailnet transport, token auth, sequence cursor

Status: Accepted
Date: 2026-09-30

## Context

Users want their phone and PC to share voice notes and a profile without a cloud account. Research (2026-09-30): `tailscale serve` terminates HTTPS with a real certificate and proxies to localhost, but its identity headers can be forged by any local process that reaches the service directly and are absent for tagged devices; SQLite in WAL mode with FTS5 is enough for one user; a server-assigned sequence number avoids comparing device clocks.

## Decision

- The relay is `windows/relay.py`: standard library only, one file, listens on `127.0.0.1` only, published to the tailnet with `tailscale serve --bg`. No option to bind another address, and never Funnel.
- Auth is a random bearer token in `relay.json` (constant-time compare) plus an optional `owner` check of `Tailscale-User-Login` as defence in depth.
- Sync is a cursor on a server-assigned `seq` (`GET /changes?since=`), idempotent per-note upserts, delete markers without content, and last-writer-wins by `updated_at`.
- The profile is one opaque JSON document with an integer version; writes need `If-Match`.
- The first step (P7a) is the server and its tests only; clients come next.

## Consequences

- Any machine with Python can host it; the phone needs the Tailscale app running to reach it.
- Anyone with the token and network access can read every note; the token is only as safe as the file it is stored in (`relay.json`, `chmod 600` is a no-op on Windows).
- Nothing is encrypted at rest.
- Clients must keep working when the relay is unreachable (offline-first); not built yet.

## Alternatives considered

- FastAPI or Node: more dependencies and a larger frozen exe for no benefit at one user.
- Trusting the Tailscale identity header alone: forgeable locally, missing for tagged devices.
- CRDTs or an event log: unnecessary for one user with mostly append-only notes.
- Proxying all speech and cleanup calls through the relay (keys never leave it): planned as an option later; not in this step.
