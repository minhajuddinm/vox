"""Lists and paragraphs (windows/structure.py). The list rules also run on the phone (Structure.java); spec/golden.txt
(kind structure) ties the two together. These tests add what does not fit one golden line: idempotence over every
golden row, the pipeline order around the fidelity guard, the setting, and the pause-based paragraphs (Windows only)."""
import os

import pytest

import structure
import vox_core as core

B = chr(92)
ROOT = os.path.join(os.path.dirname(__file__), "..")


def fmt(text, mode="auto", style="neutral"):
    return structure.format_structure(text, mode, style)


# ------------------------------------------------------------------ lists from spoken cues

@pytest.mark.parametrize("text,expected", [
    ("My three priorities this week are, first, the pricing page, second, the onboarding emails, third, the checkout bug.",
     "My three priorities this week are:\n1. The pricing page\n2. The onboarding emails\n3. The checkout bug"),
    ("The plan is first we eat, second we sleep.", "The plan is:\n1. We eat\n2. We sleep"),
    ("Firstly, it is cheap. Secondly, it is fast.", "1. It is cheap\n2. It is fast"),
    ("First, milk. Second, eggs. That is all for today.", "1. Milk\n2. Eggs\n\nThat is all for today."),
    ("The steps are step one, open the app, step two, sign in, step three, pick a file.",
     "The steps are:\n1. Open the app\n2. Sign in\n3. Pick a file"),
    ("Point one, we ship on Friday. Two, we test on Monday.", "1. We ship on Friday\n2. We test on Monday"),
    ("Step 1, unplug it. Step 2, wait ten seconds.", "1. Unplug it\n2. Wait ten seconds"),
    ("Number one, the budget. Number two, the hiring plan.", "1. The budget\n2. The hiring plan"),
    ("Bullet point milk, bullet point eggs.", "- Milk\n- Eggs"),
    ("Shopping list: bullet milk. Bullet eggs.", "Shopping list:\n- Milk\n- Eggs"),
    ("I need to buy bullet point milk bullet point eggs", "I need to buy:\n- Milk\n- Eggs"),
    ("Agenda. Next point, the budget. Next point, the trip.", "Agenda.\n- The budget\n- The trip"),
    ("Aaj ke kaam: pehla, doodh lana, doosra, bijli ka bill bharna.", "Aaj ke kaam:\n1. Doodh lana\n2. Bijli ka bill bharna"),
    ("Pehla point doodh, doosra point ande.", "1. Doodh\n2. Ande"),
    ("पहला, दूध लाना। दूसरा, बिल भरना।",
     "1. दूध लाना\n2. बिल भरना"),
    ("First of all, thanks for coming. Second, the budget is late.", "1. Thanks for coming\n2. The budget is late"),
    ("First, the iPhone app. Second, the web app.", "1. The iPhone app\n2. The web app"),
    ("First, iPhone. Second, web.", "1. iPhone\n2. Web"),
])
def test_spoken_cues_make_a_list(text, expected):
    assert fmt(text) == expected


@pytest.mark.parametrize("text", [
    "I need milk, eggs, bread and butter.",                       # commas alone are never a list
    "At first I thought the second option was better.",           # ordinals inside a clause are prose
    "First, call the bank.",                                      # a single stray first stays prose
    "Second, eggs. First, milk.",                                 # out of order: not a sequence starting at one
    "First, second, third.",                                      # an empty item: no list
    "My next point is simple and my next point is short.",        # next point inside a sentence
    "He is the number one player and number two is his brother.",
    "The bullet hit the wall.",
    "One, two, three, go.",                                       # bare numbers need point one / item one / step one
    "",
    "Groceries:\n- milk\n- eggs",                                 # already a list: left alone
    "Groceries:\n1. milk\n2. eggs",
])
def test_prose_stays_prose(text):
    assert fmt(text) == text


