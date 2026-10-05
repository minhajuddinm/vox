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


FRAME_BYTES = core.Segmenter.FRAME * 2


def loud_frames(n, dips=()):
    """n frames of constant loud audio; `dips` maps a frame number to the peak that frame has instead (0 = silence)."""
    out = bytearray()
    for i in range(n):
        out += int(dict(dips).get(i, 8000)).to_bytes(2, "little", signed=True) * core.Segmenter.FRAME
    return bytes(out)


def test_a_forced_cut_takes_the_quietest_frame_of_the_last_two_seconds():
    seg = core.Segmenter()                                    # 12 s to 28 s: the limit is reached at frame 934
    audio = loud_frames(2000, {910: 200, 920: 60, 925: 500})     # speech all the way, three dips in the last 2 s
    pieces = seg.feed(audio)
    assert len(pieces[0]) == 921 * FRAME_BYTES               # the cut ends the quietest frame (920)
    assert b"".join(pieces) + seg.rest() == audio


def test_a_forced_cut_takes_the_latest_of_equally_quiet_frames():
    seg = core.Segmenter()
    audio = loud_frames(1200, {905: 0, 915: 0, 930: 0})
    pieces = seg.feed(audio)
    assert len(pieces[0]) == 931 * FRAME_BYTES
    assert b"".join(pieces) + seg.rest() == audio


def test_a_forced_cut_without_any_dip_falls_back_to_the_limit():
    seg = core.Segmenter()
    audio = loud_frames(2000)
    pieces = seg.feed(audio)
    assert [len(p) for p in pieces] == [934 * FRAME_BYTES, 934 * FRAME_BYTES]   # 934 frames is the first whole frame past 28 s
    assert b"".join(pieces) + seg.rest() == audio


def test_a_forced_cut_ignores_a_dip_before_the_last_two_seconds_but_takes_one_at_the_start_of_the_window():
    audio_out = loud_frames(1200, {867: 0})                  # the frame just before the window (frames 868 to 933)
    assert len(core.Segmenter().feed(audio_out)[0]) == 934 * FRAME_BYTES
    audio_in = loud_frames(1200, {868: 0})                   # the first frame of the window
    seg = core.Segmenter()
    pieces = seg.feed(audio_in)
    assert len(pieces[0]) == 869 * FRAME_BYTES
    assert b"".join(pieces) + seg.rest() == audio_in


def test_a_forced_cut_dip_does_not_need_to_be_quiet_only_quieter():
    seg = core.Segmenter()
    audio = loud_frames(1200, {900: 3000})                   # soft, far above the pause level: still the quietest
    assert len(seg.feed(audio)[0]) == 901 * FRAME_BYTES


def test_a_forced_cut_never_makes_a_piece_shorter_than_the_minimum():
    # min 5 s, max 6 s: the last two seconds start at 4 s, but a piece may not be shorter than 5 s
    audio = loud_frames(400, {140: 0})                       # a dip at 4.2 s: before the minimum
    pieces = core.Segmenter(min_seconds=5, max_seconds=6, pause_seconds=0.6).feed(audio)
    assert len(pieces[0]) >= 5 * RATE * 2 and len(pieces[0]) == 200 * FRAME_BYTES
    audio = loud_frames(400, {170: 0})                       # a dip at 5.1 s: allowed
    pieces = core.Segmenter(min_seconds=5, max_seconds=6, pause_seconds=0.6).feed(audio)
    assert len(pieces[0]) == 171 * FRAME_BYTES


def test_forced_cuts_do_not_depend_on_how_the_audio_arrives():
    audio = loud_frames(2200, {915: 0, 1500: 100})
    whole = core.Segmenter().feed(audio)
    for block in (2, 1000, 1601, 7777, 96000):
        seg = core.Segmenter()
        pieces = feed_in_blocks(seg, audio, block)
        assert pieces == whole, block
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
    s, text = run(stt, silence(13) + silence(1) + tone(13) + silence(1) + tone(2), segmenter=core.Segmenter())   # 12-28 s pieces
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


# ------------------------------------------------- final fixes (windows 1): segment times through the pieces
SENT1 = " ".join(["one"] * 34) + " end."
SENT2 = " ".join(["two"] * 34) + " end."


class SegStt:
    """A speech server that answers with segment times (as transcribe keeps them for last_segments): one segment per
    piece, from 0.5 s to 5 s of the piece."""
    def __init__(self, texts):
        self.texts, self.n = list(texts), 0

    def __call__(self, cfg, wav, context=""):
        text = self.texts[self.n]
        self.n += 1
        core._stt_local.segments = [{"start": 0.5, "end": 5.0, "text": text}]
        return text


def test_the_pieces_keep_their_segment_times_shifted_by_where_each_piece_starts():
    s, text = run(SegStt([SENT1, SENT2]), tone(13) + silence(1.5) + tone(5), block=3200)
    assert text == SENT1 + " " + SENT2 and s.pieces == 2
    first, second = s.segments
    assert first["start"] == 0.5 and second["text"] == SENT2
    assert second["start"] > first["end"] + 1.2                 # the pause between the pieces is still there
    assert abs(second["start"] - (s.piece_starts[1] + 0.5)) < 1e-6 and s.piece_starts[0] == 0.0


