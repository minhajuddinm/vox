"""Smaller uploads (task B4): FLAC when the optional soundfile package loads and the speech server is known to read it,
WAV otherwise. The choice is pure; the encoding test runs only where soundfile is installed (skipped in CI)."""
import array
import io
import math

import pytest

import vox_core as core

GROQ, OPENAI = "https://api.groq.com/openai/v1", "https://api.openai.com/v1"


@pytest.mark.parametrize("setting,base,relay,flac_ok,expected", [
    ("auto", GROQ, False, True, "flac"),
    ("auto", OPENAI, False, True, "flac"),
    ("auto", "https://API.GROQ.COM/openai/v1", False, True, "flac"),
    ("auto", GROQ, False, False, "wav"),                  # soundfile missing: as before
    ("auto", GROQ, True, True, "wav"),                    # the relay forwards to a server it chooses
    ("auto", "http://localhost:8000/v1", False, True, "wav"),   # a local server may not read FLAC (and is fast)
    ("auto", "https://api.together.xyz/v1", False, True, "wav"),
    ("wav", GROQ, False, True, "wav"),
    ("anything", GROQ, False, True, "flac"),              # an unknown value means auto
    ("auto", "", False, True, "wav"),
])
def test_the_choice_rule(setting, base, relay, flac_ok, expected):
    assert core.choose_upload_format(setting, base, relay, flac_ok) == expected


def test_the_setting_wav_never_even_looks_for_soundfile(monkeypatch):
    monkeypatch.setattr(core, "flac_available", lambda: (_ for _ in ()).throw(AssertionError("looked")))
    assert core.upload_format({"upload_format": "wav"}) == "wav"


def test_the_default_config_says_auto_and_groq_gets_flac_when_it_can(monkeypatch):
    assert core.DEFAULT_CONFIG["upload_format"] == "auto"
    monkeypatch.setattr(core, "flac_available", lambda: True)
    assert core.upload_format({"api_key": "k"}) == "flac"
    monkeypatch.setattr(core, "flac_available", lambda: False)
    assert core.upload_format({"api_key": "k"}) == "wav"


def test_a_failed_flac_encoding_sends_wav(monkeypatch):
    monkeypatch.setattr(core, "upload_format", lambda cfg: "flac")

    def broken(pcm):
        raise RuntimeError("libsndfile")

    monkeypatch.setattr(core, "pcm_to_flac", broken)
    assert core.upload_audio({}, b"\x00\x00" * 100)[:4] == b"RIFF"


def test_the_upload_is_named_by_its_format(monkeypatch):
    seen = []

    class R:
        status_code = 200

        def json(self):
            return {"text": "hi"}

    monkeypatch.setattr(core.requests, "post", lambda url, **kw: seen.append(kw["files"]["file"]) or R())
    core.transcribe({"api_key": "k"}, b"fLaC....")
    core.transcribe({"api_key": "k"}, b"RIFF....")
    assert [(n, m) for n, _, m in seen] == [("audio.flac", "audio/flac"), ("audio.wav", "audio/wav")]


def test_every_upload_of_a_dictation_goes_through_the_chosen_format(monkeypatch):
    monkeypatch.setattr(core, "upload_audio", lambda cfg, pcm: b"fLaC" + bytes(len(pcm) // 4))
    sizes = []
    monkeypatch.setattr(core, "transcribe", lambda cfg, audio, context="": sizes.append(audio[:4]) or "words")
    core.process_detailed({"cleanup": False}, b"\x10\x27" * 16000, "", "")
    assert sizes == [b"fLaC"]


def test_the_18_minute_fallback_still_sends_pieces_below_the_limit(monkeypatch):
    """Hands-free allows 18 minutes; when streaming fails the whole recording is cut into pieces (R2-M9)."""
    sizes = []
    monkeypatch.setattr(core, "flac_available", lambda: False)
    monkeypatch.setattr(core, "transcribe", lambda cfg, audio, context="": sizes.append(len(audio)) or "word")
    loud = array.array("h", (int(8000 * math.sin(i * 0.3)) for i in range(16000))).tobytes()
    pcm = loud * (18 * 60)   # 18 minutes, about 35 MB
    core.process_detailed({"cleanup": False}, pcm, "", "")
    assert len(sizes) > 1 and all(s < core.MAX_UPLOAD_BYTES for s in sizes)


def test_flac_is_smaller_and_lossless_when_soundfile_is_installed():
    sf = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    if not core.flac_available():
        pytest.skip("soundfile cannot write FLAC here")
    pcm = array.array("h", (int(6000 * math.sin(i * 0.05)) for i in range(16000 * 3))).tobytes()
    flac = core.pcm_to_flac(pcm)
    assert flac[:4] == b"fLaC" and len(flac) < len(core.pcm_to_wav(pcm)) * 0.7
    data, rate = sf.read(io.BytesIO(flac), dtype="int16")
    assert rate == core.SAMPLE_RATE and np.array_equal(data, np.frombuffer(pcm, dtype="<i2"))
