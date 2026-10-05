"""The clip recorder of the benchmark (tools/bench_record.py, tools/bench_clips.py) with a fake recorder and typed
answers: no microphone, no sound, nothing outside the test folder."""
import array
import os
import sys
import wave

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bench_clips as clips    # noqa: E402
import bench_record as rec     # noqa: E402


def voice(seconds):
    """A loud square wave: not silent."""
    return array.array("h", [8000 if (i // 20) % 2 else -8000 for i in range(int(16000 * seconds))]).tobytes()


def quiet(seconds):
    return bytes(int(32000 * seconds))


class FakeRecorder:
    def __init__(self, *takes):
        self.takes, self.started, self.played = list(takes), 0, []

    def start(self):
        self.started += 1

    def stop(self):
        return self.takes.pop(0)

    def play(self, pcm):
        self.played.append(len(pcm))


class Typist:
    """Answers the prompts in order; then the input ends (as Ctrl+Z / a closed window would)."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a


def session(tmp_path, recorder, typist, target=50):
    said = []
    s = rec.Session(str(tmp_path / "clips"), recorder, target, typist, said.append)
    return s, said


def one_clip(verbatim="um send it to priya", intended="", terms=""):
    return ["", "", "", verbatim, intended, terms]   # start, stop, keep, three texts


def test_two_clips_are_saved_with_their_texts_and_a_progress_count(tmp_path):
    s, said = session(tmp_path, FakeRecorder(voice(2), voice(3.5)),
                      Typist(*one_clip("um send it to priya", "Send it to Priya.", "Priya"),
                             *one_clip("thursday no wait friday", "", " Friday ,  ,Ledgerly ")))
    assert s.run() == 2
    folder = str(tmp_path / "clips")
    assert clips.clips_on_disk(folder) == ["clip-001", "clip-002"]
    rows = {r["id"]: r for r in clips.load_manifest(folder)}
    assert rows["clip-001"]["ref_verbatim"] == "um send it to priya" and rows["clip-001"]["ref_intended"] == "Send it to Priya."
    assert rows["clip-001"]["terms"] == ["Priya"] and rows["clip-001"]["seconds"] == 2.0 and rows["clip-001"]["kind"]
    assert rows["clip-002"]["ref_intended"] == rec.core.fallback_text("thursday no wait friday") == "Thursday no wait Friday."
    assert rows["clip-002"]["intended_typed"] is False and rows["clip-001"]["intended_typed"] is True   # Enter = the suggestion
    assert any("(Enter = Thursday no wait Friday.)" in p for p in s._ask.prompts)
    assert rows["clip-001"]["mic"] == "default"
    assert rows["clip-002"]["terms"] == ["Friday", "Ledgerly"]
    text = "\n".join(said)
    assert "Clip 1 of 50" in text and "Clip 2 of 50" in text and "Saved clip-002 (2 of 50 done)" in text
    assert "Run the same command again" in text
    with wave.open(clips.wav_path(folder, "clip-002"), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (16000, 1, 2, 56000)


def test_a_new_session_continues_after_the_last_clip_and_never_overwrites(tmp_path):
    s, _ = session(tmp_path, FakeRecorder(voice(1)), Typist(*one_clip()))
    s.run()
    folder = str(tmp_path / "clips")
    before = open(clips.wav_path(folder, "clip-001"), "rb").read()
    s, said = session(tmp_path, FakeRecorder(voice(2)), Typist(*one_clip("second one")))
    assert s.run() == 2
    assert "1 clips done so far" in said[1] and any("Clip 2 of 50" in x for x in said)
    assert open(clips.wav_path(folder, "clip-001"), "rb").read() == before
    assert clips.clips_on_disk(folder) == ["clip-001", "clip-002"]


def test_texts_not_typed_before_a_close_are_asked_for_first(tmp_path):
    s, _ = session(tmp_path, FakeRecorder(voice(1)), Typist("", "", ""))   # recorded and kept, then the window closed
    assert s.run() == 0
    folder = str(tmp_path / "clips")
    assert clips.clips_on_disk(folder) == ["clip-001"]
    rec_ = FakeRecorder(voice(2))
    s, said = session(tmp_path, rec_, Typist("p", "", "what i said", "", "", *one_clip("next one")))
    assert s.run() == 2
    assert any("clip-001 was recorded but its texts were not typed yet" in x for x in said)
    assert rec_.played == [32000]                     # p played the saved clip
    rows = {r["id"]: r for r in clips.load_manifest(folder)}
    assert rows["clip-001"]["ref_verbatim"] == "what i said" and rows["clip-001"]["kind"]   # its kind was kept
    assert rows["clip-002"]["ref_verbatim"] == "next one"


def test_an_untyped_clip_can_be_skipped_and_the_next_clip_gets_a_new_number(tmp_path):
    folder = str(tmp_path / "clips")
    clips.write_new_wav(clips.wav_path(folder, "clip-001"), voice(1))   # a WAV without any manifest line
    s, _ = session(tmp_path, FakeRecorder(voice(1)), Typist("s", *one_clip()))
    assert s.run() == 1
    assert clips.clips_on_disk(folder) == ["clip-001", "clip-002"]
    assert [r["id"] for r in s.done()] == ["clip-002"]


def test_too_short_silent_redo_play_and_discard(tmp_path):
    r = FakeRecorder(voice(0.2), quiet(2), voice(2), voice(4))
    s, said = session(tmp_path, r, Typist("", "",          # too short: asked again
                                          "", "", "r",     # silent, warned, record again
                                          "", "", "p", "q"))   # played, then discarded and stopped
    assert s.run() == 0
    text = "\n".join(said)
    assert "too short" in text and "sounds silent" in text
    assert r.played == [64000] and r.started == 3
    assert clips.clips_on_disk(str(tmp_path / "clips")) == []   # nothing kept, nothing saved


def test_the_verbatim_line_must_not_be_empty(tmp_path):
    t = Typist("", "", "", "", "  ", "said this", "", "")
    s, _ = session(tmp_path, FakeRecorder(voice(1)), t)
    assert s.run() == 1
    assert sum("1/3 Verbatim" in p for p in t.prompts) == 3


@pytest.mark.parametrize("stop", ["q", KeyboardInterrupt()])
def test_q_or_ctrl_c_stops_cleanly(tmp_path, stop):
    r = FakeRecorder(voice(1))
    s, said = session(tmp_path, r, Typist(stop))
    assert s.run() == 0 and r.started == 0
    assert "Stopped. 0 clips done" in said[-1]


def test_ctrl_c_while_recording_stops_the_recorder_and_saves_nothing(tmp_path):
    r = FakeRecorder(voice(1))
    s, _ = session(tmp_path, r, Typist("", KeyboardInterrupt()))
    assert s.run() == 0 and r.takes == []   # stop() was called
    assert clips.clips_on_disk(str(tmp_path / "clips")) == []


def test_reaching_the_goal_asks_before_going_on(tmp_path):
    s, said = session(tmp_path, FakeRecorder(voice(1), voice(1)), Typist(*one_clip(), "", *one_clip("more"), "q"), target=1)
    assert s.run() == 2
    assert any("Goal reached: 1 clips" in x for x in said) and any("bench_stt.py --provider groq" in x for x in said)


def test_main_uses_the_given_recorder_and_folder(tmp_path):
    folder = str(tmp_path / "f")
    assert rec.main(["--folder", folder, "--target", "1"], recorder=FakeRecorder(voice(1)),
                    ask=Typist(*one_clip(), "q"), say=lambda t: None) == 0
    assert clips.clips_on_disk(folder) == ["clip-001"]


def test_ctrl_c_during_playback_only_stops_the_playback(tmp_path):
    class Interrupted(FakeRecorder):
        def play(self, pcm):
            raise KeyboardInterrupt
    s, said = session(tmp_path, Interrupted(voice(1)), Typist("", "", "p", "", "said it", "", ""))
    assert s.run() == 1   # the take was still kept after the stopped playback
    assert any("Playback stopped" in x for x in said)


def test_a_missing_microphone_falls_back_to_the_default_one(monkeypatch):
    import audio_devices
    monkeypatch.setitem(sys.modules, "sounddevice", object())
    monkeypatch.setattr(audio_devices, "input_index", lambda name, sd=None: None)
    said = []
    r = rec.MicRecorder("Gone USB mic", said.append)
    assert r.device is None and r.name == "" and "Microphone not found: Gone USB mic" in said[0] and "default" in said[0]
    monkeypatch.setattr(audio_devices, "input_index", lambda name, sd=None: 3)
    r = rec.MicRecorder("USB mic", said.append)
    assert r.device == 3 and r.name == "USB mic"


def test_the_suggestions_cover_the_kinds_the_plan_asks_for():
    kinds = {k for k, _ in rec.SUGGESTIONS}
    assert len(rec.SUGGESTIONS) >= 50
    assert {"chat", "email", "names", "numbers", "list", "hinglish", "selfcorr", "command", "long", "short"} <= kinds


# ------------------------------------------------------------------ bench_clips

def test_write_new_wav_never_overwrites(tmp_path):
    p = str(tmp_path / "c" / "clip-001.wav")
    clips.write_new_wav(p, voice(1))
    with pytest.raises(FileExistsError):
        clips.write_new_wav(p, voice(2))
    assert clips.read_pcm(p) == voice(1) and not os.path.exists(p + ".part")


def test_next_clip_id_looks_at_the_files_and_the_manifest(tmp_path):
    folder = str(tmp_path)
    assert clips.next_clip_id(folder) == "clip-001"
    clips.write_new_wav(clips.wav_path(folder, "clip-004"), voice(0.1))
    clips.append_row(folder, {"id": "clip-009"})
    assert clips.next_clip_id(folder) == "clip-010"
    open(os.path.join(folder, "clip-x.wav"), "wb").close()   # not a clip name: ignored
    assert clips.clips_on_disk(folder) == ["clip-004"]


def test_the_manifest_skips_a_cut_line_and_keeps_the_newest_line_per_clip(tmp_path):
    folder = str(tmp_path)
    clips.append_row(folder, {"id": "clip-001", "kind": "chat"})
    with open(os.path.join(folder, clips.MANIFEST), "a", encoding="utf-8") as f:
        f.write('{"id": "clip-002", "ref_ver')   # a write cut off
    clips.append_row(folder, {"id": "clip-001", "kind": "chat", "ref_verbatim": "x"})
    assert clips.load_manifest(folder) == [{"id": "clip-001", "kind": "chat", "ref_verbatim": "x"}]


def test_read_pcm_refuses_another_format(tmp_path):
    p = str(tmp_path / "x.wav")
    with wave.open(p, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(bytes(400))
    with pytest.raises(ValueError):
        clips.read_pcm(p)


def test_all_terms_is_every_clips_terms_once_in_order():
    assert clips.all_terms([{"terms": ["Priya", "Ledgerly"]}, {}, {"terms": ["Priya", "Groq"]}]) == ["Priya", "Ledgerly", "Groq"]


def test_the_stt_cache_round_trips_and_a_broken_one_reads_as_empty(tmp_path):
    folder = str(tmp_path)
    clips.save_stt(folder, "clip-001", {"a": {"text": "hi"}})
    assert clips.load_stt(folder, "clip-001") == {"a": {"text": "hi"}}
    with open(clips.stt_path(folder, "clip-002"), "w") as f:
        f.write("{oops")
    assert clips.load_stt(folder, "clip-002") == {} and clips.load_stt(folder, "clip-003") == {}
