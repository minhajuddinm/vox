"""Learn from my corrections on Windows: for up to 3 minutes after Vox pastes, or until the text is sent, look at the text
of the focused control and learn the words the user fixes (rules: autolearn.py).

The engine calls arm() right after a successful paste. A daemon thread then polls every POLL_S seconds, only while the
watch is armed and only while the foreground window is the one Vox pasted into; it stops when the watch ends. The lock is
never held while the desktop is asked (a slow app can take seconds to answer UI Automation): poll_once() notes what it needs
under the lock, reads without it, and uses the answer only if the watch is still the same one. The text is
read through Windows UI Automation (the .NET UIAutomationClient, through pythonnet, which pywebview already brings), behind
the small Provider interface so tests use a fake. Password controls are never read. A control that exposes no text
(terminals, many Electron apps) ends the watch quietly; this is logged once. Nothing here logs text: only counts.
Only .NET methods reach into the other app (ENG-2): pythonnet holds the GIL while a property getter runs, so a slow app
answering a property would freeze all of Vox (the keyboard hook, the audio callback, the pill). Nothing is read while
Vox records (`quiet`).
"""
import importlib.util
import logging
import sys
import threading
import time

import autolearn
import vox_core as core

log = logging.getLogger("vox.learn")
POLL_S = 2.0


class Provider:
    """What the watcher needs from the desktop."""

    def foreground(self):
        """An id of the foreground window (anything comparable), or None."""
        raise NotImplementedError

    def focused_text(self):
        """(the whole text of the focused control or None when it exposes none, True when it is a password control).
        A password control's text is never read."""
        raise NotImplementedError


class UiaProvider(Provider):
    """Windows UI Automation through the .NET UIAutomationClient. Loaded on the first read, in the watcher's thread."""
    ASSEMBLIES = ("UIAutomationClient", "UIAutomationTypes")

    def __init__(self):
        self._uia = None

    def foreground(self):
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow() or None

    def _load(self):
        if self._uia is None:
            import clr   # pythonnet
            for name in self.ASSEMBLIES:
                clr.AddReference(name + ", Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")
            from System.Windows.Automation import AutomationElement, TextPattern, ValuePattern
            self._uia = (AutomationElement, TextPattern, ValuePattern, clr.GetClrType)
        return self._uia

    def focused_text(self):
        """Methods only: GetCurrentPropertyValue and GetCurrentPattern, and reflection (PropertyInfo.GetValue, a method)
        for the two properties that have no method (FocusedElement, DocumentRange). A .NET method call releases the GIL,
        a property getter does not (ENG-2)."""
        element_cls, text_pattern, value_pattern, clr_type = self._load()
        el = clr_type(element_cls).GetProperty("FocusedElement").GetValue(None, None)
        if el is None:
            return None, False
        if el.GetCurrentPropertyValue(element_cls.IsPasswordProperty):
            return None, True
        text = None
        try:   # TextPattern: Word, browsers' text areas, rich edit controls
            pat = el.GetCurrentPattern(text_pattern.Pattern)
            pat = getattr(pat, "__implementation__", pat)
            text = pat.GetType().GetProperty("DocumentRange").GetValue(pat, None).GetText(autolearn.MAX_TEXT + 1)
        except Exception:
            try:   # ValuePattern: single-line edits, many simple fields
                el.GetCurrentPattern(value_pattern.Pattern)   # raises when the control has none
                text = el.GetCurrentPropertyValue(value_pattern.ValueProperty)
            except Exception:
                text = None
        return (None if text is None else str(text)), False


def make_provider():
    """The UI Automation provider, or None where it cannot work (not Windows, or pythonnet is missing)."""
    if sys.platform != "win32" or importlib.util.find_spec("clr") is None:
        return None
    return UiaProvider()