@pytest.mark.parametrize("style", ["neutral", "formal", "casual", "very_casual"])
@pytest.mark.parametrize("text", [   # TXT-5: everyday prose with cue-like words (from the review's 65 test dictations)
    "Number one priority is getting the release out. Two customers are waiting on the fix.",
    "First of all, congrats on the promotion. Second of all, you owe us drinks.",
    "First impressions of the new laptop: fast, quiet, but the keyboard is mushy. Second monitor support is flaky.",
    "Hi Nina, number one priority this week is the security review. Everything else can wait.",
    "Hey, step two of the deploy failed again. Same error as last time, something about the certificate.",
    "Thanks for the feedback. Point two is fair, I'll rewrite that section. Point five I disagree with, but happy to discuss.",
    "Hello, the first payment went through, but the second one bounced. Could you check the account details?",
    "Haha, that's hilarious. Number one rule of the group chat, no spoilers.",
    "Did you watch the game last night? Second half was insane.",
    "I'm so tired. Two meetings back to back and then a dentist appointment.",
    "Let's do dinner at seven. First round of drinks is on me.",
    "That movie was way better the second time. One of the best endings ever.",
    "Thanks again for yesterday. Second time this month you've saved me.",
    "One sec, my phone is about to die. Two percent battery left.",
    "Three people signed up for the workshop so far. Two of them are from the sales team.",
    "First thing tomorrow, email the accountant. Then pay the electricity bill.",
    "Look into why the nightly backup failed. Third time this week.",
])
def test_everyday_prose_with_cue_words_stays_prose(text, style):
    assert fmt(text, "auto", style) == text


def test_off_and_raw_never_change_anything():
    text = "First, milk. Second, eggs."
    assert fmt(text, "off") == text
    assert fmt(text, "auto", "raw") == text
    assert fmt(text, "lists") == "1. Milk\n2. Eggs"


def test_casual_styles_stay_flat_unless_the_speaker_used_explicit_cues():
    assert fmt("First, milk. Second, eggs.", "auto", "casual") == "First, milk. Second, eggs."
    assert fmt("pehla doodh, doosra ande", "auto", "very_casual") == "pehla doodh, doosra ande"
    assert fmt("Bullet milk, bullet eggs.", "auto", "casual") == "- Milk\n- Eggs"
    assert fmt("bullet milk, bullet eggs", "auto", "very_casual") == "- milk\n- eggs"   # no capitals added
    assert fmt("Step one, milk. Step two, eggs.", "auto", "casual") == "1. Milk\n2. Eggs"


def test_every_other_word_is_kept_in_order():
    text = "So my plan is, first, we fix the login bug, second, we write the tests, third, we ship it on Friday."
    out = fmt(text)
    assert out.startswith("So my plan is:\n1. ")
    cue = {"first", "second", "third"}
    assert [w for w in core.word_tokens(text) if w not in cue] == [w for w in core.word_tokens(out) if not w.isdigit()]


def test_unknown_mode_is_auto():
    assert structure.structure_mode("banana") == "auto"
    assert structure.structure_mode(None) == "auto"
    assert structure.structure_mode(" Lists ") == "lists"
    assert structure.structure_mode("OFF") == "off"
    assert fmt("First, milk. Second, eggs.", "banana") == "1. Milk\n2. Eggs"


def golden_rows():
    with open(os.path.join(ROOT, "spec", "golden.txt"), encoding="utf-8") as fh:
        return [ln.rstrip("\r\n").split("\t") for ln in fh if ln.startswith("structure\t")]


def unesc(s):
    return s.replace(B + "n", "\n").replace(B + "t", "\t")


def test_golden_has_rows_for_every_rule():
    assert len(golden_rows()) >= 30


@pytest.mark.parametrize("row", golden_rows())
def test_applying_twice_changes_nothing(row):
    once = structure.format_structure(unesc(row[3]), row[1], row[2])
    assert structure.format_structure(once, row[1], row[2]) == once


# ------------------------------------------------------------------ the pipeline

def _cfg(**kw):
    return dict(core.DEFAULT_CONFIG, api_key="k", **kw)


