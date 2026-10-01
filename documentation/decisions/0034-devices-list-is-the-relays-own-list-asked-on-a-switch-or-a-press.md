# 0034. The devices list is the relay's own list, asked only on a switch or a press

Status: Accepted
Date: 2026-10-01

## Context

Yuvraj wanted a devices list in Settings on both apps, because the relay is mainly for use over Tailscale. "Devices" could mean the machines on the tailnet, or the devices that have used the relay. The Windows and Android apps also have a rule ([09-security-privacy.md](../09-security-privacy.md)): they contact a relay only when a sync or AI-server switch is on, or when the user presses a button next to the relay address. The relay already counts every client that sends `X-Vox-Device` and shows the table on its management page.

## Decision

- **The list is what the relay knows:** a new route `GET /devices` returns the relay's own devices table (name, first and last seen, request count, Tailscale login), newest first, behind the same token as every other route. The apps show only name, "this device", a state and an age. No Tailscale peer listing (it needs the Tailscale API or an OAuth key, and a tailnet device that never used Vox is not useful here).
- **Both apps compute the rows the same way** from one pure rule with golden rows ([0007](0007-shared-golden-file.md)). The state limits (active under 10 minutes, recent under a day) and the wording of the age are this step's choice.
- **The card asks the relay only when the user presses Refresh or a good Test, or by itself while "sync notes" or "Use my relay as the AI server" is on.** With both switches off, opening Settings shows "Press Refresh to ask the relay which devices have used it." and sends nothing.
- **A 401 or 403 from the Test call counts as "reachable"**; a 403 also means the token was right, because the relay checks the token before the tailnet owner. The decision is one pure rule in both apps.
- **The relay-set-up steps are one text file** (`ui-shared/relay-steps.txt`) generated into both pages like the other shared parts ([0028](0028-shared-ui-parts-are-generated-into-both-pages.md)); a test checks every command against `relay/README.md`.

## Consequences

- A device shows up only after it has sent a request with its name, so a phone that has never synced is not listed. The Android header turns non-ASCII characters into `?`, and the relay lists that phone under that spelling; the view matches both.
- A relay older than this change answers 404; the card says so instead of showing an empty list.
- A non-Vox web server that answers 401 or 403 looks "reachable" with the token refused. Accepted: the 2xx case needs a real Vox answer, and the status is all a refusal gives.
- The relay's answer carries `login` (the Tailscale user); anyone who has the token could already read it on the management page. The apps neither draw nor store it.
- The privacy rule keeps meaning "no relay is contacted without a switch or a press"; one more button (Refresh) is on the list of presses.

## Alternatives considered

- **List the tailnet's peers** (`tailscale status`, the Tailscale API): shows devices that never ran Vox, and (inferred, not tried) needs the Tailscale CLI or an API key, which a phone app does not have. Rejected by Yuvraj's choice and by the cost.
- **Ask the relay every time Settings opens:** simpler, but it would contact the relay with no switch on. Rejected for the privacy rule.
- **Put the list inside the existing `/admin/activity` answer:** it also returns the last 100 requests; the apps do not need them, and the route is the management page's.
- **Return a bare `[]` on failure from the bridge:** cannot say why. The bridge answers `{ok, error, devices}` instead.
