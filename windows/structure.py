"""Lists from spoken cues, and paragraph breaks at long pauses. Pure: no I/O, no network.

The list rules also run on the phone (android Structure.java); spec/golden.txt (kind `structure`) keeps the two equal, so a
change here needs the same change there and new golden rows. The paragraph breaks need the speech server's segment times
and exist on Windows only.

Lists are made only from cues the speaker says, never from commas: ordinals in sequence ("first ... second ...", also
"firstly", "first of all"), introduced numbers ("point one", "item one", "step one", "number one" then "two" or "point two"),
Hindi and Hinglish ordinals (pehla, doosra, teesra; पहला, दूसरा, तीसरा) and bullet cues ("bullet", "bullet point", "new bullet",
"next bullet", "next point", "next item"). The cue words are removed and every other word is kept, in order. Prose that
only looks like a cue stays prose: "number one priority", "two people", "first impressions" (a weak cue: no punctuation
after it and no item word such as "we" or "the", in a list with no colon before it, under three cues and not ordinals
after commas), "number two is" (a verb after the cue), "second of all", "one bullet point to make".
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
# An ordinal or numbered cue with no punctuation after it starts an item only before one of these ("first we eat"); before
# any other word it is prose ("first impressions", "number one priority", "two people", "second time").
_ITEM_START = frozenset("i we you he she they it the a an my our your his her their its this that these those there here "
                        "let's lets let i'll we'll you'll i'm we're you're it's i've we've".split())
# A numbered, ordinal or bare cue right before one of these is the subject of a sentence ("number two is the budget",
# "second was better"), not a cue.
_VERBS = frozenset("is are was were has have had will would can could should must may might".split())
# A bullet cue right after one of these is the noun ("one bullet point to make", "a new bullet").
_DETERMINERS = frozenset("a an the one this that another each every any some no my our your his her their its".split())
_ABBREVIATIONS = frozenset("dr mr mrs ms st vs etc jr sr prof".split())   # "Dr. Smith": its dot ends no sentence
_CLAUSE_PUNCT = ".,;:!?।"           # । = the Devanagari danda
_SENTENCE_PUNCT = ".!?।"
_SPACE = " \t\r\n"
_ALREADY_LIST = re.compile(r"(?m)^[ \t]*(?:[-*•]|[0-9]+[.)])[ \t]+[^ \t\r\n]")
_NEWLINES = re.compile(r"[ \t\r\n]*\n[ \t\r\n]*")
_WORD_END = ("", " ", "\t", ",", ";", ":", "!", "?")   # what may follow an item's first word for it to get a capital
_SENTENCE_INSIDE = re.compile("[.!?।][ \t\r\n]")   # a sentence end inside an item (the dot of example.com is not one)
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


def _item_follows(text, words, after):
    """True when the cue that ends before word `after` is followed by punctuation or a line break, by nothing, or by a
    word that starts an item (_ITEM_START): "First, milk" and "first we eat", not "first impressions"."""
    if after >= len(words):
        return True
    gap = text[words[after - 1][2]:words[after][1]]
    return any(c not in " \t" for c in gap) or words[after][0].replace("’", "'") in _ITEM_START


def _verb_follows(text, words, after):
    return after < len(words) and words[after][0] in _VERBS and _only_space(text[words[after - 1][2]:words[after][1]])


def _match(text, words, i):
    """The cue starting at word i as (family, number, index after its last word, where it may stand, weak), or None.
    Families: bul (a bullet), num (point/item/step/number N), bare (a bare number continuing a num list), ord, hi.
    Where: ANYWHERE, CLAUSE (at a clause start) or SENTENCE (at a sentence start: a bare "two" after a comma is prose).
    Weak: an ordinal, a "number N" or a bare number not followed by an item (_item_follows: "first impressions", "number
    one priority", "two people"); a list with a weak cue needs more (_sure). A numbered, ordinal or bare cue before a verb
    (_VERBS: "number two is the budget") is no cue, "second of all" is prose (only "first of all" is a cue) and a bullet
    cue after a determiner is the noun."""
    w = words[i][0]
    nxt = words[i + 1][0] if i + 1 < len(words) and _only_space(text[words[i][2]:words[i + 1][1]]) else None
    if (w, nxt) in _BULLETS_ANYWHERE:
        noun = i > 0 and words[i - 1][0] in _DETERMINERS and _only_space(text[words[i - 1][2]:words[i][1]])
        return None if noun else ("bul", 0, i + 2, ANYWHERE, False)
    if (w, nxt) in _BULLETS_AT_CLAUSE:
        return "bul", 0, i + 2, CLAUSE, False
    if w == "bullet":
        return "bul", 0, i + 1, CLAUSE, False
    if w in _INTROS and nxt in _NUMBERS:
        if _verb_follows(text, words, i + 2):
            return None
        return "num", _NUMBERS[nxt], i + 2, CLAUSE, w == "number" and not _item_follows(text, words, i + 2)
    if w in _ORD and nxt == "of" and i + 2 < len(words) and words[i + 2][0] == "all"             and _only_space(text[words[i + 1][2]:words[i + 2][1]]):
        return ("ord", 1, i + 3, CLAUSE, False) if w == "first" else None
    if w in _ORD:
        if not w.endswith("ly") and _verb_follows(text, words, i + 1):
            return None
        return "ord", _ORD[w], i + 1, CLAUSE, not (w.endswith("ly") or _item_follows(text, words, i + 1))
    if w in _HINDI:
        return "hi", _HINDI[w], i + 2 if nxt == "point" else i + 1, CLAUSE, False
    if w in _NUMBERS and _NUMBERS[w] >= 2:
        if _verb_follows(text, words, i + 1):
            return None
        return "bare", _NUMBERS[w], i + 1, SENTENCE, not _item_follows(text, words, i + 1)
    return None


def _cues(text, words):
    """Every cue in a position where it counts, in order: (family, number, start, end, weak) with character offsets."""
    out, i = [], 0
    while i < len(words):
        m = _match(text, words, i)
        if m:
            fam, n, after, where, weak = m
            ok = where == ANYWHERE or _starts(text, words[i][1], _SENTENCE_PUNCT if where == SENTENCE else _CLAUSE_PUNCT) \
                or (n == 1 and fam in ("ord", "num") and _after_be(text, words, i))
            if ok:
                out.append((fam, n, words[i][1], words[after - 1][2], weak))
                i = after
                continue
        i += 1
    return out


def _sure(text, chosen):
    """True when a list with a weak cue (see _match) is still meant as one: the lead-in ends with a colon ("three things:
    first eggs, second milk"), there are three cues or more, or the cues are ordinals and each one after the first follows
    a comma or semicolon ("first check the logs, second restart the server"). After a full stop it stays prose ("First
    impressions ... Second monitor support is flaky", "First buy milk. Second call mom")."""
    if not any(c[4] for c in chosen) or len(chosen) >= 3 or text[:chosen[0][2]].rstrip(_SPACE).endswith(":"):
        return True
    return chosen[0][0] == "ord" and all(text[:c[2]].rstrip(_SPACE)[-1:] in (",", ";") for c in chosen[1:])


def _sequence(text, cues, flat):
    """The first run of cues that makes a list of two or more items (_sure when a cue is weak), or None."""
    for k, (fam, n, _, _, _) in enumerate(cues):
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
        if len(chosen) >= 2 and _sure(text, chosen):
            return chosen
    return None


def _capitalised(item):
    """The item with a capital first letter when its first word is all lowercase (an iPhone stays an iPhone)."""
    j = 0
    while j < len(item) and _is_word_char(item[j]):
        j += 1
    first = item[:j]
    if not first or not first[0].islower() or any(c.isupper() for c in first) or item[j:j + 1] not in _WORD_END:
        return item   # a word glued to more (me@example.com, node.js) keeps its case too
    return first[0].upper() + item[1:]


def _clean_item(body, flat_case):
    """One item: line breaks inside made spaces, the punctuation around the cue and at the end dropped (a full stop too
    when the item is one sentence), and a capital first letter unless very casual."""
    s = _NEWLINES.sub(" ", body).lstrip(_SPACE + ",.;:-–—।").rstrip(_SPACE + ",;:")
    inner = s[:-1]
    if s[-1:] in (".", "।") and not any(not _abbreviation_dot(inner, m.start()) for m in _SENTENCE_INSIDE.finditer(inner)):
        s = s[:-1].rstrip(_SPACE)
    return s if flat_case else _capitalised(s)


def _abbreviation_dot(text, pos):
    """True when text[pos] is the dot of an abbreviation (_ABBREVIATIONS) or of a single letter ("Dr. Smith", "3 p.m.
    today", "e.g. tea"): it ends no sentence."""
    k = pos
    while k > 0 and _is_word_char(text[k - 1]):
        k -= 1
    word = text[k:pos].lower()
    return text[pos] == "." and (len(word) == 1 and word.isalpha() or word in _ABBREVIATIONS)


def _sentence_end(text):
    """The first sentence end in text (_SENTENCE_END) that is not an abbreviation's dot, or None."""
    return next((m for m in _SENTENCE_END.finditer(text) if not _abbreviation_dot(text, m.start())), None)


def format_structure(text, mode="auto", style="neutral"):
    """Text with a spoken list written as one: the lead-in, then one item per line ("1. " after ordinals and numbers,
    "- " after bullet cues), then the rest of the text after a blank line. Off, the raw style, text that already has list
    markers and text without two cues in sequence come back unchanged; running it twice changes nothing.
    Casual and very casual styles take only the explicit cues (bullets and point/item/step/number one). Twin: Structure.format."""
    style = (style or "").strip().lower()
    if not text or structure_mode(mode) == "off" or style == "raw" or _ALREADY_LIST.search(text):
        return text
    seq = _sequence(text, _cues(text, _words(text)), style in FLAT_STYLES)
    if not seq:
        return text
    bodies = [text[seq[k][3]:seq[k + 1][2]] for k in range(len(seq) - 1)] + [text[seq[-1][3]:]]
    last = bodies[-1].lstrip(_SPACE + ",.;:-–—।")
    trailer = ""
    m = _sentence_end(last)
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