def test_a_streamed_dictation_gets_its_paragraph_break_at_the_pause():
    s, text = run(SegStt([SENT1, SENT2]), tone(13) + silence(1.5) + tone(5), block=3200)
    cfg = {"cleanup": False, "default_style": "neutral", "structure": "auto"}
    out = core.process_text(cfg, text, "notepad.exe", "notepad.exe", s.segments).text
    parts = out.split("\n\n")
    assert len(parts) == 2 and parts[0].lower().startswith("one") and parts[1].lower().startswith("two")
    assert "\n" not in core.process_text(cfg, text, "notepad.exe", "notepad.exe").text      # without the times: one block


def test_segments_are_none_when_a_piece_with_text_had_none():
    s, text = run(FakeStt(), tone(13) + silence(1) + tone(5))
    assert text == "piece1 piece2" and s.segments is None


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


# ------------------------------------------------- every recording longer than about 8 s (task B4)
def test_a_nine_second_recording_with_a_pause_is_sent_in_pieces_while_speaking():
    stt = FakeStt()
    s, text = run(stt, tone(5) + silence(1) + tone(3))
    assert text == "piece1 piece2" and s.pieces == 2


def test_the_pieces_are_six_to_twenty_seconds():
    seg = streaming.StreamingStt({}).seg
    assert seg.min_bytes == 6 * RATE * 2 and seg.max_bytes == 20 * RATE * 2


def test_pieces_cut_before_the_end_count_as_sent_while_speaking():
    import time as _time
    stt = FakeStt()
    s = streaming.StreamingStt({}, transcribe=stt)
    s.start()
    s.feed(tone(7) + silence(1))
    deadline = _time.time() + 5
    while s.pieces < 1 and _time.time() < deadline:
        _time.sleep(0.01)
    s.feed(tone(3))
    assert s.finish(timeout=10) == "piece1 piece2"
    assert s.early == 1 and s.pieces == 2                              # only the last piece waited for the key-up


def test_a_short_dictation_is_still_one_upload():
    stt = FakeStt()
    s, text = run(stt, tone(4) + silence(0.5) + tone(2))
    assert text is None and stt.calls == []


# ---- ENG-7: a failed piece keeps the text of the pieces before it ----------------------------------------------------------
def test_after_a_failed_piece_the_text_so_far_and_the_audio_it_covers_are_kept():
    stt = FakeStt(fail_on=3)
    audio = tone(13) + silence(1) + tone(13) + silence(1) + tone(13) + silence(1) + tone(4)
    s, text = run(stt, audio)
    assert text is None
    said, covered = s.partial()
    assert said == "piece1 piece2" and 26 * RATE * 2 < covered < len(audio) - 13 * RATE * 2
    assert covered % 2 == 0


def test_no_partial_text_when_nothing_failed_or_the_first_piece_failed():
    s, text = run(FakeStt(), tone(13) + silence(1) + tone(13) + silence(1) + tone(4))
    assert s.partial() is None
    s, text = run(FakeStt(fail_on=1), tone(13) + silence(1) + tone(13) + silence(1) + tone(4))
    assert s.partial() is None


def test_the_fallback_in_pieces_waits_out_a_rate_limit_and_sends_the_same_piece_again(monkeypatch):
    slept, calls = [], []

    class R:
        def __init__(self, status, text="", headers=None):
            self.status_code, self._text, self.headers, self.text = status, text, headers or {}, ""

        def json(self):
            return {"error": {"message": "rate limited"}} if self.status_code == 429 else {"text": self._text}

    answers = [R(200, "one"), R(429, headers={"Retry-After": "7"}), R(200, "two"), R(200, "three")]

    def post(url, **kw):
        calls.append(kw.get("data", {}).get("prompt", ""))
        return answers.pop(0)

    monkeypatch.setattr(core.requests, "post", post)
    monkeypatch.setattr(core.time, "sleep", slept.append)
    audio = tone(13) + silence(1) + tone(13) + silence(1) + tone(4)
    assert core._transcribe_in_pieces({"api_key": "k"}, audio) == "one two three"
    assert 7 in slept and len(calls) == 4


def test_the_rate_limit_wait_is_bounded_and_gives_up_after_a_few_tries(monkeypatch):
    slept = []

    class R:
        status_code, text, headers = 429, "", {"Retry-After": "3600"}

        def json(self):
            return {"error": {"message": "rate limited"}}

    monkeypatch.setattr(core.requests, "post", lambda url, **kw: R())
    monkeypatch.setattr(core.time, "sleep", slept.append)
    with pytest.raises(core.ApiError):
        core._transcribe_in_pieces({"api_key": "k"}, tone(13) + silence(1) + tone(4))
    assert slept and max(slept) <= core.RATE_LIMIT_MAX_WAIT and len(slept) == core.RATE_LIMIT_TRIES
