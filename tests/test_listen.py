"""Keep listening, the running part (windows/listen.py): audio in, speech to text in the background, the Note and Type
targets. The speech and cleanup calls and the window are replaced; the threads are real. No hardware, no network."""
import array
import math
import os
import threading

import listen
import session
import vox_core as core

RATE = core.SAMPLE_RATE


def tone(seconds, amp=8000):
    n = int(seconds * RATE)
    return array.array("h", (int(amp * math.sin(i * 0.3)) for i in range(n))).tobytes()


def silence(seconds):
    return b"\x00\x00" * int(seconds * RATE)


def utterances(n, pause=1.0):
    return b"".join(tone(4) + silence(pause) for _ in range(n))


class Host:
    """What the engine offers a session."""

    def __init__(self):
        self.messages, self.states, self.flashes, self.pasted, self.notes = [], [], [], [], []
        self.mic_closed = 0
        self.paste_ok = True
        self.pasted_once, self.paused_once = threading.Event(), threading.Event()

    def notify(self, msg):
        self.messages.append(msg)
        if "paused" in msg.lower():
            self.paused_once.set()

    def close_mic(self):
        self.mic_closed += 1

    def listen_state(self, name):
        self.states.append(name)

    def flash(self, kind):
        self.flashes.append(kind)

    def paste(self, text):
        self.pasted.append(text)
        self.pasted_once.set()
        return self.paste_ok

    def save_note(self, text, raw, secs):
        self.notes.append((text, raw))


class Script:
    """core.transcribe: answers from a list (an Exception in it is raised) and remembers the contexts it was given.
    `hooks` maps a call number to a function run at the start of that call."""

    def __init__(self, answers, hooks=None):
        self.answers, self.contexts, self.hooks = list(answers), [], hooks or {}

    def __call__(self, cfg, wav, context=""):
        self.contexts.append(context)
        if len(self.contexts) in self.hooks:
            self.hooks[len(self.contexts)]()
        a = self.answers.pop(0) if self.answers else ""
        if isinstance(a, Exception):
            raise a
        return a


NOCLEAN = {"cleanup": False}


def run(host, audio, target="note", cfg=NOCLEAN, focus=None, **kw):
    lis = listen.Listening(host, cfg, target, focus=focus or (lambda: "notepad.exe"), **kw)
    lis.start()
    for i in range(0, len(audio), 3200):
        lis.audio(audio[i:i + 3200], 0, 0, None)
    lis.stop()
    assert lis.done.wait(20), "the session did not finish"
    return lis


# ------------------------------------------------------------------ Note target
def test_a_note_session_sends_each_piece_with_context_and_saves_one_note_at_the_end(monkeypatch):
    stt = Script(["red thing.", "green thing.", "blue thing."])   # (not first/second/third: those would make a list)
    monkeypatch.setattr(core, "transcribe", stt)
    host = Host()
    run(host, utterances(3))
    assert stt.contexts == ["", "red thing.", "red thing. green thing."]
    assert host.notes == [("red thing. green thing. blue thing.", "red thing. green thing. blue thing.")]
    assert host.pasted == [] and host.mic_closed >= 1
    assert host.states == ["busy", "idle"] and host.flashes == ["sent"]


def test_the_note_is_cleaned_in_chunks_that_keep_every_chunk_and_the_guard_applies(monkeypatch):
    text = " ".join("This is sentence number %d." % i for i in range(100))           # 500 words, two chunks
    monkeypatch.setattr(core, "transcribe", Script([text]))
    seen = []

    def cleanup(cfg, raw, style, label):
        seen.append(raw)
        return "Short." if len(seen) == 2 else raw.upper()                            # the second answer lost the words

    monkeypatch.setattr(core, "cleanup", cleanup)
    host = Host()
    run(host, utterances(1), cfg={"cleanup": True})
    assert len(seen) == 2 and all(len(r.split()) <= session.CHUNK_WORDS for r in seen)
    note, raw = host.notes[0]
    assert raw == text and note.startswith(seen[0].upper())                           # chunk 1: the cleaned text
    assert note.endswith(seen[1])                                                      # chunk 2: the guard fell back to the spoken words
    assert len(note.split()) == 500


