"""Runs the relay (relay/relay.py) as a child process of the Vox engine, for the tray item "Run relay on this PC".

The child is the same program started with --relay (see vox_app.py), so the installed Vox.exe needs no Python.
Nothing here imports the relay or any GUI library.
"""
import logging
import os
import socket
import subprocess
import sys
import threading

log = logging.getLogger("vox")

DEFAULT_PORT = 8765            # the relay's own default (relay.py load_config)
STOP_WAIT_SECONDS = 5          # how long stop() waits for the child to end before killing it
EARLY_EXIT_SECONDS = 10        # a child that ends within this long of starting counts as "could not start"
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

_job = None                    # Windows job object handle; kept open for the life of this process


def default_data_dir():
    """Same folder as `python relay.py` uses on Windows, so both ways of running it share one relay."""
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "VoxRelay")


def port_from(cfg):
    """The relay port from the settings; anything unusable means the default."""
    try:
        port = int(cfg.get("relay_port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def serve_hint(port):
    return f"tailscale serve --bg {port}"


def port_busy(port):
    """True when something already accepts connections on 127.0.0.1:port. Asked before starting because on Windows
    the relay's own bind succeeds on a taken port (the server sets SO_REUSEADDR), so it would not fail by itself."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def relay_command(data_dir, port):
    """Command line that runs the relay: the installed Vox.exe, or vox_app.py with the same Python in development."""
    tail = ["--relay", "--data-dir", data_dir, "--port", str(port)]
    if getattr(sys, "frozen", False):
        return [sys.executable] + tail
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vox_app.py")] + tail


def _kill_with_parent(proc):
    """Windows: puts the child in a job object that kills it when this process ends, however it ends (crash,
    End task, os._exit). The relay is then never left running with nobody in charge of it."""
    global _job
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    if _job is None:
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        _job = job
    if not k32.AssignProcessToJobObject(_job, int(proc._handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def spawn_hidden(cmd):
    """Starts `cmd` with no console window and its output discarded; on Windows it dies with this process."""
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    if sys.platform == "win32":
        try:
            _kill_with_parent(proc)
        except Exception:
            log.exception("could not tie the relay process to Vox; stopping it on quit still works")
    return proc


class RelayHost:
    """Starts, watches and stops the relay child process. `notify(text)` shows a message to the user."""

    def __init__(self, data_dir, port=DEFAULT_PORT, notify=None, popen=spawn_hidden, busy=port_busy):
        self.data_dir, self.port = data_dir, port
        self._notify = notify or (lambda text: None)
        self._popen = popen
        self._busy = busy
        self._lock = threading.Lock()
        self.proc = None

    def running(self):
        proc = self.proc
        return proc is not None and proc.poll() is None

    def start(self):
        """Starts the relay unless it is already running. Returns True when it is running afterwards."""
        with self._lock:
            if self.running():
                return True
            if self._busy(self.port):
                self._notify(f"Port {self.port} is already in use (is another relay running?), so Vox did not start its own. "
                             "Stop the other one, or change relay_port.")
                return False
            first_time = not os.path.exists(os.path.join(self.data_dir, "relay.json"))
            try:
                proc = self._popen(relay_command(self.data_dir, self.port))
            except OSError as e:
                log.exception("relay did not start")
                self._notify(f"Could not start the relay: {e}")
                return False
            self.proc = proc
        log.info("relay started on port %s (pid %s)", self.port, getattr(proc, "pid", "?"))
        if first_time:
            self._notify(f"The relay is running on this PC (port {self.port}). To reach it from your other devices, run: {serve_hint(self.port)} "
                         "(Vox > Settings > Privacy > How to set up the relay has the steps and the Test connection button.)")
        threading.Thread(target=self._watch, args=(proc,), daemon=True, name="relay-watch").start()
        return True

    def stop(self):
        """Ends the relay if it is running (it is stopped hard after a few seconds if it does not end when asked)."""
        with self._lock:
            proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(STOP_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(STOP_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                log.error("the relay process did not end")
        log.info("relay stopped")

    def _watch(self, proc):
        """Tells the user when the relay ends right after it started (bad port, unwritable data folder, ...)."""
        try:
            code = proc.wait(EARLY_EXIT_SECONDS)
        except subprocess.TimeoutExpired:
            return
        with self._lock:
            if self.proc is not proc:   # stopped on purpose, or already replaced
                return
            self.proc = None
        log.error("relay ended right after starting (exit code %s)", code)
        self._notify(f"The relay stopped right after starting (port {self.port}). Details are in relay.log in Vox's data folder.")