def test_the_default_is_auto():
    assert core.DEFAULT_CONFIG["structure"] == "auto"


def test_lists_are_made_after_the_cleanup_and_its_guard(monkeypatch):
    raw = "first milk second eggs third bread"
    monkeypatch.setattr(core, "cleanup", lambda cfg, r, style, label: "First, milk. Second, eggs. Third, bread.")
    r = core.process_text(_cfg(cleanup_min_words=1), raw, "notepad.exe", "Notepad")
    assert r.cleaned and r.text == "1. Milk\n2. Eggs\n3. Bread"


def test_the_guard_sees_the_cleanup_answer_not_the_list(monkeypatch):
    seen = []
    real = core.looks_valid
    monkeypatch.setattr(core, "looks_valid", lambda raw, c, s=None: seen.append(c) or real(raw, c, s))
    monkeypatch.setattr(core, "cleanup", lambda cfg, r, style, label: "First, milk. Second, eggs.")
    core.process_text(_cfg(cleanup_min_words=1), "first milk second eggs", "notepad.exe", "Notepad")
    assert seen == ["First, milk. Second, eggs."]


def test_lists_are_made_on_the_fallback_path_too(monkeypatch):
    monkeypatch.setattr(core, "cleanup", lambda cfg, r, style, label: "A summary.")   # loses words: the guard says no
    r = core.process_text(_cfg(cleanup_min_words=1), "first, milk. second, eggs.", "notepad.exe", "Notepad")
    assert r.fidelity_fallback and r.text == "1. Milk\n2. Eggs"


def test_lists_are_made_when_cleanup_is_off_or_fails(monkeypatch):
    assert core.process_text(_cfg(cleanup=False), "First, milk. Second, eggs.", "x.exe", "x").text == "1. Milk\n2. Eggs"

    def boom(*a):
        raise core.ApiError(500, "down")
    monkeypatch.setattr(core, "cleanup", boom)
    r = core.process_text(_cfg(), "First, milk. Second, eggs.", "x.exe", "x")
    assert r.cleanup_error and r.text == "1. Milk\n2. Eggs"


def test_structure_off_keeps_the_text_flat():
    assert core.process_text(_cfg(cleanup=False, structure="off"), "First, milk. Second, eggs.", "x.exe", "x").text == \
        "First, milk. Second, eggs."


def test_a_raw_style_app_gets_no_list():
    cfg = _cfg(cleanup=False, app_styles={"x.exe": "raw"})
    assert core.process_text(cfg, "First, milk. Second, eggs.", "x.exe", "x").text == "First, milk. Second, eggs."


# ------------------------------------------------------------------ the prompt follows the setting

def test_the_prompt_asks_for_no_list_when_structure_is_off():
    off = core.system_prompt("formal", [], "", "", "light", "", "off")
    # the examples are part of the static (cached) prompt; the late Layout line says flat, and the list rule defers to it
    assert "\n" + core.LAYOUT_TEXT["flat"] + "\n" in off and "Lists, unless the Layout line below says flat" in off
    assert core.LAYOUT_TEXT["auto"] not in off and core.STRUCTURE_BY_STYLE["casual"] in off


def test_auto_is_the_prompt_as_before():
    for style in ("neutral", "formal", "casual", "notes"):
        assert core.system_prompt(style, ["Ada"], "Slack") == core.system_prompt(style, ["Ada"], "Slack", "", "light", "", "auto")


def test_lists_only_asks_for_lists_and_no_paragraph_breaks():
    p = core.system_prompt("neutral", [], "", "", "light", "", "lists")
    assert "\nLayout: lists as described above; no blank lines unless the speaker says new paragraph.\n" in p
    assert core.LAYOUT_TEXT["auto"] not in p and "- First, the pricing page" in p
    assert core.system_prompt("casual", [], "", "", "light", "", "lists").count(core.STRUCTURE_BY_STYLE["casual"]) == 1


