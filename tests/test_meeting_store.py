"""A meeting is never lost: a failed export still lists it, and an interrupted one is recovered at startup."""
import json
import os
import sys
import types

import pytest


@pytest.fixture
def meeting_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    stubbed = False
    try:
        import numpy  # noqa: F401
    except ImportError:   # CI's tests job has no numpy; meeting.py imports it at module level
        monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
        stubbed = True
    try:
        import meeting
        yield meeting
    finally:
        if stubbed:
            sys.modules.pop("meeting", None)


class FakeSource:
    def __init__(self, who, raw_path):
        self.who, self.raw_path, self.pieces, self.error = who, raw_path, [], None

    def join(self, timeout=None):
        pass


def test_a_failed_export_still_lists_the_meeting_and_removes_the_raw_audio(meeting_mod, monkeypatch):
    mt = meeting_mod
    m = mt.Meeting(lambda: {"final_pass": False})
    m.id, m.started = "20261001-120000", 1759320000.0
    m.entries = [{"t": 5, "who": "You", "text": "hello"}]
    raw = os.path.join(m.folder(), "you.raw")
    open(raw, "wb").write(b"\x00\x00")
    m.sources = [FakeSource("You", raw)]
    monkeypatch.setattr(m, "_notes", lambda cfg, transcript: "# T\n\nok")
    monkeypatch.setattr(m, "attribute_speakers", lambda: None)

    def denied(cfg):
        raise PermissionError("Controlled Folder Access")

    monkeypatch.setattr(mt, "notes_export_dir", denied)
    m._finish()
    listed = {x["id"]: x for x in mt.list_meetings()}
    assert "20261001-120000" in listed
    assert listed["20261001-120000"].get("export_error") == "PermissionError"
    assert not os.path.exists(raw)
    assert "ok" in mt.read_notes("20261001-120000")


def test_a_title_with_control_characters_is_exported(meeting_mod, monkeypatch, tmp_path):
    mt = meeting_mod
    m = mt.Meeting(lambda: {"final_pass": False})
    m.id, m.started = "20261001-130000", 1759320000.0
    m.event = {"title": "Sync\twith\nBob", "attendees": []}
    m.entries = [{"t": 5, "who": "You", "text": "hello"}]
    m.sources = []
    monkeypatch.setattr(m, "_notes", lambda cfg, transcript: "# T\n\nok")
    monkeypatch.setattr(m, "attribute_speakers", lambda: None)
    out = tmp_path / "export"
    out.mkdir()
    monkeypatch.setattr(mt, "notes_export_dir", lambda cfg: str(out))
    m._finish()
    meta = [x for x in mt.list_meetings() if x["id"] == "20261001-130000"][0]
    assert "export" in meta and os.path.exists(meta["export"])
    assert all(ord(c) >= 32 for c in os.path.basename(meta["export"]))


