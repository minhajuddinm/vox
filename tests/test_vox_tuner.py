"""Vox Tuner (tools/vox_tuner.py) with a fake recorder and fake speech and cleanup calls: no microphone, no network,
nothing outside the test profile. The HTTP server runs in-process on 127.0.0.1."""
import json
import math
import os
import struct
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bench_cleanup  # noqa: E402
import bench_clips as clips  # noqa: E402
import bench_stt  # noqa: E402
import vox_core as core  # noqa: E402
import vox_tuner as vt  # noqa: E402


def tone(seconds=1.0):
    n = int(seconds * core.SAMPLE_RATE)
    return b"".join(struct.pack("<h", int(4000 * math.sin(2 * math.pi * 300 * i / core.SAMPLE_RATE))) for i in range(n))


class FakeRecorder:
    def __init__(self, pcm=None):
        self.pcm = tone() if pcm is None else pcm
        self.started = 0

    def start(self):
        self.started += 1

    def stop(self):
        return self.pcm


class FakeStt:
    def __init__(self, texts):
        self.texts, self.calls = dict(texts), []

    def __call__(self, cfg, pcm, model):
        self.calls.append(model)
        v = self.texts[model]
        if isinstance(v, Exception):
            raise v
        return v


def cand(text, cleaned=True, requests=1):
    def fn(cfg, raw, exe, strength):
        fn.calls.append((raw, exe, strength))
        return {"text": text.format(raw=raw), "cleaned": cleaned, "rejected": False, "error": "", "requests": requests,
                "ms": 5}
    fn.calls = []
    return fn


TURBO, LARGE = "whisper-large-v3-turbo", "whisper-large-v3"


def make(tmp_path, stt=None, recorder=None, cfg=None, **kw):
    stt = stt or FakeStt({TURBO: "um send the logs to priya", LARGE: "um send the logs to Priya please"})
    fns = dict(old=cand("Old: {raw}"), new=cand("New: {raw}"), forced=cand("Forced: {raw}"))
    fns.update(kw)
    t = vt.Tuner(str(tmp_path / "bench"), cfg or dict(core.DEFAULT_CONFIG), recorder or FakeRecorder(), stt=stt,
                 now=lambda: datetime(2026, 10, 5, 21, 0, 0), **fns)
    return t, stt, fns


def record(t):
    t.start()
    return t.stop()


# ------------------------------------------------------------------ the flow

def test_record_transcribe_clean_save_writes_a_bench_clip_and_feedback(tmp_path):
    t, stt, fns = make(tmp_path)
    record(t)
    assert os.path.exists(os.path.join(t.bench_dir, vt.PENDING_WAV))
    st = t.transcribe()
    assert stt.calls == [TURBO, LARGE]
    assert st["clip"]["transcript"] == "um send the logs to Priya please"   # they differ: large-v3 is the start
    assert [w for w, changed in st["clip"]["marks"][LARGE] if changed] == ["please"]   # case ignored, extra word marked
    st = t.clean(st["clip"]["transcript"], "ai-terminal", "windowsterminal.exe", "light")
    assert set(st["clip"]["cand"]) == {"old", "new", "forced"}
    assert fns["new"].calls == [("um send the logs to Priya please", "windowsterminal.exe", "light")]
    st = t.save("new", "", "um send the logs to Priya please", "  keep the um out  ", "ai-terminal",
                "windowsterminal.exe", "light")
    assert st["saved"] == "clip-001" and st["clip"] is None
    assert not os.path.exists(os.path.join(t.bench_dir, vt.PENDING_WAV))
    assert clips.read_pcm(clips.wav_path(t.clips_dir, "clip-001")) == FakeRecorder().pcm
    row = clips.load_manifest(t.clips_dir)[0]
    assert row["ref_verbatim"] == "um send the logs to Priya please"
    assert row["ref_intended"] == "New: um send the logs to Priya please"
    assert row["kind"] == "ai-terminal" and row["terms"] == [] and row["choice"] == "new"
    assert row["note"] == "keep the um out" and row["exe"] == "windowsterminal.exe" and row["strength"] == "light"
    assert row["stt"] == {TURBO: "um send the logs to priya", LARGE: "um send the logs to Priya please"}
    assert row["old"]["text"].startswith("Old:") and row["forced"]["text"].startswith("Forced:")
    assert vt.read_jsonl(t.feedback_path)[0]["id"] == "clip-001"
    assert t.counts()["per_scenario"]["ai-terminal"] == 1