def test_a_failed_cleanup_still_saves_the_spoken_words(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["hello there my friend."]))

    def boom(cfg, raw, style, label):
        raise core.ApiError(503, "down")

    monkeypatch.setattr(core, "cleanup", boom)
    host = Host()
    run(host, utterances(1), cfg={"cleanup": True})
    assert host.notes == [("Hello there my friend.", "hello there my friend.")] and host.flashes == ["sent"]   # rules layer


def test_a_long_pause_becomes_a_paragraph_break_in_the_note(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["one one one.", "two two two."]))
    host = Host()
    run(host, tone(4) + silence(4) + tone(4) + silence(1))
    assert host.notes[0][1] == "one one one.\n\ntwo two two."


def test_a_session_where_nothing_was_said_saves_nothing(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script([]))
    host = Host()
    run(host, silence(6))
    assert host.notes == [] and host.flashes == ["error"] and host.states == ["busy", "idle"]
    assert any("did not hear" in m for m in host.messages)


# ------------------------------------------------------------------ ending
def test_the_stop_phrase_closes_the_microphone_and_is_not_in_the_text(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["remember the milk.", "that is all. Stop listening."]))
    host = Host()
    run(host, utterances(2) + tone(2))                    # the user keeps going for a moment after the phrase
    assert host.notes[0][1].startswith("remember the milk. that is all.")
    assert "stop listening" not in host.notes[0][1].lower()
    assert host.mic_closed >= 1 and host.flashes == ["sent"]


def test_the_time_limit_ends_the_session_and_keeps_the_audio_before_it(monkeypatch):
    monkeypatch.setattr(session, "MAX_SECONDS", 10)
    monkeypatch.setattr(core, "transcribe", Script(["a a a.", "b b b."]))
    host = Host()
    lis = listen.Listening(host, NOCLEAN, "note", focus=lambda: "x.exe")
    lis.start()
    audio = utterances(4)                                 # 20 s, the limit is 10 s
    for i in range(0, len(audio), 3200):
        lis.audio(audio[i:i + 3200], 0, 0, None)
    assert lis.done.wait(20)                              # no stop() call: it ended itself
    assert host.mic_closed >= 1 and host.notes and host.notes[0][1].startswith("a a a.")


def test_the_pill_shows_the_session_clock_and_the_limit_warning(monkeypatch):
    monkeypatch.setattr(session, "WARN_SECONDS", 3)
    monkeypatch.setattr(session, "MAX_SECONDS", 600)
    monkeypatch.setattr(core, "transcribe", Script([]))
    lis = listen.Listening(Host(), NOCLEAN, "note", focus=lambda: "x.exe")
    assert lis.seconds == 0 and lis.message == ""
    lis.start()
    lis.audio(tone(4), 0, 0, None)
    lis.stop()
    assert lis.done.wait(20)
    assert lis.seconds == 4.0 and lis.message.startswith("Listening ends in")


# ------------------------------------------------------------------ Type target
def test_the_type_target_cleans_and_types_each_piece_in_the_chosen_app(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["hello big world.", "second one here."]))
    monkeypatch.setattr(core, "cleanup", lambda cfg, raw, style, label: raw.upper())
    host = Host()
    run(host, utterances(2), target="type", cfg={"cleanup": True, "cleanup_min_words": 3})
    assert host.pasted == ["HELLO BIG WORLD.", " SECOND ONE HERE."]                     # a space between pieces, none first
    assert host.notes == [] and host.states == ["busy", "idle"] and host.flashes == ["sent"]


def test_short_pieces_skip_the_cleanup_as_in_a_dictation(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["yes ok."]))
    monkeypatch.setattr(core, "cleanup", lambda *a: (_ for _ in ()).throw(AssertionError("cleanup must not run")))
    host = Host()
    run(host, utterances(1), target="type", cfg={"cleanup": True, "cleanup_min_words": 3})
    assert host.pasted == ["Yes ok."]   # skipped as short: the rules layer's text


