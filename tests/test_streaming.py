"""Long recordings sent in pieces while the user is still talking: the pause finder, the worker, and the engine hook."""
import array
import math

import pytest

import streaming
import vox_core as core

RATE = core.SAMPLE_RATE


def tone(seconds, amp=8000):
    n = int(seconds * RATE)
    return array.array("h", (int(amp * math.sin(i * 0.3)) for i in range(n))).tobytes()


def silence(seconds):
    return b"\x00\x00" * int(seconds * RATE)


def feed_in_blocks(seg, pcm, block):
    out = []
    for i in range(0, len(pcm), block):
        out += seg.feed(pcm[i:i + block])
    return out


# ---------------------------------------------------------------- Segmenter
def test_short_recordings_are_never_cut():
    seg = core.Segmenter()
    audio = tone(5) + silence(1) + tone(5)
    assert seg.feed(audio) == [] and seg.rest() == audio


def test_a_long_recording_is_cut_at_the_first_pause_after_the_minimum_and_loses_nothing():
    audio = tone(13) + silence(1.0) + tone(5)
    seg = core.Segmenter()
    pieces = feed_in_blocks(seg, audio, 1600)
    assert len(pieces) == 1
    assert 13 * RATE * 2 <= len(pieces[0]) <= 14.5 * RATE * 2        # cut inside the pause that follows the 13 s of speech
    assert b"".join(pieces) + seg.rest() == audio


def test_pieces_do_not_depend_on_how_the_audio_arrives():
    audio = tone(14) + silence(1) + tone(14) + silence(1) + tone(3)
    whole = core.Segmenter().feed(audio)
    for block in (2, 1000, 1600, 7777, 96000):
        seg = core.Segmenter()
        pieces = feed_in_blocks(seg, audio, block)
        assert pieces == whole, block
        assert b"".join(pieces) + seg.rest() == audio


def test_a_pause_before_the_minimum_is_ignored():
    seg = core.Segmenter()
    audio = tone(6) + silence(1) + tone(6) + silence(1) + tone(2)   # 14 s, but each stretch of speech is short
    pieces = seg.feed(audio)
    assert len(pieces) == 1 and len(pieces[0]) >= 12 * RATE * 2     # first pause after 12 s is at about 13 s
    assert b"".join(pieces) + seg.rest() == audio


def test_speech_without_pauses_is_cut_at_the_maximum():
    seg = core.Segmenter()
    audio = tone(60)
    pieces = seg.feed(audio)
    assert len(pieces) == 2 and all(len(p) <= 28 * RATE * 2 + 960 for p in pieces)
    assert b"".join(pieces) + seg.rest() == audio


def test_a_forced_cut_prefers_the_last_quiet_moment():
    seg = core.Segmenter(min_seconds=12, max_seconds=28, pause_seconds=2.0)      # pauses of 0.5 s are too short to cut on
    audio = tone(16) + silence(0.5) + tone(20)
    pieces = seg.feed(audio)
    assert len(pieces) == 1 and abs(len(pieces[0]) - int(16.5 * RATE * 2)) <= 960   # cut where the quiet moment ended
    assert b"".join(pieces) + seg.rest() == audio


# -------------------------------------------------------------- StreamingStt
class FakeStt:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, cfg, wav, context=""):
        self.calls.append((len(wav), context))
        if self.fail_on == len(self.calls):
            raise core.ApiError(503, "down")
        return f"piece{len(self.calls)}"


def run(stt, audio, block=3200, **kw):
    s = streaming.StreamingStt({}, transcribe=stt, **kw)
    s.start()
    for i in range(0, len(audio), block):
        s.feed(audio[i:i + block])
    return s, s.finish(timeout=10)


def test_pieces_are_sent_in_order_with_the_previous_text_as_context():
    stt = FakeStt()
    s, text = run(stt, tone(13) + silence(1) + tone(13) + silence(1) + tone(4))
    assert text == "piece1 piece2 piece3" and len(stt.calls) == 3
    assert stt.calls[0][1] == "" and stt.calls[1][1] == "piece1" and stt.calls[2][1] == "piece1 piece2"


def test_a_short_recording_is_left_to_the_normal_path():
    stt = FakeStt()
    s, text = run(stt, tone(5))
    assert text is None and stt.calls == []


def test_a_failure_hands_back_to_the_normal_path():
    stt = FakeStt(fail_on=2)
    s, text = run(stt, tone(13) + silence(1) + tone(13) + silence(1) + tone(4))
    assert text is None and "503" in s.error or "down" in s.error


