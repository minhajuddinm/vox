# 0005. Configurable server address; plain http only for private hosts

Status: Accepted
Date: 2026-09-29

## Context

The original code hard-coded Groq. The owner wants to point Vox at Whisper and a chat model running on his own PC (reached from the phone over Tailscale), with Groq as the fallback. Such servers usually speak plain `http://`, but Android 9+ blocks cleartext by default and sending an API key and voice over plain http on the internet would be unsafe.

## Decision

- New setting `base_url` on both platforms (blank or the Groq address means Groq). The key becomes optional when the address is not Groq's.
- `http://` is accepted only for private hosts: this device, private LAN ranges, link-local, Tailscale (`100.64.0.0/10`, `*.ts.net`), single-label names, `.local`, `.lan`. Everything else must use `https://`. Implemented as `vox_core.endpoint_error` / `is_private_host` and `Endpoint.error` / `isPrivateHost`, checked before every request and when saving the setting.
- Android: `network_security_config.xml` permits cleartext for the whole app (the platform cannot express ranges); the code check above is the real gate.

## Consequences

- A self-hosted server works with no Vox-side certificates.
- The API key never travels in clear over the internet, but one Android-wide cleartext allowance exists; any new code that makes HTTP requests must call `Endpoint.error` first.
- The Python and Java implementations differ slightly on exotic reserved ranges (known issue; not in the golden file yet).
- Privacy page and README were updated: audio, text and key go wherever the address points.

## Alternatives considered

- Allow only https: blocks the intended home-server setup without certificates.
- Per-domain entries in the network security config: cannot cover LAN or Tailscale IPs.
