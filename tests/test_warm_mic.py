"""The warm microphone's pure parts (windows/warm_mic.py): the ring buffer of the last 400 ms and the stream keeper, with a
fake stream. No sounddevice, no real microphone."""
import threading

import pytest

import warm_mic
from warm_mic import NotAvailable, RingBuffer, WarmMic

RATE = warm_mic.SAMPLE_RATE


def pcm(n_samples, value=1):
    return int(value).to_bytes(2, "little", signed=True) * n_samples


# ------------------------------------------------------------------- RingBuffer
def test_the_ring_holds_the_last_400_ms_and_nothing_older():
    ring = RingBuffer()
    assert ring.max_bytes == int(0.4 * RATE) * 2 == 12800
    ring.append(pcm(5000, 1))
    ring.append(pcm(5000, 2))          # 10 000 samples = 20 000 bytes: only the newest 6400 samples stay
    held = ring.take()
    assert len(held) == 12800
    assert held == pcm(1400, 1) + pcm(5000, 2)      # oldest first, the old part cut off


def test_the_ring_keeps_whole_samples_when_blocks_are_odd_sizes():
    ring = RingBuffer()
    for _ in range(50):
        ring.append(pcm(333, 7))
    held = ring.take()
    assert len(held) % 2 == 0 and len(held) == 12800 and held == pcm(6400, 7)


def test_a_block_bigger_than_the_ring_keeps_only_its_end():
    ring = RingBuffer()
    ring.append(pcm(100, 1) + pcm(20000, 2))
    assert ring.take() == pcm(6400, 2)


def test_taking_empties_the_ring_and_a_small_ring_grows_to_its_limit():
    ring = RingBuffer()
    assert ring.take() == b"" and len(ring) == 0
    ring.append(pcm(100))
    assert len(ring) == 200
    assert ring.take() == pcm(100) and len(ring) == 0 and ring.take() == b""


def test_clear_drops_the_audio():
    ring = RingBuffer()
    ring.append(pcm(100))
    ring.clear()
    assert ring.take() == b""


def test_the_ring_size_follows_its_arguments():
    assert RingBuffer(seconds=1.0, rate=8000, width=2).max_bytes == 16000


