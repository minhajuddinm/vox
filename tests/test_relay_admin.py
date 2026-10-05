"""The relay's management side: the web page, status, devices and activity, masked profile, export, backup,
compact, purge, token rotation, per-platform data folders, and the AI server (proxy) settings with their write-only keys."""
import http.client
import json
import logging
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


ADMIN_ONLY = ("/admin/rotate-token", "/admin/upstream")   # bf-e SEC-4: changes there need the admin token


def call(srv, method, path, body=None, token=True, headers=None, raw_response=False):
    h = dict(headers or {})
    if token:
        mine = srv.admin_token if path in ADMIN_ONLY and method != "GET" else srv.token
        h["Authorization"] = "Bearer " + (mine if token is True else token)
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


# ------------------------------------------------ AI server (proxy) settings
STT_KEY = "sk-test-STT-0123456789abcdef"
LLM_KEY = "gsk_test_LLM_fedcba9876543210"
UNSET = {"stt": {"base_url": "", "key_set": False}, "llm": {"base_url": "", "key_set": False}}

GOOD_ADDRESSES = [
    "https://api.groq.com/openai/v1",
    "https://example.com",
    "HTTPS://Example.com:8443/v1",
    "https://[2606:4700::1111]/v1",
    "http://127.0.0.1:8000/v1",
    "http://localhost:11434/v1",
    "http://[::1]:8000/v1",
    "http://192.168.1.20:8080",
    "http://10.0.0.5/v1",
    "http://172.16.0.1",
    "http://172.31.255.255",
    "http://100.64.0.1:9000",            # Tailscale (100.64.0.0/10)
    "http://100.127.255.255",
    "http://your-pi:8000/v1",            # one-label name
    "http://your-pi.your-tailnet.ts.net", # MagicDNS
    "http://my-nas.local/v1",
    "http://printer.lan",
]
BAD_ADDRESSES = [
    "",
    "   ",
    "ftp://example.com/v1",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "//example.com/v1",
    "example.com/v1",
    "host:8000/v1",
    "just some words",
    "https://",
    "http://",
    "https:///v1",
    "https://user:hunter2@example.com/v1",
    "https://user@example.com/v1",
    "https://example.com/v1#frag",
    "https://example.com/v1#",
    "https://example.com/v1?x=1",
    "https://example.com/v1?",
    "https://exa mple.com/v1",
    "https://example.com/v1\n",
    " https://example.com/v1",
    "https://example.com\\@evil.test/",
    "https://example.com:99999/v1",
    "https://example.com:abc/v1",
    "http://[::1/v1",
    "https://bad$host/v1",
    "https://bücher.example/v1",
    "https://example.com/" + "a" * 3000,
    "http://example.com",                # plain http to the public internet
    "http://example.com/v1",
    "http://8.8.8.8",
    "http://100.63.255.255",             # just below Tailscale's range
    "http://100.128.0.1",                # just above it
    "http://172.15.0.1",
    "http://172.32.0.1",
    "http://192.169.0.1",
    "http://[2606:4700::1111]/v1",
    "http://my.server.internal",
]


def addr_id(url):
    return "long-address" if len(url) > 60 else repr(url)


@pytest.mark.parametrize("url", GOOD_ADDRESSES, ids=addr_id)
def test_upstream_problem_accepts_https_and_plain_http_on_private_hosts(url):
    assert relay.upstream_problem(url) is None


@pytest.mark.parametrize("url", BAD_ADDRESSES, ids=addr_id)
def test_upstream_problem_refuses_everything_else(url):
    msg = relay.upstream_problem(url)
    assert isinstance(msg, str) and msg


@pytest.mark.parametrize("value", [None, 5, ["https://example.com"], {"a": 1}])
def test_upstream_problem_refuses_non_text(value):
    assert relay.upstream_problem(value)


def test_upstream_problem_explains_plain_http_and_never_repeats_the_address():
    assert "Plain http" in relay.upstream_problem("http://example.com")
    for url in ("https://user:hunter2@example.com/v1", "http://example.com/hunter2", "https://example.com/v1?key=hunter2",
                "https://example.com/hunter2#hunter2", "ftp://hunter2.example.com"):
        assert "hunter2" not in (relay.upstream_problem(url) or "")


