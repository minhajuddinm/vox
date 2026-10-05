"""Renders the screenshots in docs/screenshots/ from the apps' own pages, with made-up sample data.

    python tools/render_screenshots.py                  # every image
    python tools/render_screenshots.py windows-home og  # only these
    python tools/render_screenshots.py --list           # the names
    python tools/render_screenshots.py --browser "C:/Program Files/Google/Chrome/Application/chrome.exe"

What it does: for each image it copies the page (windows/ui/index.html or android/assets/index.html) into a temporary
folder with a stand-in bridge put in front of the page's own scripts (window.pywebview.api on Windows, the window.Vox
JavaScript interface on Android), opens that copy in headless Microsoft Edge or Google Chrome and saves a PNG. The bridge
answers from sample data built here: the Windows defaults, presets, status line, Speed card and Improve card come from
the app's own Python modules (windows/vox_core.py, providers.py, timing.py, improve.py), with `requests` replaced by a
stand-in so nothing can reach the network. The sample data is invented (Sam, Acme): no real names, keys, tokens, host
names, notes or dictations.

The images are the window and phone pages only. The recording pill, the Android bubble and the notifications are drawn
by native code and are not shown; say so wherever the images are used.

Privacy: the browser gets a fresh profile in the temporary folder, every host name resolves to nothing
(--host-resolver-rules), and no real Vox profile is read (APPDATA is not used). Standard library only; needs Edge or
Chrome. Run it from any folder; it writes only to docs/screenshots/ (or --out).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN_PAGE = os.path.join(ROOT, "windows", "ui", "index.html")
ANDROID_PAGE = os.path.join(ROOT, "android", "assets", "index.html")
OUT = os.path.join(ROOT, "docs", "screenshots")

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/microsoft-edge",
]

# name: (platform, width, height, what to show after the page has loaded)
SHOTS = {
    "windows-home": ("windows", 1100, 760, {"page": "home"}),
    "windows-settings": ("windows", 1100, 980, {"page": "settings"}),
    "windows-dictionary": ("windows", 1100, 1640, {"page": "dictionary"}),
    "windows-voice-notes": ("windows", 1100, 760, {"page": "voicenotes"}),
    "windows-privacy": ("windows", 1100, 760, {"page": "settings", "scroll": "leaves", "offset": 56}),
    "android-home": ("android", 390, 844, {"page": "home"}),
    "android-settings": ("android", 390, 844, {"page": "settings"}),
    "android-notes": ("android", 390, 844, {"page": "notes"}),
    "android-install-help": ("android", 390, 844, {"page": "settings", "open": "install-help", "scroll": "install-help", "offset": 12}),
    "og": ("og", 1200, 630, {}),
}

# ------------------------------------------------------------------ sample data (all invented)

USER_CONTEXT = ("I'm Sam, a product designer at Acme. I write short, friendly messages in British English. "
                "Projects: Acme Notes and the Orbit redesign. I often mix in a few Hindi words.")
DICTIONARY = ["Acme", "Orbit", "Figma", "Kubernetes", "standup => stand-up", "acme notes => Acme Notes"]
PEOPLE = ["Priya Raman", "Jonas Weber", "Alex Chen"]
SNIPPETS = {"my email": "sam@example.com", "my signature": "Best,\nSam\nAcme Design Team"}
DICTATIONS = [  # (minutes ago, app, words as spoken, text typed)
    (3, "slack.exe", "sounds good i'll send the orbit mock ups after lunch",
     "Sounds good, I'll send the Orbit mock-ups after lunch."),
    (18, "outlook.exe", "hi priya thanks for the notes from today first move the review to thursday second "
     "share the figma link with jonas third book the room",
     "Hi Priya, thanks for the notes from today.\n\n1. Move the review to Thursday.\n2. Share the Figma link with Jonas.\n3. Book the room."),
    (47, "code.exe", "camel case user name equals get user open paren close paren", "userName = getUser()"),
    (95, "winword.exe", "the new onboarding flow cuts the setup from five steps to three which we expect to help new teams",
     "The new onboarding flow cuts the setup from five steps to three, which we expect to help new teams."),
    (60 * 26, "whatsapp.exe", "running ten minutes late see you at the cafe", "Running ten minutes late, see you at the café."),
]
VOICE_NOTES = [  # (hours ago, title, text, seconds, tags)
    (2, "Orbit review", "Ideas for the Orbit review:\n- start with the three biggest problems from the user tests\n"
     "- show the new navigation before the colours\n- ask Jonas about the timeline for the beta", 38, ["work"]),
    (26, "Call the dentist", "Call the dentist on Monday morning and ask to move the appointment to Friday.", 7, ["todo"]),
    (24 * 4, "Weekend", "Pick up the bike from the repair shop, buy a birthday card for Alex, water the plants.", 11, []),
]
DEVICES = [{"name": "Sam's laptop", "this": True, "state": "active", "ago": "just now"},
           {"name": "Sam's phone", "this": False, "state": "recent", "ago": "2 h ago"},
           {"name": "Acme desktop", "this": False, "state": "old", "ago": "3 days ago"}]
RELAY_URL = "https://your-pi.your-tailnet.ts.net"   # the neutral example the docs use


def _timing(i, now_ms, stt_model, llm_model, app_words):
    """One made-up timing entry in the shape of timing.Timing.entry (milliseconds)."""
    start, stt, llm, ins = 60 + 15 * (i % 3), 380 + 45 * (i % 4), 290 + 60 * (i % 5) if app_words > 3 else 0, 40 + 5 * i
    return {"stages": {"start": start, "rec": 2500 + 400 * i, "stt": stt, "llm": llm, "insert": ins,
                       "total": stt + llm + ins}, "stt_model": stt_model, "llm_model": llm_model,
            "provider": "groq", "relay": False}


def windows_modules():
    """The app's own modules, imported with `requests` replaced so nothing can open a connection."""
    sys.path.insert(0, os.path.join(ROOT, "windows"))
    sys.modules.setdefault("requests", mock.MagicMock())
    import improve  # noqa: E402
    import providers  # noqa: E402
    import timing  # noqa: E402
    import vox_core  # noqa: E402
    return vox_core, providers, timing, improve


