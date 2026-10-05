"""Speech-to-text basics (cleanup-quality stream 5): the silent start and end of a recording are not sent, Whisper's
made-up segments are dropped, an answer that only repeats the prompt is dropped, and the paragraph breaks still land
where the speaker paused. The rules themselves are pinned by the golden rows edgetrim, sttseg, sttkept and echo."""
import array
import builtins
import io
import math
import os
import wave

import pytest

import streaming
import vox_core as core

RATE = core.SAMPLE_RATE
FRAME_BYTES = core.Segmenter.FRAME * 2


def tone(seconds, amp=8000):
    n = int(seconds * RATE)
    return array.array("h", (int(amp * math.sin(i * 0.3)) for i in range(n))).tobytes()


def silence(seconds):
    return b"\x00\x00" * int(seconds * RATE)


def pcm_of(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return w.readframes(w.getnframes())


def _cfg(**kw):
    return dict(core.DEFAULT_CONFIG, api_key="k", upload_format="wav", **kw)


class _Answer:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


# ---------------------------------------------------------------- the trim itself
def test_frame_peaks_are_the_same_with_and_without_numpy(monkeypatch):
    audio = silence(0.3) + tone(0.5) + b"\x00\x80" * 7 + tone(0.011, amp=700)   # -32768 and a short last frame
    with_np = core.frame_peaks(audio)
    real_import = builtins.__import__

    def no_numpy(name, *a, **kw):
        if name == "numpy":
            raise ImportError(name)
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_numpy)
    assert core.frame_peaks(audio) == with_np
    assert len(with_np) == -(-len(audio) // FRAME_BYTES) and 32768 in with_np


def test_the_silent_edges_go_and_about_300_ms_of_quiet_stays():
    audio = silence(2) + tone(1) + silence(0.8) + tone(1) + silence(3)
    out, head = core.trim_edges(audio)
    pad = core.TRIM_PAD_FRAMES * FRAME_BYTES
    assert 0.28 <= pad / (RATE * 2) <= 0.32
    assert head == pytest.approx(2.0 - pad / (RATE * 2), abs=0.031)
    assert audio[int(head * RATE * 2):][:len(out)] == out
    assert silence(0.8) in out                                   # the pause inside is untouched
    assert len(out) == pytest.approx(len(tone(1) + silence(0.8) + tone(1)) + 2 * pad, abs=2 * FRAME_BYTES)


def test_a_quiet_first_and_last_word_are_sent():
    # cqf: a soft "so" (peak 500, under SILENCE_PEAK) 300 ms before the speech and a soft "thanks" (600) after a pause
    soft_in, soft_out = tone(0.24, amp=500), tone(0.3, amp=600)
    audio = silence(1) + soft_in + silence(0.3) + tone(1) + silence(0.24) + soft_out + silence(1.5)
    out, head = core.trim_edges(audio)
    assert soft_in in out and soft_out in out and head > 0 and len(out) < len(audio)   # the silence beyond still goes
    room = tone(1, amp=250)                                     # room noise under TRIM_SOFT_PEAK is still cut
    out, head = core.trim_edges(room + tone(1) + room)
    assert head > 0.5 and len(out) < len(room + tone(1))


@pytest.mark.parametrize("audio", [silence(3), silence(1) + tone(0.03) + silence(1), b"", b"\x01"],
                         ids=["silence", "a click", "empty", "one byte"])
def test_a_recording_is_never_trimmed_to_nothing(audio):
    assert core.trim_edges(audio) == (audio, 0.0)


def test_only_the_edge_asked_for_is_cut():
    audio = silence(2) + tone(1) + silence(2)
    lead, h1 = core.trim_edges(audio, lead=True, tail=False)
    tail, h2 = core.trim_edges(audio, lead=False, tail=True)
    assert lead.endswith(silence(2)) and h1 > 1.6 and audio.endswith(lead)
    assert tail.startswith(silence(1.7)) and h2 == 0.0 and audio.startswith(tail) and len(tail) < len(audio)


# ---------------------------------------------------------------- the whole-recording upload
def test_process_detailed_sends_the_trimmed_audio_and_moves_the_segment_times_back(monkeypatch):
    sent = []
    a = "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty."
    b = "Twenty one twenty two twenty three twenty four twenty five twenty six twenty seven twenty eight twenty nine thirty one more."
    c = "Thirty two thirty three thirty four thirty five thirty six thirty seven thirty eight thirty nine forty forty one forty two."
    segs = [{"start": 0.2, "end": 6, "text": a}, {"start": 7.5, "end": 13, "text": b}, {"start": 13.4, "end": 19, "text": c}]

    def post(url, **kw):
        sent.append(pcm_of(kw["files"]["file"][1]))
        return _Answer({"text": " ".join([a, b, c]), "segments": segs})
    monkeypatch.setattr(core, "post_with_retry", post)
    audio = silence(3) + tone(19) + silence(2)
    r = core.process_detailed(_cfg(cleanup=False), audio, "x.exe", "x")
    assert len(sent[0]) < len(audio) - 4 * RATE * 2                  # about 4.6 s of silence not sent
    assert r.text == a + "\n\n" + b + " " + c                         # the paragraph break is still at the pause
    pad = core.TRIM_PAD_FRAMES * 0.03
    head = (len(audio) - len(sent[0]) - (2 - pad) * RATE * 2) / (RATE * 2)
    assert head == pytest.approx(3 - pad, abs=0.04)


def test_process_detailed_hands_on_segment_times_of_the_whole_recording(monkeypatch):
    seen = {}
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: _Answer(
        {"text": "hi", "segments": [{"start": core.TRIM_PAD_FRAMES * 0.03, "end": 1.0, "text": "hi"}]}))
    monkeypatch.setattr(core, "process_text", lambda cfg, raw, exe, label, segments=None: seen.setdefault("s", segments))
    core.process_detailed(_cfg(), silence(3) + tone(1) + silence(1), "x.exe", "x")
    assert seen["s"][0]["start"] == pytest.approx(3.0, abs=0.031)   # where the speech starts in the recording


