"""The optional warm microphone (setting `warm_mic`, off by default, Windows only).

Normally the microphone is opened when the shortcut goes down, so the first 100 to 300 ms of speech that starts at the
same moment can be lost. With `warm_mic` on, the engine keeps one input stream open. While nobody is recording, its audio
goes only into a RingBuffer that holds the last RING_SECONDS (400 ms) and keeps nothing older: it is never written to disk,
never sent, and gone when it is overwritten, when a recording takes it, when the setting is turned off or when Vox quits.
At key-down `attach()` hands that audio and then every new block to the recording, and `detach()` at the end sends the
blocks back into the ring. Windows shows the microphone-in-use icon all the time while the stream is open.

This module knows nothing about sounddevice: the engine gives WarmMic a function that opens (and starts) a stream.
"""
import logging
import threading
import time

log = logging.getLogger("vox.warm")

RING_SECONDS = 0.4          # how much audio from before the key-down goes in front of a recording
RETRY_SECONDS = 30          # after a failed open, the next try is not before this
SAMPLE_RATE = 16000
SAMPLE_BYTES = 2            # 16-bit mono


class NotAvailable(Exception):
    """Raised by the open function when there is nothing to open yet (for example the chosen microphone is not
    connected): not an error worth a notification, the normal recording path says what is wrong."""


class RingBuffer:
    """The last `seconds` of 16-bit mono audio, as bytes. Thread safe."""

    def __init__(self, seconds=RING_SECONDS, rate=SAMPLE_RATE, width=SAMPLE_BYTES):
        self.max_bytes = int(seconds * rate) * width
        self._buf = bytearray()
        self._lock = threading.Lock()

    def append(self, data):
        with self._lock:
            self._buf += data
            extra = len(self._buf) - self.max_bytes
            if extra > 0:
                del self._buf[:extra]

    def take(self):
        """Returns everything held (oldest first) and empties the buffer."""
        with self._lock:
            out = bytes(self._buf)
            self._buf = bytearray()
            return out

    def clear(self):
        with self._lock:
            self._buf = bytearray()

    def __len__(self):
        with self._lock:
            return len(self._buf)


class WarmMic:
    """One input stream kept open. `open_stream(callback)` must open and start a stream whose audio callback is
    `callback(indata, frames, time, status)` and return it (an object with `stop()`, `close()` and, optionally,
    `active`); it may raise NotAvailable, or any other exception for a real failure (one notification, then retries)."""

    def __init__(self, open_stream, notify=None, clock=time.monotonic):
        self._open_stream, self._notify, self._clock = open_stream, notify, clock
        self.ring = RingBuffer()
        self._stream = None
        self._key = None
        self._sink = None
        self._failed_at = None
        self._told = False
        self._lock = threading.Lock()      # the sink and the ring: the audio thread against attach() and detach()
        self._op = threading.Lock()        # open, close and attach: one at a time

    # --------------------------------------------------------------- state
    @property
    def is_open(self):
        return self._stream is not None

    def _alive(self):
        return self._stream is not None and getattr(self._stream, "active", True)

    # ------------------------------------------------------ audio thread
    def _callback(self, indata, frames, t, status):
        with self._lock:
            sink = self._sink
            if sink is None:
                self.ring.append(bytes(indata))
                return
            try:   # inside the lock, so detach() returns only after the last block was handed over
                sink(indata, frames, t, status)
            except Exception as e:   # a failing sink must not abort the stream (PortAudio would stop it)
                self._sink = None
                log.warning("warm microphone: the recording callback failed (%s)", type(e).__name__)

    # ----------------------------------------------------- open and close
    def ensure(self, key):
        """Makes sure a stream for `key` (the chosen microphone) is open: opens it, reopens it when `key` changed or the
        stream died. True when it is open. After a failure the next try waits RETRY_SECONDS."""
        with self._op:
            if self._alive() and self._key == key:
                return True
            if self._stream is not None:
                self._close_locked()
            now = self._clock()
            if self._failed_at is not None and now - self._failed_at < RETRY_SECONDS:
                return False
            try:
                stream = self._open_stream(self._callback)
            except NotAvailable:
                self._failed_at = now
                return False
            except Exception as e:
                self._failed_at = now
                log.warning("warm microphone: could not open (%s)", type(e).__name__)
                if not self._told:
                    self._told = True
                    if self._notify:
                        self._notify("Vox could not keep the microphone ready, so it opens it when you press the "
                                     "shortcut instead (%s)." % (str(e) or type(e).__name__))
                return False
            self._stream, self._key, self._failed_at, self._told = stream, key, None, False
            log.info("warm microphone: open")
            return True

    def close(self):
        """Closes the stream and drops the audio held. True when a stream was open."""
        with self._op:
            return self._close_locked()

    def _close_locked(self):
        stream, self._stream, self._key = self._stream, None, None
        with self._lock:
            self._sink = None
            self.ring.clear()
        if stream is None:
            return False
        for step in ("stop", "close"):
            try:
                getattr(stream, step)()
            except Exception:
                pass
        log.info("warm microphone: closed")
        return True

    # ----------------------------------------------------- use by a recording
    def attach(self, sink, prime=None):
        """Hands the audio to `sink(indata, frames, time, status)` from now on. First, when something is held,
        `prime(held_bytes)` is called with the last RING_SECONDS, before any new block. False (nothing changed) when
        there is no live stream, or it is being opened or closed right now: the caller opens a stream itself."""
        if not self._op.acquire(blocking=False):
            return False
        try:
            if self._stream is None:
                return False
            if not self._alive():
                self._close_locked()   # the microphone went away: the engine reopens it later
                return False
            with self._lock:
                held = self.ring.take()
                if held and prime is not None:
                    try:
                        prime(held)
                    except Exception as e:
                        log.warning("warm microphone: could not add the audio before the key (%s)", type(e).__name__)
                self._sink = sink
            return True
        finally:
            self._op.release()

    def detach(self):
        """Ends a recording's use of the stream (the stream stays open). True when a recording had it."""
        with self._lock:
            had, self._sink = self._sink is not None, None
        return had
