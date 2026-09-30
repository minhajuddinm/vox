# Spec P7a: the relay server

Status: Implemented on branch `feat/relay-server`. Date: 2026-09-30. Decision record: [0020](../decisions/0020-relay-design.md). Protocol: [14-relay.md](../14-relay.md).

## Goal
A tiny self-hosted server that stores notes with a change cursor and one versioned profile, safe to publish to one's own tailnet.

## Design
`windows/relay.py` (`RelayStore`, `Handler`, `make_server`, `load_config`, `main`), `tests/test_relay.py` (17 tests over real HTTP on localhost). Details are in the protocol page.

## Not in this step
Windows and Android clients, an outbox, audio, keys served or proxied, tray or exe integration, purge, help with `tailscale serve`.

## Done when
Tests pass; the server starts from the command line, refuses requests without the token, stores and finds a note (checked with a real process on 2026-09-30). Not checked: reaching it through `tailscale serve` from a phone.
