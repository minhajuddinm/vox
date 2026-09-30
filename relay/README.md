# Vox relay

A small server that keeps your voice notes and profile in one place, so your phone and PC (and anything else on your Tailscale network) share them. One file, `relay.py`, standard library only. It runs on a Raspberry Pi (arm64 Linux), any other Linux box, macOS or Windows, with **Python 3.9 or newer**.

It listens on `127.0.0.1` only. You publish it to your own tailnet with `tailscale serve`, which adds HTTPS. Open the address in a browser for the management page (status, notes, devices and activity, profile, AI server settings, backup and clean-up, token).

The full description is in [documentation/14-relay.md](../documentation/14-relay.md). This page is the set-up guide.

## On a Raspberry Pi (or any Linux box)

You need: Python 3.9 or newer (`python3 --version`; Raspberry Pi OS Bookworm has 3.11), Tailscale already set up on the Pi, and HTTPS certificates enabled for your tailnet (Tailscale admin console, DNS page). The machine name becomes part of a public certificate log, so use a neutral name.

1. **Copy the file.**
   ```
   sudo mkdir -p /opt/vox-relay
   sudo cp relay.py /opt/vox-relay/
   ```
2. **Try it once by hand.** It prints where it listens and where the token is.
   ```
   python3 /opt/vox-relay/relay.py --data-dir /tmp/vox-relay-test --show-token
   ```
   Stop it with Ctrl+C.
3. **Install it as a service** so it starts at boot and restarts if it stops.
   ```
   sudo cp vox-relay.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now vox-relay
   systemctl status vox-relay
   ```
   The data lives in `/var/lib/vox-relay` (private to the service).
4. **Read the token** (root only):
   ```
   sudo cat /var/lib/vox-relay/relay.json
   ```
5. **Publish it to your tailnet** (this is the one that adds HTTPS; never use `tailscale funnel`):
   ```
   sudo tailscale serve --bg 8765
   tailscale serve status
   ```
   `serve status` shows the address, something like `https://yuvipi.your-tailnet.ts.net/`.
6. **Open that address** in a browser on your phone or PC (Tailscale must be running there), enter the token, and you are in.

Optional: lock it to your own Tailscale login so a shared or tagged device cannot use it even with the token:
```
sudo systemctl edit vox-relay      # add:  [Service]  then  ExecStart=  and the same ExecStart line with  --owner you@example.com
sudo systemctl restart vox-relay
```
(`--owner` is remembered in `relay.json`, so it is enough to run once with it.)

### Updating, backing up, removing

- **Update:** copy the new `relay.py` to `/opt/vox-relay/` and `sudo systemctl restart vox-relay`. The database is upgraded in place.
- **Back up:** the Maintenance tab of the management page downloads a consistent copy of the database. On a Pi with an SD card, keep one somewhere else.
- **Remove:** `sudo systemctl disable --now vox-relay`, delete `/etc/systemd/system/vox-relay.service`, `/opt/vox-relay` and `/var/lib/vox-relay`, and `sudo tailscale serve reset`.

## On Windows, macOS or a PC

```
python relay.py --show-token
tailscale serve --bg 8765
```
The data folder is `%APPDATA%\VoxRelay` on Windows, `~/Library/Application Support/VoxRelay` on macOS and `~/.local/share/vox-relay` elsewhere. Change it with `--data-dir`.

## Things to know

- Anyone who has the token and can reach the relay can read every note and the profile. Keep the token private; the Maintenance tab can make a new one at any time (all devices then need it).
- The **AI server (proxy)** tab keeps an address and a key for a speech-to-text server and a text-cleanup server. A key is write-only: you can replace or clear it, but neither the page nor any endpoint shows it again, and a backup does not contain it (it lives in `relay.json`, so keep that file private). Changing an address removes its saved key unless you type a new one. Plain `http` is accepted only for this machine, your local network and Tailscale; use `https` for anything else. The relay does not send any requests to these servers yet.
- Nothing is encrypted on disk. The systemd unit keeps the folder private to the service; on a shared machine, protect it yourself.
- The unit's hardening settings were written for a normal Raspberry Pi OS or Debian system and have not been tried on a real Pi yet; if the service will not start, `journalctl -u vox-relay` says why, and removing lines from the "It only listens" block is safe.
- Phones and PCs must have Tailscale running to reach the relay. The apps are meant to keep working when it is unreachable (they do not use it yet).