def test_a_silent_recording_goes_up_whole_and_the_gate_decides_as_before(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: sent.append(pcm_of(kw["files"]["file"][1])) or _Answer({"text": ""}))
    audio = silence(1) + tone(0.03) + silence(1)                      # a click: not speech, nothing to trim
    core.process_detailed(_cfg(), audio, "x.exe", "x")
    assert sent == [audio]


def test_transcribe_rest_trims_only_the_end(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: sent.append(pcm_of(kw["files"]["file"][1])) or _Answer({"text": "rest"}))
    audio = silence(1) + tone(1) + silence(2)
    assert core.transcribe_rest(_cfg(), audio, "before") == "rest"
    assert sent[0].startswith(silence(1)) and len(sent[0]) < len(audio) - RATE * 2


# ---------------------------------------------------------------- streamed pieces
class SegStt:
    """Answers each piece with its text and one segment starting 0.25 s into the audio it got, and keeps that audio."""

    def __init__(self, texts):
        self.texts, self.got = list(texts), []

    def __call__(self, cfg, wav, context=""):
        self.got.append(pcm_of(wav))
        text = self.texts[len(self.got) - 1]
        core._stt_local.segments = [{"start": 0.25, "end": 1.0, "text": text}]
        return text


def run(stt, audio, block=3200):
    s = streaming.StreamingStt({"upload_format": "wav"}, transcribe=stt)
    s.start()
    for i in range(0, len(audio), block):
        s.feed(audio[i:i + block])
    return s, s.finish(timeout=10)


def test_only_the_first_piece_loses_its_start_and_only_the_last_its_end():
    audio = silence(2) + tone(7) + silence(1) + tone(7) + silence(1) + tone(3) + silence(2)
    stt = SegStt(["one", "two", "three"])
    s, text = run(stt, audio)
    assert text == "one two three" and s.pieces == 3
    first, middle, last = stt.got
    assert not first.startswith(silence(0.5)) and first.endswith(silence(0.6))   # the pause at the cut stays
    assert middle.endswith(silence(0.6))                                        # a middle piece is sent as cut
    assert last.startswith(silence(0.3)) and not last.endswith(silence(0.5))
    assert s.done_bytes == len(audio)                                           # byte counts are of the recording


def test_streamed_segment_times_count_the_trimmed_start():
    audio = silence(2) + tone(7) + silence(1) + tone(3)
    s, _ = run(SegStt(["one", "two"]), audio)
    first, second = s.segments
    assert first["start"] == pytest.approx(2.0 - core.TRIM_PAD_FRAMES * 0.03 + 0.25, abs=0.031)   # where it is in the recording
    assert second["start"] == pytest.approx(s.piece_starts[1] + 0.25)


# ---------------------------------------------------------------- the speech request and its answer
def test_dictations_on_whisper_ask_for_verbose_json_whatever_the_structure_setting(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: sent.append(kw["data"]["response_format"]) or _Answer({"text": "hi"}))
    for structure in ("auto", "lists", "off"):
        core.transcribe(_cfg(structure=structure), b"RIFF")
    core.transcribe(_cfg(stt_model="gpt-4o-transcribe"), b"RIFF")
    assert sent == ["verbose_json"] * 3 + ["json"]


