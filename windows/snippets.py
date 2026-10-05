"""Snippets: a trigger phrase you say ("my email") becomes the text you saved for it, in every app. Pure: no I/O.

The setting `snippets` is {trigger: text}. Applied last: after the AI cleanup (so the saved text never goes to the cleanup
server) and after lists (so the list pass never re-formats it; vox_core.apply_layout). Whole phrase, case ignored, any run of spaces between its words; the longest trigger wins and the
text put in is not looked at again. The same rules run on the phone (Snippets.java, golden rows `snippets`); the setting
travels with the synced profile.
"""
import json
import re
import unicodedata

MAX_SNIPPETS = 50       # snippets used (the first ones)
MAX_EXPANSION = 2000    # characters of one saved text
MAX_TRIGGER = 100       # characters of one trigger phrase
MAX_TOTAL = 20000       # bytes of all triggers and saved texts as the relay stores them (wire_size): the relay keeps a
                        # profile of at most 64,000 bytes, and a letter outside ASCII takes 6 bytes there (an emoji 12)
_SPACES = re.compile(r"[ \t\r\n]+")


def _has_word_char(s):
    return any(unicodedata.category(c)[0] in "LNM" for c in s)


def _is_word(c):
    """A letter, a combining mark (a Hindi vowel sign), a number or _ (vox_core.is_word; Java [\\p{L}\\p{M}\\p{N}_])."""
    return c == "_" or unicodedata.category(c)[0] in "LNM"


def wire_size(s):
    """Bytes `s` takes in the relay's profile: JSON with ASCII escapes, as relay.py measures it (a letter outside ASCII is
    6 bytes, an emoji 12, a line break, quote or backslash 2). Twin: Snippets.wireSize and snipWireSize in common.js."""
    return len(json.dumps(s)) - 2


def clean_snippets(value):
    """The setting made safe: only text triggers with text, a trigger's spaces made single and trimmed (at most
    MAX_TRIGGER characters and at least one letter or digit), line breaks as \\n, each text cut at MAX_EXPANSION characters,
    a repeated trigger (case ignored) dropped, at most MAX_SNIPPETS snippets and MAX_TOTAL bytes of triggers and texts as
    the relay stores them (a snippet that would go over is left out). Order kept. Twin: Snippets.clean."""
    out, seen, total = {}, set(), 0
    if not isinstance(value, dict):
        return out
    for trigger, text in value.items():
        if len(out) >= MAX_SNIPPETS:
            break
        if not isinstance(trigger, str) or not isinstance(text, str):
            continue
        t = _SPACES.sub(" ", trigger).strip(" ")
        x = text.replace("\r\n", "\n").replace("\r", "\n")[:MAX_EXPANSION]
        if not t or len(t) > MAX_TRIGGER or not _has_word_char(t) or not x.strip(" \t\n") or t.lower() in seen:
            continue
        size = wire_size(t) + wire_size(x)
        if total + size > MAX_TOTAL:
            continue
        seen.add(t.lower())
        out[t] = x
        total += size
    return out


def apply_snippets(text, value):
    """Text with every trigger phrase replaced by its saved text. Twin: Snippets.apply."""
    return expand(text, value)[0]


def expand(text, value):
    """apply_snippets, plus where each saved text landed: (text, [[start, end, the phrase as said], ...]), offsets in
    the new text. The history keeps them, so Improve my cleanup puts the phrase back from the entry itself and never
    sends a saved text, even after the snippet was changed or deleted (PRV-3; see put_back)."""
    snips = clean_snippets(value)
    if not text or not snips:
        return text, []
    items = sorted(snips.items(), key=lambda kv: -len(kv[0]))   # longest first; equal lengths keep their order
    parts = [re.compile(r"[ \t\r\n]+".join(re.escape(w) for w in t.split(" ")), re.I) for t, _ in items]
    anywhere = re.compile("|".join(p.pattern for p in parts), re.I)
    out, pos, last, size, spans = [], 0, 0, 0, []
    while True:   # the first trigger, longest first, that stands as whole words where one starts (a word's marks are
        m = anywhere.search(text, pos)   # part of it: कर never matches in करें); checked here, not with lookarounds
        if not m:
            break
        hit = None
        if not (m.start() and _is_word(text[m.start() - 1])):
            for k, p in enumerate(parts):
                h = p.match(text, m.start())
                if h and not (h.end() < len(text) and _is_word(text[h.end()])):
                    hit = (k, h.end())
                    break
        if hit:
            saved = items[hit[0]][1]
            size += m.start() - last
            spans.append([size, size + len(saved), text[m.start():hit[1]]])
            size += len(saved)
            out += [text[last:m.start()], saved]
            last = pos = hit[1]
        else:
            pos = m.start() + 1
    return "".join(out) + text[last:], spans


def put_back(text, spans):
    """`text` with each span [start, end, phrase] that expand recorded replaced by its phrase, or None when the spans do
    not fit the text (not a list of in-order, non-overlapping ranges inside it): then the entry is not sent at all."""
    if not isinstance(text, str) or not isinstance(spans, list):
        return None
    out, last = [], 0
    for s in spans:
        if not (isinstance(s, list) and len(s) == 3 and all(isinstance(v, int) and not isinstance(v, bool) for v in s[:2])
                and isinstance(s[2], str) and last <= s[0] <= s[1] <= len(text)):
            return None
        out += [text[last:s[0]], s[2]]
        last = s[1]
    return "".join(out) + text[last:]