def test_the_cleanup_request_carries_the_structure_setting(monkeypatch):
    bodies = []

    class R:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "Hello there."}}]}
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: bodies.append(kw["json"]) or R())
    core.cleanup(_cfg(structure="off"), "hello there", "neutral", "")
    assert bodies[0]["messages"][0]["content"] == core.system_prompt("neutral", [], "", "", "light", "", "off")


# ------------------------------------------------------------------ paragraphs at pauses (Windows only)

def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


SENT_A = "I spent most of the morning on the billing bug and it turns out the retry job charged people twice."
SENT_B = "Then I looked at the dashboard work and the new charts load fast but the legend overlaps on small screens."
SENT_C = "Tomorrow I will fix that and then start on the export feature with the new design from the design team at work."


def test_a_long_pause_starts_a_new_paragraph():
    segs = [seg(0, 6, SENT_A), seg(7.5, 13, SENT_B), seg(13.4, 19, SENT_C)]
    text = " ".join([SENT_A, SENT_B, SENT_C])
    assert structure.add_paragraphs(text, segs) == SENT_A + "\n\n" + SENT_B + " " + SENT_C


def test_the_break_follows_the_cleaned_text():
    segs = [seg(0, 6, "um " + SENT_A.lower()), seg(7.5, 13, SENT_B.lower()), seg(13.4, 19, SENT_C.lower())]
    text = " ".join([SENT_A, SENT_B, SENT_C])   # what the cleanup made of it (noise dropped, capitals)
    assert structure.add_paragraphs(text, segs) == SENT_A + "\n\n" + SENT_B + " " + SENT_C


@pytest.mark.parametrize("segs,text", [
    ([seg(0, 6, SENT_A), seg(6.5, 13, SENT_B), seg(13.4, 19, SENT_C)], " ".join([SENT_A, SENT_B, SENT_C])),   # no long pause
    ([seg(0, 2, "Hello there."), seg(5, 7, "How are you?")], "Hello there. How are you?"),                       # under 60 words
    ([], " ".join([SENT_A, SENT_B, SENT_C])),                                                                   # no segments
    (None, " ".join([SENT_A, SENT_B, SENT_C])),
    ([seg(0, 6, SENT_A[:-1] + " and"), seg(7.5, 13, SENT_B), seg(13.4, 19, SENT_C)],                          # pause mid-sentence
     SENT_A[:-1] + " and " + SENT_B + " " + SENT_C),
])
def test_no_break_without_a_long_pause_at_a_sentence_end(segs, text):
    assert structure.add_paragraphs(text, segs) == text


def test_paragraphs_twice_change_nothing():
    segs = [seg(0, 6, SENT_A), seg(7.5, 13, SENT_B), seg(13.4, 19, SENT_C)]
    once = structure.add_paragraphs(" ".join([SENT_A, SENT_B, SENT_C]), segs)
    assert structure.add_paragraphs(once, segs) == once


def test_process_text_breaks_paragraphs_only_in_auto_and_not_in_casual_styles():
    segs = [seg(0, 6, SENT_A), seg(7.5, 13, SENT_B), seg(13.4, 19, SENT_C)]
    raw = " ".join([SENT_A, SENT_B, SENT_C])
    split = SENT_A + "\n\n" + SENT_B + " " + SENT_C
    assert core.process_text(_cfg(cleanup=False), raw, "x.exe", "x", segs).text == split
    assert core.process_text(_cfg(cleanup=False, structure="lists"), raw, "x.exe", "x", segs).text == raw
    assert core.process_text(_cfg(cleanup=False, structure="off"), raw, "x.exe", "x", segs).text == raw
    assert core.process_text(_cfg(cleanup=False, default_style="casual"), raw, "x.exe", "x", segs).text == raw
    assert core.process_text(_cfg(cleanup=False), raw, "x.exe", "x").text == raw   # no segments: no breaks


