"""The relay's management side: the web page, status, devices and activity, masked profile, export, backup,
compact, purge, token rotation, per-platform data folders."""
import http.client
import json
import os
import re
import sqlite3
import threading
import time
import uuid

import pytest

import relay


def nid():
    return uuid.uuid4().hex


def note(text="hello", **kw):
    now = time.time()
    return dict({"id": nid(), "text": text, "title": text[:20], "created_at": now, "updated_at": now, "device": "pc"}, **kw)


@pytest.fixture
def server(tmp_path):
    srv = relay.make_server(str(tmp_path), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, token=True, headers=None, raw_response=False):
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + (srv.token if token is True else token)
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    if raw_response:
        return r, data
    return r.status, (json.loads(data) if data else None)


# ---------------------------------------------------------------- the page
def test_the_page_is_served_without_a_token_and_holds_no_data(server):
    server.store.upsert_note(note("very private words"))
    r, data = call(server, "GET", "/", token=False, raw_response=True)
    html = data.decode()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/html")
    assert "very private words" not in html and "Vox relay" in html
    assert call(server, "GET", "/ui", token=False, raw_response=True)[0].status == 200


def test_the_page_is_locked_down_by_a_per_response_nonce(server):
    r1, d1 = call(server, "GET", "/", token=False, raw_response=True)
    r2, d2 = call(server, "GET", "/", token=False, raw_response=True)
    csp1 = r1.getheader("Content-Security-Policy")
    nonce = re.search(r"script-src 'nonce-([^']+)'", csp1).group(1)
    assert f'nonce="{nonce}"' in d1.decode() and "__NONCE__" not in d1.decode()
    assert nonce != re.search(r"script-src 'nonce-([^']+)'", r2.getheader("Content-Security-Policy")).group(1)
    for part in ("default-src 'none'", "connect-src 'self'", "frame-ancestors 'none'", "base-uri 'none'"):
        assert part in csp1
    assert "unsafe-inline" not in csp1 and "unsafe-eval" not in csp1
    assert r1.getheader("X-Frame-Options") == "DENY" and r1.getheader("Referrer-Policy") == "no-referrer"
    html = d1.decode()
    assert not re.search(r"\son(click|load|error)\s*=", html) and "innerHTML" not in html   # no inline handlers, no HTML injection


def test_the_pages_script_parses():
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    import subprocess
    import tempfile
    js = re.search(r"<script nonce=\"__NONCE__\">(.*?)</script>", relay.UI_HTML, re.S).group(1)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    try:
        assert subprocess.run([node, "--check", f.name], capture_output=True).returncode == 0
    finally:
        os.unlink(f.name)


def test_data_endpoints_still_need_the_token(server):
    for path in ("/admin/status", "/admin/activity", "/admin/profile", "/admin/export", "/admin/backup"):
        assert call(server, "GET", path, token=False)[0] == 401
        assert call(server, "GET", path, token="nope")[0] == 401
    assert call(server, "POST", "/admin/purge", {}, token=False)[0] == 401


# ------------------------------------------------------------- status etc
def test_status_reports_the_numbers(server):
    n = note("one")
    server.store.upsert_note(n)
    server.store.delete_note(server.store.upsert_note(note("two"))[0]["id"])
    st, s = call(server, "GET", "/admin/status")
    assert st == 200 and s["ok"] and s["notes"] == 1 and s["markers"] == 1 and s["seq"] == 3
    assert s["version"] == relay.RELAY_VERSION and s["port"] == server.server_address[1]
    assert s["db_bytes"] > 0 and s["uptime"] >= 0 and s["python"] and s["machine"] and s["owner_set"] is False
    assert s["serve_command"] == f"tailscale serve --bg {s['port']}" and s["profile_version"] == 0
    assert set(s["requests"]) == {"requests", "errors", "auth_failures", "last_auth_failure"}


def test_activity_counts_requests_errors_and_refused_tokens_without_content(server):
    n = note("secret words")
    call(server, "PUT", "/notes/" + n["id"], n, headers={"X-Vox-Device": "pixel"})
    call(server, "GET", "/notes?q=secret", headers={"X-Vox-Device": "pixel"})
    call(server, "GET", "/notes/" + nid(), headers={"X-Vox-Device": "pc"})      # 404
    call(server, "GET", "/changes", token="wrong")                              # refused
    _, a = call(server, "GET", "/admin/activity")
    routes = [(e["method"], e["route"], e["status"], e["device"]) for e in a["events"]]
    assert ("PUT", "/notes/{id}", 200, "pixel") in routes and ("GET", "/notes/{id}", 404, "pc") in routes
    assert "secret" not in json.dumps(a["events"]) and n["id"] not in json.dumps(a["events"])
    s = call(server, "GET", "/admin/status")[1]["requests"]
    assert s["auth_failures"] == 1 and s["errors"] >= 1 and s["requests"] >= 3 and s["last_auth_failure"]
    devs = {d["name"]: d for d in a["devices"]}
    assert devs["pixel"]["requests"] == 2 and devs["pc"]["requests"] == 1


def test_devices_are_remembered_across_restarts_and_carry_the_tailscale_user(tmp_path):
    a = relay.make_server(str(tmp_path), port=0)
    a.store.touch_device("laptop", "me@example.com")
    a.store.touch_device("laptop", "me@example.com")
    a.server_close()
    b = relay.make_server(str(tmp_path), port=0)
    try:
        d = b.store.devices()[0]
        assert d["name"] == "laptop" and d["requests"] == 2 and d["login"] == "me@example.com"
    finally:
        b.server_close()


