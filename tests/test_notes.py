"""The voice notes store: add, edit, delete, search (FTS5 and the LIKE fallback), filters."""
import time

import pytest

import notes


@pytest.fixture(autouse=True)
def tmp_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(notes, "USE_FTS", True)


def test_add_returns_the_note_with_an_automatic_title():
    n = notes.add("  Call the dentist about the crown next Tuesday morning please  ", raw="uh call the dentist", secs=4.26, device="win")
    assert n["title"] == "Call the dentist about the crown next..."
    assert n["text"].startswith("Call the dentist") and n["secs"] == 4.3 and n["device"] == "win"
    assert notes.get(n["id"])["text"] == n["text"] and notes.count() == 1


def test_titles_tags_and_ids():
    a = notes.add("one", title="Mine", tags="Work, work , ,\"quoted\", home")
    b = notes.add("two")
    assert a["title"] == "Mine" and a["tags"] == ["Work", "work", "quoted", "home"]
    assert a["id"] != b["id"] and len(a["id"]) == 32


def test_update_changes_text_title_and_search_index():
    n = notes.add("buy oat milk")
    up = notes.update(n["id"], text="buy almond milk", title="Shopping")
    assert up["title"] == "Shopping" and up["text"] == "buy almond milk" and up["updated_at"] >= n["updated_at"]
    assert [x["id"] for x in notes.search("almond")] == [n["id"]]
    assert notes.search("oat") == []
    assert notes.update("missing", text="x") is None


@pytest.mark.parametrize("fts", [True, False])
def test_search_matches_word_starts_and_needs_every_word(fts, monkeypatch):
    monkeypatch.setattr(notes, "USE_FTS", fts)
    a = notes.add("Meeting with the design team about onboarding")
    b = notes.add("Grocery list: eggs, bread, coffee")
    assert [x["id"] for x in notes.search("onboard")] == [a["id"]]
    assert [x["id"] for x in notes.search("design onboarding")] == [a["id"]]
    assert notes.search("design coffee") == []
    assert {x["id"] for x in notes.search("")} == {a["id"], b["id"]}
    assert [x["id"] for x in notes.search("COFFEE")] == [b["id"]]


def test_search_handles_punctuation_in_the_query():
    notes.add("Say hello to Dr. O'Neil")
    assert len(notes.search('hello "O\'Neil" (dr.)')) == 1


def test_filters_by_time_source_and_tag_newest_first():
    now = time.time()
    old = notes.add("old idea", created=now - 40 * 86400, tags=["work"])
    mid = notes.add("mid idea", created=now - 3 * 86400, source="dictation")
    new = notes.add("new idea", tags=["home", "work"])
    assert [x["id"] for x in notes.search("idea")] == [new["id"], mid["id"], old["id"]]
    assert [x["id"] for x in notes.search("idea", since=now - 7 * 86400)] == [new["id"], mid["id"]]
    assert [x["id"] for x in notes.search("idea", until=now - 7 * 86400)] == [old["id"]]
    assert [x["id"] for x in notes.search("", source="dictation")] == [mid["id"]]
    assert [x["id"] for x in notes.search("idea", tag="work")] == [new["id"], old["id"]]
    assert [x["id"] for x in notes.search("", limit=1)] == [new["id"]]


def test_delete_keeps_a_marker_without_the_content():
    n = notes.add("secret plan", raw="secret plan raw", tags=["x"])
    assert notes.delete(n["id"]) is True
    assert notes.delete(n["id"]) is False
    assert notes.get(n["id"]) is None and notes.search("secret") == [] and notes.count() == 0
    with notes._connect() as con:
        r = con.execute("SELECT * FROM notes WHERE id = ?", (n["id"],)).fetchone()
    assert r["deleted"] == 1 and r["text"] == "" and r["raw"] == "" and r["title"] == ""


def test_store_survives_a_missing_fts5(monkeypatch):
    monkeypatch.setattr(notes, "_has_fts", lambda con: False)
    n = notes.add("plain like search still works")
    assert [x["id"] for x in notes.search("plain")] == [n["id"]]
    assert notes.update(n["id"], text="changed text")["text"] == "changed text"
    assert notes.delete(n["id"])


# ---- a deleted or edited note leaves no text in the file (DAT-11 / PRV-9 of the v2 review) ---------------------------

def _file_bytes():
    import os
    out = b""
    for p in (notes.db_path(), notes.db_path() + "-wal"):
        if os.path.exists(p):
            with open(p, "rb") as f:
                out += f.read()
    return out


def test_a_deleted_note_leaves_no_text_in_the_database_file():
    keep = notes.add("shopping list with apples")
    n = notes.add("my bank pin is ZQXJSECRETWORD 7781")
    assert b"ZQXJSECRETWORD" in _file_bytes()
    assert notes.delete(n["id"])
    assert notes.search("ZQXJSECRETWORD") == [] and notes.get(keep["id"])
    assert b"ZQXJSECRETWORD" not in _file_bytes()


def test_the_old_text_of_an_edited_note_is_not_kept_in_the_file():
    n = notes.add("the door code is QUOKKAZEBRA", title="Door")
    notes.update(n["id"], text="no code here")
    notes.wipe_free_space()
    assert b"QUOKKAZEBRA" not in _file_bytes()


def test_deleting_twice_or_an_unknown_note_is_false():
    n = notes.add("x")
    assert notes.delete(n["id"]) is True
    assert notes.delete(n["id"]) is False and notes.delete("0" * 32) is False
