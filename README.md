# Vox

[![build](https://github.com/minhajuddinm/vox/actions/workflows/build.yml/badge.svg)](https://github.com/minhajuddinm/vox/actions/workflows/build.yml)
[![latest release](https://img.shields.io/github/v/release/minhajuddinm/vox?label=release)](../../releases/latest)

Voice dictation for **Windows** and **Android**. Speak, and cleaned-up text appears in whatever app you are typing in: email, chat, documents, code editors, browsers.

Vox has no server and no account of its own. It sends your audio to a speech-to-text server and the text to a cleanup model that **you** choose: Groq's free tier by default, any other OpenAI-compatible provider, or your own machine. You use your own API key and your own free quota.

Vox was written by Muhammad Minhajuddin ([minhajuddinm](https://github.com/minhajuddinm)). The v2 work (providers, voice notes, the relay, keep listening and more) was added by [Yuvi-5](https://github.com/Yuvi-5) with Claude Code. See [CHANGELOG.md](CHANGELOG.md).

**Jump to:** [Features](#features) · [Status and known limits](#status-and-known-limits) · [Install](#install) · [Using Vox](#using-vox-on-windows) · [Privacy](#privacy) · [Build and test](#build-and-test-from-source) · [Contributing](#contributing) · [License](#license)

---

## Features

### On both apps

- **Dictate into any app.** Windows: hold a shortcut (Ctrl + Win by default; Right Ctrl, Right Alt, Ctrl + Alt and Ctrl + Shift are the other choices), speak, release. Android: tap the floating mic bubble, speak, tap again.
- **Cleanup that keeps your words.** The cleanup model is told to copy what you said word for word and fix only punctuation, capitals, spelling and paragraphs. A check compares the answer with what you said; if words were lost, Vox types your words as spoken instead. **Light** (default) keeps fillers; **Standard** removes them and self-corrections. History has a **Use raw** button.
- **Your choice of provider.** Presets for Groq (free tier), OpenAI, OpenRouter, Together AI, Mistral, Ollama, LM Studio and a Speaches / faster-whisper server, or any OpenAI-compatible address. Speech and cleanup can use different servers and keys. The model list comes from the server, and a **Test** button checks each one.
- **Tone per app.** Formal, Neutral, Casual, Very casual or Raw (no cleanup), chosen by the app you type in.
- **Personal dictionary.** Your words, people's names and `wrong => right` replacements. Vox puts a one-word term back in your spelling when the text has it in another case (five letters or more) or one letter off (seven letters or more).
- **Learn from my corrections.** Fix a word in the text Vox just typed (within 3 minutes, before you send it) and Vox adds the fix to your dictionary, with a message saying so. Works in fields that show their text to the system (Notepad, Word, browsers, most Android apps), not in terminals. Only the changed words are kept; a Recently learned list on the Dictionary page undoes one. On by default; switch in Settings, Privacy.
- **About you.** A short text about yourself (names, jargon, languages you mix) that the cleanup model reads first.
- **Spoken commands.** "new line", "new paragraph", "comma", "question mark".
- **Lists and paragraphs.** Say "first ... second ...", "point one ... point two", "bullet point ..." or "pehla ... doosra ..." and Vox writes a numbered or bulleted list, keeping every other word; commas alone never make a list. On Windows a long pause starts a new paragraph in a long dictation. Off, Auto or Lists only in Settings.
- **Snippets.** Say a phrase such as "my email" or "my signature" and Vox types the text you saved for it. The saved text is added after the cleanup, so it is never sent to the cleanup server.
- **Nothing is lost.** A failed dictation is kept and can be retried. A silent recording is not uploaded.
- **History and a Speed card.** Search, copy and fix past dictations; see where the time of a dictation goes. Timings stay on the device.
- **Optional relay (self-hosted).** A one-file server you run on a Raspberry Pi or any PC on your own Tailscale network. It syncs voice notes and your profile (About you, dictionary, people) between devices, and can act as the AI server so your provider key lives only on the relay. See [relay/README.md](relay/README.md).

### Windows only

- **Code mode.** In code editors and terminals, "camel case user name" types `userName`, "snake case max retries" `max_retries`, "open paren" `(`, "equals equals" `==`, and what you say there is not sent to the AI cleanup unless you choose it ([the whole table](documentation/15-code-mode.md)).
- **Keep listening.** Double-tap the shortcut and talk for up to 60 minutes. **Note** (default) saves one cleaned note at the end; **Type** types each piece into the app you started in. A second shortcut (Ctrl + Alt + N) starts and stops a note.
- **Voice notes.** Record a note that is saved to a searchable list instead of being typed.
- **Meeting notes (beta).** Records your microphone and the PC's audio, shows a live transcript, answers questions about the meeting, and writes a summary with decisions and action items. Optional Google Calendar or ICS link.
- **Improve my cleanup.** A stronger model reads some of your saved dictations and suggests dictionary words and cleanup rules. It sends nothing until you press **Run once** and then **Send**, and you pick what to keep.
- **Shortcuts.** Hold or tap (a quick tap starts hands-free dictation, if you choose it), an optional hands-free shortcut (off by default; for example Ctrl + Win + H), Esc to cancel, Shift + Alt + Z to paste your last dictation again, an optional copy-last shortcut, and **edit by voice** (experimental, off by default: select text, hold a shortcut, say what to change).
- Microphone choice, API key protected by your Windows login (DPAPI), paste only into the window you started in (Ctrl + Shift + V in terminals), uploads while you speak and FLAC to Groq and OpenAI, and a tray item to run the relay on the PC.

### Android only

- A floating bubble that types through Android's accessibility service, with a watchdog that puts it back when Android drops it and a diagnostics card.
- Voice notes from a note bubble, a notification or a Quick Settings tile, with a Notes page.
- An **Install help** card that explains the warnings a sideloaded app gets (see [Install on Android](#android)).

---

## Screenshots

No screenshots are in the repository yet. These are the ones to take (made-up text only: no real names, keys, tokens, host names or personal dictations). Save them under `docs/screenshots/` with these names:

| File | What it shows |
|---|---|
| `windows-pill.png` | The recording pill at the bottom of the screen, with the live waveform, while dictating into a text editor |
| `windows-home.png` | The Vox window, Home: the Status card and the Speed card |
| `windows-providers.png` | Settings, AI providers: a preset, the model list and a passed Test |
| `windows-dictionary.png` | Dictionary: Words, People, Replacements and the About you box |
| `windows-keep-listening.png` | The pill showing `Listening m:ss` with the stop square |
| `windows-voice-notes.png` | The Voice notes page with two or three notes and the search box |
| `windows-improve.png` | Settings, Improve my cleanup, at the confirm step ("This sends N transcripts ...") |
| `windows-meeting.png` | Notes (meeting notes, beta) with a live transcript of a made-up meeting |
| `android-bubble.png` | The mic bubble next to a text field in a messaging app |
| `android-home.png` | The Android Home tab: setup steps and the Status card |
| `android-install-help.png` | Settings, System, Install help, opened |
| `android-notes.png` | The Android Notes page |
| `relay-page.png` | The relay's management page, Status tab (token not shown) |

---

## Status and known limits

The Windows app and the earlier Android app have been used on real devices, but much of the newest work has only been checked by unit tests. Please read this before you rely on it.

- **The latest release is older than most of this README.** Release v1.2.0 (2026-09-28) predates the v2 work (providers, voice notes, the relay, keep listening, Improve my cleanup, the Android notes and bubble fixes). Until a new release is tagged, build from source to get them.
- **Android v2 features have not been run on a phone.** Note mode and the note bubble, relay sync, the speed work, the bubble watchdog and diagnostics, and the Install help card are unit-tested and compile-checked only. Device checklists: [p9b](documentation/specs/p9b-measure-and-speed-up.md), [p9c](documentation/specs/p9c-devices-and-relay-setup.md), [p9d](documentation/specs/p9d-android-bubble.md), [p9g](documentation/specs/p9g-note-bubble.md), [p9g2](documentation/specs/p9g2-install-safety.md).
- **Keep listening and Improve my cleanup have not run with a real microphone or model.** Checklists: [p9e](documentation/specs/p9e-keep-listening.md), [p9f](documentation/specs/p9f-improve-my-cleanup.md).
- **The new cleanup prompt and the word check have not been measured on a real model.** The thresholds were tuned on written examples ([p9a](documentation/specs/p9a-cleanup-keeps-my-words.md)).
- **The relay has not run on a real Raspberry Pi or over Tailscale**, and the Windows build that runs the relay (`Vox.exe --relay`) has not been built and started yet. Its tests run on Linux x86 and arm64 in CI.
- **Lists, code mode and snippets have only been tested on text**, not on real speech, in a real editor or on a phone; how Whisper writes the spoken cues and symbol names on real speech is unconfirmed.
- **Meeting notes are beta** and have no automated tests.
- The Windows installer is not code-signed, and the Android APK is installed from a file, so both get warnings (see [Install](#install)).
- On Android the API keys, relay token and history are stored unencrypted inside the app's private storage.

The full, current list is in [documentation/12-known-issues-and-roadmap.md](documentation/12-known-issues-and-roadmap.md).

---

## Install

### Windows

1. Open **[Releases](../../releases/latest)** and download **`VoxSetup.exe`**.
2. Run it.
   - Windows may show *"Windows protected your PC"* because the installer is not code-signed. Click **More info**, then **Run anyway**.
   - No administrator rights are needed. Vox installs for your user only.
3. Leave **"Start Vox when I sign in to Windows"** ticked, then click **Install** and **Finish**.
4. Allow the microphone: Windows **Settings**, **Privacy & security**, **Microphone**: turn on **Microphone access**, **Let apps access your microphone** and **Let desktop apps access your microphone**.

Vox runs in the system tray (near the clock; click **^** if you do not see it). On first start it asks for an AI provider and an API key (release v1.2.0 asks for a Groq key directly).

**A free Groq key (2 minutes):** sign in at [console.groq.com](https://console.groq.com) (no credit card), open **API Keys**, **Create API Key**, copy it (it starts with `gsk_`; Groq shows it once) and paste it into Vox. Keep it private: anyone with it can use your quota. If it leaks, delete it on console.groq.com and make a new one.

### Android

1. On your phone, open **[Releases](../../releases/latest)** and download **`Vox.apk`**. Open the downloaded file.
   - "Not allowed to install from this source": tap **Settings**, turn on **Allow from this source**, go back.
   - Play Protect warning: tap **More details**, then **Install anyway**. It warns because Vox is not from the Play Store and uses an accessibility service.
2. Open Vox. The Home tab walks you through: add your API key, allow the microphone, turn on the bubble.
3. **Turn on the Vox bubble:** Settings, Accessibility, Downloaded apps (or Installed apps), **Vox dictation bubble**, On.
   - If the switch is greyed out or says **Restricted setting** (Android 13 and newer): tap it once so Android shows the message, then open **Settings, Apps, Vox, the three dots (top right), Allow restricted settings**, confirm, and turn the switch on again.
4. Start the dictation service from the Home tab. Optional but recommended: set Vox's battery use to **Unrestricted** so Android does not stop it in the background.

The same steps, with an **Open App info** button and the adb way (`adb install -r Vox.apk`), are in Vox under Settings, System, **Install help**.

**Plainly:** an app installed from a file cannot fully avoid these warnings; they come from how it was installed, not from the app. The real fixes are Google Play or an app store such as F-Droid, and neither is set up. Details: [documentation/specs/p9g2-install-safety.md](documentation/specs/p9g2-install-safety.md).

To update, install the new `Vox.apk` over the old one. If Android refuses the update, the new file was signed with a different key: uninstall the old Vox first (this clears its settings).

---

## Using Vox on Windows

| What | How |
|---|---|
| Dictate | Click into a text box, **hold Ctrl + Win**, speak, **release**. The text is pasted about a second later |
| Keep listening | **Double-tap Ctrl + Win** and speak. Double-tap again or say "stop listening" to end it and save; **Esc** cancels it without saving |
| Hands-free dictation | Settings, "A quick tap of the dictation shortcut": Hold or tap (a tap starts, the next press sends), or set a hands-free shortcut in Settings (off by default; for example Ctrl + Win + H). **Esc** cancels any recording |
| Paste the last dictation again | **Shift + Alt + Z** |
| Voice note | **Ctrl + Alt + N** to start, again to stop; or the tray menu |
| Open the window | Double-click the tray icon, or search **Vox** in the Start menu |

While you speak, a small pill at the bottom of the screen shows a live waveform; amber dots mean Vox is working; a green check means the text landed. If you switch to another window before the text is ready, Vox copies it instead of pasting it and says "Copied; the window changed".

The window has **Home** (status, speed, history), **Dictionary**, **Styles**, **Voice notes**, **Notes** (meeting notes, beta) and **Settings**. Changes apply within a second.

### Troubleshooting

| Problem | Fix |
|---|---|
| Nothing happens when I hold the shortcut | Check the tray icon is there. If not, start Vox from the Start menu |
| "Vox did not hear anything" | Pick your microphone in Settings; the message shows how loud the recording was |
| The pill appears but no text is pasted | Click into a text box first. Vox cannot type into apps running as administrator |
| "The server rejected the API key" | Settings, paste the key again, **Test** |
| "Rate limit reached" | You reached your provider's limit. Wait, or use another key or provider. When only cleanup fails, Vox types your words as spoken |
| Wrong words for names or terms | Add them in **Dictionary**, or add a replacement |
| Words in the wrong language | Settings, **Language**, pick your language instead of Auto detect |
| Another app also uses Ctrl + Win | Change the shortcut in Vox Settings, or in the other app |

Logs are in `%APPDATA%\Vox\vox.log` (paste that into the File Explorer address bar). Check a log for personal text before you attach it to an issue.

**Update:** run the new `VoxSetup.exe`; settings, key, history and notes are kept. **Uninstall:** Windows Settings, Apps, Installed apps, Vox, Uninstall. To remove your data too, delete `%APPDATA%\Vox` and `Documents\Vox Notes`.

### Using your own server

In Settings, choose a preset or enter a **Server address** (for example `http://100.x.y.z:8000/v1`, a Whisper server on your own PC reached over Tailscale), the speech and cleanup models, and a key if your server needs one. Plain `http://` is accepted only for this PC, your local network and Tailscale addresses (100.64.0.0/10, `*.ts.net`); anything else must use `https://`. Android has the same fields.

## Using Vox on Android

Tap any text box and the round mic bubble appears at the edge of the screen. **Tap** it (it turns red), speak, **tap** again; it turns amber while working, then the text appears. **Long-press** to cancel, **drag** to move it. If an app blocks typing (some banking apps), Vox copies the text so you can paste it. If the bubble disappears, set battery use to Unrestricted, and look at Settings, System, **Bubble diagnostics**.

---

## Privacy

Short version (the full page is [documentation/09-security-privacy.md](documentation/09-security-privacy.md); the public policy is [docs/privacy.html](https://minhajuddinm.github.io/vox/privacy.html)):

- **No Vox server, no analytics, no accounts.** Nothing is sent to the developers.
- **Your audio** goes to the speech server you chose. **The text** of what you said, your About you text, dictionary, people and the name of the app you are typing in go to the cleanup server you chose. The dictionary and people also go to the speech server as a spelling hint. Never the window title.
- **The relay** gets data only if you switch it on: voice notes and your profile for sync; audio and text in transit if you use it as the AI server. API keys go there only if you turn on a separate switch (off by default).
- **Learn from my corrections** (both apps, **on by default**; Settings, Privacy): for up to 3 minutes after Vox types, it reads the text of the focused field in that window or app (up to 20,000 characters, in memory only; password fields never) to learn the words you fix. Only the fixed words are kept, in your dictionary, which goes to your speech and cleanup servers as hints and to your relay when sync is on.
- **Edit by voice** (Windows, experimental, off by default) sends the selected text and your instruction to the cleanup server. Vox copies the selection with Ctrl+C, so Windows clipboard history (and the cloud clipboard, if Windows clipboard sync is on) can keep it.
- **Improve my cleanup** (Windows) is the only feature that sends saved dictations, and only after you press **Run once** and **Send**.
- **On your PC:** settings, history and notes are in `%APPDATA%\Vox` and `Documents\Vox Notes`, as plain files; the API keys are protected by DPAPI. Keep-listening audio is written to `%APPDATA%\Vox\listen` while it runs and stays there after a crash until you recover or delete it.
- **On Android:** keys, relay token and history are in the app's private storage, unencrypted.
- Your provider's handling of the data is covered by its own policy.

---

## Build and test from source

You need Python 3.13 (the Windows app needs Python 3.12 or newer: `numpy==2.5.3` has no wheel for 3.11), and for the Java tests a JDK 17 and Android's `android.jar` (platform 34). No Gradle and no full Android SDK are needed for day-to-day work.

```
python -m venv .venv
.venv\Scripts\pip install -r windows\requirements.txt -r tests\requirements.txt
.venv\Scripts\python windows\vox_app.py            # run the Windows app from source
.venv\Scripts\python -m pytest -q                  # Python tests (from the repo root)
.venv\Scripts\python documentation\tools\check_docs.py   # documentation checker (CI runs it)
```

On Windows, if pytest fails while cleaning its temp folder, add `-p no:cacheprovider --basetemp=<a folder outside the repo>`. The tests write settings to `%APPDATA%`; point `APPDATA` at a temporary folder if you do not want that.

Java tests (the Android logic, no device): with a JDK 17 on `PATH` and `ANDROID_JAR` set to `platforms/android-34/android.jar`, run `bash android/run-tests.sh` from the repo root (add `--integration` to test the sync client against the real relay). `bash android/compile-check.sh` type-checks every Android source.

Builds: `windows\build_app.bat` builds and installs the Windows app locally; `ANDROID_HOME=... ./android/build.sh` builds the APK (needs build-tools 36). CI ([.github/workflows/build.yml](.github/workflows/build.yml)) runs the tests on every pull request and builds both apps and a GitHub Release when a `v*` tag is pushed. Details, signing and releasing: [documentation/10-build-test-release.md](documentation/10-build-test-release.md).

Never commit `config.json`, `google_client.json`, `.env` files, `relay.json`, `relay.db`, keystores (`*.keystore`, `*.jks`, `*.p12`), `*.pem` files or API keys. All are in `.gitignore`, wherever in the tree they are.

---

## Repository map

| Path | What it is |
|---|---|
| `windows/` | Windows app (Python): engine, tray, pill, window (`ui/index.html`), meeting notes, installer script |
| `android/` | Android app (plain Java, no Gradle): `src/`, `assets/index.html` (the screens), `test/` (plain-Java tests), build and test scripts |
| `relay/` | The optional self-hosted relay (`relay.py`, standard library only) and its set-up guide |
| `spec/golden.txt` | Expected results shared by the Python and Java tests, so both apps behave the same |
| `tests/` | pytest tests |
| `ui-shared/` | Styles and helpers shared by both apps' pages (generated into them by `tools/sync_ui.py`) |
| `tools/` | Repo scripts: the shared-UI generator and the cleanup benchmark |
| `documentation/` | Developer documentation: architecture, every file and setting, features, security, decisions, specs. Start at [documentation/README.md](documentation/README.md) |
| `docs/` | The public website (GitHub Pages) and privacy policy |
| `.github/` | CI workflow, issue forms and the pull request template |

Coding agents: start at [AGENTS.md](AGENTS.md).

---

## Contributing

Bug reports, device test results and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first: it covers setup, the tests, the shared golden file and the rule that documentation changes with the code. For phone and PC test results against the checklists above, use the **Device test report** issue form.

- Security problems: please report them privately, see [SECURITY.md](SECURITY.md).
- Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Vox is released under the [MIT License](LICENSE): Copyright (c) 2026 Vox contributors. You may use, copy, modify and redistribute it, as long as the licence text stays with it. It comes with no warranty. Contributions are accepted under the same licence (see [CONTRIBUTING.md](CONTRIBUTING.md)).
