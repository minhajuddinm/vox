"""One guided run of the whole benchmark: record your clips, transcribe them, compare the old and new cleanup in Light
and Standard, and keep every result in one folder. Each step can be skipped or resumed: clips already recorded, speech
answers already fetched and cleanup answers already received are kept and cost nothing again.

    python tools/bench_wizard.py            (or double-click tools/bench_wizard.cmd)

It only chains tools/bench_record.py, bench_stt.py and bench_cleanup.py with the arguments of the tuning round, and
copies what they print into summary.txt next to their JSON files in %APPDATA%\\Vox\\bench\\run-DATE\\."""
import io
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "windows"))
sys.path.insert(0, HERE)

import bench_clips as clips   # noqa: E402
import vox_core as core       # noqa: E402


class Tee(io.TextIOBase):
    """Writes to the console and to the summary file at once."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)
            s.flush()
        return len(text)

    def flush(self):
        for s in self.streams:
            s.flush()


def yes(ask, question, default=True):
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        answer = ask(f"{question} {hint} ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "n", "no"):
            return answer.startswith("y")


def number(ask, question, default):
    while True:
        answer = ask(f"{question} [{default}] ").strip()
        if not answer:
            return default
        if answer.isdigit() and int(answer) > 0:
            return int(answer)


def typed_clips(folder):
    """Clips that have their texts typed (what the later steps can use)."""
    return [r for r in clips.load_manifest(folder) if r.get("ref_verbatim")]


def tools():
    """The three tools, imported late so a missing Windows package fails with a clear message, not at start."""
    import bench_cleanup
    import bench_record
    import bench_stt
    return bench_record, bench_stt, bench_cleanup


def run_step(say, title, fn, argv, log):
    say(f"\n=== {title} ===")
    say("    " + " ".join(argv))
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = Tee(old_out, log), Tee(old_err, log)
    try:
        code = fn(argv)
    except KeyboardInterrupt:
        code = 130
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    if code:
        say(f"    That step stopped (code {code}). Run the wizard again later to continue: finished parts are kept.")
    return code or 0


def main(argv=None, ask=input, say=print, tool_set=None, now=datetime.now):
    clips.safe_console()
    folder = clips.clips_dir()
    run_dir = os.path.join(core.data_dir(), "bench", now().strftime("run-%Y%m%d-%H%M%S"))
    say("Vox benchmark: record your own dictations, then measure the old and the new cleanup on them.")
    say("Steps: 1 record (about 30-45 min for 50 clips), 2 transcribe (a few minutes), 3 and 4 cleanup in Light and")
    say("Standard (about 20 min each, paced for Groq's free tier). It uses the provider and key saved in Vox; the key is")
    say(f"never shown. Clips stay on this PC in {folder}. Ctrl+C stops a step; run this again to continue.\n")
    record, stt, cleanup = tool_set or tools()
    os.makedirs(run_dir, exist_ok=True)
    codes = []
    with open(os.path.join(run_dir, "summary.txt"), "a", encoding="utf-8") as log:
        have = len(typed_clips(folder))
        say(f"Clips recorded so far: {have}.")
        if yes(ask, "Step 1: record clips now?", default=have < 50):
            target = number(ask, "How many clips in total?", max(50, have))
            codes.append(run_step(say, "Step 1: record", record.main, ["--target", str(target)], log))
        have = len(typed_clips(folder))
        if not have:
            say("No clips with their texts yet, so there is nothing to measure. Run the wizard again to record.")
            return 1
        provider = ask("Which provider for the measurements? [groq] ").strip() or "groq"
        if yes(ask, f"Step 2: transcribe your {have} clips with {provider}?"):
            codes.append(run_step(say, "Step 2: transcribe", stt.main,
                                  ["--provider", provider, "--out", os.path.join(run_dir, "stt.json")], log))
            if yes(ask, "Optional: also try two trim paddings and two Whisper prompt sizes (about 4x the requests)?",
                   default=False):
                codes.append(run_step(say, "Step 2b: trim and prompt sizes", stt.main,
                                      ["--provider", provider, "--trim-pad-ms", "210,400", "--prompt-tokens", "160,100",
                                       "--out", os.path.join(run_dir, "stt-sweep.json")], log))
        for strength in ("light", "standard"):
            if yes(ask, f"Step {3 if strength == 'light' else 4}: compare the old and new cleanup in "
                        f"{strength.capitalize()}?"):
                codes.append(run_step(say, f"Cleanup, {strength}", cleanup.main,
                                      ["--provider", provider, "--clips", "--compare-prompt", "v1,v3",
                                       "--compare-guard", "v1,v2", "--strength", strength,
                                       "--out", os.path.join(run_dir, f"cleanup-{strength}.json")], log))
    say(f"\nDone. Results: {run_dir}")
    say("  summary.txt has everything printed above; the .json files hold the details.")
    say("  The JSON files contain your transcripts: share the folder only with someone you trust.")
    say("Tell Claude: \"bench done\" and the folder name, so the defaults can be set from your numbers.")
    return 0 if all(c == 0 for c in codes) else 1


if __name__ == "__main__":
    sys.exit(main())