def test_the_forced_candidate_only_runs_for_the_ai_agent_scenarios(tmp_path):
    t, _, fns = make(tmp_path)
    record(t)
    t.transcribe()
    st = t.clean("hello there how are you", "whatsapp", "whatsapp.exe", "standard")
    assert set(st["clip"]["cand"]) == {"old", "new"} and fns["forced"].calls == []
    st = t.clean("hello there how are you", "ai-editor", "code.exe", "standard")
    assert set(st["clip"]["cand"]) == {"old", "new", "forced"}


def test_identical_transcripts_start_from_turbo(tmp_path):
    t, _, _ = make(tmp_path, stt=FakeStt({TURBO: "Hello there.", LARGE: "hello there"}))
    record(t)
    assert t.transcribe()["clip"]["transcript"] == "Hello there."


def test_a_new_recording_waits_until_the_clip_is_saved_or_skipped(tmp_path):
    t, _, _ = make(tmp_path)
    record(t)
    with pytest.raises(vt.TunerError) as e:
        t.start()
    assert e.value.status == 409
    t.skip()
    assert t.clip is None and not os.path.exists(os.path.join(t.bench_dir, vt.PENDING_WAV))
    assert clips.load_manifest(t.clips_dir) == [] and vt.read_jsonl(t.feedback_path) == []
    record(t)   # free again


def test_a_too_short_recording_is_refused_and_nothing_kept(tmp_path):
    t, _, _ = make(tmp_path, recorder=FakeRecorder(tone(0.2)))
    t.start()
    with pytest.raises(vt.TunerError, match="too short"):
        t.stop()
    assert t.clip is None and not t.recording


def test_a_speech_error_keeps_the_clip_and_a_retry_only_redoes_the_failed_model(tmp_path):
    stt = FakeStt({TURBO: core.ApiError(401, "bad key sk-SECRET"), LARGE: "call the bank"})
    t, _, _ = make(tmp_path, stt=stt, cfg=dict(core.DEFAULT_CONFIG, api_key="sk-SECRET"))
    record(t)
    st = t.transcribe()
    assert "refused the API key" in st["clip"]["stt"][TURBO]["error"]
    assert "SECRET" not in json.dumps(st)
    assert st["clip"]["transcript"] == "call the bank"   # the model that worked
    stt.texts[TURBO] = "call the bank"
    t.transcribe()
    assert stt.calls == [TURBO, LARGE, TURBO]
    assert t.clip is not None


def test_plain_errors_never_show_the_key():
    import requests
    assert "refused the API key" in vt.plain_error(core.ApiError(401, "x"))
    assert "rate limit (429)" in vt.plain_error(core.ApiError(429, "slow down"))
    assert "network" in vt.plain_error(requests.ConnectionError("down"))
    assert "too long" in vt.plain_error(requests.Timeout("t"))
    assert "k-123" not in vt.plain_error(RuntimeError("boom k-123"), ["k-123"])


def test_a_rate_limit_is_waited_out_once():
    waits, calls = [], []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise core.ApiError(429, "slow", retry_after=7)
        return "ok"
    with pytest.raises(core.ApiError):
        vt.wait_429_once(fn, waits.append)()
    assert waits == [7] and len(calls) == 2
    assert vt.wait_429_once(fn, waits.append)() == "ok"


def test_resume_brings_back_the_waiting_clip_without_new_requests(tmp_path):
    t, stt, _ = make(tmp_path)
    record(t)
    t.transcribe()
    t2, stt2, _ = make(tmp_path)
    assert t2.clip is not None and t2.clip["resumed"]
    assert t2.clip["pcm"] == FakeRecorder().pcm
    t2.transcribe()
    assert stt2.calls == []   # both transcripts were kept with the clip