def test_profile_view_hides_secrets_but_the_real_profile_keeps_them(server):
    prof = {"about": "I lead Atlas", "api_key": "gsk_real", "nested": {"stt_api_key": "abc", "model": "whisper"},
            "list": [{"token": "t0k"}], "password_hint": "", "n": 5}
    call(server, "PUT", "/profile", prof, headers={"If-Match": "0"})
    _, masked = call(server, "GET", "/admin/profile")
    dump = json.dumps(masked)
    assert "gsk_real" not in dump and "abc" not in dump and "t0k" not in dump
    assert masked["data"]["about"] == "I lead Atlas" and masked["data"]["nested"]["model"] == "whisper"
    assert masked["data"]["password_hint"] == "" and masked["data"]["n"] == 5 and masked["version"] == 1
    assert call(server, "GET", "/profile")[1]["data"]["api_key"] == "gsk_real"


# ------------------------------------------------------------ maintenance
def test_export_contains_live_notes_only_as_a_download(server):
    a = server.store.upsert_note(note("keep me"))[0]
    b = server.store.upsert_note(note("drop me"))[0]
    server.store.delete_note(b["id"])
    r, data = call(server, "GET", "/admin/export", raw_response=True)
    out = json.loads(data)
    assert 'attachment' in r.getheader("Content-Disposition")
    assert [n["id"] for n in out["notes"]] == [a["id"]] and "drop me" not in data.decode()


def test_backup_is_a_consistent_sqlite_copy(server, tmp_path):
    n = server.store.upsert_note(note("backed up"))[0]
    r, data = call(server, "GET", "/admin/backup", raw_response=True)
    assert data.startswith(b"SQLite format 3") and "attachment" in r.getheader("Content-Disposition")
    copy = tmp_path / "copy.db"
    copy.write_bytes(data)
    con = sqlite3.connect(str(copy))
    assert con.execute("SELECT text FROM notes WHERE id = ?", (n["id"],)).fetchone()[0] == "backed up"
    con.close()


def test_purge_removes_only_old_markers(server):
    old = server.store.upsert_note(note("old", updated_at=time.time() - 90 * 86400))[0]
    server.store.upsert_note(dict(old, deleted=True, updated_at=time.time() - 60 * 86400))
    fresh = server.store.upsert_note(note("fresh"))[0]
    server.store.delete_note(fresh["id"])
    live = server.store.upsert_note(note("live"))[0]
    st, out = call(server, "POST", "/admin/purge", {"days": 30})
    assert st == 200 and out["removed"] == 1
    assert server.store.details()["markers"] == 1 and server.store.get_note(live["id"])
    assert call(server, "POST", "/admin/purge", {"days": "many"})[0] == 400
    assert call(server, "POST", "/admin/purge", {"days": 0})[1]["removed"] == 1


def test_vacuum_runs_and_keeps_the_data(server):
    n = server.store.upsert_note(note("stays"))[0]
    st, out = call(server, "POST", "/admin/vacuum", {})
    assert st == 200 and out["ok"] and out["db_bytes"] > 0 and server.store.get_note(n["id"])["text"] == "stays"


def test_rotating_the_token_switches_at_once_and_is_saved(server, tmp_path):
    old = server.token
    st, out = call(server, "POST", "/admin/rotate-token", {})
    new = out["token"]
    assert st == 200 and new != old and len(new) >= 32
    assert call(server, "GET", "/health", token=old)[0] == 401
    assert call(server, "GET", "/health", token=new)[0] == 200
    assert json.load(open(os.path.join(str(tmp_path), "relay.json")))["token"] == new
    assert relay.load_config(str(tmp_path))["token"] == new


def test_admin_unknown_routes(server):
    assert call(server, "GET", "/admin/nothing")[0] == 404
    assert call(server, "POST", "/admin/status", {})[0] == 405


# ----------------------------------------------------------- portability
def test_default_data_dir_per_platform(monkeypatch):
    monkeypatch.setattr(relay.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/data/xdg")
    assert relay.default_data_dir() == os.path.join("/data/xdg", "vox-relay")
    monkeypatch.delenv("XDG_DATA_HOME")
    assert relay.default_data_dir() == os.path.join(os.path.expanduser("~/.local/share"), "vox-relay")
    monkeypatch.setattr(relay.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "C:\\Users\\x\\AppData\\Roaming")
    assert relay.default_data_dir() == os.path.join("C:\\Users\\x\\AppData\\Roaming", "VoxRelay")
    monkeypatch.setattr(relay.sys, "platform", "darwin")
    assert relay.default_data_dir().replace("\\", "/").endswith("Library/Application Support/VoxRelay")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_config_and_folder_are_private_on_posix(tmp_path):
    d = tmp_path / "data"
    relay.load_config(str(d))
    assert oct(os.stat(d).st_mode & 0o777) == "0o700"
    assert oct(os.stat(d / "relay.json").st_mode & 0o777) == "0o600"
    relay.rotate_token(str(d))
    assert oct(os.stat(d / "relay.json").st_mode & 0o777) == "0o600"


def test_the_relay_needs_only_the_standard_library():
    src = open(relay.__file__, encoding="utf-8").read()
    imported = set(re.findall(r"^(?:import|from) (\w+)", src, re.M))
    import sys
    names = getattr(sys, "stdlib_module_names", None)   # Python 3.10 and newer
    if names is None:
        pytest.skip("needs Python 3.10 or newer")
    assert imported <= set(names), imported - set(names)
