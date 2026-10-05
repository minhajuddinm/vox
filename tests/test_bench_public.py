"""The public-sample downloader (tools/bench_public.py) against a fake HTTP server on 127.0.0.1 with generated audio:
the three ways of fetching (dataset viewer rows, single files of a remote zip by range requests, the start of a
.tar.gz stream), the conversion to 16 kHz mono 16-bit, the manifest and licence file, a rerun that skips what is there,
the pacing and rate-limit wait, and the folder working with bench_stt / bench_cleanup. No network."""
import array
import gzip
import io
import json
import math
import os
import sys
import tarfile
import threading
import wave
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bench_clips as clips     # noqa: E402
import bench_cleanup as bench   # noqa: E402
import bench_public as bp       # noqa: E402
import bench_stt as stt         # noqa: E402


# ---- generated audio ----------------------------------------------------------------------------------------------
def tone(freq, rate, seconds, amp=0.5, channels=1, width=2):
    n = int(rate * seconds)
    vals = [amp * math.sin(2 * math.pi * freq * i / rate) for i in range(n)]
    frames = bytearray()
    for v in vals:
        for _ in range(channels):
            if width == 1:
                frames.append(int(round(v * 127)) + 128)
            else:
                frames += int(round(v * ((1 << (8 * width - 1)) - 1))).to_bytes(width, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def pcm_values(pcm):
    a = array.array("h")
    a.frombytes(pcm)
    return [v / 32768 for v in a]


def rms(xs):
    return math.sqrt(sum(v * v for v in xs) / len(xs)) if xs else 0.0


# ---- a fake server: static files with Range, a dataset viewer /rows, scripted failures ------------------------------
class FakeServer:
    def __init__(self):
        self.files, self.rows, self.hits, self.fail = {}, [], [], {}
        self.ranges = True
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlparse(self.path)
                rng = self.headers.get("Range")
                server.hits.append((u.path, rng))
                if server.fail.get(u.path):
                    code = server.fail[u.path].pop(0)
                    if not server.fail[u.path]:
                        del server.fail[u.path]
                    self.send_response(code)
                    self.send_header("Retry-After", "2")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if u.path == "/rows":
                    q = parse_qs(u.query)
                    off, n = int(q["offset"][0]), int(q["length"][0])
                    body = json.dumps({"num_rows_total": len(server.rows), "rows": [
                        {"row_idx": i, "row": r} for i, r in enumerate(server.rows) if off <= i < off + n]}).encode()
                    return self._send(200, body)
                if u.path not in server.files:
                    return self._send(404, b"")
                data = server.files[u.path]
                if rng and server.ranges:
                    a, _, b = rng.replace("bytes=", "").partition("-")
                    a, b = int(a), min(int(b), len(data) - 1)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
                    self.send_header("Content-Length", str(b - a + 1))
                    self.end_headers()
                    self.wfile.write(data[a:b + 1])
                    return
                self._send(200, data)

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    s = FakeServer()
    yield s
    s.close()


def http(waits=None):
    return bp.Http(pause=0, sleep=(waits.append if waits is not None else lambda s: None))


def quiet(t):
    pass


def hf_source(server, **kw):
    return dict({"id": "fake-rows", "name": "Fake rows", "kind": "indian-english", "lang": "en-IN", "licence": "CC BY 4.0",
                 "licence_url": "https://creativecommons.org/licenses/by/4.0/", "url": "https://example.org/fake",
                 "size": "tiny", "transcripts": "verbatim", "about": "A fake set.", "citation": "Nobody, 2026",
                 "method": "hf-rows", "rows_url": server.url + "/rows", "dataset": "x/y", "config": "default",
                 "split": "test", "verbatim": "text", "intended": "clean", "speaker": "speaker_id", "per_speaker": 1}, **kw)


def add_rows(server, n, rate=22050):
    for i in range(n):
        path = f"/audio/{i}.wav"
        server.files[path] = tone(300 + 10 * i, rate, 0.6)
        server.rows.append({"audio": [{"src": server.url + path, "type": "audio/wav"}],
                            "text": f"um so this is row number {i} okay", "clean": f"So this is row number {i}.",
                            "speaker_id": 1000 + i, "date_of_birth": "1990-01-01", "income": "secret"})


# ---- audio conversion ---------------------------------------------------------------------------------------------
def test_16k_mono_16bit_passes_through_unchanged():
    wav = tone(440, 16000, 0.2)
    with wave.open(io.BytesIO(wav)) as w:
        raw = w.readframes(w.getnframes())
    assert bp.to_pcm16k(wav) == raw


@pytest.mark.parametrize("rate,channels,width", [(44100, 2, 2), (22050, 1, 2), (48000, 1, 3), (8000, 1, 1)])
def test_other_formats_become_16k_mono_16bit_with_the_same_tone(rate, channels, width):
    pcm = bp.to_pcm16k(tone(440, rate, 0.5, channels=channels, width=width))
    got = pcm_values(pcm)
    assert abs(len(got) - 8000) <= 1
    ideal = [0.5 * math.sin(2 * math.pi * 440 * i / 16000) for i in range(len(got))]
    mid = slice(800, len(got) - 800)   # the filter's edges aside
    err = rms([a - b for a, b in zip(got[mid], ideal[mid])])
    assert err < 0.02, err


def test_a_tone_above_the_new_nyquist_is_filtered_out_not_aliased():
    got = pcm_values(bp.to_pcm16k(tone(10000, 44100, 0.3)))
    assert rms(got[400:-400]) < 0.01   # the input's rms is 0.35


def test_plain_python_resampler_matches_numpy(monkeypatch):
    if bp.np is None:
        pytest.skip("numpy not installed")
    x = [math.sin(i / 7) for i in range(441)]
    fast = bp.resample(x, 44100, 16000)
    monkeypatch.setattr(bp, "np", None)
    slow = bp.resample(x, 44100, 16000)
    assert len(fast) == len(slow) == 160 and max(abs(a - b) for a, b in zip(fast, slow)) < 1e-9


def test_plain_python_resampler_alone(monkeypatch):
    monkeypatch.setattr(bp, "np", None)
    got = pcm_values(bp.to_pcm16k(tone(440, 22050, 0.2)))
    ideal = [0.5 * math.sin(2 * math.pi * 440 * i / 16000) for i in range(len(got))]
    assert abs(len(got) - 3200) <= 1 and rms([a - b for a, b in zip(got[400:-400], ideal[400:-400])]) < 0.02


def float_wav(freq, rate, seconds, extensible=False):
    """An IEEE float 32-bit WAV (FLEURS ships these), plain format 3 or WAVE_FORMAT_EXTENSIBLE."""
    data = array.array("f", [0.5 * math.sin(2 * math.pi * freq * i / rate) for i in range(int(rate * seconds))]).tobytes()
    if extensible:
        fmt = ((0xFFFE).to_bytes(2, "little") + (1).to_bytes(2, "little") + rate.to_bytes(4, "little")
               + (rate * 4).to_bytes(4, "little") + (4).to_bytes(2, "little") + (32).to_bytes(2, "little")
               + (22).to_bytes(2, "little") + (32).to_bytes(2, "little") + (4).to_bytes(4, "little")
               + (3).to_bytes(2, "little") + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71")
    else:
        fmt = ((3).to_bytes(2, "little") + (1).to_bytes(2, "little") + rate.to_bytes(4, "little")
               + (rate * 4).to_bytes(4, "little") + (4).to_bytes(2, "little") + (32).to_bytes(2, "little"))
    body = b"WAVE" + b"fmt " + len(fmt).to_bytes(4, "little") + fmt + b"LIST" + (3).to_bytes(4, "little") + b"abc\x00" \
        + b"data" + len(data).to_bytes(4, "little") + data
    return b"RIFF" + len(body).to_bytes(4, "little") + body


@pytest.mark.parametrize("extensible", [False, True])
def test_float_wav_is_read_too(extensible):
    got = pcm_values(bp.to_pcm16k(float_wav(440, 16000, 0.3, extensible)))
    ideal = [0.5 * math.sin(2 * math.pi * 440 * i / 16000) for i in range(len(got))]
    assert len(got) == 4800 and rms([a - b for a, b in zip(got, ideal)]) < 0.001
    assert abs(len(pcm_values(bp.to_pcm16k(float_wav(440, 22050, 0.3)))) - 4800) <= 1


def test_not_a_pcm_wav_is_a_value_error():
    with pytest.raises(ValueError):
        bp.to_pcm16k(b"ID3\x03 an mp3 file")
    with pytest.raises(ValueError):
        bp.to_pcm16k(b"")


# ---- small helpers -----------------------------------------------------------------------------------------------
def test_text_speaker_and_order_helpers():
    assert bp.norm_text("  Yeah. <laughter>  well [noise] ok\n") == "Yeah. well ok"
    assert bp.norm_text("e\u0301") == "\u00e9"   # NFC
    a = bp.anon_speaker("monsoon-en-in", 94130244)
    assert a == bp.anon_speaker("monsoon-en-in", 94130244) and a.startswith("monsoon-") and "94130244" not in a
    assert a != bp.anon_speaker("monsoon-en-in", 94130245) and bp.anon_speaker("x", None) == ""
    assert bp.code_switched("मुझे अमृतसर बहुत पसंद आया था because") and not bp.code_switched("only english here")
    for n in (1, 2, 7, 106):
        order = bp.spread_order(n)
        assert sorted(order) == list(range(n))
    assert bp.spread_order(106)[1] > 30   # the second page read is far from the first


def test_every_built_in_source_is_complete_and_list_shows_it(tmp_path, capsys):
    need = {"id", "name", "kind", "lang", "licence", "licence_url", "url", "size", "transcripts", "about", "citation",
            "method"}
    assert len(bp.SOURCES) >= 3
    for s in bp.SOURCES:
        assert need <= set(s) and s["method"] in bp.FETCHERS and s["url"].startswith("https://")
    assert bp.main(["--list", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    for s in bp.SOURCES:
        assert s["id"] in out and s["licence"] in out
    assert bp.main(["--source", "nope", "--root", str(tmp_path)]) == 2
    assert bp.main(["--source", "fleurs-en", "--count", "0", "--root", str(tmp_path)]) == 2


# ---- HTTP ----------------------------------------------------------------------------------------------------------
def test_rate_limit_and_server_trouble_are_waited_out(server):
    server.files["/a"] = b"hello"
    server.fail["/a"] = [429, 503]
    waits = []
    assert http(waits).get(server.url + "/a") == b"hello"
    assert waits == [2.0, 2.0]   # Retry-After of the fake server
    server.fail["/b"] = [404]
    with pytest.raises(bp.urllib.error.HTTPError):
        http().get(server.url + "/b")


def test_requests_are_paced_and_only_http_links_are_opened(server):
    server.files["/a"] = b"x"
    waits, now = [], [100.0]
    h = bp.Http(pause=1.5, sleep=waits.append, clock=lambda: now[0])
    h.get(server.url + "/a")
    now[0] += 0.5
    h.get(server.url + "/a")
    assert waits == [1.0]
    with pytest.raises(ValueError):
        h.get("file:///C:/Windows/win.ini")
    with pytest.raises(ValueError):
        h.get(server.url + "/a", limit=0)   # bigger than the limit


# ---- dataset viewer rows -------------------------------------------------------------------------------------------
def test_rows_source_fills_a_clips_folder_and_a_rerun_only_adds(server, tmp_path):
    add_rows(server, 30)
    server.rows[3]["text"] = "no experience"                  # too few words: passed over
    server.rows[5]["speaker_id"] = server.rows[4]["speaker_id"]   # one clip per speaker
    src = hf_source(server)
    got = bp.fetch(src, str(tmp_path), 5, http(), say=quiet)
    assert got == {"have": 5, "added": 5, "skipped": 0}
    folder = tmp_path / "fake-rows"
    rows = clips.load_manifest(str(folder))
    assert [r["id"] for r in rows] == ["clip-001", "clip-002", "clip-003", "clip-004", "clip-005"]
    r = rows[0]
    assert r["ref_verbatim"].startswith("um so this is row number") and r["ref_intended"].startswith("So this is row")
    assert r["kind"] == "indian-english" and r["source"] == "fake-rows" and r["licence"] == "CC BY 4.0"
    assert r["terms"] == [] and r["lang"] == "en-IN" and r["source_ref"].startswith("test/") and r["seconds"] == 0.6
    assert r["speaker"].startswith("fake-") and all(str(1000 + i) not in r["speaker"] for i in range(30))
    text = (folder / "manifest.jsonl").read_text(encoding="utf-8")
    assert "date_of_birth" not in text and "secret" not in text and "1990" not in text
    for row in rows:
        pcm = clips.read_pcm(clips.wav_path(str(folder), row["id"]))   # 16 kHz mono 16-bit, else ValueError
        assert abs(clips.seconds(pcm) - 0.6) < 0.01
    lic = (folder / "LICENSE.txt").read_text(encoding="utf-8")
    assert "CC BY 4.0" in lic and "https://example.org/fake" in lic and "Nobody, 2026" in lic
    refs = {r["source_ref"] for r in rows}
    assert "test/3" not in refs and not {"test/4", "test/5"} <= refs

    audio_hits = [p for p, _ in server.hits if p.startswith("/audio/")]
    assert len(audio_hits) == 5   # only the clips taken were downloaded
    server.hits.clear()
    got = bp.fetch(src, str(tmp_path), 5, http(), say=quiet)
    assert got["added"] == 0 and server.hits == []   # all there: nothing asked
    got = bp.fetch(src, str(tmp_path), 8, http(), say=quiet)
    rows2 = clips.load_manifest(str(folder))
    assert got == {"have": 8, "added": 3, "skipped": 0} and len(rows2) == 8
    assert len({r["source_ref"] for r in rows2}) == 8
    assert not any(p.startswith("/audio/") and f"test/{p[7:-4]}" in refs for p, _ in server.hits)


def test_rows_come_from_pages_spread_over_the_split(server, tmp_path):
    add_rows(server, 60)   # 3 pages of 20
    bp.fetch(hf_source(server, per_page=2), str(tmp_path), 6, http(), say=quiet)
    refs = [r["source_ref"] for r in clips.load_manifest(str(tmp_path / "fake-rows"))]
    assert refs == ["test/0", "test/1", "test/40", "test/41", "test/20", "test/21"]


def test_a_broken_clip_is_skipped_and_too_long_ones_are_left_out(server, tmp_path):
    add_rows(server, 6)
    server.files["/audio/1.wav"] = b"not audio"
    server.files["/audio/2.wav"] = tone(300, 16000, 3.0)
    del server.files["/audio/3.wav"]   # 404
    got = bp.fetch(hf_source(server), str(tmp_path), 10, http(), max_seconds=2.0, say=quiet)
    assert got["have"] == 3 and got["skipped"] == 3


def test_a_source_whose_samples_cannot_be_read_is_given_up_early(server, tmp_path):
    add_rows(server, 30)
    for i in range(30):
        server.files[f"/audio/{i}.wav"] = b"RIFF....WAVEmp3!"
    got = bp.fetch(hf_source(server, per_speaker=None), str(tmp_path), 5, http(), say=quiet)
    assert got["have"] == 0 and got["skipped"] == bp.MAX_FAILURES
    assert len([p for p, _ in server.hits if p.startswith("/audio/")]) == bp.MAX_FAILURES


def test_the_size_limit_stops_a_run(server, tmp_path):
    add_rows(server, 10)
    got = bp.fetch(hf_source(server), str(tmp_path), 10, http(), budget=40_000, say=quiet)
    assert 1 <= got["have"] < 10


# ---- a remote zip read by range requests ---------------------------------------------------------------------------
def make_zip():
    buf = io.BytesIO()
    lines = ["AD01001.wav, मुझे अमृतसर बहुत पसंद आया था because", "AD01002.wav, the picture is a scenic view of hills",
             "AD02001.wav, food के बारे में तो basically मैं बिरयानी पसंद करता हूं", "AD02002.wav, and",
             "AD03001.wav, हम लोगो ने खाई and all that again", "AD09999.wav, a line whose audio is missing here"]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("Corpus/readme.txt", "readme")
        z.writestr("Corpus/adult/transcription/test.txt", "\n".join(lines) + "\n")
        for name in ("AD01001", "AD01002", "AD02001", "AD02002", "AD03001"):
            z.writestr(f"Corpus/adult/audio/test_split/{name}.wav", tone(220, 16000, 0.8))
        z.writestr("Corpus/children/big.bin", os.urandom(2_000_000))   # never read
    return buf.getvalue()


def zip_source(server):
    return dict(hf_source(server), id="fake-zip", kind="hinglish", lang="hi-en", method="zip-range",
                zip_url=server.url + "/Corpus.zip", audio_dir="Corpus/adult/audio/test_split/",
                transcript_file="Corpus/adult/transcription/test.txt", per_speaker=3)


def test_zip_source_reads_single_files_by_range_code_switched_first(server, tmp_path):
    server.files["/Corpus.zip"] = make_zip()
    got = bp.fetch(zip_source(server), str(tmp_path), 3, http(), say=quiet)
    assert got["have"] == 3
    rows = clips.load_manifest(str(tmp_path / "fake-zip"))
    assert [r["source_ref"] for r in rows] == ["AD01001.wav", "AD02001.wav", "AD03001.wav"]   # mixed, speakers in turn
    assert rows[0]["ref_verbatim"] == rows[0]["ref_intended"] == "मुझे अमृतसर बहुत पसंद आया था because"
    assert rows[0]["kind"] == "hinglish" and rows[0]["speaker"] != rows[1]["speaker"]
    zip_hits = [rng for p, rng in server.hits if p == "/Corpus.zip"]
    assert zip_hits and all(rng for rng in zip_hits)   # every read was a range request
    served = sum(int(r.split("-")[1]) - int(r.split("=")[1].split("-")[0]) + 1 for r in zip_hits)
    assert served < len(server.files["/Corpus.zip"]) / 2   # the 2 MB member was never read
    got = bp.fetch(zip_source(server), str(tmp_path), 10, http(), say=quiet)
    refs = [r["source_ref"] for r in clips.load_manifest(str(tmp_path / "fake-zip"))]
    assert got["have"] == 4 and refs[3] == "AD01002.wav"   # "and" is too short; AD09999 has no audio


def test_zip_source_needs_range_requests(server, tmp_path):
    server.files["/Corpus.zip"] = make_zip()
    server.ranges = False
    with pytest.raises(ValueError):
        bp.fetch(zip_source(server), str(tmp_path), 3, http(), say=quiet)


# ---- the start of a tar.gz stream ----------------------------------------------------------------------------------
def test_tar_source_stops_early_and_takes_one_clip_per_sentence(server, tmp_path):
    tsv = ["10\ta.wav\tHello, Priya.\thello priya how are you\t1\tF", "10\tb.wav\tHello, Priya.\thello priya how are you\t1\tM",
           "11\tc.wav\tThe meeting is at five.\tthe meeting is at five\t1\tF", "12\td.wav\tSee you then.\tsee you then ok\t1\tF"]
    server.files["/test.tsv"] = ("\n".join(tsv) + "\n").encode()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for name in ("a", "b", "c", "d"):
            data = tone(300, 16000, 0.7)
            info = tarfile.TarInfo(f"test/{name}.wav")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        pad = os.urandom(2_000_000)   # a long tail the stream never reaches
        info = tarfile.TarInfo("test/zz.wav")
        info.size = len(pad)
        t.addfile(info, io.BytesIO(pad))
    server.files["/test.tar.gz"] = gzip.compress(raw.getvalue(), compresslevel=1)
    src = dict(hf_source(server), id="fake-tar", kind="read", lang="en", method="tar-stream",
               tsv_url=server.url + "/test.tsv", tar_url=server.url + "/test.tar.gz", per_speaker=None)
    h = http()
    got = bp.fetch(src, str(tmp_path), 2, h, say=quiet)
    rows = clips.load_manifest(str(tmp_path / "fake-tar"))
    assert got["have"] == 2 and [r["source_ref"] for r in rows] == ["test/a.wav", "test/c.wav"]
    assert rows[0]["ref_verbatim"] == "hello priya how are you" and rows[0]["ref_intended"] == "Hello, Priya."
    assert rows[0]["speaker"] == ""


# ---- the whole command, and the folder in the benchmark ------------------------------------------------------------
def test_main_writes_under_appdata_and_bench_stt_and_cleanup_read_the_folder(server, monkeypatch, capsys):
    add_rows(server, 4)
    monkeypatch.setattr(bp, "SOURCES", (hf_source(server),))
    assert bp.main(["--source", "all", "--count", "3"], http=http()) == 0
    folder = os.path.join(os.environ["APPDATA"], "Vox", "bench", "public", "fake-rows")
    assert len(clips.load_manifest(folder)) == 3 and os.path.exists(os.path.join(folder, "LICENSE.txt"))
    assert "bench_stt.py --folder" in capsys.readouterr().err

    out = stt.run(folder, [("fake stt", {"stt_model": "m"}, "")], lambda cfg, pcm: "so this is row number 0 okay",
                  say=quiet)
    res = out["fake stt"]
    assert len(res) == 3 and all(not r["error"] for r in res) and {r["kind"] for r in res} == {"indian-english"}
    rows, label = bench.clip_rows(folder)
    assert label == "fake stt" and len(rows) == 3 and rows[0]["ref_intended"].startswith("So this is row number")


def test_main_reports_a_failed_source_and_keeps_going(server, monkeypatch, tmp_path):
    add_rows(server, 2)
    broken = dict(hf_source(server), id="broken", rows_url=server.url + "/missing")
    monkeypatch.setattr(bp, "SOURCES", (broken, hf_source(server)))
    assert bp.main(["--source", "broken,fake-rows", "--count", "2", "--root", str(tmp_path)], http=http()) == 1
    assert len(clips.load_manifest(str(tmp_path / "fake-rows"))) == 2