def _interrupted(mt, mid="20261001-120000", raw=True):
    d = os.path.join(mt.meetings_dir(), mid)
    os.makedirs(d)
    with open(os.path.join(d, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump({"id": mid, "started": 1.0, "entries": [{"t": 5, "who": "You", "text": "hello"}]}, f)
    if raw:
        open(os.path.join(d, "you.raw"), "wb").write(b"\x00\x00")
    return d


def test_an_interrupted_meeting_is_recovered_and_its_audio_removed(meeting_mod):
    mt = meeting_mod
    d = _interrupted(mt)
    mt.recover_unfinished({})
    found = [x for x in mt.list_meetings() if x["id"] == "20261001-120000"]
    assert found and found[0].get("unfinished") is True
    assert "hello" in mt.read_notes("20261001-120000")
    assert not os.path.exists(os.path.join(d, "you.raw"))
    before = open(os.path.join(d, "meta.json"), encoding="utf-8").read()
    mt.recover_unfinished({})   # a second call changes nothing
    assert open(os.path.join(d, "meta.json"), encoding="utf-8").read() == before


def _speech_raw(d, seconds, name="you.raw"):
    with open(os.path.join(d, name), "wb") as f:
        f.write(b"\x01\x00" * int(16000 * seconds))


def test_an_offline_meeting_with_an_empty_transcript_keeps_its_audio(meeting_mod):
    """Final review, Windows high 2: speech the live transcription never got (network down) exists only as audio."""
    mt = meeting_mod
    mid = "20261001-150000"
    d = os.path.join(mt.meetings_dir(), mid)
    os.makedirs(d)
    with open(os.path.join(d, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump({"id": mid, "started": 1.0, "entries": []}, f)
    _speech_raw(d, 60)
    mt.recover_unfinished({})
    assert os.path.getsize(os.path.join(d, "you.raw")) == 60 * 32000
    meta = [x for x in mt.list_meetings() if x["id"] == mid][0]
    assert meta["unfinished"] is True and meta["incomplete"] is True
    assert "audio" in mt.read_notes(mid).lower()


def test_a_transcript_that_covers_only_part_of_the_audio_keeps_the_audio(meeting_mod):
    mt = meeting_mod
    d = _interrupted(mt, "20261001-151000")     # one entry, "hello"
    _speech_raw(d, 120)                         # two minutes of speech, one word transcribed
    mt.recover_unfinished({})
    assert os.path.exists(os.path.join(d, "you.raw"))
    assert [x for x in mt.list_meetings() if x["id"] == "20261001-151000"][0]["incomplete"] is True


def test_a_transcript_that_covers_the_audio_lets_the_audio_go(meeting_mod):
    mt = meeting_mod
    mid = "20261001-152000"
    d = os.path.join(mt.meetings_dir(), mid)
    os.makedirs(d)
    words = " ".join(["word"] * 200)            # about 3 words a second for a minute of speech
    with open(os.path.join(d, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump({"id": mid, "started": 1.0, "entries": [{"t": 1, "who": "You", "text": words}]}, f)
    _speech_raw(d, 60)
    mt.recover_unfinished({})
    assert not os.path.exists(os.path.join(d, "you.raw"))
    assert not [x for x in mt.list_meetings() if x["id"] == mid][0].get("incomplete")


def test_recovery_keeps_the_audio_when_keep_audio_is_on_and_skips_the_running_meeting(meeting_mod):
    mt = meeting_mod
    d = _interrupted(mt, "20261001-120000")
    d2 = _interrupted(mt, "20261001-140000")
    mt.recover_unfinished({"keep_audio": True}, skip_id="20261001-140000")
    assert os.path.exists(os.path.join(d, "you.raw"))
    assert not os.path.exists(os.path.join(d2, "meta.json"))


def test_a_folder_that_is_not_a_meeting_id_is_left_alone(meeting_mod):
    mt = meeting_mod
    d = os.path.join(mt.meetings_dir(), "notes-of-mine")
    os.makedirs(d)
    mt.recover_unfinished({})
    assert not os.path.exists(os.path.join(d, "meta.json"))


# ---- a meeting id is checked before it becomes a folder (R3-H1 / C-U7) ---------------------------------------------------

BAD_IDS = ["..", ".", "", None, 5, "../x", "20261001-120000/..", "20261001-120000\\..","2026-10-01", "20261001-12000",
           "20261001-120000 ", "x" * 15]


def _store(mt, mid="20261001-120000"):
    """A saved meeting plus a sentinel file in the data folder (config.json), which a bad id used to reach."""
    d = os.path.join(mt.meetings_dir(), mid)
    os.makedirs(d)
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"id": mid, "title": "T", "started": 1.0}, f)
    with open(os.path.join(d, "notes.md"), "w", encoding="utf-8") as f:
        f.write("# T\n\nnotes")
    sentinel = os.path.join(os.path.dirname(mt.meetings_dir()), "config.json")
    with open(sentinel, "w", encoding="utf-8") as f:
        f.write("{}")
    return d, sentinel


@pytest.mark.parametrize("bad", BAD_IDS)
def test_a_bad_meeting_id_deletes_nothing_and_writes_nothing(meeting_mod, bad):
    mt = meeting_mod
    d, sentinel = _store(mt)
    data_dir = os.path.dirname(mt.meetings_dir())
    for call in (lambda: mt.delete_meeting(bad), lambda: mt.save_my_notes(bad, "x"), lambda: mt.read_notes(bad),
                 lambda: mt.set_done(bad, 0, True), lambda: mt.rename(bad, "x"), lambda: mt.detail(bad, {})):
        with pytest.raises(ValueError):
            call()
    assert os.path.exists(sentinel) and os.path.exists(os.path.join(d, "meta.json"))
    assert not os.path.exists(os.path.join(data_dir, "my_notes.md")) and not os.path.exists(os.path.join(data_dir, "meta.json"))


def test_a_valid_meeting_id_still_works(meeting_mod):
    mt = meeting_mod
    d, sentinel = _store(mt)
    mt.save_my_notes("20261001-120000", "mine")
    assert mt.detail("20261001-120000", {})["my_notes"] == "mine"
    assert mt.rename("20261001-120000", "New") == "New"
    mt.delete_meeting("20261001-120000")
    assert not os.path.exists(d) and os.path.exists(sentinel)


def test_a_meeting_folder_that_points_outside_the_meetings_folder_is_refused(meeting_mod, tmp_path):
    mt = meeting_mod
    d, sentinel = _store(mt)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    link = os.path.join(mt.meetings_dir(), "20261002-090000")
    try:
        os.symlink(str(outside), link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no permission to create a symbolic link here")
    with pytest.raises(ValueError):
        mt.delete_meeting("20261002-090000")
    assert (outside / "keep.txt").exists()


def test_ask_skips_a_meeting_whose_id_is_not_valid(meeting_mod, monkeypatch):
    mt = meeting_mod
    _store(mt)
    d2 = os.path.join(mt.meetings_dir(), "weird")
    os.makedirs(d2)
    with open(os.path.join(d2, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"id": "..", "title": "bad", "started": 2.0}, f)
    monkeypatch.setattr(mt, "_llm", lambda *a, **k: "answer")
    assert mt.ask({}, "notes")["sources"][0]["id"] == "20261001-120000"


# ---- no audio is dropped between 100 ms blocks (R3-M1) -------------------------------------------------------------------

def test_every_sample_of_every_block_ends_up_in_a_frame_or_the_remainder(meeting_mod):
    np = pytest.importorskip("numpy")
    if not hasattr(np, "arange"):   # CI has no numpy and other tests leave a bare stub module in sys.modules
        pytest.skip("real numpy needed")
    mt = meeting_mod
    blocks = [np.arange(i * 1600, (i + 1) * 1600, dtype=np.float32) for i in range(10)]
    rest, frames = np.zeros(0, np.float32), []
    for block in blocks:
        got, rest = mt._frames(rest, block)
        assert all(len(f) == mt.FRAME for f in got) and len(rest) < mt.FRAME
        frames += got
    assert np.array_equal(np.concatenate(frames + [rest]), np.concatenate(blocks))



# ---- issue 49, Windows: a normal stop keeps audio the transcript does not cover; titles stay out of the log --------------
def _finished_meeting(mt, monkeypatch, entries, seconds, cfg=None):
    m = mt.Meeting(lambda: dict({"final_pass": False}, **(cfg or {})))
    m.id, m.started = "20261002-090000", 1759395600.0
    m.event = {"title": "Secret merger talk", "attendees": []}
    m.entries = entries
    raw = os.path.join(m.folder(), "you.raw")
    with open(raw, "wb") as f:
        f.write(b"\x01\x00" * int(16000 * seconds))
    m.sources = [FakeSource("You", raw)]
    monkeypatch.setattr(m, "_notes", lambda cfg, transcript: "# T\n\nok")
    monkeypatch.setattr(m, "attribute_speakers", lambda: None)
    return m, raw


def test_a_stop_whose_transcript_misses_most_of_the_speech_keeps_the_audio(meeting_mod, monkeypatch, tmp_path):
    mt = meeting_mod
    monkeypatch.setattr(mt, "notes_export_dir", lambda cfg: str(tmp_path))
    m, raw = _finished_meeting(mt, monkeypatch, [{"t": 5, "who": "You", "text": "hello"}], 120)
    m._finish()
    assert os.path.getsize(raw) == 120 * 32000
    meta = [x for x in mt.list_meetings() if x["id"] == m.id][0]
    assert meta["incomplete"] is True
    assert "never transcribed" in mt.read_notes(m.id)


def test_a_stop_whose_transcript_covers_the_speech_removes_the_audio(meeting_mod, monkeypatch, tmp_path):
    mt = meeting_mod
    monkeypatch.setattr(mt, "notes_export_dir", lambda cfg: str(tmp_path))
    words = " ".join(["word"] * 200)
    m, raw = _finished_meeting(mt, monkeypatch, [{"t": 5, "who": "You", "text": words}], 60)
    m._finish()
    assert not os.path.exists(raw)
    assert "incomplete" not in [x for x in mt.list_meetings() if x["id"] == m.id][0]


def test_keep_audio_still_keeps_it(meeting_mod, monkeypatch, tmp_path):
    mt = meeting_mod
    monkeypatch.setattr(mt, "notes_export_dir", lambda cfg: str(tmp_path))
    m, raw = _finished_meeting(mt, monkeypatch, [{"t": 5, "who": "You", "text": " ".join(["w"] * 200)}], 60,
                               {"keep_audio": True})
    m._finish()
    assert os.path.exists(raw)


def test_the_meeting_title_and_export_path_are_not_logged(meeting_mod, monkeypatch, tmp_path, caplog):
    import logging
    mt = meeting_mod
    monkeypatch.setattr(mt, "notes_export_dir", lambda cfg: str(tmp_path))
    m, _ = _finished_meeting(mt, monkeypatch, [{"t": 5, "who": "You", "text": " ".join(["w"] * 200)}], 60)
    with caplog.at_level(logging.INFO):
        m._finish()
    assert "Secret merger" not in caplog.text and str(tmp_path) not in caplog.text
    assert "exported" in caplog.text and m.id in caplog.text


def test_a_failed_export_logs_only_the_kind_of_error(meeting_mod, monkeypatch, caplog):
    import logging
    mt = meeting_mod

    def denied(cfg):
        raise PermissionError("C:\\Users\\me\\Documents\\Vox Notes\\Secret merger talk.md")

    monkeypatch.setattr(mt, "notes_export_dir", denied)
    m, _ = _finished_meeting(mt, monkeypatch, [{"t": 5, "who": "You", "text": " ".join(["w"] * 200)}], 60)
    with caplog.at_level(logging.INFO):
        m._finish()
    assert "Secret merger" not in caplog.text and "PermissionError" in caplog.text and "not exported" in caplog.text


def test_the_start_line_does_not_carry_the_title(meeting_mod):
    import inspect
    src = inspect.getsource(meeting_mod.Meeting._start)
    call = src.split("log.info", 1)[1].split("sc.default_microphone()", 1)[0]
    assert '"title"' not in call and "self.id" in call
