"""Main Vox window (pywebview + Edge WebView2). Reads and writes the same files the engine uses."""
import json
import logging
import os
import sys
import time
import urllib.request

import pyperclip
import webview

import audio_devices
import improve
import meeting
import notes
import sync
import timing
import vcalendar
import providers
import vox_core as core

log = logging.getLogger("vox.ui")
FORM_KEYS = ("base_url", "api_key", "stt_base_url", "stt_api_key", "llm_base_url", "llm_api_key", "stt_model", "llm_model")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

HOTKEYS = [
    {"id": "ctrl+cmd", "keys": ["ctrl_l", "cmd"], "label": "Ctrl + Win"},
    {"id": "ctrl_r", "keys": ["ctrl_r"], "label": "Right Ctrl"},
    {"id": "alt_r", "keys": ["alt_r"], "label": "Right Alt"},
    {"id": "ctrl+alt", "keys": ["ctrl", "alt"], "label": "Ctrl + Alt"},
    {"id": "ctrl+shift", "keys": ["ctrl", "shift"], "label": "Ctrl + Shift"},
]


def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{exe}" "{os.path.join(os.path.dirname(os.path.abspath(__file__)), "vox_app.py")}"'


class Api:
    # ------------------------------------------------------------- reading
    def get_state(self):
        cfg = core.load_config()
        hist = core.read_history()
        apps = sorted({h.get("app", "").lower() for h in hist if h.get("app")} | set(cfg.get("app_styles", {})))
        hk = next((h["id"] for h in HOTKEYS if h["keys"] == cfg.get("hotkey")), "custom")
        return {
            "config": cfg,
            "hotkeys": HOTKEYS,
            "hotkey_id": hk,
            "history": list(reversed(hist[-300:])),
            "status": providers.status_summary(cfg, hist, self._note_count()),
            "apps": apps,
            "mics": audio_devices.input_names(),
            "presets": providers.PRESETS,
            "autostart": self.get_autostart(),
            "data_dir": core.data_dir(),
        }

    def _note_count(self):
        try:
            return notes.count()
        except Exception as e:
            log.warning("note count failed: %s", e)
            return 0

    # ------------------------------------------------------------- writing
    def save_config(self, cfg):
        merged = core.load_config()
        merged.update(cfg)
        core.save_config(merged)
        return True

    def _form_cfg(self, form):
        """Saved settings with the (not yet saved) values of the provider form laid over them."""
        cfg = core.load_config()
        cfg.update({k: (v or "").strip() for k, v in (form or {}).items() if k in FORM_KEYS and isinstance(v, str)})
        return cfg

    def list_models(self, role, form=None):
        """Models the role's server offers: {"models": [{"id", "kind"}], "error": str}."""
        try:
            return providers.list_models(self._form_cfg(form), role)
        except Exception as e:
            log.warning("model list failed: %s", e)
            return {"models": [], "error": "Could not load the model list."}

    def test_role(self, role, form=None):
        """One real call to the role's server: {"ok", "status", "ms", "message"}."""
        try:
            return providers.test(self._form_cfg(form), role)
        except Exception as e:
            log.warning("provider test failed: %s", e)
            return {"ok": False, "status": 0, "ms": 0, "message": "The test could not run."}

    def endpoint_problem(self, base_url):
        """Text to show under the server address field, or '' when the address is acceptable."""
        return core.endpoint_error({"base_url": base_url})

    def proxy_problem(self):
        """Text for the "Use my relay as the AI server" switch: why it cannot work yet, or '' (off counts as fine)."""
        return providers.proxy_problem(core.load_config())

    def set_hotkey(self, hid):
        for h in HOTKEYS:
            if h["id"] == hid:
                self.save_config({"hotkey": h["keys"]})
                return h["label"]
        return None

    def check_key(self, key, base_url=None):
        """True/False when the server answers; None when it cannot be reached or the address is refused."""
        try:
            if base_url is None:
                base_url = core.load_config().get("base_url")
            if core.endpoint_error({"base_url": base_url}):
                return None
            return core.check_key(key.strip(), base_url)
        except Exception as e:
            log.warning("key check failed: %s", e)
            return None

    def suggest_corrections(self, original, edited):
        """Replacements found by comparing a dictation with the user's fixed version, minus ones already saved."""
        known = {w.lower() for w in core.replacements(core.load_config())}
        return [[w, r] for w, r in core.suggest_corrections(original, edited) if w.lower() not in known]

    def copy(self, text):
        pyperclip.copy(text)
        return True

    def get_speed(self):
        """The Speed card: medians per stage over the last 50 timed dictations, per model, and the last 10. Local data only."""
        try:
            return timing.speed_view(core.read_history())
        except Exception:
            log.exception("could not build the speed view")
            return timing.speed_view([])

    # ------------------------------------------------- Improve my cleanup
    _proposal = None   # the last answer of a run; apply works on this, never on items sent by the page

    def improve_state(self, days=None):
        """The Improve my cleanup card before anything is sent (improve.preview): counts, model, server, confirm sentence,
        applied versions. Local data only: nothing leaves this PC."""
        cfg = core.load_config()
        return improve.preview(cfg, core.read_history(), cfg.get("improve_days") if days is None else days, time.time())

    def improve_run(self, days, model, count, chars):
        """One run over the history of the last `days` days. `count` and `chars` are the numbers the person confirmed (the
        sentence improve_state gave): without them, or when the history no longer matches them, nothing is sent.
        {"ok", "error", "stale", "items", "findings"}; a run changes no setting except the model, range and last-run time."""
        def fail(msg, stale=False):
            return {"ok": False, "error": msg, "stale": stale, "items": [], "findings": []}

        self._proposal = None
        cfg, now = core.load_config(), time.time()
        if not (type(count) is int and type(chars) is int and count > 0):
            return fail("Press Run once and confirm what is sent first.")
        problem = core.endpoint_error(cfg) or ("Add an API key for this server first." if core.key_missing(cfg) else "")
        if problem:
            return fail(problem)
        days, pairs = improve.selection(core.read_history(), days, now)
        if (len(pairs), improve.estimate_cost(pairs, "")["chars"]) != (count, chars):
            return fail("Your history changed since the numbers were shown. They are updated: check them and run again.", True)
        model = (model or "").strip() or improve.DEFAULT_MODEL
        messages = improve.build_request(pairs, cfg.get("user_context"), core.dictionary_terms(cfg), cfg.get("my_cleanup_rules"))
        try:
            text = improve.ask(cfg, messages, model)
        except core.ApiError as e:
            return fail(providers.explain(e.code, "llm", str(e)[:160], via_relay=providers.uses_relay(cfg)))
        except core.requests.RequestException as e:
            log.warning("improve run failed: %s", e)
            return fail(f"Could not reach the server: {type(e).__name__}")
        self._proposal = proposal = improve.parse_proposal(text)
        self.save_config({"improve_model": model, "improve_days": days, "improve_last_run": now})
        return {"ok": True, "error": proposal.error, "stale": False, "items": proposal.items, "findings": proposal.findings}

    def improve_apply(self, ids):
        """Applies the accepted items of the last proposal (only those: About you suggestions are never applied) and keeps the
        change as a version. {"ok", "applied", "versions"}; the proposal is used up once something was applied."""
        proposal = self._proposal
        if proposal is None:
            return {"ok": False, "error": "There is no proposal to apply. Run once first."}
        cfg = core.load_config()
        new = improve.apply(proposal, ids if isinstance(ids, list) else [], cfg)
        def learned(c):   # dictionary lines and rule lines
            return len(c.get("dictionary") or []) + len([r for r in (c.get("my_cleanup_rules") or "").split("\n") if r.strip()])

        applied = learned(new) - learned(cfg)
        if applied:
            self._proposal = None
            self._save_learned(new)
        return {"ok": True, "applied": applied, "versions": improve.versions_view(new)}

    def improve_revert(self, index):
        """Undoes the applied change `index` (from improve_state's versions) and every later one."""
        cfg = core.load_config()
        new = improve.revert(cfg, index)
        if len(new["my_cleanup_rules_versions"]) == len(cfg.get("my_cleanup_rules_versions") or []):
            return {"ok": False, "error": "That change is not in the list any more."}
        self._save_learned(new)
        return {"ok": True, "versions": improve.versions_view(new)}

    def _save_learned(self, cfg):
        self.save_config({k: cfg[k] for k in ("dictionary", "my_cleanup_rules", "my_cleanup_rules_versions")})

    def delete_history(self, t):
        core.write_history([h for h in core.read_history() if h.get("t") != t])
        return True

    def clear_history(self):
        core.write_history([])
        return True

    def open_url(self, url):
        import webbrowser
        if url.startswith("https://"):
            webbrowser.open(url)

    def open_data_folder(self):
        os.startfile(core.data_dir())

    # ------------------------------------------------------------ meetings
    def _engine(self, path, body=None):
        try:
            with open(os.path.join(core.data_dir(), "engine.json"), encoding="utf-8") as f:
                info = json.load(f)
            req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", method="POST",
                                         data=json.dumps(body or {}).encode(),
                                         headers={"X-Vox-Token": info["token"], "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except Exception as e:
            log.warning("engine call %s failed: %s", path, e)
            return {"error": "Vox is not running in the tray. Start Vox from the Start menu."}

    def note_toggle(self):
        """Start a voice note, or finish the one being recorded (the engine does the recording)."""
        return self._engine("/note/toggle", {})

    def note_status(self):
        return self._engine("/note/status", {})

    def notes_list(self, query="", period="all", tag=""):
        """Voice notes, newest first. period: all, today, week or month."""
        now = time.time()
        lt = time.localtime(now)
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        since = {"today": midnight, "week": now - 7 * 86400, "month": now - 30 * 86400}.get(period)
        try:
            return notes.search(query or "", source=notes.SOURCE_NOTE, since=since, tag=(tag or "").strip() or None)
        except Exception as e:
            log.warning("notes search failed: %s", e)
            return []

    def note_edit(self, nid, title, text):
        out = notes.update(nid, title=title, text=text)
        self._sync_soon()
        return out

    def note_delete(self, nid):
        out = notes.delete(nid)
        self._sync_soon()
        return out

    def _sync_soon(self):
        """Ask the engine (which owns the sync thread) to send the change; harmless when it is not running."""
        try:
            self._engine("/sync/now", {})
        except Exception:
            pass

    def sync_status(self):
        return self._engine("/sync/status", {})

    def sync_now(self):
        return self._engine("/sync/now", {})

    def sync_test(self, url, token):
        """Test connection: can this relay address and token be used? {"ok", "reachable", "token_ok", "device_name",
        "relay_version", "notes", "message"} (sync.relay_check); the same fields on a failure."""
        try:
            return sync.test_relay(url, token, sync.device_name(core.load_config()))
        except Exception as e:
            log.warning("relay test failed: %s", e)
            return sync.relay_check(0, None, "", "The test could not run.")

    def get_devices(self):
        """{"ok", "error", "devices": [{"name", "this", "state", "ago"}]} for the Devices card: the devices that have used
        the relay in the saved settings. A failure gives an empty list and the reason (sync.devices_for_ui)."""
        return sync.devices_for_ui(core.load_config())

    def meeting_status(self):
        return self._engine("/meeting/status")

    def meeting_start(self, uid=None, manual=None):
        return self._engine("/meeting/start", {"uid": uid, "manual": manual})

    def meeting_stop(self):
        return self._engine("/meeting/stop")

    def meeting_catchup(self):
        return self._engine("/meeting/catchup")

    def meeting_ask_live(self, q):
        return self._engine("/meeting/ask", {"q": q})

    def meeting_ask(self, mid, q):
        try:
            return {"text": meeting.ask_meeting(core.load_config(), mid, q)}
        except Exception as e:
            log.exception("meeting ask failed")
            return {"text": f"Could not answer: {e}"}

    def meetings(self):
        return meeting.list_meetings()

    def meeting_notes(self, mid):
        return meeting.read_notes(mid)

    def meeting_delete(self, mid):
        meeting.delete_meeting(mid)
        return True

    def meeting_detail(self, mid):
        return meeting.detail(mid, core.load_config())

    def meeting_save_notes(self, mid, text):
        meeting.save_my_notes(mid, text)
        return True

    def meeting_set_done(self, mid, index, done):
        meeting.set_done(mid, index, done)
        return True

    def meeting_rename(self, mid, title):
        return meeting.rename(mid, title)

    def meetings_ask(self, question):
        try:
            return meeting.ask(core.load_config(), question)
        except Exception as e:
            log.exception("ask failed")
            return {"answer": f"Could not answer: {e}", "sources": []}

    def meeting_open(self, mid):
        m = next((x for x in meeting.list_meetings() if x["id"] == mid), None)
        path = (m or {}).get("export")
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            os.startfile(os.path.join(meeting.meetings_dir(), os.path.basename(mid), "notes.md"))

    def open_notes_folder(self):
        os.startfile(meeting.notes_export_dir(core.load_config()))

    # ------------------------------------------------------------ calendar
    def calendar(self, force=False):
        cfg = core.load_config()
        data = vcalendar.fetch(cfg, force=force)
        now = time.time()
        upcoming = [e for e in data.get("events", []) if e["end"] > now][:20]
        cur = vcalendar.current_event(data.get("events", []))
        import gcal
        return {"connected": bool(cfg.get("calendar_url")) or gcal.connected(), "google": gcal.connected(),
                "google_email": gcal.account(), "google_available": gcal.available(),
                "events": upcoming, "current": cur, "error": data.get("error", "")}

    def google_status(self):
        import gcal
        return {"available": gcal.available(), "connected": gcal.connected(), "email": gcal.account()}

    def google_connect(self):
        import gcal
        res = gcal.connect()
        if res.get("ok") and res.get("email"):
            cfg = core.load_config()
            if not cfg.get("my_email"):
                self.save_config({"my_email": res["email"]})
        return res

    def google_disconnect(self):
        import gcal
        gcal.disconnect()
        return True

    def connect_calendar(self, url):
        self.save_config({"calendar_url": (url or "").strip()})
        return self.calendar(force=True)

    # ---------------------------------------------------------- autostart
    def get_autostart(self):
        if sys.platform != "win32":
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, "Vox")
                return True
        except OSError:
            return False

    def set_autostart(self, on):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, "Vox", 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, "Vox")
                except OSError:
                    pass
        return self.get_autostart()


def main():
    api = Api()
    webview.create_window("Vox", url=resource("ui", "index.html"), js_api=api,
                          width=1040, height=700, min_size=(860, 580), background_color="#F7F7F5")
    webview.start()
