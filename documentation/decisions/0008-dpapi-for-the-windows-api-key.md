# 0008. Protect the Windows API key with DPAPI

Status: Accepted
Date: 2026-09-29

## Context

The API key was stored as plain text in `%APPDATA%\Vox\config.json`, so a copied or backed-up file exposed it.

## Decision

Store `api_key` as `dpapi:<base64>` produced by the Windows Data Protection API (`windows/secret.py`, called through `ctypes`, no new dependency). `save_config` protects the key on every write; `load_config` unprotects it into memory and, if it finds a plain key (for example typed by hand), rewrites the file protected. If a protected value cannot be opened (different Windows user or PC) the key reads as empty and the user is asked for it again. Off Windows nothing is encrypted.

## Consequences

- The key in the file is useless outside the same Windows user on the same PC.
- Copying `config.json` to another PC means typing the key again.
- The migration edits a file the user may have edited by hand; this is documented.
- Other secrets (`google_token.json`) are not covered yet (known issue).
- Tests use a reversible fake backend so they run on any OS, plus a real round trip on Windows.

## Alternatives considered

- Windows Credential Manager: another dependency and a second store to keep in sync.
- Leave it plain text and rely on file permissions: `%APPDATA%` is user-only but backups and copies leak it.