class Watcher:
    """One autolearn.Watch fed from a Provider by a thread that runs only while the watch is armed."""

    def __init__(self, provider, notify=None, clock=time.monotonic, poll=POLL_S):
        self.provider, self.notify, self.poll = provider, notify, poll
        self.watch = autolearn.Watch(clock=clock)
        self.lock = threading.Lock()
        self._gen = 0   # changes whenever a watch is armed or ended: tells a read that started earlier its watch is gone
        self._wake = threading.Event()
        self._thread = None
        self._told_no_text = False
        self.quiet = lambda: False   # True while Vox records: nothing is read then (set by arm)

    def arm(self, text, start_thread=True):
        """Vox just pasted `text` into the foreground window: watch it (a running watch ends first and is learned from)."""
        app = self.provider.foreground()   # asked before the lock is taken
        with self.lock:
            self._gen += 1
            pairs = self.watch.arm(app, text)
            if start_thread and self.watch.armed and self._thread is None:
                self._wake.clear()
                self._thread = threading.Thread(target=self._run, name="vox-autolearn", daemon=True)
                self._thread.start()
        self._learn(pairs)

    def end(self):
        with self.lock:
            self._gen += 1
            pairs = self.watch.end()
        self._wake.set()
        self._learn(pairs)

    def poll_once(self):
        """One look at the desktop. Reads the focused control only while the pasted-into window is in front. The desktop
        is asked without the lock; if the watch ended or a new one was armed meanwhile, the answer is dropped. Skipped
        while Vox records (`quiet`): a slow answer must not cost the recording its audio."""
        if self.quiet():
            return
        with self.lock:
            if not self.watch.armed:
                return
            gen, target = self._gen, self.watch.app
        app = self.provider.foreground()
        text, password = None, False
        if app == target:
            try:
                text, password = self.provider.focused_text()
            except Exception:
                with self.lock:
                    stale = gen != self._gen
                if stale:
                    return   # the failure belongs to a watch that is gone; the new one is not to blame
                raise
        with self.lock:
            if gen != self._gen or not self.watch.armed:
                return
            if app == target and text is None and not password and not self._told_no_text:
                self._told_no_text = True
                log.info("auto-learn: control exposes no text")
            pairs = self.watch.observe(app, text)
        self._learn(pairs)

    def _run(self):
        while True:
            self._wake.wait(self.poll)
            self._wake.clear()
            try:
                self.poll_once()
            except Exception as e:   # never the text: only what went wrong
                log.warning("auto-learn: could not read the control (%s)", type(e).__name__)
                self.end()
            with self.lock:
                if not self.watch.armed:
                    self._thread = None
                    return

    def _learn(self, pairs):
        """Adds new corrections to the dictionary and says so. Counts only in the log."""
        if not pairs:
            return
        def learn(cfg):   # the settings as they are on disk now, in one locked step with the save
            if not autolearn.enabled(cfg):
                return []
            parts, added = autolearn.apply_learned(cfg, pairs)
            if added:
                cfg.update(parts)
            return added
        try:
            added = core.update_config(learn)
            if not added:
                return
        except Exception as e:
            log.warning("auto-learn: could not save what was learned (%s)", type(e).__name__)
            return
        log.info("auto-learn: learned %d corrections", len(added))
        if self.notify:
            try:
                self.notify("Learned: " + "; ".join("%s -> %s" % (w, r) for w, r in added), private=True)
            except Exception:
                pass


_watcher = None
_tried = False
_lock = threading.Lock()


def _get():
    global _watcher, _tried
    with _lock:
        if _watcher is None and not _tried:
            _tried = True
            provider = make_provider()
            if provider is None:
                log.info("auto-learn: not available on this system")
            else:
                _watcher = Watcher(provider)
        return _watcher


def arm(text, cfg, notify=None, quiet=None):
    """The engine pasted `text`: watch for the user's fixes (setting auto_learn). `quiet()` is True while Vox records
    (nothing is read then). Never raises."""
    try:
        w = _get()
        if w is None:
            return
        if not autolearn.enabled(cfg):
            w.end()
            return
        w.notify = notify
        if quiet is not None:
            w.quiet = quiet
        w.arm(text)
    except Exception:
        log.exception("auto-learn could not start")
