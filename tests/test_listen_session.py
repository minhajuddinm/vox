"""Keep listening (branch E, task E1): the pure session state machine, the same-window rule and the crash-safe
audio buffer. No audio hardware, no network."""
import array
import math
import os

import pytest

import session
import vox_core as core

RATE = core.SAMPLE_RATE


def tone(seconds, amp=8000):
    n = int(seconds * RATE)
    return array.array("h", (int(amp * math.sin(i * 0.3)) for i in range(n))).tobytes()


def silence(seconds):
    return b"\x00\x00" * int(seconds * RATE)


def feed_blocks(s, pcm, block=1600):
    out = []
    for i in range(0, len(pcm), block):
        out += s.feed(pcm[i:i + block])
    return out


def started(target="note", **kw):
    s = session.ListenSession(target, **kw)
    s.start()
    return s


# ------------------------------------------------------------------ states
def test_a_new_session_is_idle_and_ignores_audio_until_started():
    s = session.ListenSession("note")
    assert s.state == "idle" and s.feed(tone(5)) == []
    s.start()
    assert s.state == "listening"


def test_only_note_and_type_are_targets():
    with pytest.raises(ValueError):
        session.ListenSession("chat")


# ------------------------------------------------------------------ cutting
def test_utterances_are_cut_at_pauses_after_three_seconds():
    s = started()
    segs = feed_blocks(s, tone(4) + silence(1) + tone(4) + silence(1))
    assert [g.id for g in segs] == [0, 1]
    assert all(3 * RATE * 2 <= len(g.pcm) <= 5.5 * RATE * 2 for g in segs)


def test_speech_without_a_pause_is_cut_at_twenty_seconds():
    s = started()
    segs = feed_blocks(s, tone(25))
    assert len(segs) == 1 and len(segs[0].pcm) <= 20 * RATE * 2 + 960


def test_nothing_is_lost_however_the_audio_arrives_and_stop_flushes_the_rest():
    audio = tone(4) + silence(1) + tone(9) + silence(0.7) + tone(2)
    for block in (2, 1000, 1600, 7777, 200000):
        s = started()
        segs = feed_blocks(s, audio, block) + s.stop()
        assert b"".join(g.pcm for g in segs) == audio
        assert [g.id for g in segs] == list(range(len(segs)))


def test_stop_does_not_send_a_silent_rest():
    s = started()
    segs = feed_blocks(s, tone(4) + silence(1)) + feed_blocks(s, silence(2))
    assert s.stop() == [] and len(segs) == 1


def test_a_long_silence_between_utterances_becomes_a_paragraph_break():
    s = started()
    segs = feed_blocks(s, tone(4) + silence(0.8) + silence(4) + tone(4) + silence(1))
    assert [g.para for g in segs] == [False, True]          # the silent piece is not sent, the next one is marked
    for g, t in zip(segs, ("first part", "second part")):
        s.on_text(g.id, t)
    assert s.text() == "first part\n\nsecond part"


# ------------------------------------------------------------------ text
def test_results_arriving_out_of_order_are_joined_in_order_and_released_in_order():
    s = started()
    assert s.on_text(2, "three") == []
    assert s.on_text(0, "one") == ["one"]
    assert s.text() == "one three"                    # nothing already heard is hidden
    assert s.on_text(1, "two") == ["two", "three"]
    assert s.text() == "one two three"


def test_a_paragraph_mark_on_an_empty_piece_applies_to_the_next_text():
    s = started()
    s._para.add(1)
    for i, t in enumerate(("a", "", "b")):
        s.on_text(i, t)
    assert s.text() == "a\n\nb"


def test_the_first_text_never_starts_with_a_break():
    s = started()
    s._para.add(0)
    s.on_text(0, "hello")
    assert s.text() == "hello"


# ------------------------------------------------------------------ stop phrase
@pytest.mark.parametrize("said,kept", [
    ("Send the report. Stop listening.", "Send the report."),
    ("that is all, stop listening", "that is all"),
    ("STOP LISTENING!", ""),
    ("ok. Stop, listening.", "ok."),
])
def test_the_stop_phrase_at_the_end_is_recognised_and_removed(said, kept):
    assert session.stop_requested(said) is True
    s = started()
    s.on_text(0, said)
    assert s.text() == kept and s.state != "listening"