def sample_history(now, stt_model, llm_model, app_label=None):
    """Oldest first, like vox_core.read_history."""
    out = []
    for i, (mins, app, raw, text) in enumerate(reversed(DICTATIONS)):
        t = now - mins * 60
        words = len(text.split())
        e = {"t": t, "app": app_label(app) if app_label else app, "raw": raw, "text": text, "words": words,
             "secs": round(words / 2.6, 1), "timing": _timing(i, t * 1000, stt_model, llm_model, words)}
        out.append(e)
    return out


def windows_data(now):
    core, providers, timing, improve = windows_modules()
    cfg = json.loads(json.dumps(core.DEFAULT_CONFIG))
    cfg.update({
        "api_key": "sample-key-not-real", "user_context": USER_CONTEXT, "dictionary": DICTIONARY, "people": PEOPLE,
        "snippets": SNIPPETS, "relay_sync": True, "relay_url": RELAY_URL, "relay_token": "sample-token-not-real",
        "device_name": "Sam's laptop", "your_name": "Sam", "auto_learn": True,
        "learned_log": [{"t": now - 3600 * 5, "wrong": "Orbet", "right": "Orbit", "word": True},
                        {"t": now - 600, "wrong": "Jonah", "right": "Jonas", "word": True}],
    })
    hist = sample_history(now, cfg["stt_model"], cfg["llm_model"])
    hotkeys = [
        {"id": "ctrl+cmd", "keys": ["ctrl_l", "cmd"], "label": "Ctrl + Win"},
        {"id": "ctrl_r", "keys": ["ctrl_r"], "label": "Right Ctrl"},
        {"id": "alt_r", "keys": ["alt_r"], "label": "Right Alt"},
        {"id": "ctrl+alt", "keys": ["ctrl", "alt"], "label": "Ctrl + Alt"},
        {"id": "ctrl+shift", "keys": ["ctrl", "shift"], "label": "Ctrl + Shift"},
    ]
    notes = [{"id": "%032x" % (i + 1), "title": title, "text": text, "raw": text, "created_at": now - h * 3600,
              "updated_at": now - h * 3600, "secs": secs, "source": "note", "device": "Sam's laptop", "tags": tags}
             for i, (h, title, text, secs, tags) in enumerate(VOICE_NOTES)]
    state = {
        "config": cfg, "hotkeys": hotkeys, "hotkey_id": "ctrl+cmd", "history": list(reversed(hist)),
        "status": providers.status_summary(cfg, hist, len(notes)),
        "apps": sorted({h["app"] for h in hist} | set(cfg["app_styles"])),
        "mics": ["Microphone (Realtek Audio)", "Headset (USB Audio)"], "presets": providers.PRESETS,
        "autostart": True, "data_dir": "C:\\Users\\Sam\\AppData\\Roaming\\Vox",
    }
    models = {"models": [{"id": "whisper-large-v3-turbo", "kind": "stt"}, {"id": "whisper-large-v3", "kind": "stt"},
                         {"id": "openai/gpt-oss-20b", "kind": "llm"}, {"id": "openai/gpt-oss-120b", "kind": "llm"},
                         {"id": "llama-3.3-70b-versatile", "kind": "llm"}], "error": ""}
    return {
        "get_state": state,
        "get_speed": timing.speed_view(hist),
        "improve_state": improve.preview(cfg, hist, cfg.get("improve_days"), now),
        "notes_list": notes,
        "note_status": {"recording": False, "busy": False},
        "sync_status": {"enabled": True, "running": False, "last_run": now - 120, "last_ok": now - 120, "error": "",
                        "pushed": 1, "pulled": 2},
        "get_devices": {"ok": True, "error": "", "devices": DEVICES},
        "list_models": models,
        "shortcut_problems": {}, "note_hotkey_problem": "", "endpoint_problem": "", "proxy_problem": "",
        "meetings": [], "meeting_status": {"active": False},
        "calendar": {"connected": False, "google": False, "google_email": "", "google_available": False, "events": [],
                     "upcoming": [], "current": None, "error": ""},
    }


