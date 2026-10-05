"""One writer at a time for config.json and history.jsonl (DAT-1, ARC-04, DAT-2 of the v2 review).

The window and the engine are two processes and both write these files from several threads. Overlapping saves used to
share one `config.json.tmp`, which left a torn file (Vox then started on the defaults: no key, empty dictionary), and a
read-modify-write could drop a change saved in between.
"""
import glob
import json
import os
import subprocess
import sys
import threading

import pytest

import vox_core as core

WINDOWS = os.path.join(os.path.dirname(__file__), "..", "windows")


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(core, "_config_unread", False)   # a test elsewhere may have left an unopenable file behind
    return tmp_path


def test_parallel_read_modify_writes_in_threads_lose_nothing(appdata):
    core.save_config(dict(core.DEFAULT_CONFIG, dictionary=["Keepme"]))
    errors = []

    def worker(name):
        try:
            for _ in range(25):
                core.update_config(lambda c: c.__setitem__(name, c.get(name, 0) + 1))
        except Exception as e:   # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(f"k_{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    cfg = core.load_config()
    assert not errors
    assert [cfg[f"k_{i}"] for i in range(4)] == [25] * 4
    assert cfg["dictionary"] == ["Keepme"]
    assert not glob.glob(core.config_path() + ".bad-*")


_PROCESS = """
import sys
sys.path.insert(0, sys.argv[1])
import vox_core as core
for _ in range(int(sys.argv[3])):
    core.update_config(lambda c: c.__setitem__(sys.argv[2], c.get(sys.argv[2], 0) + 1))
"""


def test_two_processes_saving_at_once_never_tear_the_file(appdata):
    core.save_config(dict(core.DEFAULT_CONFIG, dictionary=["Keepme"]))
    env = dict(os.environ, APPDATA=str(appdata))
    procs = [subprocess.Popen([sys.executable, "-c", _PROCESS, WINDOWS, name, "60"], env=env)
             for name in ("k_ui", "k_engine")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    cfg = core.load_config()
    assert (cfg["k_ui"], cfg["k_engine"]) == (60, 60)
    assert cfg["dictionary"] == ["Keepme"]
    assert not glob.glob(core.config_path() + ".bad-*")


def test_update_config_returns_what_the_change_returns_and_skips_an_unchanged_file(appdata):
    core.save_config(dict(core.DEFAULT_CONFIG, language="de"))
    before = os.stat(core.config_path()).st_mtime_ns
    os.utime(core.config_path(), ns=(before - 10 ** 9, before - 10 ** 9))
    assert core.update_config(lambda c: c["language"]) == "de"
    assert os.stat(core.config_path()).st_mtime_ns == before - 10 ** 9   # nothing changed: not written


def test_update_config_refuses_while_the_file_could_not_be_opened(appdata, monkeypatch):
    core.save_config(dict(core.DEFAULT_CONFIG, language="de"))
    monkeypatch.setattr(core, "_config_unread", True)
    monkeypatch.setattr(core, "load_config", lambda: dict(core.DEFAULT_CONFIG))
    with pytest.raises(OSError):
        core.update_config(lambda c: c.__setitem__("language", "fr"))
    with open(core.config_path(), encoding="utf-8") as f:
        assert json.load(f)["language"] == "de"


def test_a_replace_refused_for_a_moment_is_retried(appdata, monkeypatch):
    real, calls = os.replace, {"n": 0}

    def busy(src, dst):
        if dst == core.config_path() and calls["n"] < 2:
            calls["n"] += 1
            raise PermissionError(13, "Access is denied")   # the other process is reading config.json
        return real(src, dst)

    monkeypatch.setattr(core.os, "replace", busy)
    core.save_config(dict(core.DEFAULT_CONFIG, language="fr"))
    assert calls["n"] == 2
    assert core.load_config()["language"] == "fr"
    assert not glob.glob(os.path.join(str(appdata), "Vox", "*.tmp"))


def test_a_failed_save_leaves_the_old_file_and_no_temp_file(appdata, monkeypatch):
    core.save_config(dict(core.DEFAULT_CONFIG, language="de"))

    def refuse(src, dst):
        raise PermissionError(13, "Access is denied")

    with monkeypatch.context() as m:
        m.setattr(core.os, "replace", refuse)
        m.setattr(core, "_OPEN_PAUSE", 0)
        with pytest.raises(PermissionError):
            core.save_config(dict(core.DEFAULT_CONFIG, language="fr"))
    assert core.load_config()["language"] == "de"
    assert not glob.glob(os.path.join(str(appdata), "Vox", "*.tmp"))


def test_the_lock_is_reentrant_in_one_thread_and_waits_in_another(appdata):
    path = core.config_path()
    got = []
    with core.file_lock(path):
        with core.file_lock(path):   # nested: no wait for itself
            pass
        t = threading.Thread(target=lambda: got.append(_try_lock(path)))
        t.start()
        t.join()
    assert got == ["busy"]
    assert _try_lock(path) == "free"


def _try_lock(path):
    try:
        with core.file_lock(path, timeout=0.2):
            return "free"
    except OSError:
        return "busy"


# ---- history: the engine appends while the window deletes (DAT-2) ----------------------------------------------------

def test_history_rewrites_and_appends_from_threads_lose_no_entry(appdata):
    for t in range(200):
        core.add_history({"t": t})
    stop = threading.Event()

    def engine():
        n = 1000
        while not stop.is_set():
            core.add_history({"t": n})
            n += 1
        added.append(n - 1000)

    added = []
    th = threading.Thread(target=engine)
    th.start()
    try:
        for t in range(0, 40):
            core.update_history(lambda entries, t=t: [e for e in entries if e["t"] != t])
    finally:
        stop.set()
        th.join()
    kept = [e["t"] for e in core.read_history()]
    assert [t for t in kept if t >= 1000] == list(range(1000, 1000 + added[0]))
    assert [t for t in kept if t < 1000] == list(range(40, 200))
