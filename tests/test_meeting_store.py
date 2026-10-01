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