ANDROID_LABELS = {"slack.exe": "Slack", "outlook.exe": "Gmail", "code.exe": "Termux", "winword.exe": "Google Docs",
                  "whatsapp.exe": "WhatsApp"}


def android_data(now):
    core, providers, timing, _ = windows_modules()
    hist = sample_history(now, core.DEFAULT_CONFIG["stt_model"], core.DEFAULT_CONFIG["llm_model"],
                          app_label=lambda a: ANDROID_LABELS.get(a, a))
    cfg = {"api_key": "sample-key-not-real", "base_url": core.DEFAULT_CONFIG["base_url"], "language": "",
           "cleanup": True, "cleanup_min_words": 3, "cleanup_strength": "light", "structure": "auto",
           "snippets": SNIPPETS, "keep_history": True, "auto_learn": True,
           "learned_log": [{"t": now - 900, "wrong": "Jonah", "right": "Jonas", "word": True}],
           "only_typing": True, "always_show_bubble": False, "note_bubble": True, "note_notification": False,
           "default_style": "neutral", "stt_model": core.DEFAULT_CONFIG["stt_model"],
           "llm_model": core.DEFAULT_CONFIG["llm_model"], "provider": "groq", "user_context": USER_CONTEXT,
           "stt_base_url": "", "stt_api_key": "", "llm_base_url": "", "llm_api_key": "",
           "relay_sync": True, "relay_url": RELAY_URL, "relay_token": "sample-token-not-real",
           "relay_sync_keys": False, "relay_proxy": False, "device_name": "Sam's phone",
           "dictionary": DICTIONARY, "people": PEOPLE,
           "app_styles": {"com.whatsapp": "casual", "com.google.android.gm": "formal", "com.slack": "neutral"}}
    notes = [{"id": "%032x" % (i + 1), "title": title, "text": text, "created_at": now - h * 3600,
              "updated_at": now - h * 3600, "secs": secs, "device": "Sam's phone", "tags": tags}
             for i, (h, title, text, secs, tags) in enumerate(VOICE_NOTES)]
    return {
        "state": {"config": cfg, "presets": providers.PRESETS, "device_default": "Pixel 8",
                  "mic": True, "a11y": True, "service": True, "history": list(reversed(hist)),
                  "status": {"notes": len(notes), "unsent": False}, "dark": False, "version": "2.0.0"},
        "notes": notes,
        "speed": timing.speed_view(hist),
        "sync": {"enabled": True, "running": False, "last_run": now - 300, "last_ok": now - 300, "error": "",
                 "pushed": 0, "pulled": 1},
        "devices": {"ok": True, "error": "", "devices": [
            {"name": "Sam's phone", "this": True, "state": "active", "ago": "just now"},
            {"name": "Sam's laptop", "this": False, "state": "recent", "ago": "2 h ago"}]},
        "mics": {"current": "", "options": [{"key": "15|Pixel 8", "label": "Pixel 8 (built-in)"},
                                            {"key": "3|USB headset", "label": "USB headset (wired)"}]},
        "diag": {"service": "connected", "connected": True, "battery": "Unrestricted", "battery_ok": True,
                 "events": ["10:02 bubble shown (text box)", "10:05 bubble hidden (keyboard closed)"]},
        "apps": [{"pkg": "com.whatsapp", "label": "WhatsApp"}, {"pkg": "com.google.android.gm", "label": "Gmail"},
                 {"pkg": "com.slack", "label": "Slack"}],
        "labels": {"com.whatsapp": "WhatsApp", "com.google.android.gm": "Gmail", "com.slack": "Slack"},
    }