@pytest.mark.parametrize("said", [
    "stop listening to that podcast", "please keep listening", "stopping listening", "", "I will not stop listeningly",
])
def test_the_phrase_inside_a_sentence_does_not_stop(said):
    assert session.stop_requested(said) is False
    s = started()
    s.on_text(0, said)
    assert s.state == "listening" and s.text() == said


def test_stop_phrase_ends_the_session_once_the_audio_is_flushed_and_the_pieces_in_flight_are_back():
    s = started()
    feed_blocks(s, tone(4) + silence(1) + tone(4) + silence(1))      # pieces 0 and 1 are out
    s.on_text(1, "bye. stop listening")
    assert s.state == "stopping"                                      # piece 0 is still in flight
    s.on_text(0, "hello")
    assert s.state == "stopping" and s.stop() == []                   # the engine flushes; the rest is silent
    assert s.state == "idle" and s.text() == "hello bye."


def test_audio_after_the_stop_phrase_is_still_cut_and_flushed_not_dropped():
    s = started()
    first = feed_blocks(s, tone(4) + silence(1) + tone(4) + silence(1))      # pieces 0 and 1 are out
    s.on_text(0, "stop listening")
    assert s.state == "stopping"
    more = feed_blocks(s, tone(5)) + s.stop()                                 # the user kept talking for a moment
    assert b"".join(g.pcm for g in more).endswith(tone(5)) and more[0].id == len(first)   # only silence may come before


def test_stop_with_nothing_in_flight_goes_straight_to_idle():
    s = started()
    s.stop()
    assert s.state == "idle"


def test_stop_waits_for_results_then_goes_idle():
    s = started()
    segs = feed_blocks(s, tone(4) + silence(1)) + s.stop()
    assert s.state == "stopping"
    for g in segs:
        s.on_text(g.id, "x")
    assert s.state == "idle"


def test_audio_after_the_end_is_ignored():
    s = started()
    s.stop()
    assert s.feed(tone(5)) == []


# ------------------------------------------------------------------ length guard
def test_the_limits_are_55_and_60_minutes():
    assert session.WARN_SECONDS == 55 * 60 and session.MAX_SECONDS == 60 * 60


def test_the_session_warns_then_ends_itself_at_the_limit_and_keeps_all_audio_before_it(monkeypatch):
    monkeypatch.setattr(session, "WARN_SECONDS", 6)
    monkeypatch.setattr(session, "MAX_SECONDS", 10)
    audio = tone(4) + silence(1) + tone(5) + tone(3)                   # the last 2 s are past the limit
    s = started()
    assert s.warning == ""
    got = feed_blocks(s, audio[:7 * RATE * 2])
    assert s.warning == "Listening ends in 1 min." and s.state == "listening"
    got += feed_blocks(s, audio[7 * RATE * 2:])
    assert s.state == "stopping" and s.warning == "Time limit reached, listening stopped."
    assert b"".join(g.pcm for g in got) == audio[:10 * RATE * 2]
    assert s.feed(tone(3)) == []                                       # nothing past the limit is taken


# ------------------------------------------------------------------ same window
@pytest.mark.parametrize("start,now,ok", [
    ("notepad.exe", "notepad.exe", True),
    ("Notepad.exe", "notepad.EXE", True),
    ("notepad.exe", "chrome.exe", False),
    ("", "notepad.exe", False),          # the start window was never known: refuse rather than guess
    ("notepad.exe", "", False),          # no focused window or it cannot be named: refuse
    ("", "", False),
    (None, "notepad.exe", False),
    ("notepad.exe", None, False),
])
def test_same_target_types_only_into_the_window_that_was_chosen(start, now, ok):
    assert session.same_target(start, now) is ok


# ------------------------------------------------------------------ crash-safe buffer
def test_the_buffer_appends_and_discard_removes_the_file(tmp_path):
    b = session.SessionBuffer(str(tmp_path), "note")
    b.append(b"\x01\x00\x02\x00")
    b.append(b"\x03\x00")
    b.close()
    assert open(b.path, "rb").read() == b"\x01\x00\x02\x00\x03\x00"
    b.discard()
    assert not os.path.exists(b.path)
    b.discard()                                            # a second discard is harmless


def test_data_is_on_disk_before_close_so_a_crash_keeps_it(tmp_path):
    b = session.SessionBuffer(str(tmp_path), "type")
    b.append(tone(1))
    assert os.path.getsize(b.path) == RATE * 2             # no close, no flush call: a killed process still has it
    b.close()


