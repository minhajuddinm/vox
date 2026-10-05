"""Public speech samples for the benchmark: downloads a small slice of open datasets (Indian English, Hinglish, disfluent
and read speech) with their transcripts, to measure Vox's speech-to-text and cleanup on other voices than your own.
Nothing is trained on them.

    python tools/bench_public.py --list                                 the sources, their licence and size
    python tools/bench_public.py --source monsoon-en-in --count 50      at most 50 clips of one source
    python tools/bench_public.py --source all --count 50                50 of each
    python tools/bench_stt.py --folder %APPDATA%\\Vox\\bench\\public\\monsoon-en-in --provider groq
(run them with the repository's venv Python, .venv\\Scripts\\python)

Each source gets its own clips folder, %APPDATA%\\Vox\\bench\\public\\<source>\\, in the format of your own clips
(bench_clips.py): clip-NNN.wav (16 kHz mono 16-bit), manifest.jsonl (ref_verbatim = the dataset's transcript,
ref_intended = its punctuated and cased transcript when it has one, else the verbatim one; plus source, licence,
speaker, kind, lang, seconds, source_ref) and LICENSE.txt (licence, link, citation, what was changed), so
bench_stt.py --folder and bench_cleanup.py --folder run on it unchanged.

Only small pieces are fetched, never a whole dataset: rows of the Hugging Face dataset viewer API (a few pages spread
over the split, then one WAV per clip), single files out of a remote zip by HTTP range requests, or the first part of a
.tar.gz stream. A rerun skips what is there and continues; requests are paced (--pause) and a rate limit is waited out.
Downloads happen only when you run this tool; nothing is uploaded. Of the speaker, only an anonymised id is kept (a
short hash of the dataset's own id); a dataset's other speaker fields (age, place, income, phone) are never written.
Audio and transcripts stay outside the repository and must not be committed.
"""
import argparse
import hashlib
import io
import json
import math
import os
import re
import sys
import tarfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import wave
import zipfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "windows"))
sys.path.insert(0, HERE)

import bench_clips as clips   # noqa: E402
import vox_core as core       # noqa: E402

try:   # numpy (an app dependency) makes the resampling fast; without it the same filter runs in plain Python
    import numpy as np
except ImportError:   # pragma: no cover - CI has no numpy
    np = None

USER_AGENT = "vox-bench-public/1 (+https://github.com/minhajuddinm/vox; small evaluation samples)"
HF_ROWS = "https://datasets-server.huggingface.co/rows"
MAX_FILE_BYTES = 60_000_000   # one download (a page of rows, a clip, a transcript file) larger than this is refused
PAGE = 20                     # rows asked per dataset viewer request (the API allows up to 100)
MAX_FAILURES = 8              # samples in a row that cannot be read: the source has changed, stop downloading it