# ------------------------------------------------------------------ stand-in bridges (JavaScript)

WINDOWS_STUB = """<script>
(() => {
  const D = %s;
  const answers = { copy: true, save_config: true, open_url: null, test_role: { ok: true, message: "Sample answer" },
    check_key: true, suggest_corrections: [], meeting_detail: null };
  const api = new Proxy({}, { get: (_, name) => (...args) => Promise.resolve(
      name in D ? JSON.parse(JSON.stringify(D[name])) : name in answers ? answers[name] : null) });
  window.pywebview = { api };
})();
</script>"""

WINDOWS_AFTER = """<script>
window.addEventListener("load", () => {
  const A = %s;
  window.dispatchEvent(new Event("pywebviewready"));
  setTimeout(() => {
    if (A.page !== "home") document.querySelector('nav button[data-page="' + A.page + '"]').click();
    setTimeout(() => {
      if (A.scroll) { const el = document.getElementById(A.scroll); el.scrollIntoView({ block: "start" });
        const m = document.querySelector("main"); if (m) m.scrollTop -= (A.offset || 0); }
      document.activeElement && document.activeElement.blur();
    }, 600);
  }, 400);
});
</script>"""

ANDROID_STUB = """<script>
(() => {
  const D = %s;
  const s = (v) => JSON.stringify(v);
  const later = (cb, v) => setTimeout(() => window[cb](typeof v === "string" ? v : s(v)), 50);
  const fixed = {
    state: () => s(D.state), notesList: () => s(D.notes), noteStatus: () => s({ recording: false, busy: false }),
    syncStatus: () => s(D.sync), getSpeed: () => s(D.speed), getMics: () => s(D.mics), getDiagnostics: () => s(D.diag),
    apps: () => s(D.apps), appLabel: (p) => D.labels[p] || p, endpointProblem: () => "",
    suggestCorrections: () => "[]", learnedRemove: () => s({ dictionary: D.state.config.dictionary, learned_log: [] }),
    getDevices: (cb) => later(cb, D.devices), syncNow: (cb) => later(cb, { ok: true, message: "Synced" }),
    syncTest: (cb) => later(cb, { ok: true, reachable: true, token_ok: true, device_name: "Sam's phone", notes: 3,
      message: "Connected. The relay holds 3 notes." }),
    testKey: (k, u, cb) => later(cb, "ok"),
    listModels: (r, f, cb) => later(cb, { models: [], error: "" }),
    testRole: (r, f, cb) => later(cb, { ok: true, message: "Sample answer" }),
    noteToggle: () => s({ ok: true, action: "start" }), noteEdit: () => s({}), noteDelete: () => s({ ok: true }),
  };
  window.Vox = new Proxy(fixed, { get: (t, name) => name in t ? t[name] : () => "" });
})();
</script>"""

ANDROID_AFTER = """<script>
window.addEventListener("load", () => {
  const A = %s;
  setTimeout(() => {
    if (A.page !== "home") go(A.page);
    setTimeout(() => {
      if (A.open) document.getElementById(A.open).open = true;
      if (A.scroll) { document.getElementById(A.scroll).scrollIntoView({ block: "start" }); window.scrollBy(0, -(A.offset || 0)); }
      document.activeElement && document.activeElement.blur();
    }, 500);
  }, 300);
});
</script>"""