def test_unfinished_sessions_are_listed_newest_first_with_target_and_length(tmp_path):
    a = session.SessionBuffer(str(tmp_path), "note"); a.append(tone(2)); a.close()
    b = session.SessionBuffer(str(tmp_path), "type"); b.append(tone(1)); b.close()
    os.utime(a.path, (1000, 1000))
    found = session.recoverable(str(tmp_path))
    assert [f["path"] for f in found] == [b.path, a.path]
    assert [f["target"] for f in found] == ["type", "note"]
    assert [f["seconds"] for f in found] == [1.0, 2.0]
    assert found[0]["started"][:2] == "20"


def test_empty_files_other_files_and_a_missing_folder_are_ignored(tmp_path):
    session.SessionBuffer(str(tmp_path), "note").close()              # nothing was recorded
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "listen-bad.pcm").write_bytes(b"\x00\x00")
    assert session.recoverable(str(tmp_path)) == []
    assert session.recoverable(str(tmp_path / "nope")) == []


def test_a_crash_in_the_middle_of_a_sample_is_trimmed_when_loaded(tmp_path):
    b = session.SessionBuffer(str(tmp_path), "note")
    b.append(b"\x01\x00\x02\x00\x03")
    b.close()
    assert session.load_pcm(b.path) == b"\x01\x00\x02\x00"
    assert session.recoverable(str(tmp_path))[0]["seconds"] == 4 / (RATE * 2)


def test_a_session_and_its_buffer_round_trip(tmp_path):
    audio = tone(4) + silence(1) + tone(4) + silence(1) + tone(2)
    s, b, live = started(), session.SessionBuffer(str(tmp_path), "note"), []
    for i in range(0, len(audio), 1600):
        b.append(audio[i:i + 1600])
        live += s.feed(audio[i:i + 1600])
    live += s.stop()
    b.close()                                              # the app died here: the session is gone, the file is not
    again = session.ListenSession("note"); again.start()
    segs = again.feed(session.load_pcm(b.path)) + again.stop()
    assert [g.pcm for g in segs] == [g.pcm for g in live] and b"".join(g.pcm for g in segs).endswith(tone(2))


# ------------------------------------------------------------------ chunks for the cleanup at the end
def sentences(n, start=0):
    return " ".join("This is sentence number %d." % i for i in range(start, start + n))


def put_back(chunks):
    return "".join(joiner + text for joiner, text in chunks)


def test_a_short_text_is_one_chunk_and_nothing_is_nothing():
    assert session.chunk_text("Hello there.") == [("", "Hello there.")]
    assert session.chunk_text("") == [] and session.chunk_text("  \n\n ") == []


def test_chunks_are_cut_at_paragraph_breaks_when_they_fit_and_hold_every_word():
    text = "\n\n".join(sentences(30, i * 30) for i in range(5))        # 5 paragraphs of 150 words
    chunks = session.chunk_text(text, max_words=400)
    assert [len(t.split()) for _, t in chunks] == [300, 300, 150]        # two paragraphs fit in a chunk
    assert chunks[0][0] == "" and all(j == "\n\n" for j, _ in chunks[1:])
    assert put_back(chunks) == text


def test_a_long_paragraph_is_cut_at_sentence_ends_and_put_back_with_a_space():
    text = sentences(100)                                                # one paragraph, 500 words
    chunks = session.chunk_text(text, max_words=200)
    assert [len(t.split()) for _, t in chunks] == [200, 200, 100]
    assert [j for j, _ in chunks] == ["", " ", " "] and all(t.endswith(".") for _, t in chunks)
    assert put_back(chunks) == text


def test_text_without_any_full_stop_is_cut_by_words_and_loses_nothing():
    text = " ".join("w%d" % i for i in range(450))
    chunks = session.chunk_text(text, max_words=100)
    assert [len(t.split()) for _, t in chunks] == [100, 100, 100, 100, 50]
    assert put_back(chunks) == text


def test_the_default_chunk_size_is_a_few_hundred_words():
    assert 200 <= session.CHUNK_WORDS <= 500
    assert len(session.chunk_text(sentences(100))) == 2                  # 500 words


# ------------------------------------------------------------------ the setting
def test_the_listen_target_setting_defaults_to_note_and_ignores_unknown_values():
    assert core.DEFAULT_CONFIG["listen_target"] == "note"
    assert session.listen_target({"listen_target": "type"}) == "type"
    assert session.listen_target({"listen_target": "x"}) == session.listen_target({}) == "note"
