"""Records your own test clips for the Vox benchmark: Enter starts a recording, Enter stops it, then you type what you
said and what you want pasted.

    python tools/bench_record.py                 about 50 clips (30-45 minutes); run it again to continue
    python tools/bench_record.py --target 60     another goal
    python tools/bench_record.py --device "Microphone (USB)"   another microphone than the one chosen in Vox
(run them with the repository's venv Python, .venv\\Scripts\\python: it has sounddevice and the app's packages)

Clips are saved in %APPDATA%\\Vox\\bench\\clips\\ (16 kHz mono WAV, the format Vox records), outside the repository, and
never committed. Nothing is sent anywhere by this tool; bench_stt.py sends the clips to the speech server you choose.
After each clip you type three short lines:
    1. verbatim:  every word as you said it (um, repeats, "no wait" and all). Punctuation does not matter here.
    2. intended:  the text you want pasted, with capitals and punctuation. Enter alone takes the suggestion shown: line
                  1 through Vox's rules layer (capitals, final mark, noises out); check it, it is the reference every
                  formatted score is measured against.
    3. terms:     names and special words in the clip, comma separated (Enter alone = none).
A clip is never overwritten. If the tool is closed before the texts are typed, the next start asks for them first. The
manifest line of a clip records its suggested kind and the microphone; edit "kind" there if you said something else.
"""
import argparse
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "windows"))
sys.path.insert(0, HERE)

import bench_clips as clips   # noqa: E402
import vox_core as core       # noqa: E402

MIN_SECONDS = 0.5
LONG_SECONDS = 60

# What to say: a mix like W3 section 7.3 (spontaneous chat/email/notes, names, numbers, lists, Hinglish,
# self-corrections, spoken commands, short and edge cases). Any wording is fine: they are ideas, not scripts.
SUGGESTIONS = (
    ("chat", "A short message to a friend about weekend plans."),
    ("email", "An email to a colleague by name asking for a document, with a greeting and a sign-off."),
    ("selfcorr", "Set a meeting day, then correct it with \"no wait\" (\"Thursday, no wait, Friday\")."),
    ("numbers", "A price and a quantity (\"order twelve units at four hundred fifty rupees each\")."),
    ("names", "Mention two people and one product or tool name you use at work."),
    ("list", "Your three priorities for this week, saying first, second, third."),
    ("hinglish", "A short Hinglish message, mixing Hindi and English the way you text."),
    ("notes", "A quick note to yourself about something to fix or buy."),
    ("chat", "Reply to someone who asked how your day went."),
    ("command", "A sentence that uses the spoken commands \"comma\" and \"new line\"."),
    ("long", "Talk for 20-30 seconds about what you worked on today."),
    ("selfcorr", "Give a time, then change it with \"actually\" (\"at five, actually six\")."),
    ("ask", "Dictate a question to a person as if it were for an assistant (\"can you check the logs for me\")."),
    ("email", "A formal email declining a meeting, with a reason."),
    ("numbers", "A date and a time (\"the third of March at half past four\")."),
    ("names", "Spell out a plan that names a teammate and a city."),
    ("short", "Just two or three words (\"sounds good\", \"on my way\")."),
    ("hinglish", "Hinglish with an English technical word in it (\"deploy kal karenge\")."),
    ("chat", "Say something with lots of fillers: um, uh, like, you know."),
    ("list", "A shopping list of four or five things."),
    ("notes", "A meeting note: who said what and one decision."),
    ("selfcorr", "Start a sentence, scratch it (\"scratch that\"), and say it differently."),
    ("numbers", "A phone-style number or an order id read digit by digit."),
    ("email", "A follow-up email with a deadline date in it."),
    ("long", "Explain a bug or a problem for 20-30 seconds."),
    ("names", "Mention a product name with odd spelling (an app, a company, a library)."),
    ("chat", "Ask a friend a question that ends with a question mark."),
    ("hinglish", "Hinglish with a self-correction (\"nahi\" or \"matlab\")."),
    ("command", "Two short paragraphs, saying \"new paragraph\" between them."),
    ("selfcorr", "Correct a name or a number with \"I mean\"."),
    ("numbers", "Money with decimals and a percentage (\"twelve fifty, ten percent off\")."),
    ("notes", "A to-do with a time (\"call the bank at ten thirty\")."),
    ("chat", "Tell someone you are running late and why."),
    ("ask", "Dictate an instruction meant for a person (\"please summarise this for the team\")."),
    ("email", "Thank someone by name for their help, two or three sentences."),
    ("list", "Steps to do something, saying first, then, finally."),
    ("long", "Describe your plan for next week for 20-30 seconds."),
    ("names", "A sentence with three names in it."),
    ("hinglish", "A Hinglish reminder about a payment or a bill."),
    ("short", "One word or a yes/no answer (\"yes\", \"done\")."),
    ("selfcorr", "Change your mind on a quantity (\"two, no wait, three tickets\")."),
    ("numbers", "A year and an address-like number (\"twenty twenty six\", \"flat four oh two\")."),
    ("chat", "A message with an email address or a website in it."),
    ("notes", "An idea you want to remember, in a rambling way."),
    ("email", "A short status update email with two numbers in it."),
    ("hinglish", "A longer Hinglish message, three or four sentences."),
    ("command", "A question that you end by saying \"question mark\"."),
    ("chat", "Something you would type in a team chat, with a teammate's name."),
    ("long", "Summarise a conversation you had today, 20-30 seconds."),
    ("edge", "Stay quiet for three seconds, or cough, then say a few words."),
)


