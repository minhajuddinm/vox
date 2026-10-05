"""The recorded clips of the benchmark: where they live and how they are read and written (bench_record.py records them,
bench_stt.py transcribes them, bench_cleanup.py cleans the transcripts).

Everything is in %APPDATA%\\Vox\\bench\\clips\\, outside the repository, and is never uploaded except the audio that
bench_stt.py sends to the speech server you choose:
    clip-001.wav          16 kHz mono 16-bit, as Vox records
    clip-001.stt.json     the cached transcripts of that clip, one per speech setting (bench_stt.py)
    manifest.jsonl        one line per clip with the texts you typed (written after the clip, so a clip without a line
                          is one whose texts are still to be typed)
A clip file is never overwritten: a new clip always gets the next free number."""
import json
import os
import re
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "windows"))

import vox_core as core   # noqa: E402

MANIFEST = "manifest.jsonl"
_CLIP = re.compile(r"^clip-(\d{3,})\.wav$")


def safe_console():
    """Printing never ends a run: a character the console's code page cannot show (a piped or redirected pwsh output is
    cp1252) prints as ? instead of raising UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):   # not a text stream that can be reconfigured: leave it
            pass


def clips_dir():
    return os.path.join(core.data_dir(), "bench", "clips")


def wav_path(folder, clip_id):
    return os.path.join(folder, clip_id + ".wav")


def load_manifest(folder):
    """The manifest rows, oldest first; a line that cannot be read (a write cut off) is skipped. The newest line wins
    for a clip that has two."""
    path = os.path.join(folder, MANIFEST)
    rows = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    rows[row["id"]] = row
    return list(rows.values())


def append_row(folder, row):
    """Adds one manifest line and flushes it to disk, so a crash right after loses nothing typed."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, MANIFEST)
    cut = False   # a line cut off by a crash: start on a new line, so this row is not glued to it
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            cut = f.read(1) != b"\n"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(("\n" if cut else "") + json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def clips_on_disk(folder):
    """The clip ids that have a WAV file, in number order."""
    if not os.path.isdir(folder):
        return []
    found = [(int(m.group(1)), m.group(0)[:-4]) for m in map(_CLIP.match, os.listdir(folder)) if m]
    return [cid for _, cid in sorted(found)]


def next_clip_id(folder):
    """The next free clip id: one above the highest number on disk or in the manifest."""
    nums = [int(c.split("-")[1]) for c in clips_on_disk(folder)]
    nums += [int(r["id"].split("-")[1]) for r in load_manifest(folder) if re.fullmatch(r"clip-\d{3,}", r["id"])]
    return f"clip-{max(nums, default=0) + 1:03d}"


def write_new_wav(path, pcm):
    """Writes 16 kHz mono 16-bit PCM as a WAV file that must not exist yet (FileExistsError otherwise: a clip is never
    overwritten). The file is written under a temporary name and renamed when complete."""
    if os.path.exists(path):
        raise FileExistsError(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(core.pcm_to_wav(pcm))
    try:
        os.rename(tmp, path)   # os.rename refuses to replace an existing file on Windows, unlike os.replace
    except FileExistsError:
        os.remove(tmp)
        raise


def read_pcm(path):
    """The PCM bytes of a clip (16 kHz mono 16-bit, else ValueError)."""
    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (core.SAMPLE_RATE, 1, 2):
            raise ValueError(f"{os.path.basename(path)} is not 16 kHz mono 16-bit")
        return w.readframes(w.getnframes())


def seconds(pcm):
    return len(pcm) / (core.SAMPLE_RATE * 2)


def stt_path(folder, clip_id):
    return os.path.join(folder, clip_id + ".stt.json")


def load_stt(folder, clip_id):
    """{label: result} cached for a clip ({} when none or unreadable)."""
    try:
        with open(stt_path(folder, clip_id), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_stt(folder, clip_id, data):
    """Replaces a clip's transcript cache (a cache, not a recording: rewriting it is fine)."""
    path = stt_path(folder, clip_id)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def best_stt_label(folder, rows):
    """The cached transcript setting to clean by default: the one that covers the most of these clips, then one with the
    prompt on (the app always sends its dictionary prompt), then the newest. None when no clip has a transcript."""
    seen = {}
    for r in rows:
        for label, hit in load_stt(folder, r["id"]).items():
            if isinstance(hit, dict) and hit.get("text") is not None:
                n, when = seen.get(label, (0, ""))
                seen[label] = (n + 1, max(when, str(hit.get("when", ""))))
    if not seen:
        return None
    return max(seen, key=lambda k: (seen[k][0], "prompt-on" in k, seen[k][1]))


def all_terms(rows):
    """The benchmark dictionary: every clip's names and terms, in order of first use, each once. Both bench_stt.py (the
    speech prompt) and bench_cleanup.py (the cleanup prompt) use this whole list, never your own dictionary, so each clip
    sees the right terms among others, as in real use."""
    return list(dict.fromkeys(t for r in rows for t in r.get("terms", [])))