def test_made_up_segments_are_dropped_from_the_text_and_the_times(monkeypatch):
    body = {"text": " Send it today. Thank you.", "segments": [
        {"start": 0, "end": 2, "text": " Send it today.", "avg_logprob": -0.2, "no_speech_prob": 0.01, "compression_ratio": 1.1},
        {"start": 2, "end": 4, "text": " Thank you.", "avg_logprob": -1.3, "no_speech_prob": 0.8, "compression_ratio": 0.9}]}
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: _Answer(body))
    assert core.transcribe(_cfg(), b"RIFF") == "Send it today."
    assert core.last_segments() == [{"start": 0.0, "end": 2.0, "text": "Send it today."}]


def test_an_answer_the_filter_would_empty_is_kept_and_the_silence_phrase_check_still_runs(monkeypatch):
    # cqf: a short real phrase can score like silence ("Haan theek hai, kal milte hain.": no_speech 0.62, logprob -1.15);
    # the filter never empties a dictation, and a lone made-up phrase is still dropped later (is_silence_hallucination)
    body = {"text": "Haan theek hai, kal milte hain.", "segments": [{"start": 0, "end": 2, "text": "Haan theek hai, kal milte hain.",
                                                                    "avg_logprob": -1.15, "no_speech_prob": 0.62, "compression_ratio": 1}]}
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: _Answer(body))
    assert core.transcribe(_cfg(), b"RIFF") == "Haan theek hai, kal milte hain." and core.last_segments()
    body = {"text": "Thanks for watching!", "segments": [
        {"start": 0, "end": 2, "text": "Thanks for watching!", "avg_logprob": -1.5, "no_speech_prob": 0.9, "compression_ratio": 1}]}
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: _Answer(body))
    assert core.transcribe(_cfg(), b"RIFF") == "Thanks for watching!"
    assert core.process_text(_cfg(cleanup=False), "Thanks for watching!", "x.exe", "x").text == ""


def test_an_answer_that_is_only_a_loop_gives_nothing(monkeypatch):
    # leftovers: the keep-everything rule above brought a confirmed loop back and it was pasted; a loop has no real words
    loop = " ".join(["हम लोग"] * 6)
    body = {"text": loop, "segments": [{"start": 0, "end": 3, "text": loop, "avg_logprob": -0.2, "no_speech_prob": 0.0,
                                        "compression_ratio": 2.6}]}
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: _Answer(body))
    assert core.transcribe(_cfg(), b"RIFF") == "" and core.last_segments() is None
    assert core.process_text(_cfg(cleanup=False), "", "x.exe", "x").text == ""   # nothing is typed


def test_hindi_in_devanagari_is_not_a_loop_and_a_quiet_middle_segment_stays(monkeypatch):
    hindi = "कल सुबह हम लोग दफ़्तर जाएंगे और फिर दोपहर में मीटिंग होगी जिसके बाद रिपोर्ट भेजनी है"
    body = {"text": "Okay. " + hindi + " Done.", "segments": [
        {"start": 0, "end": 1, "text": "Okay.", "avg_logprob": -0.2, "no_speech_prob": 0.1, "compression_ratio": 1},
        {"start": 1, "end": 25, "text": hindi, "avg_logprob": -1.2, "no_speech_prob": 0.6, "compression_ratio": 2.5},
        {"start": 25, "end": 26, "text": "Done.", "avg_logprob": -0.2, "no_speech_prob": 0.1, "compression_ratio": 1}]}
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: _Answer(body))
    assert core.transcribe(_cfg(), b"RIFF") == "Okay. " + hindi + " Done."
    assert len(core.last_segments()) == 3


def test_segments_without_scores_are_kept_and_unreadable_scores_drop_nothing(monkeypatch):
    plain = {"text": "a b", "segments": [{"start": 0, "end": 1, "text": "a"}, {"start": 1, "end": 2, "text": "b", "avg_logprob": None}]}
    odd = {"text": "a b", "segments": [{"start": 0, "end": 1, "text": "a", "no_speech_prob": "high", "avg_logprob": -3}]}
    for body in (plain, odd):
        monkeypatch.setattr(core, "post_with_retry", lambda url, b=body, **kw: _Answer(b))
        assert core.transcribe(_cfg(), b"RIFF") == "a b"