def test_the_type_target_never_types_into_another_window_and_keeps_the_text_for_a_note(monkeypatch):
    host = Host()
    window = ["notepad.exe"]

    def switch_away():
        host.pasted_once.wait(5)                           # the first piece was typed; now the user leaves the window
        window[0] = "chrome.exe"

    def switch_back():
        host.paused_once.wait(5)                           # the second piece was held back; the user comes back
        window[0] = "notepad.exe"

    stt = Script(["typed here.", "said elsewhere.", "back again."], hooks={2: switch_away, 3: switch_back})
    monkeypatch.setattr(core, "transcribe", stt)
    lis = run(host, utterances(3), target="type", focus=lambda: window[0])
    assert host.pasted == ["typed here.", " back again."]                              # nothing went to the other window
    assert host.notes == [("said elsewhere.", "said elsewhere.")]                      # kept, saved as a note at the end
    assert len([m for m in host.messages if "paused" in m.lower()]) == 1               # said once
    assert lis.message == ""                                                           # and typing resumed


def test_an_unknown_window_is_refused_and_the_start_window_is_the_one_chosen(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["some words here."]))
    host = Host()
    run(host, utterances(1), target="type", focus=lambda: "")                        # cannot tell which window: refuse
    assert host.pasted == [] and host.notes == [("some words here.", "some words here.")]
    lis = listen.Listening(Host(), NOCLEAN, "type", focus=lambda: "Notepad.exe")
    assert lis.exe == "Notepad.exe"


def test_a_paste_that_only_reached_the_clipboard_counts_as_not_typed(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["some words here."]))
    host = Host()
    host.paste_ok = False
    run(host, utterances(1), target="type")
    assert host.notes == [("some words here.", "some words here.")]


def test_the_pill_message_while_paused(monkeypatch):
    lis = listen.Listening(Host(), NOCLEAN, "type", focus=lambda: "notepad.exe")
    assert lis.message == ""
    lis.paused = True
    assert lis.message == "Paused: wrong window"


# ------------------------------------------------------------------ safety
def test_the_audio_is_kept_on_disk_while_listening_and_removed_after_a_good_session(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "transcribe", Script(["fine words here."]))
    buf = session.SessionBuffer(str(tmp_path), "note")
    run(Host(), utterances(1), buffer=buf)
    assert not os.path.exists(buf.path)


def test_a_piece_that_fails_is_reported_once_and_the_audio_is_kept_for_recovery(monkeypatch, tmp_path):
    boom = core.ApiError(503, "down")
    monkeypatch.setattr(core, "transcribe", Script(["good one here.", boom, boom, "last good one."]))
    host = Host()
    buf = session.SessionBuffer(str(tmp_path), "note")
    run(host, utterances(4), buffer=buf)
    assert host.notes[0][1] == "good one here. last good one."
    assert os.path.getsize(buf.path) >= 4 * 4 * RATE * 2                              # all the audio is still there
    assert len([m for m in host.messages if "kept" in m]) == 1
    assert session.recoverable(str(tmp_path))[0]["path"] == buf.path


def test_a_bug_in_the_cleanup_still_saves_the_spoken_words(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["fine words here."]))

    def broken(*a):
        raise RuntimeError("bug")

    monkeypatch.setattr(core, "process_text", broken)
    host = Host()
    run(host, utterances(1))
    assert host.notes == [("fine words here.", "fine words here.")] and host.flashes == ["sent"]


def test_when_the_note_cannot_be_saved_the_audio_is_kept_and_the_session_still_ends(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "transcribe", Script(["fine words here."]))
    host = Host()

    def broken(text, raw, secs):
        raise OSError("disk full")

    host.save_note = broken
    buf = session.SessionBuffer(str(tmp_path), "note")
    run(host, utterances(1), buffer=buf)
    assert os.path.exists(buf.path) and host.flashes == ["error"] and host.states == ["busy", "idle"]
    assert any("kept" in m for m in host.messages)


