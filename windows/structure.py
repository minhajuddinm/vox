"""Lists from spoken cues, and paragraph breaks at long pauses. Pure: no I/O, no network.

The list rules also run on the phone (android Structure.java); spec/golden.txt (kind `structure`) keeps the two equal, so a
change here needs the same change there and new golden rows. The paragraph breaks need the speech server's segment times
and exist on Windows only.

Lists are made only from cues the speaker says, never from commas: ordinals in sequence ("first ... second ...", also
"firstly", "first of all"), introduced numbers ("point one", "item one", "step one", "number one" then "two" or "point two"),
Hindi and Hinglish ordinals (pehla, doosra, teesra; पहला, दूसरा, तीसरा) and bullet cues ("bullet", "bullet point", "new bullet",
"next bullet", "next point", "next item"). The cue words are removed and every other word is kept, in order.
"""
import difflib
import re
import unicodedata

MODES = ("off", "auto", "lists")
FLAT_STYLES = ("casual", "very_casual")   # stay flat unless the speaker used an explicit cue (bullets, point one)

_ORD = {}
for _n, _words in enumerate(("first firstly", "second secondly", "third thirdly", "fourth fourthly", "fifth fifthly",
                             "sixth sixthly", "seventh", "eighth", "ninth", "tenth"), 1):
    for _w in _words.split():
        _ORD[_w] = _n
_HINDI = {}
for _n, _words in enumerate((
        "pehla pehli pahla pahli पहला पहली",
        "doosra doosri dusra dusri दूसरा दूसरी",
        "teesra teesri tisra tisri तीसरा तीसरी",
        "chautha chauthi चौथा चौथी",
        "paanchva paanchvan panchva पाँचवाँ पांचवां"), 1):
    for _w in _words.split():
        _HINDI[_w] = _n
_NUMBERS = {w: n for n, w in enumerate("one two three four five six seven eight nine ten".split(), 1)}
_NUMBERS.update({str(n): n for n in range(1, 11)})
_INTROS = ("point", "item", "step", "number")
_BULLETS_ANYWHERE = (("bullet", "point"), ("new", "bullet"), ("next", "bullet"))
_BULLETS_AT_CLAUSE = (("next", "point"), ("next", "item"))
_BE = ("are", "is", "were")              # "the steps are step one ...": the first cue may follow these
_CLAUSE_PUNCT = ".,;:!?।"           # । = the Devanagari danda
_SENTENCE_PUNCT = ".!?।"
_SPACE = " \t\r\n"
_ALREADY_LIST = re.compile(r"(?m)^[ \t]*(?:[-*•]|[0-9]+[.)])[ \t]+[^ \t\r\n]")
_NEWLINES = re.compile(r"[ \t\r\n]*\n[ \t\r\n]*")
_SENTENCE_END = re.compile("[.!?।][\"'”’)\\]]*[ \t\r\n]+(?=[^ \t\r\n])")


def structure_mode(value):
    """The "Lists and paragraphs" setting as off, auto or lists; unset or anything else is auto. Twin: Structure.mode."""
    v = str(value or "").strip().lower()
    return v if v in MODES else "auto"


def _is_word_char(ch):
    return unicodedata.category(ch)[0] in "LNM"


def _words(text):
    """The words of text as (lowercase word, start, end): letters, numbers and marks, an apostrophe inside a word kept."""
    out, i, n = [], 0, len(text)
    while i < n:
        if not _is_word_char(text[i]):
            i += 1
            continue
        j = i
        while j < n and (_is_word_char(text[j]) or (text[j] in "'’" and j + 1 < n and _is_word_char(text[j + 1]))):
            j += 1
        out.append((text[i:j].lower(), i, j))
        i = j
    return out


def _only_space(s):
    return all(c in _SPACE for c in s)