class Stop(Exception):
    """The user asked to stop (q, Ctrl+C or the end of input)."""


class MicRecorder:
    """Records from a microphone with sounddevice (16 kHz mono 16-bit, as Vox does) and plays a clip back."""

    def __init__(self, device_name="", say=print):
        import sounddevice as sd   # imported here: tests never open a real microphone
        import audio_devices
        self.sd = sd
        self.device = audio_devices.input_index(device_name, sd) if device_name else None
        self.name = device_name if self.device is not None else ""
        if device_name and self.device is None:   # as the app: a missing microphone falls back to the default one
            say(f"Microphone not found: {device_name}. Using the Windows default microphone "
                '(--device "NAME" picks another).')
        self.chunks, self.stream = [], None

    def start(self):
        self.chunks = []
        self.stream = self.sd.RawInputStream(samplerate=core.SAMPLE_RATE, channels=1, dtype="int16", device=self.device,
                                             callback=lambda data, frames, t, status: self.chunks.append(bytes(data)))
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        return b"".join(self.chunks)

    def play(self, pcm):
        with self.sd.RawOutputStream(samplerate=core.SAMPLE_RATE, channels=1, dtype="int16") as out:
            out.write(pcm)


class Session:
    """One recording session. `ask(prompt) -> str` reads a line (input), `say(text)` prints; both are swapped in tests,
    as is the recorder (start(), stop() -> PCM bytes, play(pcm))."""

    def __init__(self, folder, recorder, target, ask=input, say=print):
        self.folder, self.recorder, self.target, self._ask, self.say = folder, recorder, target, ask, say

    def play(self, pcm):
        """Plays a take back; Ctrl+C only stops the playback (the take is kept for the next answer)."""
        try:
            self.recorder.play(pcm)
        except KeyboardInterrupt:
            self.say("  Playback stopped.")

    def ask(self, prompt):
        try:
            return self._ask(prompt)
        except (EOFError, KeyboardInterrupt):
            raise Stop() from None

    def rows(self):
        return clips.load_manifest(self.folder)

    def done(self):
        return [r for r in self.rows() if r.get("ref_verbatim")]

    def run(self):
        os.makedirs(self.folder, exist_ok=True)
        done = self.done()
        self.say(f"Vox benchmark recorder. Clips are saved in {self.folder}")
        self.say(f"{len(done)} clips done so far, goal {self.target}. "
                 "Speak as you normally dictate; the suggestions are ideas, any wording is fine.")
        try:
            self.finish_untyped()
            while True:
                n = len(self.done())
                if n >= self.target:
                    self.say(f"\nGoal reached: {n} clips. Next: .venv\\Scripts\\python tools\\bench_stt.py --provider groq")
                    if self.ask("Record more? Enter = yes, q = stop: ").strip().lower() == "q":
                        break
                    self.target = n + 10
                self.one_clip(n)
        except Stop:
            pass
        n = len(self.done())
        self.say(f"\nStopped. {n} clips done ({sum(r.get('seconds', 0) for r in self.done()):.0f} s of speech). "
                 "Run the same command again to continue where you left off.")
        return n

    def finish_untyped(self):
        """Clips saved before the texts were typed (the tool was closed): asks for their texts first."""
        typed = {r["id"] for r in self.done()}
        rows = {r["id"]: r for r in self.rows()}
        for cid in clips.clips_on_disk(self.folder):
            if cid in typed:
                continue
            row = rows.get(cid) or {"id": cid, "audio": cid + ".wav", "kind": "other", "suggestion": ""}
            self.say(f"\n{cid} was recorded but its texts were not typed yet."
                     + (f" Suggestion was: {row['suggestion']}" if row.get("suggestion") else ""))
            while True:
                c = self.ask("Enter = type them now, p = play it first, s = skip it for now: ").strip().lower()
                if c == "p":
                    self.play(clips.read_pcm(clips.wav_path(self.folder, cid)))
                elif c == "s":
                    break
                else:
                    self.type_texts(row)
                    break

    def one_clip(self, n):
        kind, idea = SUGGESTIONS[n % len(SUGGESTIONS)]
        self.say(f"\n--- Clip {n + 1} of {self.target}  [{kind}]  Idea: {idea}")
        while True:
            if self.ask("Press Enter to START recording (q = stop for today): ").strip().lower() == "q":
                raise Stop()
            self.recorder.start()
            try:
                self.ask("  Recording... press Enter to STOP. ")
            finally:
                pcm = self.recorder.stop()
            secs = clips.seconds(pcm)
            if secs < MIN_SECONDS:
                self.say(f"  Only {secs:.1f} s: too short, let us try again.")
                continue
            notes = []
            if core.is_silent(pcm):
                notes.append("it sounds silent: check the microphone")
            if secs > LONG_SECONDS:
                notes.append("it is long; fine, but 3-30 s clips are the most useful")
            self.say(f"  Got {secs:.1f} s" + (f" ({'; '.join(notes)})." if notes else "."))
            choice = self.keep_or_redo(pcm)
            if choice == "keep":
                break
            if choice == "quit":
                raise Stop()
        cid = clips.next_clip_id(self.folder)
        clips.write_new_wav(clips.wav_path(self.folder, cid), pcm)
        row = {"id": cid, "audio": cid + ".wav", "seconds": round(secs, 2), "kind": kind, "suggestion": idea,
               "recorded": datetime.now().isoformat(timespec="seconds"),
               "mic": getattr(self.recorder, "name", "") or "default"}
        clips.append_row(self.folder, row)   # the audio's line first: a close now is resumed by finish_untyped
        self.type_texts(row)

    def keep_or_redo(self, pcm):
        while True:
            c = self.ask("  Enter = keep, p = play it back, r = record again, q = discard and stop: ").strip().lower()
            if c == "p":
                self.play(pcm)
            elif c == "r":
                return "redo"
            elif c == "q":
                return "quit"
            elif c == "":
                return "keep"

    def type_texts(self, row):
        self.say("  Now type three lines for this clip.")
        verbatim = ""
        while not verbatim:
            verbatim = self.ask("  1/3 Verbatim, every word as you said it (um, repeats, 'no wait'...): ").strip()
        suggested = core.fallback_text(verbatim) or verbatim   # the rules layer: capitals, final mark, noises out
        intended = self.ask("  2/3 Intended text to paste, with capitals and punctuation\n"
                            f"      (Enter = {suggested}): ").strip()
        terms = self.ask("  3/3 Names and special terms in it, comma separated (Enter = none): ")
        row = dict(row, ref_verbatim=verbatim, ref_intended=intended or suggested, intended_typed=bool(intended),
                   terms=[t.strip() for t in terms.split(",") if t.strip()])
        clips.append_row(self.folder, row)
        self.say(f"  Saved {row['id']} ({len(self.done())} of {self.target} done).")


def main(argv=None, recorder=None, ask=input, say=print):
    clips.safe_console()
    ap = argparse.ArgumentParser(description="Record your own clips for the Vox speech and cleanup benchmark.")
    ap.add_argument("--target", type=int, default=50, help="how many clips to aim for (default 50)")
    ap.add_argument("--device", help="microphone name (default: the one chosen in Vox, else the Windows default)")
    ap.add_argument("--folder", help="where the clips go (default: %%APPDATA%%\\Vox\\bench\\clips)")
    args = ap.parse_args(argv)
    if recorder is None:
        device = args.device if args.device is not None else core.load_config().get("input_device", "")
        recorder = MicRecorder(device, say)
    Session(args.folder or clips.clips_dir(), recorder, max(1, args.target), ask, say).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
