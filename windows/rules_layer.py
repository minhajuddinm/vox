"""Deterministic cleanup without the AI (the "rules layer"): the text Vox types when the AI cleanup was wanted but did not
give the text, that is a phrase under `cleanup_min_words`, an error or timeout, or an answer the fidelity guard rejected.

It only removes pure noises (um, uh, er, erm, ah, hmm) and spoken punctuation commands, writes the marks those commands
name, and fixes capitals and the final mark for the style. It never adds a word, and in Light strength it never drops
any other word. Standard strength also takes a typed-value self-correction ("by thursday no wait friday" -> "by friday"):
only when a cue sits between two values of the same kind (weekday, month, number or time) that differ. The idea of acting
only on typed values comes from whisper-local (MIT, src/whisper_key/dictation_cleanup.py); no code was copied.

Spoken "new line" / "new paragraph" are applied before (vox_core.apply_spoken_commands). Twin: RulesLayer.java; the
`rulelayer` and `fallback` rows of spec/golden.txt keep the two equal. ASCII-only patterns on purpose: Hindi and
Hinglish words are never touched (only the capital at a sentence start).
"""
import re

_WORD = "A-Za-z0-9_'’\\-"     # a character that continues a word (so "uh-huh" and "umbrella" are not noises)
_L = "(?<![" + _WORD + "])"
_R = "(?![" + _WORD + "])"
_END = "(?![\\s\\S])"                # the end of the text ($ also matches before a last line break)
_SP = "[ \t]"
_OPEN = ".?!,;:\n"                     # text ending in one of these has nothing a mark could follow

# pure noises, lowercase or with a capital only (so "ER" or "AH" as an abbreviation stay); "mm" is left (5 mm); "Er" with a
# capital only before a comma ("Er Rahul Sharma": the title for an engineer)
_NOISE = re.compile("(," + _SP + "*)?" + _L + "(?:[Uu](?:m+|h+|hm+)|[Ee]rm+|er|Er(?=,)|[Aa]h+|[Hh]m+)" + _R
                    + "([,.?!;:]?)" + _SP + "*")
_PUNCT = re.compile("(" + _SP + "*,?" + _SP + "*)" + _L + "(comma|period|full" + _SP + "+stop|question" + _SP
                    + "+mark|exclamation" + _SP + "+(?:mark|point))" + _R + "([.,?!]?)", re.I | re.A)
_MARKS = {"comma": ",", "period": ".", "full stop": ".", "question mark": "?", "exclamation mark": "!",
          "exclamation point": "!"}
# a punctuation name right after one of these words is a noun ("the trial period", "a comma", "a full stop")
_NOUN_AFTER = frozenset("a an the this that these those my your his her its our their each every any no one per same "
                        "whole entire first last next trial grace notice waiting free time probation billing cooling "
                        "holding oxford serial big huge small extra missing single short long given certain".split())
# "period" / "full stop" with one of these up to 3 words before it in its sentence is the noun ("over a six-month period")
_NOUN_NEAR = frozenset("a an the this that these those my your our his her their its each every per over during for in "
                       "of".split())
_PREV_WORD = re.compile("([A-Za-z]+)" + _END)
_NEXT_WORD = re.compile(_SP + "*([A-Za-z]+)")

_DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = "january|february|march|april|june|july|august|september|october|november|december"   # not "may"
_NUMW = ("zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
         "seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakh|"
         "million|crore|billion")
_AMPM = "(?:a\\.m\\.|p\\.m\\.|a" + _SP + "?m|p" + _SP + "?m)"
_NUM = ("(?:[0-9]+(?:[:.,][0-9]+)*|(?:" + _NUMW + ")(?:" + _SP + "+(?:" + _NUMW + "))*)(?:" + _SP + "*" + _AMPM
        + ")?")
_VALUE = "(" + _DAYS + "|" + _MONTHS + "|" + _NUM + ")"
_STRONG_CUE = ("(?:no[,.]?" + _SP + "+wait|wait[,.]?" + _SP + "+no|no[,.]?" + _SP + "+no|nahi[,.]?" + _SP + "+nahi|i"
               + _SP + "+mean)")
# a weak cue (sorry, actually) only inside the sentence: "Call me at five. Sorry, six is better." is no correction
_CORRECTION = re.compile(_L + _VALUE + "(?:,?" + _SP + "+(?:" + _STRONG_CUE + "|sorry|actually)|\\." + _SP + "+"
                         + _STRONG_CUE + ")[,.]?" + _SP + "+" + _VALUE + _R, re.I | re.A)
_DAY_SET = frozenset(_DAYS.split("|"))
_MONTH_SET = frozenset(_MONTHS.split("|"))

_I = re.compile(_L + "i(?=[ \t\n,;:!?)\"]|['’](?:m|ll|ve|d)" + _R + "|\\.(?![A-Za-z0-9])|" + _END + ")")
_NAMES = re.compile(_L + "(monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|april|june|july|"
                    "august|september|october|november|december)" + _R)   # "may" and "march" are also verbs
