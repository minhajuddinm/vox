"""Pure rules of windows/timing.py (the per-dictation timing core). The shared numbers (median, p90, the biggest
stage, the "1.4 s" text, stage maths, summaries) are also pinned by the timing_* rows of spec/golden.txt, which the
Android twin (Timing.java) runs too; these tests cover the behaviour around them."""
import timing
from timing import Timing


def full(**over):
    marks = dict(key_down=1000, rec_start=1040, key_up=3040, stt_start=3050, stt_done=3650, llm_start=3660,
                 llm_done=3960, inserted=3990)
    marks.update(over)
    t = Timing()
    for name, at in marks.items():
        if at is not None:
            t.mark(name, at)
    return t


def test_stages_of_a_full_dictation():
    assert full().stages() == {"start": 40, "rec": 2000, "stt": 600, "llm": 300, "insert": 30, "total": 950}


def test_stage_keys_and_order_are_fixed():
    assert list(Timing().stages()) == list(timing.STAGES) == ["start", "rec", "stt", "llm", "insert", "total"]
    assert list(timing.MARKS) == ["key_down", "rec_start", "key_up", "stt_start", "stt_done", "llm_start",
                                  "llm_done", "inserted", "seg_end", "seg_text"]


def test_keep_listening_marks_do_not_change_the_stages():
    """seg_end and seg_text time one piece of a keep-listening session (Windows only, no Android twin); they are
    kept apart from the dictation stages."""
    t = full()
    t.mark("seg_end", 5000)
    t.mark("seg_text", 5700)
    assert t.stages() == full().stages()
    assert t.get("seg_text") - t.get("seg_end") == 700


def test_skipped_cleanup_gives_llm_zero_and_insert_from_stt_done():
    st = full(llm_start=None, llm_done=None).stages()
    assert st["llm"] == 0
    assert st["insert"] == 340          # inserted (3990) - stt_done (3650)
    assert st["total"] == 950


def test_missing_marks_give_zero_not_an_error():
    st = Timing().stages()
    assert all(v == 0 for v in st.values())
    t = Timing()
    t.mark("key_up", 100)
    assert t.stages()["total"] == 0     # no inserted mark yet


def test_a_clock_that_went_backwards_never_gives_a_negative_stage():
    st = full(rec_start=900).stages()
    assert st["start"] == 0
    assert all(v >= 0 for v in st.values())


def test_unknown_mark_name_is_refused():
    t = Timing()
    try:
        t.mark("nope", 1)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_mark_uses_the_clock_when_no_time_is_given():
    ticks = iter([5.0, 7.5])            # seconds, like time.monotonic
    t = Timing(clock=lambda: next(ticks))
    t.mark("key_down")
    t.mark("rec_start")
    assert t.stages()["start"] == 2500
    assert t.get("key_down") == 5000


def test_a_later_mark_replaces_an_earlier_one():
    t = Timing()
    t.mark("stt_start", 10)
    t.mark("stt_start", 20)
    assert t.get("stt_start") == 20
    assert t.has("stt_start") and not t.has("stt_done")


def test_entry_carries_stages_and_model_names():
    e = full().entry(stt_model="whisper-large-v3-turbo", llm_model="openai/gpt-oss-20b", provider="groq", relay=True)
    assert e == {"stages": {"start": 40, "rec": 2000, "stt": 600, "llm": 300, "insert": 30, "total": 950},
                 "stt_model": "whisper-large-v3-turbo", "llm_model": "openai/gpt-oss-20b", "provider": "groq",
                 "relay": True}
    assert list(e) == ["stages", "stt_model", "llm_model", "provider", "relay"]   # the order Timing.historyMap has (Java twin)


def test_median_and_p90_basics():
    assert timing.median([]) == 0 and timing.p90([]) == 0
    assert timing.median([5]) == 5 and timing.p90([5]) == 5
    assert timing.median([9, 1, 5]) == 5
    assert timing.median([1, 2, 3, 10]) == 2          # (2 + 3) // 2
    assert timing.p90(list(range(1, 11))) == 9        # nearest rank: ceil(0.9 * 10) = 9th value
    assert timing.p90(list(range(1, 12))) == 10       # ceil(0.9 * 11) = 10th


def test_input_list_is_not_changed():
    xs = [3, 1, 2]
    timing.median(xs)
    timing.p90(xs)
    assert xs == [3, 1, 2]


def test_biggest_ignores_recording_and_total():
    assert timing.biggest({"start": 40, "rec": 9000, "stt": 600, "llm": 300, "insert": 30, "total": 5000}) == "stt"
    assert timing.biggest({"start": 0, "rec": 9000, "stt": 0, "llm": 0, "insert": 0, "total": 5000}) == ""
    assert timing.biggest({}) == ""
    assert timing.biggest({"stt": 500, "llm": 500}) == "stt"      # a tie goes to the earlier stage


def test_format_ms():
    assert timing.format_ms(0) == "0 ms"
    assert timing.format_ms(850) == "850 ms"
    assert timing.format_ms(999) == "999 ms"
    assert timing.format_ms(1000) == "1.0 s"
    assert timing.format_ms(1449) == "1.4 s"
    assert timing.format_ms(1450) == "1.5 s"
    assert timing.format_ms(12340) == "12.3 s"
    assert timing.format_ms(-5) == "0 ms"


