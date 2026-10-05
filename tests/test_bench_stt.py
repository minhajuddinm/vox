"""The speech benchmark (tools/bench_stt.py) on fake clips with a fake speech call or a fake server: the cache makes a
rerun free, settings take turns, a rate limit is waited out, the key never shows. No network, no microphone."""
import array
import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bench_clips as clips   # noqa: E402
import bench_stt as stt       # noqa: E402
import vox_core as core       # noqa: E402


def voice(seconds, step=20):
    return array.array("h", [8000 if (i // step) % 2 else -8000 for i in range(int(16000 * seconds))]).tobytes()


def make_clips(folder, n=3):
    for i in range(1, n + 1):
        cid = f"clip-{i:03d}"
        clips.write_new_wav(clips.wav_path(folder, cid), voice(1, step=10 + i))
        clips.append_row(folder, {"id": cid, "audio": cid + ".wav", "kind": "chat" if i % 2 else "names",
                                  "ref_verbatim": f"hello priya number {i}", "ref_intended": f"Hello Priya, number {i}.",
                                  "terms": ["Priya"] if i == 1 else ["Ledgerly"]})


def settings(*labels_prompts):
    return [(label, dict(core.DEFAULT_CONFIG, stt_model="m"), prompt) for label, prompt in labels_prompts]


def test_run_transcribes_each_clip_once_per_setting_and_a_rerun_is_free(tmp_path):
    folder = str(tmp_path)
    make_clips(folder)
    calls = []

    def fake(cfg, pcm):
        calls.append(len(pcm))
        return "hello priya number one"
    out = stt.run(folder, settings(("A", "p1"), ("B", "p2")), fake, say=lambda t: None)
    assert len(calls) == 6 and set(out) == {"A", "B"} and [r["id"] for r in out["A"]] == ["clip-001", "clip-002", "clip-003"]
    assert out["A"][0]["wer"] == [0, 4] and out["A"][1]["wer"] == [1, 4]   # "one" = "1"; "one" for "2" is an error
    assert out["A"][0]["terms"] == 1.0 and out["A"][1]["terms"] == 0.0
    cache = clips.load_stt(folder, "clip-001")
    assert set(cache) == {"A", "B"} and cache["A"]["text"] == "hello priya number one" and len(cache["A"]["audio_sha256"]) == 64
    calls.clear()
    again = stt.run(folder, settings(("A", "p1"), ("B", "p2")), fake, say=lambda t: None)
    assert calls == [] and again["A"][0]["wer"] == out["A"][0]["wer"]
    stt.run(folder, settings(("A", "p1 changed")), fake, say=lambda t: None)   # another prompt: the cache is out of date
    assert len(calls) == 3


def test_settings_take_turns_clip_by_clip(tmp_path):
    folder = str(tmp_path)
    make_clips(folder)
    order = []
    s = [("A", dict(core.DEFAULT_CONFIG, _l="A"), ""), ("B", dict(core.DEFAULT_CONFIG, _l="B"), "")]
    stt.run(folder, s, lambda cfg, pcm: order.append(cfg["_l"]) or "x", say=lambda t: None)
    assert order == ["A", "B", "B", "A", "A", "B"]


def test_a_rate_limit_is_waited_out_and_other_errors_are_recorded(tmp_path):
    folder = str(tmp_path)
    make_clips(folder, 2)
    sleeps, tries = [], []

    def fake(cfg, pcm):
        tries.append(1)
        if len(tries) == 1:
            raise core.ApiError(429, "slow down", retry_after=5)
        if len(tries) == 3:
            raise core.ApiError(500, "down")
        return "hello"
    out = stt.run(folder, settings(("A", "")), fake, pause=1, sleep=sleeps.append, say=lambda t: None)
    assert sleeps == [5, 1] and out["A"][1]["error"] == "down"
    assert "A" not in clips.load_stt(folder, "clip-002")   # a failure is not cached: the next run tries again
    s = stt.summarize(out["A"])
    assert s["clips"] == 2 and s["errors"] == 1


def test_summarize_gives_micro_wer_per_kind_and_time_per_audio_second():
    rows = [{"id": "a", "kind": "chat", "wer": [1, 4], "terms": 1.0, "empty": False, "ms": 400, "seconds": 2, "error": ""},
            {"id": "b", "kind": "names", "wer": [3, 6], "terms": 0.0, "empty": True, "ms": 600, "seconds": 3, "error": ""}]
    s = stt.summarize(rows)
    assert s["wer"] == pytest.approx(0.4) and s["term_recall"] == 0.5 and s["empty"] == 1
    assert s["wer_by_kind"] == {"chat": 0.25, "names": 0.5} and s["ms_per_audio_second"] == 200


def test_the_clip_goes_through_the_apps_speech_path_with_the_benchmark_terms_only(monkeypatch):
    seen = []

    class Reply:
        status_code, text = 200, ""

        def json(self):
            return {"text": " hello priya "}
    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.append((url, kw)) or Reply())
    cfg = stt.stt_config(dict(core.DEFAULT_CONFIG, api_key="k", people=["MyOwnFriend"], dictionary=["x => MySecret"],
                              snippets={"addr": "my home address"}, cleanup=True), "whisper-large-v3-turbo", ["Priya"], "en")
    assert stt.transcribe_clip(cfg, voice(1)) == "hello priya"
    assert len(seen) == 1 and seen[0][0].endswith("/audio/transcriptions")   # no cleanup request
    data = seen[0][1]["data"]
    assert "Priya" in data["prompt"] and "MyOwnFriend" not in data["prompt"] and "MySecret" not in data["prompt"]
    assert data["language"] == "en" and data["model"] == "whisper-large-v3-turbo"
    assert stt.prompt_text(stt.stt_config(cfg, "m", [], "")) == ""


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    os.makedirs(tmp_path / "Vox")

    def write(**kw):
        with open(tmp_path / "Vox" / "config.json", "w", encoding="utf-8") as f:
            json.dump(dict(core.DEFAULT_CONFIG, **kw), f)
    write(api_key="SECRET-KEY-123", language="")
    return tmp_path, write


def test_main_runs_every_model_and_prompt_mode_prints_wer_and_saves_json(app, capsys):
    tmp_path, _ = app
    folder = os.path.join(str(tmp_path), "Vox", "bench", "clips")
    make_clips(folder)
    seen = []
    out = tmp_path / "s.json"
    code = stt.main(["--provider", "groq", "--compare", "m1,m2", "--prompt", "on,off", "--pause", "0", "--out", str(out)],
                    transcribe=lambda cfg, pcm: seen.append((cfg["stt_model"], tuple(cfg["people"]))) or "hello priya number 1")
    assert code == 0 and len(seen) == 12
    assert ("m1", ("Priya", "Ledgerly")) in seen and ("m2", ()) in seen
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert set(saved["settings"]) == {"api.groq.com m1 prompt-on", "api.groq.com m1 prompt-off",
                                      "api.groq.com m2 prompt-on", "api.groq.com m2 prompt-off"}
    shown = capsys.readouterr()
    assert "WER" in shown.out and "WER by kind" in shown.out and "bench_cleanup.py" in shown.err
    for text in (shown.out, shown.err, out.read_text(encoding="utf-8")):
        assert "SECRET-KEY-123" not in text


def test_main_without_clips_says_to_record_first(app, capsys):
    assert stt.main(["--pause", "0"], transcribe=lambda *a: 1 / 0) == 2
    assert "bench_record.py" in capsys.readouterr().err


def test_main_without_a_key_stops_before_any_request(app, capsys):
    tmp_path, write = app
    write(api_key="")
    make_clips(os.path.join(str(tmp_path), "Vox", "bench", "clips"), 1)
    called = []
    assert stt.main(["--pause", "0"], transcribe=lambda *a: called.append(1)) == 2 and not called
    assert "key" in capsys.readouterr().err.lower()


def test_main_refuses_an_unknown_prompt_mode(app, capsys):
    assert stt.main(["--prompt", "maybe"]) == 2


def test_main_error_texts_never_show_the_key(app, capsys):
    tmp_path, _ = app
    make_clips(os.path.join(str(tmp_path), "Vox", "bench", "clips"), 1)

    def leaky(cfg, pcm):
        raise core.ApiError(401, "bad key SECRET-KEY-123")
    assert stt.main(["--pause", "0", "--out", str(tmp_path / "o.json")], transcribe=leaky) == 1
    shown = capsys.readouterr()
    assert "SECRET-KEY-123" not in shown.out + shown.err + (tmp_path / "o.json").read_text(encoding="utf-8")
