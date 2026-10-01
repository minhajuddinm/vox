"""Keep listening (the double-press mode): the pure session logic, with no audio hardware and no network.

`ListenSession` cuts the microphone audio into utterances at pauses (`vox_core.Segmenter`, short pieces for low
latency), numbers them, collects the text that comes back for each one in any order, and tells the engine when
the user said the stop phrase or the time limit is reached. The engine (task E2) owns the threads, the speech
calls and the pill. `SessionBuffer` appends the audio to a file so a crash does not lose a long session.
"""
import math
import os
import re
from collections import namedtuple
from datetime import datetime

import vox_core as core

TARGETS = ("note", "type")
WARN_SECONDS = 55 * 60       # the pill warns from here
MAX_SECONDS = 60 * 60        # the session ends itself here
_BYTES_PER_SECOND = core.SAMPLE_RATE * 2
_STOP = re.compile(r"[\s,]*\bstop,?\s+listening[\s.!?,;:]*$", re.I)

Segment = namedtuple("Segment", "id pcm para")   # para: a long pause came before this piece


def stop_requested(text):
    """True when the text ends with the stop phrase ("stop listening", any case, with or without punctuation)."""
    return bool(_STOP.search(text))


def same_target(window_at_start, window_now):
    """Typing is allowed only while the focused app is the one chosen at the start (lower-case exe names, as
    `paste.SystemDeps.foreground_exe` gives them). Unlike a paste, a keep-listening session types many times, so an
    unknown window on either side means refuse: the text is kept for the note instead."""
    return bool(window_at_start) and bool(window_now) and window_at_start.lower() == window_now.lower()


class ListenSession:
    """States: idle (before `start`, and after the end), listening, stopping (stop asked for or heard, not all
    audio flushed and answered yet). One session is used once."""

    def __init__(self, target, segmenter=None):
        if target not in TARGETS:
            raise ValueError("target must be one of %s" % (TARGETS,))
        self.target = target
        self.seg = segmenter or core.Segmenter(min_seconds=3.0, max_seconds=20.0)
        self.state = "idle"
        self._bytes = 0
        self._next_id = 0
        self._texts = {}       # segment id -> text ("" when nothing was said or the call failed)
        self._para = set()     # ids that follow a long pause
        self._gap = False
        self._released = 0     # ids below this were handed out by `on_text` already
        self._closed = False   # the rest of the audio was flushed or the limit was reached

    def start(self):
        if self.state == "idle":
            self.state = "listening"

    @property
    def seconds(self):
        return self._bytes / _BYTES_PER_SECOND

    @property
    def warning(self):
        """What the pill should say about the time limit ("" while there is plenty left)."""
        if self.seconds >= MAX_SECONDS:
            return "Time limit reached, listening stopped."
        if self.seconds >= WARN_SECONDS:
            return "Listening ends in %d min." % math.ceil((MAX_SECONDS - self.seconds) / 60)
        return ""

    def feed(self, pcm):
        """Audio from the microphone. Returns the finished utterances to send to speech-to-text. Pieces of pure
        silence are not returned; a long one marks a paragraph break before the next piece."""
        if self.state == "idle" or self._closed:
            return []
        self._bytes += len(pcm)
        out = self._take(self.seg.feed(pcm))
        if self._bytes >= MAX_SECONDS * _BYTES_PER_SECOND:
            out += self.stop()
        return out

    def stop(self):
        """Ends the session and returns the last piece, so no audio is lost. The state becomes idle once every
        piece sent has been answered with `on_text`."""
        if self.state == "idle" or self._closed:
            return []
        self._closed = True
        self.state = "stopping"
        out = self._take([self.seg.rest()])
        self._settle()
        return out

    def on_text(self, segment_id, text):
        """The text for a piece ("" when the piece had nothing or failed: every piece sent must be answered).
        Returns the non-empty texts that are now complete in order, which is what the Type target types."""
        m = _STOP.search(text)
        if m:
            text = text[:m.start()]
            if self.state == "listening":
                self.state = "stopping"
        self._texts[segment_id] = text.strip()
        out = []
        while self._released in self._texts:
            t = self._texts[self._released]
            self._released += 1
            if t:
                out.append(t)
        self._settle()
        return out

    def text(self):
        """Everything heard so far in spoken order: a space between pieces, a blank line after a long pause."""
        parts, brk = [], False
        for i in sorted(self._texts):
            brk = brk or i in self._para
            if self._texts[i]:
                if parts:
                    parts.append("\n\n" if brk else " ")
                parts.append(self._texts[i])
                brk = False
        return "".join(parts)

    def _take(self, pieces):
        out = []
        for pcm in pieces:
            if not pcm:
                continue
            if core.is_silent(pcm):
                self._gap = True
                continue
            if self._gap:
                self._para.add(self._next_id)
            out.append(Segment(self._next_id, pcm, self._gap))
            self._next_id += 1
            self._gap = False
        return out

    def _settle(self):
        if self.state == "stopping" and self._closed and len(self._texts) >= self._next_id:
            self.state = "idle"


# ------------------------------------------------------------ crash-safe buffer
_FILE = re.compile(r"^listen-(\d{8}-\d{6})-\d{6}-(note|type)\.pcm$")


def buffer_dir():
    return os.path.join(core.data_dir(), "listen")


class SessionBuffer:
    """The session's audio, appended to a file as it arrives (unbuffered, so a killed process loses nothing that was
    appended). `discard()` removes it after the session ended well; a file left behind is offered for recovery."""

    def __init__(self, folder, target):
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.path = os.path.join(folder, "listen-%s-%s.pcm" % (stamp, target))
        self._f = open(self.path, "ab", buffering=0)

    def append(self, pcm):
        self._f.write(pcm)

    def close(self):
        self._f.close()

    def discard(self):
        self.close()
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def recoverable(folder):
    """Sessions that left audio behind, newest first: {"path", "target", "started" (ISO text), "seconds"}."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = []
    for name in names:
        m = _FILE.match(name)
        path = os.path.join(folder, name)
        if not m or os.path.getsize(path) < 2:
            continue
        started = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").isoformat()
        found.append((os.path.getmtime(path), {"path": path, "target": m.group(2), "started": started,
                                               "seconds": os.path.getsize(path) // 2 * 2 / _BYTES_PER_SECOND}))
    return [f for _, f in sorted(found, key=lambda x: x[0], reverse=True)]


def load_pcm(path):
    """The audio of a left-behind session, without a half sample at the end if the crash cut a write."""
    with open(path, "rb") as f:
        data = f.read()
    return data[:len(data) // 2 * 2]