def test_upstream_starts_empty_and_needs_the_token(server):
    assert call(server, "GET", "/admin/upstream") == (200, UNSET)
    assert call(server, "GET", "/admin/upstream", token=False)[0] == 401
    assert call(server, "GET", "/admin/upstream", token="nope")[0] == 401
    assert call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": "https://example.com"}, token=False)[0] == 401
    assert call(server, "DELETE", "/admin/upstream")[0] == 405 and call(server, "POST", "/admin/upstream", {})[0] == 405


def test_saving_an_address_and_key_persists_and_the_view_never_has_the_key(server, tmp_path):
    st, view = call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": "  https://stt.example.com/v1/\n", "api_key": STT_KEY})
    assert st == 200
    assert view == {"stt": {"base_url": "https://stt.example.com/v1", "key_set": True}, "llm": UNSET["llm"]}   # trimmed, trailing slash dropped
    assert call(server, "GET", "/admin/upstream")[1] == view
    cfg = json.load(open(os.path.join(str(tmp_path), "relay.json")))
    assert cfg["upstream"]["stt"] == {"base_url": "https://stt.example.com/v1", "api_key": STT_KEY}   # the file is where the key lives
    call(server, "PUT", "/admin/upstream", {"role": "llm", "base_url": "http://192.168.1.20:8080/v1", "api_key": LLM_KEY})
    _, view = call(server, "GET", "/admin/upstream")
    assert view["stt"]["base_url"] == "https://stt.example.com/v1" and view["llm"] == {"base_url": "http://192.168.1.20:8080/v1", "key_set": True}


def test_saved_settings_survive_a_restart(tmp_path):
    a = relay.make_server(str(tmp_path), port=0)
    a.set_upstream("llm", "http://localhost:11434/v1", STT_KEY)
    a.server_close()
    b = relay.make_server(str(tmp_path), port=0)
    try:
        assert b.upstream_view() == {"stt": UNSET["stt"], "llm": {"base_url": "http://localhost:11434/v1", "key_set": True}}
        assert relay.load_config(str(tmp_path))["upstream"]["llm"]["api_key"] == STT_KEY
    finally:
        b.server_close()


def test_a_missing_key_keeps_the_stored_one_and_an_empty_key_clears_it(server):
    put = lambda **kw: call(server, "PUT", "/admin/upstream", dict({"role": "stt", "base_url": "https://stt.example.com"}, **kw))[1]["stt"]  # noqa: E731
    assert put(api_key=STT_KEY)["key_set"] is True
    assert put()["key_set"] is True                                  # omitted: keep
    assert put(api_key=None)["key_set"] is True                      # null counts as omitted
    assert put(api_key="")["key_set"] is False                       # empty: clear
    assert put()["key_set"] is False
    assert put(api_key="  " + LLM_KEY + "\n")["key_set"] is True     # pasted whitespace is trimmed
    assert relay.load_config(server.data_dir)["upstream"]["stt"]["api_key"] == LLM_KEY


def test_the_key_stays_only_while_the_address_is_unchanged(server):
    """A key belongs to the address it was saved for: pointing the role somewhere else must not send it there."""
    put = lambda url, **kw: call(server, "PUT", "/admin/upstream", dict({"role": "stt", "base_url": url}, **kw))[1]["stt"]  # noqa: E731
    put("https://one.example.com/v1", api_key=STT_KEY)
    assert put("https://one.example.com/v1/")["key_set"] is True            # same address, trailing slash only
    assert put("https://two.example.com/v1")["key_set"] is False            # moved: old key dropped
    assert relay.load_config(server.data_dir)["upstream"]["stt"]["api_key"] == ""
    put("https://two.example.com/v1", api_key=LLM_KEY)
    assert put("https://three.example.com/v1", api_key=LLM_KEY)["key_set"] is True   # moved with a new key: kept
    assert put("")["key_set"] is False and put("")["base_url"] == ""        # clearing the address clears the key too