def test_a_recovered_session_is_replayed_into_one_note_and_the_old_file_is_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "transcribe", Script(["part one here.", "part two here."]))
    old = tmp_path / "old.pcm"
    old.write_bytes(utterances(2))
    host = Host()
    done = []
    lis = listen.Listening(host, NOCLEAN, "note", focus=lambda: "x.exe", after=lambda: done.append(1))
    lis.start()
    lis.replay(session.load_pcm(str(old)))
    assert lis.done.wait(20)
    assert host.notes[0][1] == "part one here. part two here." and done == [1]


# ------------------------------------------------------------------ latency marks
def test_every_piece_gets_seg_end_and_seg_text_marks(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["one one one.", "two two two.", "three three three."]))
    lis = run(Host(), utterances(3))
    assert len(lis.timings) == 3 and len(lis.latency_ms) == 3
    assert all(t.has("seg_end") and t.has("seg_text") for t in lis.timings)
    assert all(ms >= 0 for ms in lis.latency_ms)


def test_the_audio_callback_only_queues_the_bytes(monkeypatch):
    lis = listen.Listening(Host(), NOCLEAN, "note", focus=lambda: "x.exe")
    lis.audio(memoryview(tone(0.1)), 0, 0, None)
    assert lis._q.get_nowait() == tone(0.1)


def test_a_buffer_that_cannot_be_discarded_still_ends_the_session_cleanly(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["hello there my friend."]))

    class Stuck:
        def append(self, pcm):
            pass

        def close(self):
            pass

        def discard(self):
            raise PermissionError("held by another program")

    host = Host()
    run(host, utterances(1), buffer=Stuck())
    assert host.notes and host.states == ["busy", "idle"] and host.flashes == ["sent"]


def test_a_microphone_that_goes_silent_ends_the_session_and_saves_what_was_heard(monkeypatch):
    monkeypatch.setattr(listen, "MIC_SILENT_SECONDS", 0.3)
    monkeypatch.setattr(core, "transcribe", Script(["before it died."]))
    host = Host()
    lis = listen.Listening(host, NOCLEAN, "note", focus=lambda: "notepad.exe")
    lis.start()
    lis.audio(utterances(1), 0, 0, None)             # then the callback stops: nothing more arrives, no stop() call
    assert lis.done.wait(20), "a dead microphone left the session listening"
    assert host.notes == [("before it died.", "before it died.")]
    assert any("microphone" in m.lower() for m in host.messages)
    assert host.mic_closed >= 1 and host.flashes == ["error"] and host.states == ["busy", "idle"]


def test_a_dead_microphone_with_nothing_heard_says_so_once(monkeypatch):
    monkeypatch.setattr(listen, "MIC_SILENT_SECONDS", 0.3)
    host = Host()
    lis = listen.Listening(host, NOCLEAN, "note", focus=lambda: "notepad.exe")
    lis.start()
    assert lis.done.wait(20)
    assert host.notes == [] and len([m for m in host.messages if "microphone" in m.lower()]) == 1


def test_a_replayed_session_is_not_cut_short_by_the_microphone_watch(monkeypatch, tmp_path):
    monkeypatch.setattr(listen, "MIC_SILENT_SECONDS", 0.3)
    monkeypatch.setattr(core, "transcribe", Script(["old words."]))
    host = Host()
    lis = listen.Listening(host, NOCLEAN, "note", focus=lambda: "notepad.exe")
    lis.start()
    lis.replay(utterances(1))
    assert lis.done.wait(20)
    assert host.notes == [("old words.", "old words.")] and not any("microphone" in m.lower() for m in host.messages)


# ------------------------------------------------------------------ recovery must not delete audio it did not hear
def test_a_session_that_lost_its_microphone_does_not_remove_the_recovery_file(monkeypatch):
    monkeypatch.setattr(listen, "MIC_SILENT_SECONDS", 0.05)
    monkeypatch.setattr(core, "transcribe", Script([]))
    host, removed = Host(), []
    lis = listen.Listening(host, NOCLEAN, "note", focus=lambda: "notepad.exe", after=lambda: removed.append(1))
    lis.start()          # nothing is replayed: the saved audio was not read in time
    assert lis.done.wait(20)
    assert removed == []