# ----------------------------------------------------------------------- WarmMic
class FakeStream:
    def __init__(self, callback):
        self.callback = callback
        self.active = True
        self.stopped = self.closed = False

    def stop(self):
        self.stopped = True
        self.active = False

    def close(self):
        self.closed = True

    def push(self, data, frames=None):
        self.callback(data, frames or len(data) // 2, None, None)


class Opener:
    """open_stream for WarmMic: records the streams it made; `fail` is raised instead when set."""

    def __init__(self):
        self.streams, self.fail = [], None

    def __call__(self, callback):
        if self.fail:
            raise self.fail
        s = FakeStream(callback)
        self.streams.append(s)
        return s


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def wm():
    opener, clock, said = Opener(), Clock(), []
    m = WarmMic(opener, notify=lambda msg, private=False: said.append(msg), clock=clock)
    m.opener, m.clock, m.said = opener, clock, said
    return m


def test_nothing_is_open_until_ensure_and_ensure_opens_one_stream_once(wm):
    assert not wm.is_open and wm.opener.streams == []
    assert wm.ensure("USB Mic") is True and wm.is_open
    assert wm.ensure("USB Mic") is True
    assert len(wm.opener.streams) == 1


def test_while_idle_audio_goes_only_into_the_ring(wm):
    wm.ensure("")
    s = wm.opener.streams[0]
    for _ in range(100):
        s.push(pcm(160, 5))              # 16 000 samples in 10 ms blocks
    assert len(wm.ring) == 12800         # only the last 400 ms, nothing more is kept anywhere


def test_attach_hands_over_the_last_400_ms_first_and_then_every_block_in_order(wm):
    wm.ensure("")
    s = wm.opener.streams[0]
    s.push(pcm(8000, 1))
    s.push(pcm(160, 2))
    got = []
    assert wm.attach(lambda indata, frames, t, status: got.append(("block", bytes(indata))),
                     prime=lambda held: got.append(("pre", held))) is True
    s.push(pcm(160, 3))
    s.push(pcm(160, 4))
    assert got[0] == ("pre", pcm(6240, 1) + pcm(160, 2))      # 6400 samples: the end of what came before
    assert got[1:] == [("block", pcm(160, 3)), ("block", pcm(160, 4))]
    assert len(wm.ring) == 0             # while a recording has the audio, the ring is not filled


def test_attach_with_nothing_held_does_not_call_prime(wm):
    wm.ensure("")
    primed = []
    assert wm.attach(lambda *a: None, prime=primed.append)
    assert primed == []


def test_detach_sends_the_audio_back_to_the_ring_and_keeps_the_stream_open(wm):
    wm.ensure("")
    s = wm.opener.streams[0]
    got = []
    wm.attach(lambda indata, *a: got.append(bytes(indata)))
    s.push(pcm(160, 1))
    assert wm.detach() is True
    s.push(pcm(160, 2))
    assert got == [pcm(160, 1)]                           # nothing reaches the recording after detach
    assert len(wm.ring) == 320 and wm.is_open and not s.closed
    assert wm.detach() is False                           # no recording had it


def test_attach_needs_a_live_stream(wm):
    assert wm.attach(lambda *a: None) is False            # never opened
    wm.ensure("")
    wm.opener.streams[0].active = False                   # the microphone was unplugged
    assert wm.attach(lambda *a: None) is False
    assert not wm.is_open and wm.opener.streams[0].closed  # the dead stream was closed


def test_a_sink_that_raises_does_not_stop_the_stream(wm):
    wm.ensure("")
    s = wm.opener.streams[0]

    def bad(*a):
        raise RuntimeError("boom")
    wm.attach(bad)
    s.push(pcm(160))                                      # must not raise out of the audio callback
    s.push(pcm(160))
    assert s.active and wm.detach() is False              # the sink was dropped
    assert len(wm.ring) == 320                            # and the audio goes back to the ring


def test_a_changed_device_reopens_the_stream(wm):
    wm.ensure("Mic A")
    wm.ensure("Mic B")
    first, second = wm.opener.streams
    assert first.stopped and first.closed and not second.closed
    assert wm.ensure("Mic B") is True and len(wm.opener.streams) == 2


def test_a_dead_stream_is_reopened_by_ensure(wm):
    wm.ensure("")
    wm.opener.streams[0].active = False
    assert wm.ensure("") is True
    assert len(wm.opener.streams) == 2 and wm.opener.streams[0].closed


def test_close_stops_the_stream_and_drops_the_audio(wm):
    wm.ensure("")
    wm.opener.streams[0].push(pcm(160))
    assert wm.close() is True
    s = wm.opener.streams[0]
    assert s.stopped and s.closed and not wm.is_open and len(wm.ring) == 0
    assert wm.close() is False
    assert wm.attach(lambda *a: None) is False


def test_closing_while_a_recording_has_the_audio_ends_its_feed(wm):
    wm.ensure("")
    got = []
    wm.attach(lambda indata, *a: got.append(bytes(indata)))
    s = wm.opener.streams[0]
    wm.close()
    s.push(pcm(160))
    assert got == []


def test_a_failed_open_says_so_once_and_retries_after_a_while(wm):
    wm.opener.fail = OSError("no device")
    assert wm.ensure("") is False
    assert len(wm.said) == 1 and "no device" in wm.said[0]
    wm.clock.t += 5
    assert wm.ensure("") is False                         # too soon: not tried again
    wm.clock.t += warm_mic.RETRY_SECONDS
    assert wm.ensure("") is False
    assert len(wm.said) == 1                              # still one notification
    wm.opener.fail = None
    wm.clock.t += warm_mic.RETRY_SECONDS
    assert wm.ensure("") is True and wm.is_open
    wm.opener.fail = OSError("gone again")
    wm.opener.streams[0].active = False
    wm.clock.t += warm_mic.RETRY_SECONDS
    assert wm.ensure("") is False
    assert len(wm.said) == 2                              # a new failure after a success tells again


def test_a_microphone_that_is_not_there_is_not_a_notification(wm):
    wm.opener.fail = NotAvailable()
    assert wm.ensure("USB Mic") is False
    assert wm.said == [] and not wm.is_open


def test_attach_does_not_wait_while_the_stream_is_being_opened():
    started, release = threading.Event(), threading.Event()

    def slow_open(callback):
        started.set()
        release.wait(5)
        return FakeStream(callback)
    m = WarmMic(slow_open)
    t = threading.Thread(target=lambda: m.ensure(""), daemon=True)
    t.start()
    assert started.wait(5)
    assert m.attach(lambda *a: None) is False             # the caller opens its own stream instead
    release.set()
    t.join(5)
    assert m.is_open


# ------------------------------------------------------------------ the Settings row
def test_the_settings_page_has_the_warm_mic_switch_wired_to_the_setting():
    import os
    with open(os.path.join(os.path.dirname(__file__), "..", "windows", "ui", "index.html"), encoding="utf-8") as f:
        page = f.read()
    assert 'id="warm-mic"' in page
    assert "Keep the microphone ready" in page and "microphone-in-use icon all the time" in page
    assert 'save({ warm_mic: e.target.checked })' in page
    assert '$("warm-mic").checked = !!c.warm_mic;' in page      # off unless it was turned on
    import vox_core
    assert vox_core.DEFAULT_CONFIG["warm_mic"] is False