SOURCES = (
    {"id": "monsoon-en-in", "name": "Voice Arena Monsoon en-IN (public test split)",
     "kind": "indian-english", "lang": "en-IN", "licence": "CC BY 4.0",
     "licence_url": "https://creativecommons.org/licenses/by/4.0/",
     "url": "https://huggingface.co/datasets/VoiceArena/MonsoonASR-Open-ASR-leaderboard-en-IN",
     "size": "2,102 clips, 5.6 h, 1,444 speakers; 1.6 GB in full",
     "transcripts": "verbatim, lowercase, no punctuation, fillers kept (ahh, uh)",
     "about": "Conversational Indian English recorded on phones by speakers from 428 districts, part of the Open ASR "
              "Leaderboard.",
     "citation": "Voice Arena, Monsoon en-IN public test set, Hugging Face, "
                 "https://huggingface.co/datasets/VoiceArena/MonsoonASR-Open-ASR-leaderboard-en-IN",
     "method": "hf-rows", "dataset": "VoiceArena/MonsoonASR-Open-ASR-leaderboard-en-IN", "config": "default",
     "split": "test", "verbatim": "text", "intended": None, "speaker": "speaker_id", "per_speaker": 1, "per_page": 4},
    {"id": "hiacc-hinglish", "name": "HiACC Hinglish Adult & Children Code-switched Corpus (adult test split)",
     "kind": "hinglish", "lang": "hi-en", "licence": "CC BY 4.0",
     "licence_url": "https://creativecommons.org/licenses/by/4.0/",
     "url": "https://zenodo.org/records/15551669",
     "size": "5.24 h, adults and children; Corpus.zip 532 MB in full",
     "transcripts": "Hindi in Devanagari, English in Latin script, as spoken; some punctuation",
     "about": "Hindi-English code-switched speech (spontaneous answers, story reading, picture description) by 24 adult "
              "speakers; only the adult test split is used.",
     "citation": "Shruti Singh, Muskaan Singh, Virender Kadyan, HiACC: Hinglish Adult & Children Code-switched Corpus, "
                 "Zenodo, https://doi.org/10.5281/zenodo.15551669",
     "method": "zip-range", "zip_url": "https://zenodo.org/records/15551669/files/Corpus.zip?download=1",
     "audio_dir": "Corpus/adult/audio/test_split/",
     "transcript_file": "Corpus/adult/transcription/combined_output_changed_test_output.txt", "per_speaker": 3},
    {"id": "disfluency-speech", "name": "DisfluencySpeech (test split)",
     "kind": "disfluent", "lang": "en-US", "licence": "Apache-2.0",
     "licence_url": "https://www.apache.org/licenses/LICENSE-2.0",
     "url": "https://huggingface.co/datasets/amaai-lab/DisfluencySpeech",
     "size": "5,000 clips, about 10 h, one speaker; test split 250 clips; 1.5 GB in full",
     "transcripts": "punctuated and cased; transcript_a keeps every word (fillers, restarts), transcript_c drops "
                    "fillers, editing terms, discourse markers and false starts",
     "about": "One studio speaker re-performs Switchboard telephone conversations, disfluencies included: the targets "
              "for what the cleanup should remove (verbatim = transcript_a, intended = transcript_c).",
     "citation": "Kyra Wang and Dorien Herremans, DisfluencySpeech: Single-Speaker Conversational Speech Dataset with "
                 "Paralanguage, 2024, https://arxiv.org/abs/2406.08820",
     "method": "hf-rows", "dataset": "amaai-lab/DisfluencySpeech", "config": "default", "split": "test",
     "verbatim": "transcript_a", "intended": "transcript_c", "speaker": None, "per_speaker": None},
    {"id": "fleurs-en", "name": "FLEURS en_us (test split)",
     "kind": "read", "lang": "en", "licence": "CC BY 4.0",
     "licence_url": "https://creativecommons.org/licenses/by/4.0/",
     "url": "https://huggingface.co/datasets/google/fleurs",
     "size": "647 test clips; test audio 290 MB (only the start of it is read)",
     "transcripts": "raw_transcription punctuated and cased; transcription lowercase without punctuation",
     "about": "Read Wikipedia sentences (FLoRes-101), the closest public match to dictated, punctuated text; speakers "
              "are not Indian, so it calibrates punctuation and case only.",
     "citation": "Conneau et al., FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech, 2022, "
                 "https://arxiv.org/abs/2205.12446",
     "method": "tar-stream", "tsv_url": "https://huggingface.co/datasets/google/fleurs/resolve/main/data/en_us/test.tsv",
     "tar_url": "https://huggingface.co/datasets/google/fleurs/resolve/main/data/en_us/audio/test.tar.gz",
     "per_speaker": None},
)


def source_by_id(sid):
    for s in SOURCES:
        if s["id"] == sid:
            return s
    raise KeyError(sid)


def public_root():
    return os.path.join(core.data_dir(), "bench", "public")


