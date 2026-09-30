"""Transcribes a long recording piece by piece while it is still being recorded.

The engine feeds audio to `StreamingStt.feed` from the microphone callback (a queue put, nothing slow). A worker
thread cuts the audio at pauses (`vox_core.Segmenter`), sends each finished piece to speech-to-text with the end of
the previous text as context, and keeps the texts in order. When the user finishes, only the last piece is left to
send, so a long dictation is ready sooner. If anything goes wrong, or the recording was too short to be cut,
`finish()` returns None and the engine transcribes the whole recording as before: streaming is only a shortcut.
"""
import queue
import threading

import vox_core as core

MIN_TAIL_SECONDS = 0.3     # a last piece shorter than this is not sent
CONTEXT_CHARS = 150        # how much of the previous text goes into the next request


class StreamingStt:
    def __init__(self, cfg, transcribe=None, segmenter=None):
        self.cfg = cfg
        self._transcribe = transcribe or core.transcribe
        self.seg = segmenter or core.Segmenter()
        self.texts = []
        self.pieces = 0
        self.error = ""
        self._q = queue.Queue()
        self._done = threading.Event()
        self._cancelled = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="vox-stream", daemon=True)
        self._thread.start()

    def feed(self, pcm):
        """Called from the audio callback: only queues the bytes."""
        if not self._cancelled:
            self._q.put(bytes(pcm))

    def cancel(self):
        self._cancelled = True
        self._q.put(None)

    def finish(self, timeout=120):
        """The text of the whole recording, or None when the caller should transcribe the whole audio itself
        (nothing was cut, something failed, or it took too long)."""
        self._q.put(None)
        if not self._done.wait(timeout):
            self._cancelled = True
            self.error = "timed out"
        if self.error or self.pieces == 0:
            return None
        return " ".join(t for t in self.texts if t).strip()

    def _run(self):
        try:
            while True:
                data = self._q.get()
                if data is None or self._cancelled:
                    break
                for piece in self.seg.feed(data):
                    self._send(piece)
            if not self._cancelled:
                rest = self.seg.rest()
                if self.pieces and len(rest) >= core.SAMPLE_RATE * 2 * MIN_TAIL_SECONDS:
                    self._send(rest)
        except Exception as e:   # includes ApiError and network errors: the caller falls back to the whole recording
            self.error = str(e) or type(e).__name__
        finally:
            self._done.set()

    def _send(self, pcm):
        self.pieces += 1
        if core.is_silent(pcm):
            return   # a piece of pure silence has nothing to say
        text = self._transcribe(self.cfg, core.pcm_to_wav(pcm), " ".join(self.texts)[-CONTEXT_CHARS:])
        if text and not core.is_silence_hallucination(text):
            self.texts.append(text)