def entry(**stages):
    base = {"start": 0, "rec": 0, "stt": 0, "llm": 0, "insert": 0, "total": 0}
    base.update(stages)
    return {"stages": base, "stt_model": "m", "llm_model": "l", "provider": "p", "relay": False}


def test_summarize_shape_and_biggest():
    entries = [entry(start=40, stt=600, llm=300, insert=30, rec=2000, total=930) for _ in range(3)]
    s = timing.summarize(entries)
    assert s["count"] == 3 and s["biggest"] == "stt"
    assert s["stt"] == {"median": 600, "p90": 600}
    assert set(timing.STAGES) <= set(s)


def test_summarize_skips_stages_that_did_not_run():
    entries = [entry(stt=500, llm=0, total=500), entry(stt=700, llm=400, total=1100), entry(stt=900, llm=0, total=900)]
    s = timing.summarize(entries)
    assert s["llm"] == {"median": 400, "p90": 400}      # the two skipped cleanups are not counted as 0 ms
    assert s["stt"]["median"] == 700


def test_summarize_uses_only_the_newest_n():
    entries = [entry(stt=10000)] * 5 + [entry(stt=100)] * 3
    s = timing.summarize(entries, n=3)
    assert s["count"] == 3 and s["stt"]["median"] == 100


def test_summarize_empty():
    s = timing.summarize([])
    assert s["count"] == 0 and s["biggest"] == "" and s["stt"] == {"median": 0, "p90": 0}


def test_summarize_tolerates_bad_entries():
    s = timing.summarize([None, {}, {"stages": "x"}, entry(stt=300)])
    assert s["count"] == 1 and s["stt"]["median"] == 300


# ---------------------------------------------------------------- per model and the Speed card's data

def mentry(stt_model, llm_model, **stages):
    e = entry(**stages)
    e["stt_model"], e["llm_model"] = stt_model, llm_model
    return e


def test_by_model_groups_by_voice_and_cleanup_model_most_used_first():
    entries = [mentry("w", "a", stt=500, llm=300, total=900), mentry("w", "b", stt=700, llm=900, total=1700),
               mentry("w", "a", stt=600, llm=500, total=1200), mentry("w", "a", stt=800, llm=0, total=800)]
    rows = timing.by_model(entries)
    assert [(r["stt_model"], r["llm_model"], r["count"]) for r in rows] == [("w", "a", 3), ("w", "b", 1)]
    assert rows[0]["stt"] == 600 and rows[0]["llm"] == 400 and rows[0]["total"] == 900   # the skipped cleanup is not a 0 ms


def test_by_model_uses_the_newest_n_and_ignores_entries_without_stages():
    entries = [mentry("old", "old", stt=1)] * 4 + [None, {"stt_model": "x"}, mentry("new", "new", stt=5)]
    rows = timing.by_model(entries, n=2)
    assert [(r["stt_model"], r["count"]) for r in rows] == [("new", 1)]


def test_by_model_ties_are_ordered_by_name_and_a_missing_model_is_empty():
    rows = timing.by_model([mentry("b", "x", stt=1), mentry("a", "x", stt=1), {"stages": {"stt": 2}}])
    assert [r["stt_model"] for r in rows] == ["", "a", "b"]


def history_row(t, words=5, **kw):
    e = mentry(kw.pop("stt_model", "w"), kw.pop("llm_model", "l"), **kw)
    return {"t": t, "app": "notepad.exe", "raw": "x", "text": "y", "words": words, "timing": e}


def test_speed_view_shape_and_last_ten_newest_first():
    hist = [{"t": 1.0, "raw": "old entry without timing"}]
    hist += [history_row(100.0 + i, stt=500 + i, llm=300, total=900) for i in range(12)]
    v = timing.speed_view(hist, n=50, last=10)
    assert v["count"] == 12 and v["biggest"] == "stt"
    assert v["stages"]["stt"]["median"] == 505 and set(v["stages"]) == set(timing.STAGES)
    assert [r["t"] for r in v["last"]] == [111.0 - i for i in range(10)]
    assert v["last"][0]["stages"]["stt"] == 511 and v["last"][0]["stt_model"] == "w" and v["last"][0]["app"] == "notepad.exe"
    assert v["models"][0]["count"] == 12


def test_speed_view_of_no_history_is_empty_not_an_error():
    v = timing.speed_view([])
    assert v == {"count": 0, "biggest": "", "stages": v["stages"], "models": [], "last": []}
    assert all(v["stages"][s] == {"median": 0, "p90": 0} for s in timing.STAGES)
    assert timing.speed_view([{"raw": "a"}, None, {"timing": "x"}])["count"] == 0


def test_the_entry_records_the_pieces_and_the_upload_format_only_when_there_is_something_to_say():
    t = timing.Timing(clock=lambda: 0)
    assert "pieces" not in t.entry() and "upload" not in t.entry()
    e = t.entry(pieces=3, upload="flac")
    assert e["pieces"] == 3 and e["upload"] == "flac"
    assert timing.summarize([e])["count"] == 1                      # the Speed card reads it as before