_SENTENCE_START = re.compile(r"(^|[.!?][ \t]+|\n[ \t]*)([^\W\d_])")
_LOWER_START = re.compile("(^|[.!?][ \t]+|\n[ \t]*)([A-Z])(?=[a-z]+(?:['’][a-z]+)?" + _R + ")")
_QUESTION = frozenset("what why how where who whose which is are can did does kya kab kahan kaun kaise kyun".split())
_ASCII_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_ONLY_MARKS = frozenset(" \t\n.,?!;:-")


def capitals(text):
    """A capital letter at the start and after each sentence end or line break; the rest as it is."""
    return _SENTENCE_START.sub(lambda m: m.group(1) + m.group(2).upper(), text)


def _is_open(prefix):
    return prefix == "" or prefix[-1] in _OPEN


def _drop_noises(t):
    def fix(m):
        comma, mark = m.group(1) is not None, m.group(2)
        is_open = _is_open(t[:m.start()].rstrip(" \t"))
        rest = t[m.end():]
        at_end = rest == "" or rest[0] == "\n"
        if mark and mark != ",":
            return "" if is_open else mark + " "
        if mark == "," or not at_end:
            return ", " if comma and not is_open and not at_end else " "
        return ""
    return _NOISE.sub(fix, t)


def _noun_near(prefix):
    """True when one of _NOUN_NEAR is among the last 3 words of prefix's sentence."""
    sentence = re.split("[.?!\n]", prefix)[-1]
    words = [re.sub("[^a-z]", "", w.lower()) for w in re.split("[ \t]+", sentence.strip(" \t"))]
    return any(w in _NOUN_NEAR for w in words[-3:])


def _spoken_marks(t):
    def fix(m):
        prefix = t[:m.start()].rstrip(" \t")
        if _is_open(prefix):
            return m.group(0)   # nothing before it to end: a word, not a command
        prev = _PREV_WORD.search(prefix)
        if prev and prev.group(1).lower() in _NOUN_AFTER:
            return m.group(0)
        name = " ".join(m.group(2).lower().split())
        if name in ("period", "full stop") and "," not in m.group(1) and _noun_near(prefix):
            return m.group(0)   # "the exam period", "a sudden full stop": a comma before it makes it the command
        rest = t[m.end():]
        nxt = _NEXT_WORD.match(rest)
        if name == "comma" and nxt and nxt.group(1).lower() in ("separated", "delimited", "splice"):
            return m.group(0)
        if name == "period" and not (m.group(3) or rest.strip(" \t") == "" or rest.lstrip(" \t")[:1] == "\n"):
            return m.group(0)   # "period" mid-sentence is far more often the word ("the period of")
        return _MARKS[name] + (" " if rest and rest[0] not in " \t\n" else "")
    return _PUNCT.sub(fix, t)


def _kind(value):
    v = value.lower()
    return "day" if v in _DAY_SET else "month" if v in _MONTH_SET else "number"


def _corrections(t):
    def fix(m):
        a, b = m.group(1), m.group(2)
        if _kind(a) == _kind(b) and " ".join(a.lower().split()) != " ".join(b.lower().split()):
            return b
        return m.group(0)
    return _CORRECTION.sub(fix, t)


def _tidy(t):
    t = re.sub("[ \t]+([,.?!;:])", r"\1", t)
    t = re.sub("[ \t]{2,}", " ", t)
    t = re.sub("[ \t]+\n", "\n", t)
    t = re.sub("\n[ \t]+", "\n", t)
    t = re.sub("^[ \t,;:]+", "", t)   # a mark left at the start had nothing before it
    return t.rstrip(" \t,")


def _words(text):
    return [w for w in re.split("[ \t\n]+", text) if w]


def _final_mark(t, style):
    if not t or t[-1] not in _ASCII_ALNUM:
        return t   # already ends in a mark, or in another script (Devanagari): left as it is
    sentence = re.split("(?<=[.?!])[ \t]+", t.split("\n")[-1])[-1]
    words = _words(sentence)
    first = re.sub("[^a-z]", "", words[0].lower()) if words else ""
    mark = "?" if first in _QUESTION and len(words) <= 8 else "."
    if style == "casual" and mark == "." and len(_words(t)) <= 12:
        return t   # a short casual message has no final period
    return t + mark


def rules_cleanup(text, style="neutral", strength="light"):
    """The rules layer on a transcript whose spoken line breaks are already applied (see the module docstring).
    style: neutral, formal, casual or very_casual (anything else counts as neutral); strength: "standard" also takes
    typed-value self-corrections. Filler-only input gives "" (spoken line breaks alone give those breaks)."""
    t = _drop_noises(text or "")
    if str(strength or "").strip().lower() == "standard":
        t = _corrections(t)
    t = _spoken_marks(t)
    if style != "very_casual":
        t = _NAMES.sub(lambda m: m.group(1).capitalize(), _I.sub("I", t))
    t = _tidy(t)
    if all(c in _ONLY_MARKS for c in t):
        return "\n" * t.count("\n")   # "new line" / "new paragraph" alone still types the break
    if style == "very_casual":   # like a text message: no capitals added, Whisper's sentence capitals undone, no final period
        t = _LOWER_START.sub(lambda m: m.group(1) + m.group(2).lower(), t)
        return t[:-1] if t.endswith(".") and not t.endswith("..") else t
    return _final_mark(capitals(t), style)
