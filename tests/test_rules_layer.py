"""The rules layer (windows/rules_layer.py): the spec rows are in spec/golden.txt (kinds rulelayer and fallback, run by
test_parity.py and ParityTest.java). These tests check the promise behind them: in Light strength no word is dropped
except a pure noise or a spoken punctuation command, and no word is added."""
import os
import re
from collections import Counter

import rules_layer
import vox_core as core

GOLDEN = os.path.join(os.path.dirname(__file__), "..", "spec", "golden.txt")
NOISE = re.compile(r"^(?:um+|uh+|uhm+|erm+|er|ah+|hm+)$")
COMMANDS = ("comma", "period", "full stop", "question mark", "exclamation mark", "exclamation point", "new line",
            "new paragraph")


def unesc(s):
    return s.replace("\\n", "\n").replace("\\t", "\t")


def words(text):
    return [w for w in re.split(r"[^\w'’-]+", text.lower()) if w]


def light_rows():
    with open(GOLDEN, encoding="utf-8") as f:
        for line in f:
            f = line.rstrip("\n").split("\t")
            if f[0] == "rulelayer" and f[2] == "light" and f[1] not in ("raw", "code"):
                yield unesc(f[3]), unesc(f[4])


def test_light_drops_only_noises_and_spoken_commands_and_adds_nothing():
    rows = list(light_rows())
    assert len(rows) > 40
    for raw, out in rows:
        kept = Counter(words(out))
        spoken = Counter(w.lower() for w in re.split(r"[^\w'’-]+", raw)
                         if w and not (NOISE.match(w.lower()) and w[1:] == w[1:].lower()))   # "ER" is no noise
        assert not kept - spoken, (raw, out)   # nothing added
        command_words = set(" ".join(COMMANDS).split())
        assert all(w in command_words for w in spoken - kept), (raw, out)   # only spoken command words went


def test_hinglish_words_are_never_noise():
    raw = "haan accha toh hum kal milte hai na arre yaar matlab bas"
    assert core.fallback_text(raw).lower().rstrip(".").split() == raw.split()


def test_actually_for_emphasis_stays_in_both_strengths():
    for strength in ("light", "standard"):
        assert core.fallback_text("it was actually really good", "neutral", strength) == "It was actually really good."


def test_an_unknown_or_missing_style_counts_as_neutral():
    assert core.fallback_text("um hello there", None, None) == "Hello there."
    assert rules_layer.rules_cleanup("hello there", "banana") == "Hello there."