# ------------------------------------------------------------------ Esc: cancel (task B1)
def test_cancel_sends_nothing_more_saves_no_note_and_shows_no_flash(monkeypatch, tmp_path):
    box = {}
    stt = Script(["first thing.", "second thing.", "third thing."], hooks={1: lambda: box["lis"].cancel()})
    monkeypatch.setattr(core, "transcribe", stt)
    host = Host()
    buf = session.SessionBuffer(str(tmp_path), "note")
    lis = box["lis"] = listen.Listening(host, NOCLEAN, "note", buffer=buf, focus=lambda: "notepad.exe")
    lis.start()
    audio = utterances(3)
    for i in range(0, len(audio), 3200):
        lis.audio(audio[i:i + 3200], 0, 0, None)
    lis.stop()
    assert lis.done.wait(20)
    assert len(stt.contexts) == 1                                     # the piece in flight; nothing after it
    assert host.notes == [] and host.flashes == [] and host.states[-1] == "idle"
    assert any("cancelled" in m.lower() for m in host.messages)
    assert not os.path.exists(buf.path)                               # a short session leaves nothing behind


def test_a_long_cancelled_session_keeps_its_audio_for_recovery(monkeypatch, tmp_path):
    box = {}
    monkeypatch.setattr(listen, "CANCEL_KEEP_SECONDS", 1)
    monkeypatch.setattr(core, "transcribe", Script(["first thing."], hooks={1: lambda: box["lis"].cancel()}))
    host = Host()
    buf = session.SessionBuffer(str(tmp_path), "note")
    lis = box["lis"] = listen.Listening(host, NOCLEAN, "note", buffer=buf, focus=lambda: "notepad.exe")
    lis.start()
    audio = utterances(2)
    for i in range(0, len(audio), 3200):
        lis.audio(audio[i:i + 3200], 0, 0, None)
    lis.stop()
    assert lis.done.wait(20)
    assert host.notes == [] and session.recoverable(str(tmp_path))[0]["path"] == buf.path
    assert any("kept" in m for m in host.messages)


def test_a_cancelled_type_session_types_nothing_more(monkeypatch):
    box = {}
    monkeypatch.setattr(core, "transcribe", Script(["first thing here.", "second thing here."],
                                                   hooks={1: lambda: box["lis"].cancel()}))
    host = Host()
    lis = box["lis"] = listen.Listening(host, NOCLEAN, "type", focus=lambda: "notepad.exe")
    lis.start()
    audio = utterances(2)
    for i in range(0, len(audio), 3200):
        lis.audio(audio[i:i + 3200], 0, 0, None)
    lis.stop()
    assert lis.done.wait(20)
    assert host.pasted == [] and host.notes == []


# ------------------------------------------------------------------ ENG-4: busy never comes after idle
def test_the_pill_goes_busy_before_the_microphone_is_closed():
    """A slow close_mic used to run first: a session that ended meanwhile (a second stop, the silent-microphone timeout)
    set idle, and the first stop then set busy for good, so every shortcut was ignored until Quit."""
    order = []
    host = Host()
    host.close_mic = lambda: order.append("close")
    host.listen_state = lambda name: order.append(name)
    lis = listen.Listening(host, NOCLEAN, "note", focus=lambda: "notepad.exe")
    lis._halt()
    lis._halt()
    assert order == ["busy", "close"]


def test_a_stop_still_closing_the_microphone_when_the_session_ends_leaves_it_idle(monkeypatch):
    monkeypatch.setattr(core, "transcribe", Script(["hello there"]))
    host = Host()
    closing, release = threading.Event(), threading.Event()

    def slow_close():
        closing.set()
        release.wait(10)   # a Bluetooth driver whose stop hangs

    host.close_mic = slow_close
    lis = listen.Listening(host, NOCLEAN, "note", focus=lambda: "notepad.exe")
    lis.start()
    lis.audio(utterances(1), 0, 0, None)
    stopper = threading.Thread(target=lis.stop)
    stopper.start()
    assert closing.wait(10)
    lis._q.put(None)              # the session ends while the first stop is still inside close_mic
    assert lis.done.wait(20)
    release.set()
    stopper.join(10)
    assert host.states[-1] == "idle"