OG_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>og</title><style>
html,body{margin:0;width:1200px;height:630px;overflow:hidden}
body{background:#F7F7F5;color:#1B1B1F;font:22px/1.4 "Segoe UI",system-ui,sans-serif;display:grid;grid-template-columns:520px 1fr}
.l{padding:64px 0 0 64px}.brand{display:flex;align-items:center;gap:16px;font-weight:700;font-size:40px}
.logo{width:64px;height:64px;border-radius:16px;background:linear-gradient(135deg,#3D6BFF,#7A4DFF);display:grid;place-items:center}
h1{font-size:44px;line-height:1.15;margin:40px 0 18px;letter-spacing:-.01em}p{margin:0 0 12px;color:#4A4A52}
.tag{display:inline-block;margin-top:22px;padding:8px 14px;border-radius:10px;background:#2F5BEA;color:#fff;font-weight:600;font-size:20px}
.r{position:relative}.r img{position:absolute;left:24px;top:70px;width:900px;border-radius:14px;border:1px solid #E4E4E0;box-shadow:0 18px 50px rgba(0,0,0,.18)}
</style></head><body><div class="l"><div class="brand"><div class="logo"><svg viewBox="0 0 512 512" width="38" height="38"><path d="M140 150 L256 380 L372 150" fill="none" stroke="#fff" stroke-width="64" stroke-linecap="round" stroke-linejoin="round"/><circle cx="386" cy="128" r="36" fill="#FF5A5F" stroke="#fff" stroke-width="12"/></svg></div>Vox 2.0</div>
<h1>Voice dictation for Windows and Android</h1><p>Speak, and the words you said are typed into the app you are in.</p>
<p>Your own AI provider and key, or your own server.</p><span class="tag">Free and open source, MIT</span></div>
<div class="r"><img src="windows-home.png" alt=""></div></body></html>"""


FRAME = """<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff}
iframe{display:block;border:0;width:%dpx;height:%dpx}</style></head><body><iframe src="%s"></iframe></body></html>"""


def wrap(page_path, stub, after):
    with open(page_path, encoding="utf-8") as f:
        text = f.read()
    i = text.index("<head>") + len("<head>")
    text = text[:i] + "\n" + stub + text[i:]
    j = text.rindex("</body>")
    return text[:j] + after + "\n" + text[j:]


def find_browser(given):
    for path in ([given] if given else []) + BROWSERS:
        if path and os.path.exists(path):
            return path
    found = shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")
    if not found:
        sys.exit("No Edge or Chrome found; pass --browser PATH")
    return found


def shoot(browser, html_path, png_path, width, height, profile):
    args = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
            "--force-color-profile=srgb", "--blink-settings=preferredColorScheme=1", "--no-first-run", "--lang=en-US", "--no-default-browser-check", "--disable-extensions",
            "--disable-sync", "--disable-background-networking", "--disable-component-update",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost", "--user-data-dir=" + profile,
            "--allow-file-access-from-files", "--virtual-time-budget=6000",
            "--window-size=%d,%d" % (width, height), "--screenshot=" + png_path,
            "file:///" + html_path.replace("\\", "/")]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    if not os.path.exists(png_path):
        raise RuntimeError("the browser wrote no image for " + html_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", help="images to render (default: all)")
    ap.add_argument("--browser", help="path to msedge.exe or chrome.exe")
    ap.add_argument("--out", default=OUT, help="output folder (default docs/screenshots)")
    ap.add_argument("--list", action="store_true", help="print the image names and exit")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder (prints its path)")
    a = ap.parse_args(argv)
    if a.list:
        for name, (plat, w, h, _) in SHOTS.items():
            print("%-22s %-8s %dx%d" % (name, plat, w, h))
        return 0
    names = a.names or list(SHOTS)
    unknown = [n for n in names if n not in SHOTS]
    if unknown:
        sys.exit("unknown image: " + ", ".join(unknown))
    browser = find_browser(a.browser)
    os.makedirs(a.out, exist_ok=True)
    now = time.time()
    tmp = tempfile.mkdtemp(prefix="vox-shots-")
    try:
        data = {}
        for name in names:
            plat, width, height, view = SHOTS[name]
            src = os.path.join(tmp, name + ".html")
            if plat == "windows":
                data.setdefault("windows", windows_data(now))
                page = wrap(WIN_PAGE, WINDOWS_STUB % json.dumps(data["windows"]), WINDOWS_AFTER % json.dumps(view))
            elif plat == "android":
                data.setdefault("android", android_data(now))
                page = wrap(ANDROID_PAGE, ANDROID_STUB % json.dumps(data["android"]), ANDROID_AFTER % json.dumps(view))
            else:
                home = os.path.join(a.out, "windows-home.png")
                if not os.path.exists(home):
                    sys.exit("og needs docs/screenshots/windows-home.png: render windows-home first")
                shutil.copyfile(home, os.path.join(tmp, "windows-home.png"))
                page = OG_PAGE
            with open(src, "w", encoding="utf-8") as f:
                f.write(page)
            if plat == "android":   # Chrome and Edge do not lay a window out narrower than 500 px: frame the page
                frame = os.path.join(tmp, name + "-frame.html")
                with open(frame, "w", encoding="utf-8") as f:
                    f.write(FRAME % (width, height, os.path.basename(src)))
                src = frame
            png = os.path.join(a.out, name + ".png")
            shoot(browser, src, png, width, height, os.path.join(tmp, "profile"))
            print("%-22s %6d bytes  %s" % (name, os.path.getsize(png), os.path.relpath(png, ROOT)))
    finally:
        if a.keep:
            print("kept", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
