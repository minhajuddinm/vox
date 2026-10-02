"""Snippets: a trigger phrase you say ("my email") becomes the text you saved for it, in every app. Pure: no I/O.

The setting `snippets` is {trigger: text}. Applied after the AI cleanup (so the saved text never goes to the cleanup
server) and before lists. Whole phrase, case ignored, any run of spaces between its words; the longest trigger wins and the
text put in is not looked at again. The same rules run on the phone (Snippets.java, golden rows `snippets`); the setting
travels with the synced profile.
"""
import re
import unicodedata

MAX_SNIPPETS = 50       # snippets used (the first ones)
MAX_EXPANSION = 2000    # characters of one saved text
MAX_TRIGGER = 100       # characters of one trigger phrase
MAX_TOTAL = 20000       # characters of all saved texts together: the relay keeps a profile of at most 64,000 bytes
_SPACES = re.compile(r"[ \t\r\n]+")


def _has_word_char(s):
    return any(unicodedata.category(c)[0] in "LNM" for c in s)


def clean_snippets(value):
    """The setting made safe: only text triggers with text, a trigger's spaces made single and trimmed (at most
    MAX_TRIGGER characters and at least one letter or digit), line breaks as \\n, each text cut at MAX_EXPANSION characters,
    a repeated trigger (case ignored) dropped, at most MAX_SNIPPETS snippets and MAX_TOTAL characters of text (a snippet
    that would go over is left out). Order kept. Twin: Snippets.clean."""
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
        if total + len(x) > MAX_TOTAL:
            continue
        seen.add(t.lower())
        out[t] = x
        total += len(x)
    return out


def apply_snippets(text, value):
    """Text with every trigger phrase replaced by its saved text. Twin: Snippets.apply."""
    snips = clean_snippets(value)
    if not text or not snips:
        return text
    items = sorted(snips.items(), key=lambda kv: -len(kv[0]))   # longest first; equal lengths keep their order
    alts = "|".join("(" + r"[ \t\r\n]+".join(re.escape(w) for w in t.split(" ")) + ")" for t, _ in items)
    pattern = re.compile(r"(?<!\w)(?:" + alts + r")(?!\w)", re.I)
    return pattern.sub(lambda m: items[m.lastindex - 1][1], text)


def unexpand(text, value):
    """Text with each saved text put back as its trigger phrase: what Improve my cleanup sends instead of the saved texts."""
    if not isinstance(text, str):
        return text
    for t, x in sorted(clean_snippets(value).items(), key=lambda kv: -len(kv[1])):
        text = text.replace(x, t)
    return text
