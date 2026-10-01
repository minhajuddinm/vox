# 0027. Relay proxy is per role, whitelisted, with write-only keys

Status: Accepted
Date: 2026-09-30

## Context

The user wants the AI provider key on one machine (the relay) instead of on every phone and PC, and wants the apps to keep working with the relay in the middle. A relay that forwards requests is a risk: if a client could choose the target, the relay would be an open proxy into the home network, and the token would also unlock the provider key.

## Decision

- Two roles, `stt` and `llm`, each with its own address and key in `relay.json` (`upstream`). A role's key is only ever sent to that role's address.
- Four exact routes (`POST /proxy/stt/audio/transcriptions`, `GET /proxy/stt/models`, `POST /proxy/llm/chat/completions`, `GET /proxy/llm/models`). The upstream URL is the saved address plus a fixed suffix; no part of the request chooses host, port, scheme or path. Anything else under `/proxy/` is 404.
- Keys are write-only: no endpoint, page, export, backup, log or error message returns them. A key is kept only while its address is unchanged, so saving a new address without a key drops the old key.
- The client's `Authorization` and identity headers are never forwarded; the relay builds the upstream headers from scratch.
- Hard caps: body size by route, answer 8 MB, a deadline for the whole exchange, four calls at once (the fifth is 429), no redirects.
- The apps switch with one setting, `relay_proxy`, that only changes the address and key `role_settings` returns, so every existing call path follows.

## Consequences

- Provider keys stay off the phones; one key to rotate.
- Audio and text transit the relay and are held in memory there. Keys are plain text in `relay.json` (`0600` on POSIX only).
- A token holder can re-point a role with `PUT /admin/upstream` and so make the relay GET or POST to any loopback, LAN, link-local or tailnet `http` address (and any `https` one) under a path ending in `/models`, `/chat/completions` or `/audio/transcriptions`, and read the answer. Accepted for the single-user token model; see [09-security-privacy.md](../09-security-privacy.md).
- An upstream 401 or 403 passes through, so a wrong key on the relay can look like a wrong token; the apps' hint names both.
- No streaming: answers are buffered and capped.

## Alternatives considered

- A generic pass-through (any path under `/proxy/`): flexible, but an open proxy for anyone with the token.
- One shared upstream for both roles: simpler, but speech and cleanup are usually different servers with different keys.
- Keys readable by the management page: easier to check, but the token would then also reveal the provider key.
- Keeping the key on the devices and sending it through the relay: the relay would see it on every call and it would live on every device again.