def test_roles_are_independent(server):
    call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": "https://stt.example.com", "api_key": STT_KEY})
    call(server, "PUT", "/admin/upstream", {"role": "llm", "base_url": "https://llm.example.com", "api_key": LLM_KEY})
    call(server, "PUT", "/admin/upstream", {"role": "llm", "base_url": "", "api_key": ""})
    _, view = call(server, "GET", "/admin/upstream")
    assert view == {"stt": {"base_url": "https://stt.example.com", "key_set": True}, "llm": {"base_url": "", "key_set": False}}


def test_bad_addresses_are_refused_and_change_nothing(server):
    call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": "https://good.example.com", "api_key": STT_KEY})
    path = os.path.join(server.data_dir, "relay.json")
    before = open(path, "rb").read()
    for url in BAD_ADDRESSES:
        if not url or url != url.strip():
            continue   # the setter trims whitespace, and "" clears the address
        st, out = call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": url, "api_key": LLM_KEY})
        assert st == 400 and out["error"] and LLM_KEY not in json.dumps(out), addr_id(url)
        assert call(server, "GET", "/admin/upstream")[1]["stt"] == {"base_url": "https://good.example.com", "key_set": True}, addr_id(url)
        assert open(path, "rb").read() == before, addr_id(url)


BAD_BODIES = [
    None, ["stt"], "stt", {}, {"base_url": "https://example.com"},
    {"role": "stt"}, {"role": "tts", "base_url": "https://example.com"}, {"role": None, "base_url": "https://example.com"},
    {"role": ["stt"], "base_url": "https://example.com"}, {"role": "STT", "base_url": "https://example.com"},
    {"role": "stt", "base_url": 5}, {"role": "stt", "base_url": None}, {"role": "stt", "base_url": ["https://example.com"]},
    {"role": "stt", "base_url": "https://example.com", "api_key": 12345},
    {"role": "stt", "base_url": "https://example.com", "api_key": ["k"]},
    {"role": "stt", "base_url": "https://example.com", "api_key": "two words"},
    {"role": "stt", "base_url": "https://example.com", "api_key": "line\nbreak"},
    {"role": "stt", "base_url": "https://example.com", "api_key": "café"},
    {"role": "stt", "base_url": "https://example.com", "api_key": "k" * (relay.MAX_KEY + 1)},
]


def test_bad_bodies_are_refused_and_change_nothing(server):
    for body in BAD_BODIES:
        st, out = call(server, "PUT", "/admin/upstream", body)
        assert st == 400 and out["error"], body
    assert call(server, "GET", "/admin/upstream")[1] == UNSET
    assert "upstream" not in json.load(open(os.path.join(server.data_dir, "relay.json")))


def test_a_key_of_the_longest_allowed_size_is_accepted(server):
    st, out = call(server, "PUT", "/admin/upstream", {"role": "llm", "base_url": "https://example.com", "api_key": "k" * relay.MAX_KEY})
    assert st == 200 and out["llm"]["key_set"] is True


def test_rotating_the_token_keeps_the_upstream_settings(server, tmp_path):
    call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": "https://stt.example.com", "api_key": STT_KEY})
    st, out = call(server, "POST", "/admin/rotate-token", {})
    assert st == 200 and STT_KEY not in json.dumps(out)
    assert call(server, "GET", "/admin/upstream")[1]["stt"] == {"base_url": "https://stt.example.com", "key_set": True}
    cfg = relay.load_config(str(tmp_path))
    assert cfg["token"] == out["token"] and cfg["upstream"]["stt"] == {"base_url": "https://stt.example.com", "api_key": STT_KEY}
    assert relay.rotate_token(str(tmp_path)) != out["token"]
    assert relay.load_config(str(tmp_path))["upstream"]["stt"]["api_key"] == STT_KEY