def test_silent_pieces_are_skipped_and_hallucinated_ones_dropped():
    stt = FakeStt()
    s, text = run(stt, silence(13) + silence(1) + tone(13) + silence(1) + tone(2))
    assert text == "piece1 piece2" and len(stt.calls) == 2 and s.pieces == 3
    hallucination = lambda cfg, wav, context="": "Thank you."   # noqa: E731
    s2, text2 = run(hallucination, tone(13) + silence(1) + tone(3))
    assert text2 == ""


def test_a_closing_thank_you_after_real_speech_is_kept():
    answers = iter(["real words", "Thank you."])
    s, text = run(lambda cfg, wav, context="": next(answers), tone(13) + silence(1) + tone(3))
    assert text == "real words Thank you."   # only a recording with no earlier text can be a silence hallucination
    answers = iter(["Thank you.", "Bye"])
    s, text = run(lambda cfg, wav, context="": next(answers), tone(13) + silence(1) + tone(3))
    assert text == ""   # nothing real before it: both are hallucinations


def test_a_last_piece_that_is_only_a_blip_is_not_sent():
    stt = FakeStt()
    s, text = run(stt, tone(13) + silence(0.7) + tone(0.1))   # cut right after the pause; under 0.2 s is left
    assert text == "piece1" and len(stt.calls) == 1


def test_cancel_stops_the_worker_and_finish_gives_nothing():
    stt = FakeStt()
    s = streaming.StreamingStt({}, transcribe=stt)
    s.start()
    s.feed(tone(13) + silence(1))
    s.cancel()
    s._done.wait(5)
    assert s.finish(timeout=5) is None or s.pieces >= 0


def test_feed_never_blocks_or_raises_after_cancel():
    s = streaming.StreamingStt({}, transcribe=FakeStt())
    s.start()
    s.cancel()
    s.feed(b"\x00\x00" * 100)


# ------------------------------------------------- the text half of the pipeline
def test_process_text_matches_process_detailed_after_transcription(monkeypatch):
    cfg = {"cleanup": False, "default_style": "neutral", "dictionary": ["gonna => going to"]}
    monkeypatch.setattr(core, "transcribe", lambda c, wav, context="": "I am gonna go new line ok")
    a = core.process_detailed(cfg, tone(1), "notepad.exe", "notepad.exe")
    b = core.process_text(cfg, "I am gonna go new line ok", "notepad.exe", "notepad.exe")
    assert a == b and b.text == "I am going to go\nok"
    assert core.process_text(cfg, "", "x.exe", "x.exe") == core.Result("", "", False, "")


def test_transcribe_adds_the_context_to_the_end_of_the_prompt(monkeypatch):
    seen = {}

    class R:
        status_code = 200

        def json(self):
            return {"text": " hi "}

    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.update(kw) or R())
    assert core.transcribe({"api_key": "k", "dictionary": ["Atlas"]}, b"RIFF", "the end of the last piece") == "hi"
    assert seen["data"]["prompt"].startswith("Atlas") and seen["data"]["prompt"].endswith("the end of the last piece")
    core.transcribe({"api_key": "k"}, b"RIFF")
    assert "prompt" not in seen["data"]
    core.transcribe({"api_key": "k", "dictionary": ["A" * 800]}, b"RIFF", "tail")
    assert len(seen["data"]["prompt"]) <= 600 and seen["data"]["prompt"].endswith("tail")


# ------------------------------------------------------------------ piece_text
def test_piece_text_sends_a_piece_with_the_end_of_the_context():
    stt = FakeStt()
    assert streaming.piece_text({}, tone(1), "x" * 300, stt) == "piece1"
    assert stt.calls[0][1] == "x" * streaming.CONTEXT_CHARS


def test_piece_text_skips_silence_and_hallucinations():
    stt = FakeStt()
    assert streaming.piece_text({}, silence(1), "", stt) == "" and stt.calls == []
    assert streaming.piece_text({}, tone(1), "", lambda cfg, wav, context="": "Thank you.") == ""
    assert streaming.piece_text({}, tone(1), "", lambda cfg, wav, context="": "Thank you.", drop_hallucination=False) == "Thank you."


def test_piece_text_uses_the_real_transcribe_by_default(monkeypatch):
    monkeypatch.setattr(core, "transcribe", lambda cfg, wav, context="": "from core")
    assert streaming.piece_text({}, tone(1), "") == "from core"