# ---- HTTP: paced, retried, http(s) only ---------------------------------------------------------------------------
class Http:
    """GET with a pause between requests, a rate limit (429) or server trouble (5xx) waited out (Retry-After, at most
    60 s), a size limit, and only http/https links."""

    def __init__(self, pause=1.0, timeout=60, tries=4, sleep=time.sleep, clock=time.monotonic, say=None):
        self.pause, self.timeout, self.tries, self.sleep, self.clock = pause, timeout, tries, sleep, clock
        self.say = say or (lambda t: None)
        self.last = None
        self.requests = 0
        self.bytes = 0

    def _turn(self):
        if self.last is not None and self.pause:
            wait = self.pause - (self.clock() - self.last)
            if wait > 0:
                self.sleep(wait)
        self.last = self.clock()

    def open(self, url, headers=None):
        """The open response (a stream); the caller closes it."""
        if urllib.parse.urlparse(url).scheme not in ("http", "https"):
            raise ValueError(f"not an http(s) link: {url[:60]}")
        for attempt in range(1, self.tries + 1):
            self._turn()
            self.requests += 1
            req = urllib.request.Request(url, headers=dict({"User-Agent": USER_AGENT}, **(headers or {})))
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                if e.code != 429 and e.code < 500 or attempt == self.tries:
                    raise
                wait, why = _retry_after(e.headers.get("Retry-After"), 5 * attempt), f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == self.tries:
                    raise
                wait, why = 5 * attempt, str(getattr(e, "reason", e))
            self.say(f"    {why}: waiting {wait:g} s, then trying again")
            self.sleep(wait)
        raise RuntimeError("unreachable")   # pragma: no cover

    def get(self, url, headers=None, limit=MAX_FILE_BYTES):
        with self.open(url, headers) as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"download larger than {limit // 1_000_000} MB refused")
        self.bytes += len(data)
        return data

    def json(self, url):
        return json.loads(self.get(url).decode("utf-8"))


def _retry_after(value, default):
    try:
        return max(1.0, min(60.0, float(value)))
    except (TypeError, ValueError):
        return default


class RangeFile(io.RawIOBase):
    """A remote file read by HTTP range requests (seekable), so zipfile can read one member of a large zip without
    downloading the rest."""

    def __init__(self, http, url, size=None):
        self.http, self.url, self.pos = http, url, 0
        if size is None:
            with http.open(url, {"Range": "bytes=0-0"}) as r:
                total = (r.headers.get("Content-Range") or "").rpartition("/")[2]
                if r.status != 206 or not total.isdigit():
                    raise ValueError("the server does not answer range requests")
                size = int(total)
        self.size = size

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        data = self.http.get(self.url, {"Range": f"bytes={self.pos}-{self.pos + n - 1}"}, limit=n)
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)