def test_saving_keeps_everything_else_in_relay_json(tmp_path):
    """A hand-edited relay.json: other keys, other roles and extra fields inside a role all survive a save."""
    path = tmp_path / "relay.json"
    path.write_text(json.dumps({"token": "t" * 43, "port": 1, "owner": "", "future": {"a": [1, 2]},
                                "upstream": {"tts": {"base_url": "https://tts.example.com", "api_key": "x"},
                                             "stt": {"base_url": "https://old.example.com", "api_key": "old", "model": "whisper-1"}}}))
    srv = relay.make_server(str(tmp_path), port=0)
    try:
        assert srv.upstream_view() == {"stt": {"base_url": "https://old.example.com", "key_set": True}, "llm": UNSET["llm"]}
        srv.set_upstream("llm", "https://llm.example.com", LLM_KEY)
        srv.set_upstream("stt", "https://old.example.com", None)
    finally:
        srv.server_close()
    cfg = json.loads(path.read_text())
    assert cfg["future"] == {"a": [1, 2]} and cfg["token"] == "t" * 43
    assert cfg["upstream"]["tts"] == {"base_url": "https://tts.example.com", "api_key": "x"}
    assert cfg["upstream"]["stt"] == {"base_url": "https://old.example.com", "api_key": "old", "model": "whisper-1"}
    assert cfg["upstream"]["llm"] == {"base_url": "https://llm.example.com", "api_key": LLM_KEY}


@pytest.mark.parametrize("junk", [None, 5, "text", ["x"], {"stt": "https://example.com"}, {"stt": {"base_url": 5, "api_key": ["k"]}}, {"llm": None}])
def test_a_hand_edited_upstream_of_the_wrong_shape_reads_as_not_set(tmp_path, junk):
    (tmp_path / "relay.json").write_text(json.dumps({"token": "t" * 43, "upstream": junk}))
    srv = relay.make_server(str(tmp_path), port=0)
    try:
        assert srv.upstream_view() == UNSET
        assert srv.set_upstream("stt", "https://example.com", "k")["stt"] == {"base_url": "https://example.com", "key_set": True}
    finally:
        srv.server_close()


def test_saving_needs_a_data_folder(tmp_path):
    srv = relay.RelayServer(("127.0.0.1", 0), relay.RelayStore(str(tmp_path / "x.db")), "tok", admin_token="admin-tok")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert call(srv, "GET", "/admin/upstream") == (200, UNSET)
        st, out = call(srv, "PUT", "/admin/upstream", {"role": "stt", "base_url": "https://example.com"})
        assert st == 409 and out["error"]
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_relay_json_stays_private_after_saving_settings(server, tmp_path):
    path = tmp_path / "relay.json"
    call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": "https://stt.example.com", "api_key": STT_KEY})
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": ""})
    call(server, "POST", "/admin/rotate-token", {})
    assert oct(os.stat(path).st_mode & 0o777) == "0o600" and oct(os.stat(tmp_path).st_mode & 0o777) == "0o700"