def _starts(text, pos, marks):
    """True when pos starts a clause (marks = _CLAUSE_PUNCT) or a sentence (_SENTENCE_PUNCT): the start of the text, a
    line break before it, or one of the marks before it (spaces between are fine)."""
    i = pos - 1
    while i >= 0 and text[i] in _SPACE:
        if text[i] == "\n":
            return True
        i -= 1
    return i < 0 or text[i] in marks


def _after_be(text, words, i):
    return i > 0 and words[i - 1][0] in _BE and _only_space(text[words[i - 1][2]:words[i][1]])


ANYWHERE, CLAUSE, SENTENCE = 0, 1, 2


def _match(text, words, i):
    """The cue starting at word i as (family, number, index after its last word, where it may stand), or None.
    Families: bul (a bullet), num (point/item/step/number N), bare (a bare number continuing a num list), ord, hi.
    Where: ANYWHERE, CLAUSE (at a clause start) or SENTENCE (at a sentence start: a bare "two" after a comma is prose)."""
    w = words[i][0]
    nxt = words[i + 1][0] if i + 1 < len(words) and _only_space(text[words[i][2]:words[i + 1][1]]) else None
    if (w, nxt) in _BULLETS_ANYWHERE:
        return "bul", 0, i + 2, ANYWHERE
    if (w, nxt) in _BULLETS_AT_CLAUSE:
        return "bul", 0, i + 2, CLAUSE
    if w == "bullet":
        return "bul", 0, i + 1, CLAUSE
    if w in _INTROS and nxt in _NUMBERS:
        return "num", _NUMBERS[nxt], i + 2, CLAUSE
    if w == "first" and nxt == "of" and i + 2 < len(words) and words[i + 2][0] == "all" \
            and _only_space(text[words[i + 1][2]:words[i + 2][1]]):
        return "ord", 1, i + 3, CLAUSE
    if w in _ORD:
        return "ord", _ORD[w], i + 1, CLAUSE
    if w in _HINDI:
        return "hi", _HINDI[w], i + 2 if nxt == "point" else i + 1, CLAUSE
    if w in _NUMBERS and _NUMBERS[w] >= 2:
        return "bare", _NUMBERS[w], i + 1, SENTENCE
    return None


def _cues(text, words):
    """Every cue in a position where it counts, in order: (family, number, start, end) with character offsets."""
    out, i = [], 0
    while i < len(words):
        m = _match(text, words, i)
        if m:
            fam, n, after, where = m
            ok = where == ANYWHERE or _starts(text, words[i][1], _SENTENCE_PUNCT if where == SENTENCE else _CLAUSE_PUNCT) \
                or (n == 1 and fam in ("ord", "num") and _after_be(text, words, i))
            if ok:
                out.append((fam, n, words[i][1], words[after - 1][2]))
                i = after
                continue
        i += 1
    return out


def _sequence(cues, flat):
    """The first run of cues that makes a list of two or more items, or None."""
    for k, (fam, n, _, _) in enumerate(cues):
        if fam == "bare" or (fam != "bul" and n != 1) or (flat and fam in ("ord", "hi")):
            continue
        chosen, expected = [cues[k]], 2
        for c in cues[k + 1:]:
            if fam == "bul":
                if c[0] == "bul":
                    chosen.append(c)
            elif (c[0] == fam or (fam == "num" and c[0] == "bare")) and c[1] == expected:
                chosen.append(c)
                expected += 1
        if len(chosen) >= 2:
            return chosen
    return None


def _capitalised(item):
    """The item with a capital first letter when its first word is all lowercase (an iPhone stays an iPhone)."""
    j = 0
    while j < len(item) and _is_word_char(item[j]):
        j += 1
    first = item[:j]
    if not first or not first[0].islower() or any(c.isupper() for c in first):
        return item
    return first[0].upper() + item[1:]


