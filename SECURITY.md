# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability** (GitHub private vulnerability reporting). Only the maintainers can see the report. If that button is not shown, open a public issue that only asks the maintainers to enable private reporting, without any details of the problem.

Please include what is affected (Windows app, Android app, relay, CI), the version or commit, the steps to reproduce, and what an attacker could do. Do not include real API keys, tokens or personal data; use made-up values.

This is a volunteer project. We will acknowledge a report as soon as we can, agree on a fix and a disclosure date with you, and credit you unless you prefer otherwise.

## Supported versions

Only the latest commit on `main` and the latest release are supported. Fixes are not back-ported.

## Trust model in short

- **No Vox server.** The apps talk only to the speech and cleanup servers the user configures (Groq by default), to an optional relay the user runs, and (Windows, optional) to Google Calendar or an ICS link. There is no analytics or crash reporting.
- **API keys:** on Windows they are protected with DPAPI (only the same Windows user on the same PC can read them); on Android they sit in the app's private storage, unencrypted. Plain `http://` is accepted only for loopback, local-network and Tailscale addresses; everything else needs `https://`.
- **The relay** (`relay/relay.py`) listens on `127.0.0.1` only and is meant to be published to your own tailnet with `tailscale serve` (never Funnel). One bearer token protects every data and management endpoint (the page shell at `/` holds no data); anyone holding it can read all notes and the profile and can point the relay's AI-server routes at other addresses on your network. Treat the token like a password and share it only with your own devices. Upstream keys are stored in plain text in `relay.json`.
- **The Android accessibility service** reads only the focused text field and the current app's package name, to insert text. It does nothing in password fields.

Known gaps and the full description: [documentation/09-security-privacy.md](documentation/09-security-privacy.md), the relay page [documentation/14-relay.md](documentation/14-relay.md) and the relay set-up guide [relay/README.md](relay/README.md).

## Out of scope

- Problems that need an attacker who already controls the user's Windows account, the phone, or the relay machine.
- Data handling by the AI provider the user chose (covered by that provider's policy).
- The warnings Windows and Android show for an unsigned installer or a sideloaded APK (documented in the README).
