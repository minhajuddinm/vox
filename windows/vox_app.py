"""Vox entry point.

  Vox.exe            start the background engine (tray, hotkey, overlay). If it is already running, open the window.
  Vox.exe --window   open the main window.
  Vox.exe --relay    run the relay server (no tray, no window); the options after it are relay.py's (--data-dir, --port, ...).
"""
import ctypes
import logging
import os
import sys
import threading

import vox_core as core


def _setup_logging(name):
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(os.path.join(core.data_dir(), name), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("vox")
    sys.excepthook = lambda *e: log.error("uncaught", exc_info=e)
    threading.excepthook = lambda a: log.error("thread crashed", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def _already_running(name):
    """Holds a named mutex for this process. True when another process already holds it."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    _already_running.handle = kernel32.CreateMutexW(None, False, name)
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _focus_existing_window():
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "Vox")
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


def _run_relay(argv):
    """Runs relay/relay.py's main with `argv` and nothing else: no GUI, audio or keyboard library is imported."""
    _setup_logging("relay.log")
    for name in ("stdout", "stderr"):   # a windowed exe has no console, so give the relay's print() somewhere to go
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    if not getattr(sys, "frozen", False):   # the frozen exe has the relay built in (build_app.bat, build.yml)
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "relay"))
    import relay
    return relay.main(argv)


def main():
    if "--relay" in sys.argv[1:]:
        args = sys.argv[1:]
        args.remove("--relay")
        sys.exit(_run_relay(args))

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    if "--window" in sys.argv:
        if _already_running("Local\\VoxWindow"):
            _focus_existing_window()
            return
        _setup_logging("window.log")
        import ui_app
        ui_app.main()
        return

    if _already_running("Local\\VoxEngine"):
        import engine
        engine.open_window()
        return
    _setup_logging("vox.log")
    import engine
    engine.Engine().run()


if __name__ == "__main__":
    main()