def test_an_answer_that_reads_the_prompt_back_is_dropped(monkeypatch):
    cfg = _cfg(dictionary=["Kubernetes", "Tailscale", "Groq"])
    sent = []
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: sent.append(kw["data"].get("prompt")) or _Answer({"text": "Kubernetes, Tailscale, Groq."}))
    assert core.transcribe(cfg, b"RIFF") == "" and core.last_segments() is None
    assert "Kubernetes" in sent[0]
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: _Answer({"text": "Groq"}))
    assert core.transcribe(cfg, b"RIFF") == "Groq"                          # one real word is never an echo


def test_the_echo_check_ignores_the_words_of_the_whisper_v2_sentence(monkeypatch):
    # integration of Whisper prompt v2 (a sentence) and the echo drop: the frame words do not count, on either side
    cfg = _cfg(dictionary=["Docker", "Groq"], people=["Priya"])
    sent = []

    def answer(text):
        return lambda url, **kw: sent.append(kw["data"].get("prompt")) or _Answer({"text": text})
    monkeypatch.setattr(core, "post_with_retry", answer("Talked with Priya."))
    assert core.transcribe(cfg, b"RIFF") == "Talked with Priya."             # a real dictation of the frame is kept
    assert sent[0] == "Talked with Priya about Docker and Groq."
    monkeypatch.setattr(core, "post_with_retry", answer("Talked with Priya about Docker."))
    assert core.transcribe(cfg, b"RIFF") == "Talked with Priya about Docker."   # 2 words beyond the frame: kept, as 2 terms were before
    monkeypatch.setattr(core, "post_with_retry", answer("Talked with Priya about Docker and Groq."))
    assert core.transcribe(cfg, b"RIFF") == ""                               # the whole sentence read back is not
    cfg = _cfg(dictionary=["Ada", "Grace", "Kubernetes"])
    monkeypatch.setattr(core, "post_with_retry", answer("Ada, Grace, Kubernetes."))
    assert core.transcribe(cfg, b"RIFF") == ""                               # nor the terms as a list
    assert sent[-1] == "We talked about Ada, Grace and Kubernetes."


def test_the_400_fallback_to_plain_json_still_works_and_still_drops_an_echo(monkeypatch):
    sent = []

    def post(url, **kw):
        sent.append(kw["data"]["response_format"])
        if kw["data"]["response_format"] == "verbose_json":
            a = _Answer({"error": {"message": "bad response_format"}})
            a.status_code, a.text = 400, "bad"
            return a
        return _Answer({"text": "Ada, Grace, Kubernetes."})
    monkeypatch.setattr(core, "post_with_retry", post)
    assert core.transcribe(_cfg(dictionary=["Ada", "Grace", "Kubernetes"]), b"RIFF") == ""
    assert sent == ["verbose_json", "json"]


def test_the_meeting_transcript_uses_the_same_thresholds():
    meeting = pytest.importorskip("meeting")
    seg = {"text": "real words here", "logprob": -1.2, "no_speech": 0.6, "compression": 1.0}
    assert not meeting._good(seg)
    assert meeting._good(dict(seg, no_speech=0.5))
    assert not meeting._good(dict(seg, no_speech=0.0, compression=2.5))


# ---------------------------------------------------------------- Settings > Language: the hint and the one-time suggestion
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PAGES = ["windows/ui/index.html", "android/assets/index.html"]


def test_the_language_default_stays_auto_and_the_suggestion_starts_unanswered():
    assert core.DEFAULT_CONFIG["language"] == "" and core.DEFAULT_CONFIG["language_tip_done"] is False


@pytest.mark.parametrize("page", PAGES)
def test_both_pages_show_the_shared_hint_and_offer_english_once(page):
    with open(f"{ROOT}/{page}", encoding="utf-8") as f:
        html = f.read()
    assert 'id="language-hint"' in html and '$("language-hint").textContent = LANGUAGE_HINT' in html
    assert 'id="lang-tip-en"' in html and "languageTipDue(" in html
    assert 'save({ language: "en", language_tip_done: true }' in html and "save({ language_tip_done: true }" in html


def test_the_suggestion_is_due_only_on_auto_and_until_answered(tmp_path):
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = tmp_path / "t.js"
    js = open(f"{ROOT}/ui-shared/common.js", encoding="utf-8").read()
    script.write_text(js + """
console.log(JSON.stringify([languageTipDue({language: ""}), languageTipDue({}), languageTipDue(null), languageTipDue({language: "en"}),
  languageTipDue({language: "", language_tip_done: true}), languageTipDue({language: "  "}), LANGUAGE_HINT]));
""", encoding="utf-8")
    r = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1]) == [True, True, True, False, False, True,
                                                            "English only? Choose English for fewer mistakes."]