def _clean_item(body, flat_case):
    s = _NEWLINES.sub(" ", body).lstrip(_SPACE + ",.;:-–—।").rstrip(_SPACE + ",;:")
    if s[-1:] in (".", "।") and not any(c in ".!?।" for c in s[:-1]):
        s = s[:-1].rstrip(_SPACE)
    return s if flat_case else _capitalised(s)


def format_structure(text, mode="auto", style="neutral"):
    """Text with a spoken list written as one: the lead-in, then one item per line ("1. " after ordinals and numbers,
    "- " after bullet cues), then the rest of the text after a blank line. Off, the raw style, text that already has list
    markers and text without two cues in sequence come back unchanged; running it twice changes nothing.
    Casual and very casual styles take only the explicit cues (bullets and point/item/step/number one). Twin: Structure.format."""
    style = (style or "").strip().lower()
    if not text or structure_mode(mode) == "off" or style == "raw" or _ALREADY_LIST.search(text):
        return text
    seq = _sequence(_cues(text, _words(text)), style in FLAT_STYLES)
    if not seq:
        return text
    bodies = [text[seq[k][3]:seq[k + 1][2]] for k in range(len(seq) - 1)] + [text[seq[-1][3]:]]
    last = bodies[-1].lstrip(_SPACE + ",.;:-–—।")
    trailer = ""
    m = _SENTENCE_END.search(last)
    if m:
        cut = len(last[:m.end()].rstrip(_SPACE))
        last, trailer = last[:cut], last[m.end():].strip(_SPACE)
    bodies[-1] = last
    items = [_clean_item(b, style == "very_casual") for b in bodies]
    if not all(items):
        return text
    marker = "- " if seq[0][0] == "bul" else None
    lines = [(marker or "%d. " % (k + 1)) + it for k, it in enumerate(items)]
    lead = text[:seq[0][2]].strip(_SPACE).rstrip(_SPACE + ",;-–—")
    if lead and lead[-1] not in ".!?:।":
        lead += ":"
    out = "\n".join(([lead] if lead else []) + lines)
    return out + "\n\n" + trailer if trailer else out


# ------------------------------------------------------------------ paragraphs at pauses (Windows only)

PAUSE_SECONDS = 1.2         # a gap this long between two speech segments starts a new paragraph
PARAGRAPH_MIN_WORDS = 60    # shorter texts are never split
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")


def _plain_words(text):
    return [(m.group(0).lower().replace("’", "'"), m.start()) for m in _WORD.finditer(text or "")]


def _ends_sentence(before):
    b = before.rstrip("\"'”’)")
    return b[-1:] in (".", "!", "?", "।")


def add_paragraphs(text, segments, pause=PAUSE_SECONDS, min_words=PARAGRAPH_MIN_WORDS):
    """Text with a blank line where the speaker paused at least `pause` seconds between two segments of the speech
    server's answer ({"start", "end", "text"}). The pause is found in the raw words and placed in `text` (the cleaned
    words) by matching the two word lists; a pause that does not land right after a sentence end, or lands where the words
    differ, is skipped. Texts of `min_words` words or fewer, and no segments, give the text unchanged."""
    if not text or not segments or len(segments) < 2:
        return text
    final = _plain_words(text)
    if len(final) <= min_words:
        return text
    raw, breaks, prev_end = [], [], None
    for s in segments:
        if prev_end is not None and s["start"] - prev_end >= pause:
            breaks.append(len(raw))
        raw += [w for w, _ in _plain_words(s["text"])]
        prev_end = s["end"]
    if not breaks:
        return text
    blocks = difflib.SequenceMatcher(None, raw, [w for w, _ in final], autojunk=False).get_matching_blocks()
    cuts = set()
    for k in breaks:
        for i, j, size in blocks:
            if i <= k < i + size:
                cuts.add(final[j + k - i][1])
                break
    out = text
    for pos in sorted(cuts, reverse=True):
        before = out[:pos].rstrip(" \t")
        if before and not before.endswith("\n") and _ends_sentence(before):
            out = before + "\n\n" + out[pos:]
    return out
