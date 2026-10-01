# Vox

Free voice dictation for **Windows** and **Android**. Speak, and clean text appears wherever you are typing: email, chat, documents, code editors, browsers.

Vox uses Groq's free speech and AI models with **your own free Groq key**, so there is no subscription and no weekly word limit from Vox. Everyone who installs Vox uses their own key and their own free quota.

- Filler words (um, uh, like) removed
- Self-corrections applied ("send it Tuesday, no wait, Friday" → "send it Friday")
- Punctuation, capitals and lists fixed
- Tone matched to the app (formal in Outlook, casual in WhatsApp)
- Your own words and people's names always spelled right
- Meeting notes with transcript and summary (**beta**, Windows only)

**Jump to:** [Windows](#1-install) · [Android](#android) · [Troubleshooting](#8-troubleshooting) · [Privacy](#10-privacy)

---

# Windows

## 1. Install

1. Open **[Releases](../../releases/latest)** and download **`VoxSetup.exe`**.
2. Run it.
   - Windows may show *"Windows protected your PC"*. This appears because the installer is not code-signed (signing costs money). Click **More info** → **Run anyway**.
   - No administrator rights are needed. Vox installs for your user only.
3. Leave **"Start Vox when I sign in to Windows"** ticked, then click **Install** and **Finish**.

Vox now runs in the system tray (the grey dot near the clock; click **^** if you do not see it).

## 2. Get your free Groq key (2 minutes)

Vox asks for this the first time it opens.

1. Go to **[console.groq.com](https://console.groq.com)** and sign in (Google sign-in works). No credit card needed.
2. Click **API Keys** → **Create API Key**, name it `Vox`, click **Submit**.
3. Copy the key (it starts with `gsk_`). Groq shows it only once.
4. In Vox, paste it into the **Welcome** box on the Home page and click **Save key**. Vox tests the key before saving.

Keep your key private. Anyone with it can use your quota. If it leaks, delete it on console.groq.com and create a new one.

## 3. Allow the microphone

Windows **Settings** → **Privacy & security** → **Microphone**:

- **Microphone access**: On
- **Let apps access your microphone**: On
- Click the **⌄** next to it, scroll to the bottom, turn on **Let desktop apps access your microphone**

## 4. Dictate

| What | How |
|---|---|
| Dictate | Click into any text box, **hold Ctrl + Win**, speak, **release** |
| Keep listening (long notes, or typing as you pause) | **Double-tap Ctrl + Win** and speak. It saves a note by default (Type needs the app you start in). **Double-tap** again, say the stop phrase or press **Esc** to end it and save |
| Paste the last dictation again | **Ctrl + V**, if **Settings → Keep dictation on the clipboard** is on. If you switch to a different program before the text is pasted, Vox does not paste it: it copies it and says "Copied; the window changed", so press **Ctrl + V** where you want it |
| Open the Vox window | Double-click the tray icon, or search **Vox** in the Start menu |

While you speak, a small pill at the bottom of the screen shows a live waveform. Amber dots mean Vox is processing. The text is pasted about 1 second after you release.

Speak naturally. You can say "new line", "new paragraph", "comma" or "question mark", and list items ("first..., second...") become a list.

## 5. The Vox window

- **Home**: words this week, total words, words per minute, time saved compared with typing, and your full history (search, copy, delete).
- **Dictionary**
  - **Words**: your own terms and acronyms (product names, course codes, jargon).
  - **People**: names of people you mention, spelled the way they spell them.
  - **Replacements**: when Vox keeps mishearing something, force the fix, e.g. `lume x r` → `LoomXR`.
- **Styles**: the tone for each app. Vox detects the app you are typing in. Styles: *Formal*, *Neutral*, *Casual*, *Very casual*, *Raw* (no cleanup, good for code editors and terminals).
- **Settings**: Groq key (with a **Test** button), shortcut (Ctrl + Win, Right Ctrl, Right Alt, Ctrl + Alt, Ctrl + Shift), language, AI cleanup on or off, keep dictation on the clipboard, your name, start with Windows.

Changes apply within a second. No restart needed.

## 6. Meeting notes (beta)

Vox can record a meeting from your mic and your PC's audio (Zoom, Teams, Meet, anything). No bot joins the call.

1. Open Vox → **Notes** → **Start notes**. Optionally type the meeting title and the people in it (names help Vox label who said what).
2. Tell people you are recording.
3. **During the meeting**
   - The live transcript fills in about 10 to 15 seconds behind the speaker. Two meters show that your mic and the call audio are both being heard.
   - **Ask** box: type anything, e.g. *"what was Peyman saying about payment 2 minutes ago?"*, or tap *What did I miss?*. Answers include times; click a time to jump to that line.
4. Click **Stop** when the meeting ends. Vox re-transcribes the whole recording with a more accurate model, then writes the notes (1 to 3 minutes): summary, discussion by topic, decisions, action items, open questions, next steps, who said what, and the full transcript. Notes are also saved as Markdown files in `Documents\Vox Notes`.
5. Each saved meeting has its own **Ask about this meeting** box. **Ask about your meetings** (top left) searches across all of them.

**Tips:** wear headphones if you can, so the call audio does not leak into your mic. Audio is kept only until the notes are written, then deleted.

**Calendar (optional):** Notes → **Calendar** → **Connect Google Calendar**, then sign in. Google shows *"Google hasn't verified this app"* because Vox is a free personal project: click **Advanced** → **Go to Vox**. Vox only reads events. Some school and work accounts block unverified apps; in that case type the title and people when you start notes, or paste an Outlook ICS link instead.

**Beta limits:** transcription quality depends on audio levels and background noise. Your own lines are always labelled correctly; other people's names are best guesses marked "likely".

## 7. Free limits

Groq's free plan (per key, per day, at the time of writing):

| | Free limit | What it means |
|---|---|---|
| Speech to text | 2,000 requests, 8 hours of audio | About 2,000 dictations a day |
| AI cleanup | 1,000 requests | About 1,000 cleaned dictations a day. After that Vox still pastes the raw transcript |
| Meeting notes | 1,000 requests, 200K tokens | A 1-hour meeting uses about 2 hours of live audio quota, plus the final accurate pass on whisper-large-v3 |

## 7b. Use your own server instead of Groq (optional)

Vox talks to any OpenAI-compatible speech and chat server. Open **Settings**, and set:

- **Server address**, for example `http://100.x.y.z:8000/v1` (a Whisper server such as faster-whisper-server on your own PC, reached over Tailscale) or `https://your-host/v1`. Leave it empty for Groq.
- **Speech model** and **Cleanup model** (Settings, Advanced) to the model names your server offers.
- **API key**: leave it empty if your server does not need one.

Plain `http://` is only accepted for this PC, your local network and Tailscale addresses (100.64.0.0/10, `*.ts.net`). Any other server must use `https://`, so your key and voice never cross the internet unencrypted. The **Test** button checks the address and key together. On Android the same fields are in Settings → API key → Server address.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| Nothing happens when I hold Ctrl + Win | Check the tray icon is there. If not, start Vox from the Start menu |
| Pill appears but no text is pasted | Click into a text box first. Vox cannot type into apps running as administrator |
| "The server rejected the API key" | Settings → paste the key again → **Test** |
| "Rate limit reached" (Groq free limit) | Wait a few minutes (per-minute limit) or until tomorrow (daily limit) |
| Wrong words for names or terms | Add them in **Dictionary**, or add a replacement |
| Words in the wrong language | Settings → **Language** → pick your language instead of Auto detect |
| Another app also uses Ctrl + Win (e.g. Wispr Flow) | Change the shortcut in Vox Settings, or in the other app |
| Windows Start menu opens after dictating | Update to the latest Vox. If it persists, switch the shortcut to Right Ctrl |

Logs for bug reports are in `%APPDATA%\Vox\vox.log` (type that into the File Explorer address bar).

## 9. Update or uninstall

- **Update:** download the new `VoxSetup.exe` from Releases and run it. Your settings, key, history and notes are kept.
- **Uninstall:** Windows **Settings** → **Apps** → **Installed apps** → **Vox** → **Uninstall**. To remove your data too, delete `%APPDATA%\Vox` and `Documents\Vox Notes`.

## 10. Privacy

- Vox has no analytics, no accounts and no server of its own. Nothing is sent to the developer.
- Your audio goes to the speech server you chose in Settings (Groq by default, under your own key). The text of what you said, your "About you" text, dictionary, people and the name of the app you are typing into go to the cleanup server you chose. Spelling hints (dictionary and people) also go to the speech server.
- If you turn on **Use my relay as the AI server** (Windows and Android), both go through your own relay instead. If you turn on **Sync voice notes with my relay**, the full text, raw transcript, tags and device name of your voice notes and your "About you", dictionary, people, default style, cleanup switch, language and learned cleanup rules go to your relay. Provider settings and API keys go there only if you also turn on "Also share my provider settings and API keys" (off by default).
- Long recordings on Windows are sent in pieces while you are still speaking, and so is **keep listening** (Windows, double-tap the shortcut): its audio is written to `%APPDATA%\Vox\listen` while it runs and removed when it ends cleanly; after a failure or a crash it stays there, unencrypted, until you recover it or delete it.
- **Improve my cleanup** (Windows, Settings) is the one feature that sends your saved dictations (what you said and what Vox typed, up to about 40,000 characters, with your About you text, dictionary and cleanup rules) to the cleanup server you chose, to suggest dictionary words and rules. It runs only when you press **Run once** and then **Send** after reading how much will be sent; nothing is applied until you tick it. The rules it learns also travel with the relay sync to your phone.
- Settings, history and notes are stored on your PC in `%APPDATA%\Vox` (voice notes in `notes.db`) and `Documents\Vox Notes`. After pasting, Vox puts your earlier copied text back (a copied image or file is not restored) unless you turn on "Keep dictation on the clipboard" (off by default).
- On Android the API key and the history are stored unencrypted on the phone. The relay stores the AI server keys in plain text in its `relay.json`.
- Full policy: [minhajuddinm.github.io/vox/privacy.html](https://minhajuddinm.github.io/vox/privacy.html)

---

# Android

## A1. Install

1. On your phone, open **[Releases](../../releases/latest)** and download **`Vox.apk`**.
2. Open the downloaded file (from the notification or the **Files** app).
   - "Not allowed to install from this source": tap **Settings**, turn on **Allow from this source**, go back.
   - Play Protect warning: tap **More details** → **Install anyway**. It warns because Vox is not from the Play Store.
3. To update later, download the new `Vox.apk` and install it over the old one. Your settings stay.

## A2. Set up (the Home tab walks you through it)

1. **Add your free Groq key**: the same kind of key as on Windows (see [step 2](#2-get-your-free-groq-key-2-minutes)). You can use the same key on your phone and PC; they share its free limits.
2. **Allow the microphone.**
3. **Turn on the Vox bubble**: Settings → Accessibility → Downloaded apps (or Installed apps) → **Vox dictation bubble** → On.
   - If it is greyed out or says **Restricted setting**: tap the switch once so Android shows the message, then open **Settings → Apps → Vox → ⋮ (top right) → Allow restricted settings**, confirm, and turn the switch on again. Android does this for every app installed outside the Play Store. The same steps, the Play Protect "Install anyway" note and the adb way are in Vox, Settings, System, **Install help**.
4. **Start the dictation service.** A small "Vox is ready" notification stays while it runs.
5. Optional but recommended: Vox → Settings → **Battery: Unrestricted**, so Android does not close Vox in the background.

## A3. Dictate

1. Tap any text box. The round mic **bubble** appears at the edge of the screen.
2. **Tap** the bubble (it turns red), speak, **tap** again. It turns amber while processing, then the text appears in the box.
3. **Long-press** the bubble to cancel. **Drag** it to move it.

The app has the same **Dictionary** (words, people, replacements) and **Styles** (tone per app, pick apps from a list) as Windows. History and stats are on the **Home** tab.

**Android troubleshooting:** if text does not appear in an app (some banking apps block typing), Vox copies it to the clipboard instead, so long-press → Paste. If the bubble disappears after a while, set Battery to Unrestricted and turn the service on again.

---

## For the maintainer

**Publish a release**

```
git add .
git commit -m "Describe the change"
git push
git tag v1.0.1
git push origin v1.0.1
```

GitHub Actions builds `VoxSetup.exe` and `Vox.apk` (about 10 minutes) and attaches both to a new Release. Use a new tag number each time, and raise `versionCode`/`versionName` in `android/AndroidManifest.xml` for each Android release.

**Google sign-in in releases:** repository **Settings** → **Secrets and variables** → **Actions** → secret `GOOGLE_CLIENT_JSON` containing the Desktop OAuth client JSON. Without it, releases build without the Google button.

**Android signing key:** every APK must be signed with the same key, or phones refuse the update. The key (`android/vox.keystore`) is kept out of git. Store it as the repository secret `ANDROID_KEYSTORE_B64` (the file in base64). Without the secret a tag build fails; other CI builds use a new throw-away key and name the artifact `Vox-android-debug-key`.

**Build locally:** Android: `ANDROID_HOME=... ./android/build.sh` (JDK 17+, platform 34, build-tools 36). Windows: run `windows\build_app.bat` (Python 3.10+). It builds and installs to `%LOCALAPPDATA%\Programs\Vox`. For Google sign-in, put the client JSON at `windows\google_client.json` (ignored by git).

**Never commit** a `config.json` (contains a key), `google_client.json`, `.env` files, `relay.json` or `relay.db` (the relay's token and notes), keystores (`*.keystore`, `*.jks`, `*.p12`) or `*.pem` files. All are in `.gitignore`, wherever in the tree they are.

**Code map:** `vox_app.py` entry point · `engine.py` tray, hotkey, recording, paste · `overlay.py` pill · `vox_core.py` Groq calls, prompts, config · `ui_app.py` + `ui/index.html` window · `meeting.py` notes · `gcal.py`, `vcalendar.py` calendar · `installer.iss` installer · `android/` Android app (`assets/index.html` UI, `VoxAccessibilityService.java` bubble and text insertion, `DictationService.java` recording and Groq).
