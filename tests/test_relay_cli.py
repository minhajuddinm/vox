"""Running the relay from the Windows app: `Vox.exe --relay`, the RelayHost that starts and stops it, and the tray toggle.

The app-level tests start windows/vox_app.py as a real subprocess with a temp APPDATA and temp relay folder."""
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

import relay
import relay_host
import vox_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS = os.path.join(HERE, "..", "windows")
VOX_APP = os.path.abspath(os.path.join(WINDOWS, "vox_app.py"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------- the command
def test_relay_command_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\Vox\Vox.exe")
    assert relay_host.relay_command(r"D:\relay", 8770) == [r"C:\Apps\Vox\Vox.exe", "--relay", "--data-dir", r"D:\relay", "--port", "8770"]


def test_relay_command_dev_runs_vox_app_with_the_same_python(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = relay_host.relay_command("/tmp/relay", 8771)
    assert cmd[0] == sys.executable
    assert os.path.samefile(cmd[1], VOX_APP)
    assert cmd[2:] == ["--relay", "--data-dir", "/tmp/relay", "--port", "8771"]


def test_config_defaults_use_the_relays_own_port(tmp_path):
    assert core.DEFAULT_CONFIG["relay_run"] is False
    assert core.DEFAULT_CONFIG["relay_port"] == relay.load_config(str(tmp_path / "fresh"))["port"]


def test_port_from_config_falls_back_for_nonsense():
    assert relay_host.port_from({"relay_port": 8790}) == 8790
    assert relay_host.port_from({"relay_port": "8791"}) == 8791
    for bad in (None, "", "abc", 0, 70000, -5, [8765]):
        assert relay_host.port_from({"relay_port": bad}) == core.DEFAULT_CONFIG["relay_port"]
    assert relay_host.port_from({}) == core.DEFAULT_CONFIG["relay_port"]


@pytest.mark.skipif(sys.platform != "win32", reason="the app is Windows only; the relay's default folder differs elsewhere")
def test_default_data_dir_is_the_relays_own(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert relay_host.default_data_dir() == relay.default_data_dir()


# ------------------------------------------------------------- RelayHost
class FakeProc:
    pid = 4242

    def __init__(self, stubborn=False):
        self.code, self.stubborn, self.calls = None, stubborn, []

    def poll(self):
        return self.code

    def terminate(self):
        self.calls.append("terminate")
        if not self.stubborn:
            self.code = 1

    def kill(self):
        self.calls.append("kill")
        self.code = 1

    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("relay", timeout)
        return self.code


class Host:
    """A RelayHost wired to a fake process launcher; `.procs` are the processes it started."""

    def __init__(self, tmp_path, port=8770, stubborn=False, fail=None, busy=False):
        self.procs, self.cmds, self.messages = [], [], []
        self.data_dir = str(tmp_path / "relay")

        def popen(cmd):
            self.cmds.append(cmd)
            if fail:
                raise fail
            self.procs.append(FakeProc(stubborn))
            return self.procs[-1]

        self.host = relay_host.RelayHost(self.data_dir, port, notify=self.messages.append, popen=popen, busy=lambda port: busy)


def test_start_launches_the_relay_once(tmp_path):
    h = Host(tmp_path, port=8770)
    assert not h.host.running()
    assert h.host.start() is True
    assert h.host.running()
    assert h.host.start() is True                       # already running: no second process
    assert h.cmds == [relay_host.relay_command(h.data_dir, 8770)]


def test_start_uses_the_current_port_attribute(tmp_path):
    h = Host(tmp_path, port=8770)
    h.host.port = 8799
    h.host.start()
    assert h.cmds[0][-2:] == ["--port", "8799"]


def test_stop_terminates_the_child_and_is_safe_to_repeat(tmp_path):
    h = Host(tmp_path)
    h.host.stop()                                       # never started: nothing to do
    h.host.start()
    h.host.stop()
    assert h.procs[0].calls == ["terminate"]
    assert not h.host.running()
    h.host.stop()
    assert h.procs[0].calls == ["terminate"]


def test_stop_kills_a_child_that_ignores_terminate(tmp_path, monkeypatch):
    monkeypatch.setattr(relay_host, "STOP_WAIT_SECONDS", 0.05)
    h = Host(tmp_path, stubborn=True)
    h.host.start()
    h.host.stop()
    assert h.procs[0].calls == ["terminate", "kill"]
    assert not h.host.running()


def test_can_start_again_after_stop(tmp_path):
    h = Host(tmp_path)
    h.host.start()
    h.host.stop()
    assert h.host.start() is True
    assert len(h.procs) == 2 and h.host.running()


def test_running_is_false_when_the_child_died_by_itself(tmp_path):
    h = Host(tmp_path)
    h.host.start()
    h.procs[0].code = 3
    assert not h.host.running()
    assert h.host.start() is True                       # a dead child is replaced
    assert len(h.procs) == 2


def test_first_start_shows_the_exact_tailscale_hint_and_later_starts_do_not(tmp_path):
    h = Host(tmp_path, port=8770)
    h.host.start()
    assert len(h.messages) == 1 and "tailscale serve --bg 8770" in h.messages[0]
    os.makedirs(h.data_dir)
    with open(os.path.join(h.data_dir, "relay.json"), "w") as f:     # what the relay writes on its first start
        json.dump({"token": "x", "port": 8770}, f)
    h.host.stop()
    h.host.start()
    assert len(h.messages) == 1


def test_first_start_hint_points_at_the_in_app_set_up_card(tmp_path):
    h = Host(tmp_path, port=8770)
    h.host.start()
    assert "Settings > Privacy > How to set up the relay" in h.messages[0] and "Test connection" in h.messages[0]


def test_a_launcher_error_is_reported_not_raised(tmp_path):
    h = Host(tmp_path, fail=OSError("no such file"))
    assert h.host.start() is False
    assert not h.host.running()
    assert len(h.messages) == 1 and "no such file" in h.messages[0]


def test_start_does_not_launch_a_second_relay_on_a_port_that_is_already_serving(tmp_path):
    """On Windows the relay's own bind would succeed on a taken port (SO_REUSEADDR), so the host checks first."""
    h = Host(tmp_path, port=8770, busy=True)
    assert h.host.start() is False
    assert h.cmds == [] and not h.host.running()
    assert len(h.messages) == 1 and "8770" in h.messages[0] and "already" in h.messages[0]


def test_port_busy_sees_a_real_listener():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert relay_host.port_busy(s.getsockname()[1]) is True
    assert relay_host.port_busy(free_port()) is False


def test_early_exit_is_reported(tmp_path):
    h = Host(tmp_path, port=8770)
    h.host.start()
    proc = h.procs[0]
    proc.code = 1                                       # e.g. the port was taken
    h.messages.clear()
    h.host._watch(proc)
    assert len(h.messages) == 1 and "8770" in h.messages[0]
    assert not h.host.running()


def test_a_deliberate_stop_is_not_reported_as_a_failure(tmp_path):
    h = Host(tmp_path)
    h.host.start()
    proc = h.procs[0]
    h.host.stop()
    h.messages.clear()
    h.host._watch(proc)
    assert h.messages == []


# ------------------------------------------------ the real process launcher
def alive(pid):
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        return code.value == 259                        # STILL_ACTIVE
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_a_child_does_not_outlive_a_parent_that_is_killed(tmp_path):
    parent_py = tmp_path / "parent.py"
    parent_py.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {os.path.abspath(WINDOWS)!r})\n"
        "import relay_host\n"
        "p = relay_host.spawn_hidden([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(120)\n")
    parent = subprocess.Popen([sys.executable, str(parent_py)], stdout=subprocess.PIPE, text=True)
    child = None
    try:
        child = int(parent.stdout.readline())
        assert alive(child)
        parent.kill()                                   # TerminateProcess: no cleanup code runs in the parent
        parent.wait(10)
        deadline = time.time() + 10
        while alive(child) and time.time() < deadline:
            time.sleep(0.1)
        assert not alive(child)
    finally:
        parent.kill()
        if child and alive(child):
            subprocess.run(["taskkill", "/F", "/PID", str(child)], capture_output=True)


# ------------------------------------------------------ Vox.exe --relay
def blockers(tmp_path):
    """Modules that fail on import: --relay must run without any GUI, audio or keyboard library."""
    folder = tmp_path / "blockers"
    folder.mkdir()
    for name in ("pystray", "webview", "pynput", "sounddevice", "tkinter", "pyperclip"):
        (folder / f"{name}.py").write_text("raise ImportError('must not be imported by --relay')\n")
    return str(folder)


def wait_for_health(data, port, still_running):
    """GET /health with the token from relay.json, retrying while the relay starts. Returns (status, json)."""
    deadline = time.time() + 30
    while time.time() < deadline:
        assert still_running(), "the relay process ended before it answered"
        try:
            with open(os.path.join(data, "relay.json"), encoding="utf-8") as f:
                token = json.load(f)["token"]
            req = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status, json.loads(r.read())
        except (OSError, ValueError, KeyError):
            time.sleep(0.2)
    raise AssertionError("the relay never answered")


def test_vox_app_relay_serves_health_without_any_gui(tmp_path):
    data, appdata, port = tmp_path / "relay", tmp_path / "appdata", free_port()
    appdata.mkdir()
    env = dict(os.environ, APPDATA=str(appdata), PYTHONPATH=blockers(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen([sys.executable, VOX_APP, "--relay", "--data-dir", str(data), "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        status, body = wait_for_health(str(data), port, lambda: proc.poll() is None)
        assert status == 200 and body["ok"] is True
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        proc.stdout.close()


def test_relay_host_runs_and_stops_a_real_relay(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))          # relay.log goes here, not into the real Vox folder
    (tmp_path / "appdata").mkdir()
    data, port, messages = str(tmp_path / "relay"), free_port(), []
    host = relay_host.RelayHost(data, port, notify=messages.append)
    try:
        assert host.start() is True
        status, body = wait_for_health(data, port, host.running)
        assert status == 200 and body["ok"] is True
        assert relay_host.port_busy(port)
        assert len(messages) == 1 and f"tailscale serve --bg {port}" in messages[0]
    finally:
        host.stop()
    assert not host.running()
    deadline = time.time() + 10
    while relay_host.port_busy(port) and time.time() < deadline:
        time.sleep(0.1)
    assert not relay_host.port_busy(port)                              # the process is gone, the port is free again


@pytest.mark.parametrize("path", ["windows/build_app.bat", ".github/workflows/build.yml"])
def test_both_exe_builds_bundle_the_relay(path):
    """The frozen exe runs `--relay` by importing relay.py, which lives outside windows/: PyInstaller must be told."""
    with open(os.path.join(HERE, "..", path), encoding="utf-8") as f:
        text = f.read()
    assert "--hidden-import relay" in text
    assert re.search(r"--paths \"?(%~dp0)?\.\.[\\/]relay", text)


# ---------------------------------------------------------------- the tray
@pytest.fixture
def eng(tmp_path, monkeypatch):
    for mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
        pytest.importorskip(mod)
    import engine as engine_mod
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    e = object.__new__(engine_mod.Engine)
    e.cfg = core.load_config()
    e.messages = []
    e.notify = e.messages.append

    class FakeHost:
        def __init__(self):
            self.port, self.calls = 0, []

        def start(self):
            self.calls.append(("start", self.port))
            return True

        def stop(self):
            self.calls.append(("stop",))

        def running(self):
            return bool(self.calls) and self.calls[-1][0] == "start"

    e.relay = FakeHost()
    e.engine_mod = engine_mod
    return e


def test_toggle_relay_persists_the_setting_and_starts_and_stops(eng):
    eng.cfg["relay_port"] = 8777
    eng.toggle_relay()
    assert core.load_config()["relay_run"] is True and eng.cfg["relay_run"] is True
    assert eng.relay.calls == [("start", 8777)]
    eng.toggle_relay()
    assert core.load_config()["relay_run"] is False and eng.cfg["relay_run"] is False
    assert eng.relay.calls[-1] == ("stop",)


def test_toggle_relay_keeps_the_other_settings_in_the_file(eng):
    saved = core.load_config()
    saved["user_context"] = "changed by the window a moment ago"
    core.save_config(saved)                             # the engine's own copy (eng.cfg) is stale
    eng.toggle_relay()
    assert core.load_config()["user_context"] == "changed by the window a moment ago"


def test_the_tray_menu_has_a_checkable_relay_item(eng, monkeypatch):
    engine_mod = eng.engine_mod
    monkeypatch.setattr(engine_mod.keyboard, "Controller", lambda: object())
    e = engine_mod.Engine()
    item = next(i for i in e.icon.menu.items if i.text == "Run relay on this PC")
    assert item.checked is False
    e.cfg["relay_run"] = True
    assert item.checked is True


def test_quit_stops_the_relay_before_the_process_exits(eng, monkeypatch):
    engine_mod = eng.engine_mod
    order = []
    monkeypatch.setattr(engine_mod.os, "_exit", lambda code: order.append("exit"))
    eng.meeting = type("M", (), {"active": False, "processing": False})()
    eng.sync = type("S", (), {"stop": lambda self: None})()
    eng.icon = type("I", (), {"stop": lambda self: None})()
    eng.overlay = None
    eng.relay.stop = lambda: order.append("relay stopped")
    eng.quit()
    assert order == ["relay stopped", "exit"]
