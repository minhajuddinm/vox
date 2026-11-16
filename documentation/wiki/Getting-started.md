# Getting started

## Install

**Windows:** download `VoxSetup.exe` from [Releases](https://github.com/minhajuddinm/vox/releases/latest) and run it (per user, no admin). SmartScreen warns because the installer is unsigned: More info, Run anyway. Allow desktop apps to use the microphone in Windows privacy settings. The first start asks for a provider and key; a free Groq key comes from [console.groq.com](https://console.groq.com).

**Android:** download `Vox.apk`, open it, allow installs from that source, and choose "Install anyway" on the Play Protect warning. In Vox: add your key, allow the microphone, and turn on the accessibility service (Settings, Accessibility, Vox dictation bubble). On Android 13 and later the switch is greyed out until App info, three dots, **Allow restricted settings**. Set battery use to Unrestricted. Vox shows these steps under Settings, System, **Install help**.

## Dictate

| | Windows | Android |
|---|---|---|
| Dictate | hold **Ctrl+Win**, speak, release | tap the bubble, speak, tap |
| Cancel | **Esc** | long-press the bubble |
| Keep listening (up to 60 min) | double-tap Ctrl+Win | - |
| Voice note | **Ctrl+Alt+N** or the tray | note bubble, notification, Quick Settings tile |
| Paste the last text again | **Shift+Alt+Z** | - |

## Make it yours

- **Dictionary**: names and terms Vox should spell your way.
- **About you**: a few lines that help the cleanup get your context right.
- **Snippets**: say a short phrase, get a longer text.
- **Cleanup strength**: Light keeps your words almost as spoken; Standard tidies more. **Use raw** in History gives back exactly what was heard.
- **Own server**: Settings, a preset or **Server address**. Plain `http://` works only for localhost, LAN and Tailscale addresses.

Every feature and setting: [features](https://github.com/minhajuddinm/vox/blob/main/documentation/08-features.md), [settings](https://github.com/minhajuddinm/vox/blob/main/documentation/07-config-and-data.md). What leaves your device: [privacy](https://github.com/minhajuddinm/vox/blob/main/documentation/09-security-privacy.md).
