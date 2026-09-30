# Spec P7b: relay on a Raspberry Pi, with a web page to manage it

Status: Implemented on branch `feat/relay-admin`. Date: 2026-09-30. Decision record: [0021](../decisions/0021-relay-portable-with-a-web-page.md). Protocol and page: [14-relay.md](../14-relay.md).

## Goal
Run the relay on a Raspberry Pi 5 (arm64 Linux) as well as on a PC, and manage and monitor it from a browser.

## Design
- Moved to `relay/relay.py`; per-platform data folders; private files on POSIX; SIGTERM handling; Python 3.9 compatible.
- Management page and `/admin/*` endpoints (status, activity and devices, masked profile, export, backup, compact, purge, token rotation); an optional `X-Vox-Device` header names the clients.
- `relay/vox-relay.service` (systemd, `DynamicUser`, hardened) and `relay/README.md` (Raspberry Pi guide).
- CI job `relay`: Python 3.9 and 3.13 on x86 Linux, 3.13 on arm64 Linux, standard library plus pytest only.

## Not in this step
Clients, audio, keys served or proxied, restoring a backup from the page, a separate admin token, trying the unit on a real Pi.

## Done when
Tests pass (`tests/test_relay.py`, `tests/test_relay_admin.py`); the page works in a real browser (checked on 2026-09-30 in Chromium at desktop and phone width: sign-in, all five tabs, no console errors, note text shown as text); CI passes on the four runner and Python combinations. Not checked: a real Raspberry Pi, `tailscale serve` from a phone.
