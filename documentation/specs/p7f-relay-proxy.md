# Spec P7f: the relay as the AI server (proxy mode)

Status: Implemented on branch `feat/relay-proxy` (Windows and Android, tested against stand-in servers only). Date: 2026-09-30. Decision record: [0027](../decisions/0027-relay-proxy-per-role-whitelisted-write-only-keys.md). Relay protocol: [14-relay.md](../14-relay.md). Security: [09-security-privacy.md](../09-security-privacy.md).

## Goal
A phone or PC can dictate through the relay, so the AI provider key lives on the relay (one place, one machine) and not on every device.

## Design
- **Relay settings (Task 15).** `relay.json` gets `upstream`: `{"stt": {"base_url", "api_key"}, "llm": {...}}`. `GET /admin/upstream` returns the address and `key_set` per role, never a key; `PUT /admin/upstream` `{role, base_url, api_key?}` sets one role. A key is kept only while the address is unchanged; `""` clears. `upstream_problem` is the address rule (same as the apps; no user name, query or fragment). The management page has an "AI server (proxy)" tab with write-only key fields.
- **Routes (Task 16).** Four exact method-and-path pairs: `POST /proxy/stt/audio/transcriptions`, `GET /proxy/stt/models`, `POST /proxy/llm/chat/completions`, `GET /proxy/llm/models` (`relay.PROXY_ROUTES`). The upstream URL is the saved address plus the fixed suffix of the route; nothing from the request changes it. The client's `Authorization` is never forwarded; the role's key is sent as `Bearer` only when one is stored. Only `Content-Type` and `Retry-After` come back.
- **Limits.** Request bodies: stt 25 MB, llm 1 MB, GET none; `Content-Length` required (411 for chunked or missing, 400 for repeated or malformed); answers at most 8 MB; whole-exchange deadline 180 s (stt), 60 s (llm), 15 s (models); four calls at once, the fifth is 429 at once; no redirect followed.
- **Errors.** 401/403 token; 404 anything else under `/proxy/`; 411, 413, 429 and 503 ("not configured") are `{"error": "text"}`; 502 (unreachable, redirect, reply too large or short) is `{"error": {"message": ...}}`; an upstream's own status and body pass through, including 401 and 403.
- **Clients (Task 17, F3).** Setting `relay_proxy` (default off, never synced). On, with `relay_url` and `relay_token` set, `role_settings` / `Providers.roleSettings` return `<relay>/proxy/<role>` and the relay token, so dictation, warm-up, model lists and the Test buttons use the relay with no other code change. The address rule is shared through `proxyurl` rows in `spec/golden.txt`. Settings shows a status line ("Turn on the relay first" or the relay in use) and hides the provider rows. 401/403 and 404 through the relay get their own hints; the Windows tray, meeting notes and Settings checks use them too.

## Not in this step
Streaming chat answers, retries inside the relay, counters on the management page, per-route content-type checks, a hard stop for the other end of a slow upload.

## Done when
`tests/test_relay_proxy.py` (stand-in upstream that records everything it receives), `tests/test_relay_admin.py`, `tests/test_providers.py`, `ProvidersTest` and `ParityTest` pass; the checks in [09-security-privacy.md](../09-security-privacy.md) hold. **Not yet seen:** a real speech or chat server, an `https` upstream with a real certificate, Python 3.9 at runtime, Linux, the systemd unit with `AF_INET6` on a Pi, a phone through `tailscale serve`, and either app dictating through a relay on a real device.