# ---- audio: any PCM WAV to 16 kHz mono 16-bit ---------------------------------------------------------------------
def _samples(raw, width):
    """PCM bytes of one sample width as floats in -1..1."""
    if width == 1:
        return [(b - 128) / 128 for b in raw]
    if width == 2:
        import array
        a = array.array("h")
        a.frombytes(raw[:len(raw) // 2 * 2])
        if sys.byteorder == "big":   # pragma: no cover
            a.byteswap()
        return [v / 32768 for v in a]
    if width in (3, 4):
        return [int.from_bytes(raw[i:i + width], "little", signed=True) / (1 << (8 * width - 1))
                for i in range(0, len(raw) - width + 1, width)]
    raise ValueError(f"{width * 8}-bit audio is not supported")


def resample(x, src, dst, zeros=16):
    """x (floats) from src Hz to dst Hz with a Hann-windowed sinc low-pass (cut at 0.95 of the lower Nyquist, `zeros`
    zero crossings each side). numpy when present, else the same filter in plain Python."""
    if src == dst:
        return list(x)
    ratio = dst / src
    fc = 0.5 * min(1.0, ratio) * 0.95          # cut-off in cycles per input sample
    half = zeros / (2 * fc)                     # filter half-width in input samples
    n_out = int(len(x) * ratio)
    reach = int(math.ceil(half))
    if np is not None:
        xs = np.asarray(x, dtype=np.float64)
        t = np.arange(n_out) / ratio
        base = np.floor(t).astype(np.int64)
        acc = np.zeros(n_out)
        for k in range(-reach, reach + 1):
            i = base + k
            d = t - i
            w = 2 * fc * np.sinc(2 * fc * d) * (0.5 + 0.5 * np.cos(np.pi * np.clip(d / half, -1, 1)))
            w[np.abs(d) >= half] = 0
            ok = (i >= 0) & (i < len(xs))
            acc[ok] += w[ok] * xs[i[ok]]
        return acc.tolist()
    out = []
    n_in = len(x)
    for n in range(n_out):
        t = n / ratio
        b = int(t)
        s = 0.0
        for i in range(max(0, b - reach), min(n_in, b + reach + 1)):
            d = t - i
            if abs(d) < half:
                a = 2 * fc * d
                sinc = 1.0 if a == 0 else math.sin(math.pi * a) / (math.pi * a)
                s += x[i] * 2 * fc * sinc * (0.5 + 0.5 * math.cos(math.pi * d / half))
        out.append(s)
    return out


def _float_wav(data):
    """(rate, channels, floats) of a 32/64-bit IEEE float WAV (format 3, or extensible with the float subformat; the
    wave module reads only integer PCM). ValueError when it is not one."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt, raw = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            raw = body
            break
        pos += 8 + size + (size & 1)
    if fmt is None or raw is None or len(fmt) < 16:
        raise ValueError("a WAV file without fmt or data")
    tag, ch, rate = int.from_bytes(fmt[0:2], "little"), int.from_bytes(fmt[2:4], "little"), int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = int.from_bytes(fmt[24:26], "little")
    if tag != 3 or bits not in (32, 64) or not ch or not rate:
        raise ValueError(f"unsupported WAV format {tag} ({bits}-bit)")
    import array
    a = array.array("f" if bits == 32 else "d")
    a.frombytes(raw[:len(raw) // a.itemsize * a.itemsize])
    if sys.byteorder == "big":   # pragma: no cover
        a.byteswap()
    return rate, ch, [max(-1.0, min(1.0, v)) for v in a]


def to_pcm16k(wav_bytes):
    """The PCM bytes (16 kHz mono 16-bit, Vox's own format) of a WAV file of any rate and channel count, integer PCM
    (8 to 32-bit) or IEEE float. ValueError for anything else (compressed audio, an empty file)."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            rate, ch, width, raw = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())
        s = None
    except (wave.Error, EOFError) as e:
        try:
            rate, ch, s = _float_wav(wav_bytes)
        except ValueError as e2:
            raise ValueError(f"not a WAV file Vox can read ({e}; {e2})") from None
    if s is None and (rate, ch, width) == (core.SAMPLE_RATE, 1, 2):
        return raw[:len(raw) // 2 * 2]
    if s is None:
        s = _samples(raw, width)
    if ch > 1:
        s = [sum(s[i:i + ch]) / ch for i in range(0, len(s) - ch + 1, ch)]
    s = resample(s, rate, core.SAMPLE_RATE)
    import array
    out = array.array("h", (max(-32768, min(32767, int(round(v * 32767)))) for v in s))
    if sys.byteorder == "big":   # pragma: no cover
        out.byteswap()
    return out.tobytes()


# ---- text and speaker ---------------------------------------------------------------------------------------------
def norm_text(text):
    """A transcript as stored: Unicode NFC, annotation tags (<laughter>, [noise]) out, spaces collapsed."""
    t = unicodedata.normalize("NFC", text or "")
    t = re.sub(r"<[^<>]{1,40}>|\[[^\[\]]{1,40}\]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def anon_speaker(source_id, raw):
    """An anonymised speaker id: a short hash of the dataset's own id ("" when the dataset has none)."""
    if raw in (None, ""):
        return ""
    return f"{source_id.split('-')[0]}-{hashlib.sha256(f'{source_id}:{raw}'.encode()).hexdigest()[:8]}"


def word_count(text):
    return len(re.findall(r"\w+", text))


_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")


def code_switched(text):
    return bool(_DEVANAGARI.search(text)) and bool(_LATIN.search(text))


# ---- the candidates of each kind of source ------------------------------------------------------------------------
class Item:
    """One sample offered by a source: its reference in the dataset, raw speaker id, texts, and a function that
    downloads its WAV bytes."""

    def __init__(self, ref, speaker, verbatim, intended, audio):
        self.ref, self.speaker, self.verbatim, self.intended, self.audio = ref, speaker, verbatim, intended, audio


def spread_order(n):
    """0..n-1 in an order that jumps across the range (golden-ratio stride), so the first pages read come from all
    over a split rather than its first speakers."""
    if n <= 1:
        return list(range(n))
    step = max(1, round(n * 0.618))
    while math.gcd(step, n) != 1:
        step += 1
    return [(k * step) % n for k in range(n)]


def hf_rows_items(source, http):
    """Rows of a Hugging Face dataset through the dataset viewer API (no login for a dataset that is not gated): pages
    of PAGE rows spread over the split (at most "per_page" rows offered from each, so many speakers come in); each row's
    audio is a WAV link of the viewer's cache."""
    rows_url = source.get("rows_url", HF_ROWS)

    def page(offset):
        q = urllib.parse.urlencode({"dataset": source["dataset"], "config": source["config"], "split": source["split"],
                                    "offset": offset, "length": PAGE})
        return http.json(f"{rows_url}?{q}")
    first = page(0)
    total = int(first.get("num_rows_total") or len(first.get("rows", [])))
    pages = max(1, math.ceil(total / PAGE))
    for p in spread_order(pages):
        data = first if p == 0 else page(p * PAGE)
        for r in data.get("rows", [])[:source.get("per_page") or PAGE]:
            row = r.get("row") or {}
            audio = row.get("audio")
            src = (audio[0] if isinstance(audio, list) and audio else audio or {}).get("src") if audio else None
            text = row.get(source["verbatim"])
            if not src or not isinstance(text, str):
                continue
            intended = row.get(source["intended"]) if source.get("intended") else None
            speaker = row.get(source["speaker"]) if source.get("speaker") else None
            yield Item(f"{source['split']}/{r.get('row_idx')}", speaker, text, intended,
                       lambda src=src: http.get(src))


def zip_range_items(source, http):
    """Single files of a remote zip read by range requests: the transcript list first, then one WAV per clip.
    Code-switched lines (both scripts) come first, speakers taking turns."""
    z = zipfile.ZipFile(io.BufferedReader(RangeFile(http, source["zip_url"]), 1 << 16))
    names = set(z.namelist())
    lines = z.read(source["transcript_file"]).decode("utf-8-sig", "replace").splitlines()
    pairs = []
    for line in lines:
        name, sep, text = line.partition(",")
        name = name.strip()
        if sep and name.endswith(".wav") and source["audio_dir"] + name in names:
            pairs.append((name, text.strip()))
    pairs.sort()

    def turns(group):
        by = {}
        for name, text in group:
            by.setdefault(name[:4], []).append((name, text))
        queues = [by[k] for k in sorted(by)]
        while any(queues):
            for q in queues:
                if q:
                    yield q.pop(0)
    mixed = [p for p in pairs if code_switched(p[1])]
    rest = [p for p in pairs if not code_switched(p[1])]
    for group in (mixed, rest):
        for name, text in turns(group):
            yield Item(name, name[:4], text, None, lambda name=name: z.read(source["audio_dir"] + name))


def tar_stream_items(source, http):
    """The start of a .tar.gz read as a stream (the download stops when enough clips are taken) with the transcripts
    from a TSV file (FLEURS layout: id, file, raw transcription, transcription, ...); one clip per sentence."""
    tsv = http.get(source["tsv_url"]).decode("utf-8")
    meta = {}
    for line in tsv.splitlines():
        cols = line.split("\t")
        if len(cols) >= 4:
            meta[cols[1]] = (cols[0], cols[2], cols[3])
    seen = set()
    with http.open(source["tar_url"]) as r, tarfile.open(fileobj=r, mode="r|gz") as tar:
        for m in tar:
            name = os.path.basename(m.name)
            if not m.isfile() or name not in meta or meta[name][0] in seen:
                continue
            if m.size > MAX_FILE_BYTES:
                continue
            seen.add(meta[name][0])
            sid, raw, normalised = meta[name]
            data = tar.extractfile(m).read()
            http.bytes += len(data)
            yield Item(f"test/{name}", None, normalised, raw, lambda data=data: data)


FETCHERS = {"hf-rows": hf_rows_items, "zip-range": zip_range_items, "tar-stream": tar_stream_items}


# ---- the clips folder of a source ---------------------------------------------------------------------------------
def write_licence(folder, source):
    text = (f"{source['name']}\n{source['url']}\n\nLicence: {source['licence']} ({source['licence_url']})\n"
            f"Cite: {source['citation']}\n\n{source['about']}\nTranscripts: {source['transcripts']}\n\n"
            "Changes: a small sample only; audio converted to 16 kHz mono 16-bit WAV; transcripts with spaces collapsed "
            "and annotation tags removed; speaker ids replaced by a short hash; no other speaker data kept.\n"
            f"Downloaded by Vox tools/bench_public.py on {date.today().isoformat()} for local evaluation only. Not part "
            "of the Vox repository; do not commit or redistribute without keeping this notice.\n")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def folder_bytes(folder):
    total = 0
    for dirpath, _, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def fetch(source, root, count, http, items=None, min_words=4, max_seconds=30.0, budget=None, say=print):
    """Fills root/<source id>/ up to `count` clips. Clips already there are kept and skipped; a sample that cannot be
    read, is too short in words, too long, or from a speaker who already has `per_speaker` clips is passed over.
    `budget` (bytes) stops the run when the folders under root reach it. Returns {"have", "added", "skipped"}."""
    folder = os.path.join(root, source["id"])
    os.makedirs(folder, exist_ok=True)
    rows = [r for r in clips.load_manifest(folder) if os.path.exists(clips.wav_path(folder, r["id"]))]
    done = {r.get("source_ref") for r in rows}
    speakers = {}
    for r in rows:
        speakers[r.get("speaker", "")] = speakers.get(r.get("speaker", ""), 0) + 1
    have, added, skipped, failures = len(rows), 0, 0, 0
    write_licence(folder, source)
    if have >= count:
        say(f"  {source['id']}: {have} clips already there")
        return {"have": have, "added": 0, "skipped": 0}
    gen = items if items is not None else FETCHERS[source["method"]](source, http)
    try:
        for item in gen:
            if have >= count:
                break
            if budget is not None and folder_bytes(root) >= budget:
                say(f"  {source['id']}: the size limit is reached, stopping")
                break
            speaker = anon_speaker(source["id"], item.speaker)
            verbatim = norm_text(item.verbatim)
            if item.ref in done or word_count(verbatim) < min_words:
                continue
            if source.get("per_speaker") and speaker and speakers.get(speaker, 0) >= source["per_speaker"]:
                continue
            try:
                pcm = to_pcm16k(item.audio())
                failures = 0
            except (ValueError, OSError, urllib.error.URLError, zipfile.BadZipFile, KeyError) as e:
                say(f"  {source['id']} {item.ref}: skipped ({e})")
                skipped += 1
                failures += 1
                if failures >= MAX_FAILURES:
                    say(f"  {source['id']}: {failures} samples in a row could not be read, stopping this source")
                    break
                continue
            secs = clips.seconds(pcm)
            if secs < 0.5 or secs > max_seconds:
                skipped += 1
                continue
            cid = clips.next_clip_id(folder)
            clips.write_new_wav(clips.wav_path(folder, cid), pcm)
            intended = norm_text(item.intended) if item.intended else ""
            clips.append_row(folder, {
                "id": cid, "audio": cid + ".wav", "ref_verbatim": verbatim, "ref_intended": intended or verbatim,
                "terms": [], "kind": source["kind"], "lang": source["lang"], "source": source["id"],
                "licence": source["licence"], "speaker": speaker, "source_ref": item.ref, "seconds": round(secs, 2)})
            done.add(item.ref)
            speakers[speaker] = speakers.get(speaker, 0) + 1
            have += 1
            added += 1
            say(f"  {source['id']} [{have}/{count}] {cid} ({secs:.1f} s)")
    finally:
        close = getattr(gen, "close", None)
        if close:
            close()
    if have < count:
        say(f"  {source['id']}: only {have} usable clips found")
    return {"have": have, "added": added, "skipped": skipped}


def render_list(root):
    lines = []
    for s in SOURCES:
        folder = os.path.join(root, s["id"])
        n = len(clips.load_manifest(folder)) if os.path.isdir(folder) else 0
        lines.append(f"{s['id']:<18} {s['kind']:<15} {s['licence']:<11} {s['size']}")
        lines.append(f"{'':<18} {s['name']}; {s['url']}")
        lines.append(f"{'':<18} transcripts: {s['transcripts']}; here: {n} clips")
    return "\n".join(lines)


def main(argv=None, http=None):
    """0 when every source asked for was filled (or had all it could give), 1 when a source failed, 2 for a bad call."""
    clips.safe_console()
    ap = argparse.ArgumentParser(description="Download small public speech samples with transcripts for the benchmark.")
    ap.add_argument("--list", action="store_true", help="show the sources, licence and size")
    ap.add_argument("--source", help="a source id, several comma separated, or all")
    ap.add_argument("--count", type=int, default=50, help="clips per source at most (default 50)")
    ap.add_argument("--root", help="where the source folders go (default %%APPDATA%%\\Vox\\bench\\public)")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between requests (default 1)")
    ap.add_argument("--max-seconds", type=float, default=30.0, help="longest clip kept, in seconds (default 30)")
    ap.add_argument("--max-mb", type=float, default=1000.0, help="stop when the public folders reach this size (MB)")
    args = ap.parse_args(argv)
    root = args.root or public_root()
    if args.list or not args.source:
        print(render_list(root))
        if not args.list:
            print("\nChoose one with --source <id> (or --source all) --count N.", file=sys.stderr)
        return 0
    ids = [s["id"] for s in SOURCES] if args.source.strip() == "all" else \
        [x.strip() for x in args.source.split(",") if x.strip()]
    unknown = [x for x in ids if x not in {s["id"] for s in SOURCES}]
    if unknown or not 1 <= args.count <= 1000:
        print(f"Unknown source: {', '.join(unknown)} (see --list)" if unknown else "--count takes 1 to 1000",
              file=sys.stderr)
        return 2
    say = lambda t: print(t, file=sys.stderr)   # noqa: E731
    http = http or Http(pause=args.pause, say=say)
    failed = 0
    for sid in ids:
        source = source_by_id(sid)
        say(f"{sid}: {source['name']} ({source['licence']})")
        try:
            got = fetch(source, root, args.count, http, max_seconds=args.max_seconds,
                        budget=int(args.max_mb * 1_000_000), say=say)
        except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile, tarfile.TarError) as e:
            say(f"  {sid}: failed ({e}); a rerun continues where it stopped")
            failed += 1
            continue
        say(f"  {sid}: {got['have']} clips in {os.path.join(root, sid)} ({got['added']} new)")
    say(f"{http.requests} requests, {http.bytes / 1e6:.1f} MB downloaded; the folders hold "
        f"{folder_bytes(root) / 1e6:.1f} MB.\nNext: .venv\\Scripts\\python tools\\bench_stt.py --folder "
        f"{os.path.join(root, ids[0])} --provider groq")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