def test_candidates_must_match_the_transcript_app_and_strength(tmp_path):
    t, _, _ = make(tmp_path)
    record(t)
    t.transcribe()
    t.clean("send the logs", "ai-terminal", "windowsterminal.exe", "light")
    with pytest.raises(vt.TunerError) as e:
        t.save("new", "", "send the logs please", "", "ai-terminal", "windowsterminal.exe", "light")
    assert e.value.status == 409
    with pytest.raises(vt.TunerError):
        t.save("old", "", "send the logs", "", "ai-terminal", "windowsterminal.exe", "standard")
    with pytest.raises(vt.TunerError, match="program file"):
        t.save("edited", "x", "send the logs", "", "ai-terminal", "bad name", "light")
    # an own version that equals a candidate counts as that candidate
    st = t.save("edited", "Forced: send the logs", "send the logs", "", "ai-terminal", "windowsterminal.exe", "light")
    assert clips.load_manifest(t.clips_dir)[0]["choice"] == "forced"
    assert st["saved"] == "clip-001"


def test_an_edited_version_is_saved_and_nothing_is_overwritten(tmp_path):
    t, _, _ = make(tmp_path)
    os.makedirs(t.clips_dir, exist_ok=True)
    clips.write_new_wav(clips.wav_path(t.clips_dir, "clip-001"), tone(0.6))
    record(t)
    t.transcribe()
    st = t.save("edited", "Send the logs to Priya.", "send the logs to priya", "", "email", "outlook.exe", "standard")
    assert st["saved"] == "clip-002"
    row = clips.load_manifest(t.clips_dir)[0]
    assert row["choice"] == "edited" and row["intended_typed"] and row["old"] is None
    assert clips.read_pcm(clips.wav_path(t.clips_dir, "clip-001")) == tone(0.6)


def test_saved_rows_are_read_by_the_benchmark_tools(tmp_path):
    t, _, _ = make(tmp_path)
    for text in ("send the logs to priya", "call the bank at ten"):
        record(t)
        t.transcribe()
        t.save("edited", text.capitalize() + ".", text, "", "note", "notepad.exe", "light")
    rows = [r for r in clips.load_manifest(t.clips_dir) if r.get("ref_verbatim")]
    assert [r["id"] for r in rows] == ["clip-001", "clip-002"]
    setting = ("fake", {}, "prompt")
    out = bench_stt.run(t.clips_dir, [setting], transcribe=lambda cfg, pcm: "send the logs to priya", say=lambda s: None)
    assert [r["id"] for r in out["fake"]] == ["clip-001", "clip-002"]
    assert out["fake"][0]["wer"] == [0, 5]
    clips.save_stt(t.clips_dir, "clip-001", {"fake": {"text": "send the logs to priya"}})
    crow, label = bench_cleanup.clip_rows(t.clips_dir, "fake")
    assert label == "fake" and crow[0]["ref_intended"] == "Send the logs to priya." and crow[0]["kind"] == "note"


def test_summary_and_export(tmp_path):
    t, _, _ = make(tmp_path)
    record(t)
    t.transcribe()
    t.clean("send the logs", "ai-terminal", "windowsterminal.exe", "light")
    t.save("old", "", "send the logs", "too formal", "ai-terminal", "windowsterminal.exe", "light")
    record(t)
    t.transcribe()
    t.save("edited", "Hi.", "hi", "", "whatsapp", "whatsapp.exe", "light")
    s = t.summary()
    assert s["total"] == {"clips": 2, "old": 1, "new": 0, "forced": 0, "edited": 1}
    assert [x["id"] for x in s["by_scenario"]] == ["ai-terminal", "whatsapp"]
    assert s["notes"] == [{"id": "clip-001", "kind": "ai-terminal", "choice": "old", "note": "too formal"}]
    out = t.export()
    assert out["path"].endswith("tuner-feedback-20261005.md") and out["clips"] == 2
    md = open(out["path"], encoding="utf-8").read()
    assert "too formal" in md and "> Old: send the logs" in md and "| AI agent in terminal | 1 | 1 |" in md
    assert "RIFF" not in md