def test_concurrent_saves_of_both_roles_do_not_lose_each_other(server):
    errors = []

    def worker(role, key):
        for i in range(15):
            st, _ = call(server, "PUT", "/admin/upstream", {"role": role, "base_url": f"https://{role}{i}.example.com", "api_key": key})
            if st != 200:
                errors.append(st)
    threads = [threading.Thread(target=worker, args=a) for a in (("stt", STT_KEY), ("llm", LLM_KEY))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert call(server, "GET", "/admin/upstream")[1] == {"stt": {"base_url": "https://stt14.example.com", "key_set": True},
                                                         "llm": {"base_url": "https://llm14.example.com", "key_set": True}}
    cfg = relay.load_config(server.data_dir)["upstream"]
    assert cfg["stt"]["api_key"] == STT_KEY and cfg["llm"]["api_key"] == LLM_KEY and cfg["stt"]["base_url"] == "https://stt14.example.com"


def test_a_saved_key_never_appears_in_any_response_download_or_output(server, tmp_path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    keys = (STT_KEY, LLM_KEY)
    server.store.upsert_note(note("a note about the weather"))
    call(server, "PUT", "/profile", {"about": "me", "stt_model": "whisper"}, headers={"If-Match": "0"})
    seen = []

    def grab(method, path, body=None, **kw):
        r, data = call(server, method, path, body, raw_response=True, **kw)
        seen.append((method, path, r.status, data))
        return r.status, data

    assert grab("PUT", "/admin/upstream", {"role": "stt", "base_url": "https://stt.example.com/v1", "api_key": STT_KEY})[0] == 200
    assert grab("PUT", "/admin/upstream", {"role": "llm", "base_url": "http://192.168.1.20:8080/v1", "api_key": LLM_KEY})[0] == 200
    assert grab("PUT", "/admin/upstream", {"role": "llm", "base_url": "http://192.168.1.20:8080/v1"})[0] == 200        # keep
    # refused requests that carry a key: pasted into the wrong place, or with a bad address, role or shape
    assert grab("PUT", "/admin/upstream", {"role": "stt", "base_url": "https://example.com/?key=" + STT_KEY})[0] == 400
    assert grab("PUT", "/admin/upstream", {"role": "stt", "base_url": "https://" + LLM_KEY + "@example.com", "api_key": LLM_KEY})[0] == 400
    assert grab("PUT", "/admin/upstream", {"role": "stt", "base_url": "http://example.com", "api_key": STT_KEY})[0] == 400
    assert grab("PUT", "/admin/upstream", {"role": STT_KEY, "base_url": "https://example.com", "api_key": STT_KEY})[0] == 400
    assert grab("PUT", "/admin/upstream", {"role": "stt", "base_url": "https://example.com", "api_key": STT_KEY + " with space"})[0] == 400
    assert grab("PUT", "/admin/upstream", {"role": "stt", "base_url": "https://example.com", "api_key": [STT_KEY]})[0] == 400
    for path in ("/admin/upstream", "/admin/status", "/admin/activity", "/admin/profile", "/admin/export", "/admin/backup",
                 "/profile", "/health", "/changes", "/notes", "/notes?q=weather", "/nothing"):
        assert grab("GET", path)[0] in (200, 404)
    assert grab("GET", "/", token=False)[0] == 200
    assert grab("POST", "/admin/rotate-token", {})[0] == 200
    assert grab("GET", "/admin/upstream")[0] == 200
    assert len(seen) >= 20
    for method, path, status, data in seen:
        for key in keys:
            assert key.encode() not in data, f"{method} {path} -> {status} contains a key"
    out, err = capsys.readouterr()
    for key in keys:
        assert key not in out + err + caplog.text
    on_disk = open(os.path.join(str(tmp_path), "relay.json"), encoding="utf-8").read()
    assert STT_KEY in on_disk and LLM_KEY in on_disk      # proves the assertions above had something to find


def test_the_page_has_a_write_only_ai_server_section():
    assert "AI server (proxy)" in relay.UI_HTML and "key set: " in relay.UI_HTML
    js = re.search(r"<script nonce=\"__NONCE__\">(.*?)</script>", relay.UI_HTML, re.S).group(1)
    assert 'type: "password"' in js and "/admin/upstream" in js and "innerHTML" not in js


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


# ------------------------------------------------------------------ medium round M4 (C-R4)
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX owners and modes")


@posix_only
def test_a_planted_tmp_symlink_is_not_written_through(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    d = tmp_path / "data"
    d.mkdir(mode=0o700)
    os.symlink(victim, d / "relay.json.tmp")
    cfg = relay.load_config(str(d))
    assert victim.read_text() == "keep me"
    assert json.loads((d / "relay.json").read_text())["token"] == cfg["token"]
    assert oct(os.stat(d / "relay.json").st_mode & 0o777) == "0o600"


@posix_only
def test_a_data_folder_that_others_can_write_to_is_refused(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    os.chmod(d, 0o777)
    with pytest.raises(relay.DataDirError):
        relay.load_config(str(d))
    with pytest.raises(relay.DataDirError):
        relay.make_server(str(d), port=0)
    assert not (d / "relay.json").exists()
    assert relay.main(["--data-dir", str(d)]) == 1       # the command line says why and stops


@posix_only
def test_a_folder_or_file_that_others_can_only_read_is_closed_not_refused(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    os.chmod(d, 0o755)
    relay.load_config(str(d))
    assert oct(os.stat(d).st_mode & 0o777) == "0o700"
    os.chmod(d / "relay.json", 0o644)
    relay.load_config(str(d))
    assert oct(os.stat(d / "relay.json").st_mode & 0o777) == "0o600"


@posix_only
def test_a_relay_json_that_others_can_write_is_refused(tmp_path):
    d = tmp_path / "data"
    d.mkdir(mode=0o700)
    relay.load_config(str(d))
    os.chmod(d / "relay.json", 0o666)
    with pytest.raises(relay.DataDirError):
        relay.load_config(str(d))


@posix_only
def test_a_folder_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: os.stat(tmp_path).st_uid + 1)
    with pytest.raises(relay.DataDirError):
        relay.load_config(str(tmp_path / "data"))



# ------------------------------------------------------------- bf-e: SEC-8
def test_a_refused_request_leaves_no_text_of_its_own_in_the_activity_log(server):
    path = "/Relay-moved-see-http--evil.example-" + "x" * 5000
    assert call(server, "GET", path, token="wrong")[0] == 401
    assert call(server, "GET", path, token=False)[0] == 401
    _, a = call(server, "GET", "/admin/activity")
    refused = [e for e in a["events"] if e["status"] == 401]
    assert len(refused) == 2 and all(e["route"] == "(refused)" and e["device"] == "" for e in refused)
    assert "evil" not in json.dumps(a["events"])


# ------------------------------------------------------------- bf-e: SEC-4 (an admin token the devices do not hold)
def test_relay_json_has_an_admin_token_of_its_own_that_stays(tmp_path):
    a = relay.load_config(str(tmp_path))
    assert len(a["admin_token"]) >= 32 and a["admin_token"] != a["token"]
    assert relay.load_config(str(tmp_path))["admin_token"] == a["admin_token"]
    assert relay.rotate_token(str(tmp_path)) != a["token"]
    assert relay.load_config(str(tmp_path))["admin_token"] == a["admin_token"]      # rotating the device token keeps it


def test_a_device_token_cannot_lock_the_owner_out_or_re_point_the_ai_server(server):
    old = server.token
    st, out = call(server, "POST", "/admin/rotate-token", {}, token=old)
    assert st == 403 and "admin" in out["error"] and server.token == old
    st, out = call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": "https://example.com/v1"}, token=old)
    assert st == 403 and server.upstream_view()["stt"]["base_url"] == ""
    assert call(server, "GET", "/admin/upstream", token=old)[0] == 200                # looking is fine
    assert call(server, "GET", "/health", token=old)[0] == 200


def test_the_admin_token_does_both_and_signs_in_to_the_page(server):
    admin = server.admin_token
    assert call(server, "GET", "/health", token=admin)[0] == 200
    assert call(server, "GET", "/admin/status", token=admin)[1]["admin"] is True
    assert call(server, "GET", "/admin/status")[1]["admin"] is False
    st, out = call(server, "PUT", "/admin/upstream", {"role": "stt", "base_url": "https://example.com/v1"}, token=admin)
    assert st == 200 and out["stt"]["base_url"] == "https://example.com/v1"
    st, out = call(server, "POST", "/admin/rotate-token", {}, token=admin)
    assert st == 200 and out["token"] == server.token and server.admin_token == admin


def test_show_token_prints_both(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(relay.RelayServer, "serve_forever", lambda self: None)
    relay.main(["--data-dir", str(tmp_path), "--port", "0", "--show-token"])
    out = capsys.readouterr().out
    cfg = relay.load_config(str(tmp_path))
    assert "Token: " + cfg["token"] in out and "Admin token: " + cfg["admin_token"] in out


def test_a_server_made_without_an_admin_token_allows_no_admin_changes(tmp_path):
    srv = relay.RelayServer(("127.0.0.1", 0), relay.RelayStore(str(tmp_path / "x.db")), "tok")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert call(srv, "POST", "/admin/rotate-token", {}, token="")[0] == 401         # "Bearer " is not an admin token
        assert call(srv, "POST", "/admin/rotate-token", {}, token="tok")[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()
