"""Transcribes a long recording piece by piece while it is still being recorded.

The engine feeds audio to `StreamingStt.feed` from the microphone callback (a queue put, nothing slow). A worker
thread cuts the audio at pauses (`vox_core.Segmenter`, pieces of MIN_SECONDS to MAX_SECONDS), sends each finished
piece to speech-to-text with the end of the previous text as context, and keeps the texts in order. When the user
finishes, only the last piece is left to send, so any recording longer than about 7 s with a pause in it is ready
sooner. If anything goes wrong, or the recording was too short to be cut,
`finish()` returns None and the engine transcribes the whole recording as before: streaming is only a shortcut. When a
piece failed, `partial()` gives the text of the pieces before it, and the engine sends only the rest (ENG-7).
"""
import queue
import threading

import vox_core as core

MIN_TAIL_SECONDS = 0.3     # a last piece shorter than this is not sent
CONTEXT_CHARS = 150        # how much of the previous text goes into the next request
MIN_SECONDS = 6.0          # a piece is cut at the first pause after this much audio (was 12 s: only long dictations)
MAX_SECONDS = 20.0         # and at the latest here


def piece_text(cfg, pcm, context, transcribe=None, drop_hallucination=True):
    """The text of one piece of audio, with the end of the text before it as context; "" for a piece of pure silence
    or a silence hallucination (unless drop_hallucination is off: after real speech a lone "Thank you." is real text).
    Used by StreamingStt and by the keep-listening session."""
    if core.is_silent(pcm):
        return ""   # a piece of pure silence has nothing to say
    text = (transcribe or core.transcribe)(cfg, core.upload_audio(cfg, pcm), context[-CONTEXT_CHARS:])
    return "" if not text or (drop_hallucination and core.is_silence_hallucination(text)) else text


class StreamingStt:
    def __init__(self, cfg, transcribe=None, segmenter=None):
        self.cfg = cfg
        self._transcribe = transcribe or core.transcribe
        self.seg = segmenter or core.Segmenter(min_seconds=MIN_SECONDS, max_seconds=MAX_SECONDS)
        self.texts = []
        self.piece_starts = []     # seconds into the recording where each piece sent starts
        self._segments = []        # the speech server's segment times of the pieces with text, shifted to the whole recording
        self._segments_ok = True   # False once a piece with text came back without segment times
        self._sent_seconds = 0.0
        self.pieces = 0
        self.early = 0             # pieces sent before finish() was called: while the user was still speaking
        self._finishing = False
        self.error = ""
        self.done_bytes = 0        # audio covered by the pieces whose text came back (silent ones too)
        self._piece_failed = False
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
        self._finishing = True
        self._q.put(None)
        if not self._done.wait(timeout):
            self._cancelled = True
            self.error = "timed out"
        if self.error or self.pieces == 0:
            return None
        return " ".join(t for t in self.texts if t).strip()

    def partial(self):
        """After a piece failed: (the text of the pieces before it, how many bytes at the start of the recording they
        cover), so the caller sends only the rest (ENG-7); None when nothing failed or no piece came back."""
        if not self._piece_failed or not self.done_bytes:
            return None
        return " ".join(t for t in self.texts if t).strip(), self.done_bytes

    @property
    def segments(self):
        """The segment times ({"start", "end", "text"}, seconds into the whole recording) of every piece with text, for
        the paragraph breaks at long pauses (vox_core.process_text); None when a piece came back without them."""
        return list(self._segments) if self._segments_ok and self._segments else None

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
                    self._send(rest, last=True)
        except Exception as e:   # includes ApiError and network errors: the caller falls back to the whole recording
            self.error = str(e) or type(e).__name__
            self._piece_failed = True
        finally:
            self._done.set()

    def _send(self, pcm, last=False):
        """Sends one piece. The first piece loses its silent start and the last one its silent end (vox_core.trim_edges);
        the pauses at the cuts in between stay. The times and byte counts are those of the untrimmed piece."""
        first = self.pieces == 0
        self.pieces += 1
        if not self._finishing:
            self.early += 1
        start = self._sent_seconds
        self.piece_starts.append(start)
        self._sent_seconds += len(pcm) / (core.SAMPLE_RATE * 2)
        size = len(pcm)
        head = 0.0
        if first or last:
            pcm, head = core.trim_edges(pcm, lead=first, tail=last)
        core._stt_local.segments = None   # this thread's last answer: a silent piece is not sent at all
        text = piece_text(self.cfg, pcm, " ".join(self.texts), self._transcribe, drop_hallucination=not self.texts)
        self.done_bytes += size
        if text:
            self.texts.append(text)
            segs = core.last_segments()   # transcribe ran on this worker thread
            if segs is None:
                self._segments_ok = False
            else:
                self._segments += core.shift_segments(segs, start + head)