# ------------------------------------------------------------------ the real pipelines with a fake cleanup server

class FakeChat:
    def __init__(self, fail_first=None):
        self.systems, self.fail_first = [], fail_first

    def __call__(self, cfg, body, timeout=60, retry_timeouts=True):
        self.systems.append(body["messages"][0]["content"])
        if self.fail_first is not None:
            e, self.fail_first = self.fail_first, None
            raise e
        raw = body["messages"][1]["content"].split("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0]
        return raw[0].upper() + raw[1:] + ".", "stop"


def test_new_is_what_vox_types_and_old_uses_the_v1_prompt(monkeypatch):
    chat = FakeChat()
    monkeypatch.setattr(core, "chat_reply", chat)
    cfg = dict(core.DEFAULT_CONFIG, api_key="k")
    raw = "can you send me the report by friday"
    new = vt.new_pipeline(cfg, raw, "whatsapp.exe", "light")
    assert new["cleaned"] and new["text"].startswith("Can you send") and new["requests"] == 1
    assert "You clean up dictated text" in chat.systems[-1]
    old = vt.old_pipeline(cfg, raw, "whatsapp.exe", "light")
    assert old["cleaned"] and "You are a transcript formatter" in chat.systems[-1]
    assert core.cleanup is not None and core.fidelity_check.__name__ == "fidelity_check"   # put back


def test_terminal_new_skips_the_ai_and_forced_runs_it(monkeypatch):
    chat = FakeChat()
    monkeypatch.setattr(core, "chat_reply", chat)
    cfg = dict(core.DEFAULT_CONFIG, api_key="k")
    raw = "please refactor the login handler and add a test"
    new = vt.new_pipeline(cfg, raw, "windowsterminal.exe", "light")
    assert not new["cleaned"] and new["requests"] == 0 and chat.systems == []
    r = vt.route(cfg, raw, "windowsterminal.exe")
    assert not r["ai"] and r["code"]
    forced = vt.forced_pipeline(cfg, raw, "windowsterminal.exe", "light")
    assert forced["cleaned"] and forced["requests"] == 1 and forced["text"].startswith("Please refactor")
    fr = vt.route(vt.forced_config(cfg, "windowsterminal.exe"), "hi", "windowsterminal.exe")
    assert fr["ai"] and fr["style"] == "neutral" and not fr["code"]


def test_a_cleanup_rate_limit_is_waited_once_and_errors_are_plain(monkeypatch):
    waits = []
    monkeypatch.setattr(core, "chat_reply", FakeChat(core.ApiError(429, "slow", retry_after=3)))
    cfg = dict(core.DEFAULT_CONFIG, api_key="k")
    out = vt.new_pipeline(cfg, "can you send me the report by friday", "whatsapp.exe", "light", sleep=waits.append)
    assert out["cleaned"] and waits == [3]
    monkeypatch.setattr(core, "chat_reply", FakeChat(core.ApiError(401, "nope k")))
    out = vt.new_pipeline(cfg, "can you send me the report by friday", "whatsapp.exe", "light")
    assert not out["cleaned"] and "refused the API key" in out["error"] and out["text"]   # the rules layer still types


# ------------------------------------------------------------------ HTTP on 127.0.0.1

@pytest.fixture
def served(tmp_path):
    t, stt, fns = make(tmp_path)
    server, url, token = vt.make_server(t)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield t, base, token, server
    server.shutdown()
    server.server_close()


def call(base, path, token=None, body=None, host=None):
    req = urllib.request.Request(base + path, method="GET" if body is None else "POST",
                                 data=None if body is None else json.dumps(body).encode())
    if token:
        req.add_header(vt.TOKEN_HEADER, token)
    if host:
        req.add_header("Host", host)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_the_page_loads_and_every_api_call_needs_the_token(served):
    t, base, token, server = served
    assert server.server_address[0] == "127.0.0.1"
    code, body, headers = call(base, "/")
    assert code == 200 and b"Vox Tuner" in body and "default-src 'none'" in headers["Content-Security-Policy"]
    assert b"http://" not in body.replace(b"http://127.0.0.1", b"") and b"https://" not in body   # nothing external
    for path, b in (("/api/state", None), ("/api/record/start", {}), ("/api/save", {}), ("/api/audio", None)):
        assert call(base, path, body=b)[0] == 403
        assert call(base, path, token="wrong", body=b)[0] == 403
    assert t.recorder.started == 0
    assert call(base, "/api/state", token=token, host="evil.example:80")[0] == 403   # DNS rebinding
    code, body, _ = call(base, "/api/state", token=token)
    assert code == 200 and json.loads(body)["scenarios"][0]["id"] == "ai-terminal"


def test_the_whole_flow_over_http(served):
    t, base, token, _ = served
    assert call(base, "/api/record/start", token, {})[0] == 200
    code, body, _ = call(base, "/api/record/start", token, {})
    assert code == 200   # a second start while recording is harmless
    assert json.loads(call(base, "/api/record/stop", token, {})[1])["clip"]["seconds"] == 1.0
    code, body, headers = call(base, "/api/audio", token)
    assert code == 200 and body[:4] == b"RIFF" and headers["Content-Type"] == "audio/wav"
    st = json.loads(call(base, "/api/transcribe", token, {})[1])
    ctx = {"scenario": "ai-editor", "exe": "code.exe", "strength": "light"}
    st = json.loads(call(base, "/api/clean", token, dict(ctx, transcript=st["clip"]["transcript"]))[1])
    assert set(st["clip"]["cand"]) == {"old", "new", "forced"}
    code, body, _ = call(base, "/api/record/start", token, {})
    assert code == 409 and "Save or skip" in json.loads(body)["error"]
    code, body, _ = call(base, "/api/save", token, dict(ctx, choice="new", transcript="something else", note=""))
    assert code == 409
    st = json.loads(call(base, "/api/save", token, dict(ctx, choice="new", transcript=st["clip"]["transcript"],
                                                        note="ok"))[1])
    assert st["saved"] == "clip-001"
    assert json.loads(call(base, "/api/summary", token)[1])["total"]["new"] == 1
    path = json.loads(call(base, "/api/export", token, {})[1])["path"]
    assert os.path.exists(path)
    assert call(base, "/api/nothing", token)[0] == 404
    assert call(base, "/api/clean", token, {"transcript": "", **ctx})[0] == 409   # no clip now


def test_bad_requests_get_plain_answers(served):
    _, base, token, _ = served
    req = urllib.request.Request(base + "/api/clean", method="POST", data=b"{not json")
    req.add_header(vt.TOKEN_HEADER, token)
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 400


def test_quit_stops_the_server(tmp_path):
    t, _, _ = make(tmp_path)
    server, url, token = vt.make_server(t)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    assert url.startswith(base + "/?token=") and url.endswith(token)
    assert call(base, "/?token=" + token)[0] == 200   # the page itself, with the token in the address
    assert call(base, "/api/quit", token, {})[0] == 200
    th.join(5)
    assert not th.is_alive()
    server.server_close()


# ------------------------------------------------------------------ --demo

def test_demo_mode_runs_the_real_pipelines_with_canned_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "chat_reply", core.chat_reply)   # put back after the test
    monkeypatch.setattr(core, "_post", core._post)
    t = vt.demo_tuner(str(tmp_path / "demo"))
    record(t)
    st = t.transcribe()
    assert "log in" in st["clip"]["transcript"]
    st = t.clean(st["clip"]["transcript"], "ai-terminal", "windowsterminal.exe", "light")
    c = st["clip"]["cand"]
    assert not c["new"]["cleaned"] and c["forced"]["cleaned"] and "Windows" in c["forced"]["text"]
    st = t.save("forced", "", st["clip"]["transcript"], "", "ai-terminal", "windowsterminal.exe", "light")
    assert st["saved"] == "clip-001" and clips.load_manifest(t.clips_dir)[0]["demo"] is True
    record(t)
    assert "priya" in t.transcribe()["clip"]["transcript"]   # the next canned sentence
    with pytest.raises(core.ApiError):
        core._post("https://example.invalid")   # nothing can reach a server in demo mode
