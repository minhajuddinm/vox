# 0021. The relay is portable and manages itself through a web page

Status: Accepted
Date: 2026-09-30

## Context

The owner wants to run the relay on a Raspberry Pi 5 (arm64 Linux) as well as a PC, and to manage and monitor it from a browser instead of editing files and reading logs on a headless machine. ADR 0020 placed `relay.py` under `windows/`, which suggested it was Windows-only.

## Decision

- The relay lives in its own folder, `relay/` (moved from `windows/relay.py`), with a systemd unit and a set-up guide. It stays standard library only and needs Python 3.9 or newer.
- Data folders follow each platform's convention (`%APPDATA%\VoxRelay`, `~/Library/Application Support/VoxRelay`, `$XDG_DATA_HOME/vox-relay`); on POSIX the folder is `0700` and `relay.json` is created `0600` (created private, not changed afterwards). SIGTERM stops the server cleanly.
- The server serves its own management page at `/`: a static shell with no data that asks for the same bearer token and then uses `/admin/*` JSON endpoints. Status, devices and recent requests are monitored from in-memory counters; backup, compact, purge and token rotation are done through the page.
- The page is built with `textContent` only and served with a Content-Security-Policy using a fresh nonce per response (no inline handlers, no framing, `connect-src 'self'`).
- CI runs the relay tests on Python 3.9 and 3.13 on x86 Linux and on Python 3.13 on arm64 Linux (GitHub's free arm runner, since the repository is public).

## Consequences

- One file to copy to a Pi; no packages to install.
- The `/admin` endpoints use the same token as the data endpoints, so a phone that can sync can also rotate the token or download a backup. Acceptable for a single user; a separate admin token would be a later change.
- The token sits in browser storage on the device that ticks "Remember"; anyone with that browser profile has the relay.
- The systemd hardening options are untested on a real Pi.

## Alternatives considered

- A separate admin app or command-line tool: nothing to install on the phone is more useful for a headless Pi.
- A page with its own username and password: more code and a second secret; the token plus the tailnet is enough for one user.
- Binding to the Tailscale address directly instead of `tailscale serve`: skips HTTPS certificates and widens exposure; kept loopback only.