class _Answer:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def test_transcribe_asks_whisper_for_segments_and_passes_them_on(monkeypatch):
    sent = []
    body = {"text": "hello there", "segments": [{"start": 0, "end": 1, "text": " hello"}, {"start": 3, "end": 4, "text": " there"}]}
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: sent.append(kw["data"]) or _Answer(body))
    assert core.transcribe(_cfg(), b"RIFF") == "hello there"
    assert sent[0]["response_format"] == "verbose_json"
    assert core.last_segments() == [{"start": 0.0, "end": 1.0, "text": "hello"}, {"start": 3.0, "end": 4.0, "text": "there"}]


@pytest.mark.parametrize("cfg", [
    dict(structure="lists"), dict(structure="off"), dict(stt_model="gpt-4o-transcribe"),
])
def test_plain_json_when_no_paragraphs_are_wanted_or_the_model_has_no_segments(monkeypatch, cfg):
    sent = []
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: sent.append(kw["data"]) or _Answer({"text": "hi"}))
    assert core.transcribe(_cfg(**cfg), b"RIFF") == "hi"
    assert sent[0]["response_format"] == "json" and core.last_segments() is None


def test_a_server_that_refuses_verbose_json_gets_plain_json(monkeypatch):
    sent = []

    def post(url, **kw):
        sent.append(kw["data"]["response_format"])
        if kw["data"]["response_format"] == "verbose_json":
            a = _Answer({"error": {"message": "bad response_format"}})
            a.status_code = 400
            a.text = "bad"
            return a
        return _Answer({"text": "hi"})
    monkeypatch.setattr(core, "post_with_retry", post)
    assert core.transcribe(_cfg(), b"RIFF") == "hi"
    assert sent == ["verbose_json", "json"] and core.last_segments() is None


def test_verbose_json_and_a_flac_upload_work_together(monkeypatch):
    """The segment times are asked for whatever the file is: a FLAC upload is named .flac, and a 400 for verbose_json is
    answered with the same FLAC file and plain json."""
    sent = []
    body = {"text": "hello there", "segments": [{"start": 0, "end": 1, "text": " hello"}, {"start": 3, "end": 4, "text": " there"}]}

    def post(url, **kw):
        sent.append((kw["data"]["response_format"], kw["files"]["file"][0], kw["files"]["file"][2]))
        if len(sent) == 1:
            return _Answer(body)
        if len(sent) == 2:
            a = _Answer({"error": {"message": "bad response_format"}})
            a.status_code, a.text = 400, "bad"
            return a
        return _Answer({"text": "hi"})
    monkeypatch.setattr(core, "post_with_retry", post)
    assert core.transcribe(_cfg(), b"fLaC\x00\x00") == "hello there"
    assert core.last_segments() == [{"start": 0.0, "end": 1.0, "text": "hello"}, {"start": 3.0, "end": 4.0, "text": "there"}]
    assert core.transcribe(_cfg(), b"fLaC\x00\x00") == "hi"
    assert sent == [("verbose_json", "audio.flac", "audio/flac")] + [("verbose_json", "audio.flac", "audio/flac"), ("json", "audio.flac", "audio/flac")]
    assert core.last_segments() is None


def test_segments_without_numbers_are_ignored(monkeypatch):
    body = {"text": "hi", "segments": [{"start": "x", "end": 1, "text": "hi"}, "junk"]}
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: _Answer(body))
    assert core.transcribe(_cfg(), b"RIFF") == "hi"
    assert core.last_segments() is None


def test_process_detailed_uses_the_segments_of_its_upload(monkeypatch):
    raw = " ".join([SENT_A, SENT_B, SENT_C])
    segs = [{"start": 0, "end": 6, "text": SENT_A}, {"start": 7.5, "end": 13, "text": SENT_B}, {"start": 13.4, "end": 19, "text": SENT_C}]
    monkeypatch.setattr(core, "post_with_retry", lambda url, **kw: _Answer({"text": raw, "segments": segs}))
    loud = b"\x10\x27" * 32000
    r = core.process_detailed(_cfg(cleanup=False), loud, "x.exe", "x")
    assert r.text == SENT_A + "\n\n" + SENT_B + " " + SENT_C
