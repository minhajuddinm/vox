"""Learn from my corrections: when the user fixes a word in text Vox has just typed, the fix goes into the dictionary.

Pure rules, no I/O. The Android twins are AutoLearn.java (detect, learn, the learned log) and AutoLearnWatch.java (Watch);
spec/golden.txt (kinds autocorrect and autolearn) runs the same cases against both. The Windows side that reads the field
is correction_watch.py; Android's is VoxAccessibilityService.

Privacy: the field's text is only looked at during a call, except the Watch's last snapshot, which lives in memory until
the watch ends and is then dropped. Nothing here logs text; callers log counts only.
"""
import time
import unicodedata

import vox_core as core

AUTO_LEARN_WINDOW_S = 180   # the longest a watch lasts after Vox typed (AutoLearnWatch.AUTO_LEARN_WINDOW_S)
SETTLE_S = 1.5              # the text must stay unchanged this long before it is analysed (debounce)
MAX_PAIRS = 3               # corrections taken from one look at the text
MAX_WORDS = 3               # words on either side of one correction (longer swaps are rewrites)
MAX_SHIFT = 3               # inserted words at each end that may be edited before the text can no longer be found
MAX_TOKENS = 1500           # longer texts are not compared (Corrections.MAX_TOKENS)
MAX_TEXT = 20000            # characters of a field that are read at all
MAX_DICTIONARY_LINES = 1000   # nothing is learned into a dictionary this big (well inside the relay's 64 KB profile)
LEARNED_LOG_MAX = 20        # entries kept in learned_log (the "Recently learned" list)
STOP_WORDS = frozenset("the a an is are to of and or in on".split())
EDGE = core._EDGE_PUNCT
_SOUNDEX = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"),
            "l": "4", "m": "5", "n": "5", "r": "6"}


# ------------------------------------------------------------------ small text helpers (same in AutoLearn.java)

def _is_space(c):
    return c in " \t\n\r\f\v" or unicodedata.category(c) in ("Zs", "Zl", "Zp")


def tokens(text):
    """Words of a text split on any space, including the no-break spaces some apps put in their fields."""
    out, cur = [], []
    for c in text or "":
        if _is_space(c):
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(c)
    if cur:
        out.append("".join(cur))
    return out


def _word_chars(s):
    """The words' letters, marks and digits, lowercased: what is left when case and punctuation are ignored."""
    return " ".join("".join(c for c in t.lower() if unicodedata.category(c)[0] in "LMN") for t in tokens(s))


def _special(c):
    return c.isupper() or c.isdecimal() or c in "_."


def name_like(s):
    """One word with a capital, a digit, an underscore or a dot: a name, brand or code identifier."""
    return bool(s) and not any(_is_space(c) for c in s) and any(_special(c) for c in s)


def plain(s):
    """Ordinary lowercase words (no capital, digit, underscore or dot)."""
    return bool(s) and not any(_special(c) for c in s)


def osa(a, b):
    """Edit distance counting a swap of two neighbouring letters as one edit (optimal string alignment)."""
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[n][m]


def soundex(s):
    """American Soundex of the a-z letters of `s` ("" when it has none)."""
    letters = [c for c in s.lower() if "a" <= c <= "z"]
    if not letters:
        return ""
    out, prev = letters[0].upper(), _SOUNDEX.get(letters[0], "")
    for c in letters[1:]:
        if c in "hw":
            continue
        code = _SOUNDEX.get(c, "")
        if code and code != prev:
            out += code
            if len(out) == 4:
                break
        prev = code
    return (out + "000")[:4]


def looks_like_fix(wrong, right):
    """True when wrong -> right looks like a correction of a misheard or misspelled word, not a rewrite."""
    if not wrong or not right or _word_chars(wrong) == _word_chars(right):
        return False   # empty, or only capitals or punctuation changed
    a, b = wrong.lower(), right.lower()
    if all(t in STOP_WORDS for t in tokens(a)):
        return False   # "to => too" would change every "to" from now on
    dist, longest = osa(a, b), max(len(a), len(b))
    if 2 * dist <= longest:
        return True   # at least half the letters stay
    if a[0] == b[0] and abs(len(a) - len(b)) <= 1 and 3 * dist <= 2 * longest:
        return True   # same first letter, same length, a third of the letters stay
    if soundex(a) and soundex(a) == soundex(b):
        return True   # sounds the same
    return name_like(right) and plain(wrong) and 3 * dist <= 2 * longest   # a name or identifier for ordinary words


# ------------------------------------------------------------------ finding the typed text again

def _key(t):
    return t.strip(EDGE)


def _hits(keys, sub):
    k = len(sub)
    return [p for p in range(len(keys) - k + 1) if keys[p:p + k] == sub]


def _anchor_order(n):
    """(shift, size) to try: two-word anchors first (from the very end inwards), then single words."""
    return [(i, k) for k in (2, 1) for i in range(min(MAX_SHIFT, n)) if i + k <= n]


def locate(inserted, current):
    """Where the typed text is now: (start, end) word positions in tokens(current), end exclusive, or None.

    The anchors are the first and the last words of the typed text (two words, else one; when the user edited the
    first or last words, the next ones inwards). Of all the places that fit, the one closest in length to the typed text
    wins (then the later one)."""
    ins, cur = [_key(t) for t in tokens(inserted)], [_key(t) for t in tokens(current)]
    n, m = len(ins), len(cur)
    if not n or not m or n > MAX_TOKENS:
        return None
    starts = ends = []
    for i, k in _anchor_order(n):
        hits = _hits(cur, ins[i:i + k])
        if hits:
            starts = [(max(0, p - i), p) for p in hits]
            break
    for j, k in _anchor_order(n):
        hits = _hits(cur, ins[n - j - k:n - j])
        if hits:
            ends = [(min(m, q + k + j), q + k) for q in hits]
            break
    best = None
    for s, p in starts:
        for e, qe in ends:
            if qe <= p or e <= s:
                continue
            rank = (abs((e - s) - n), -s, e)
            if best is None or rank < best[0]:
                best = (rank, s, e)
    return None if best is None else (best[1], best[2])


def detect(inserted, current):
    """[[wrong, right], ...] (at most MAX_PAIRS): the words the user corrected in the text Vox typed (`inserted`), seen
    in the whole text of the field now (`current`, which may hold other text before and after)."""
    span = locate(inserted, current)
    if span is None:
        return []
    ins_text = " ".join(tokens(inserted))
    edited = " ".join(tokens(current)[span[0]:span[1]])
    if span[1] - span[0] > MAX_TOKENS or 5 * abs(len(edited) - len(ins_text)) > 2 * len(ins_text):
        return []   # more than 40% longer or shorter: rewritten, not corrected
    out = []
    for wrong, right in core.suggest_corrections(ins_text, edited, MAX_WORDS):
        if looks_like_fix(wrong, right) and [wrong, right] not in out:
            out.append([wrong, right])
        if len(out) == MAX_PAIRS:
            break
    return out


# ------------------------------------------------------------------ what to add to the dictionary

def learn(config_replacements, config_words, pairs, max_lines=MAX_DICTIONARY_LINES):
    """What to add for `pairs`: {"replacements": [[wrong, right], ...], "words": [right, ...]}.

    A wrong word that already has a replacement (any case) is skipped; a name-like right word is also added as a word
    unless it is there already. Nothing is added once the dictionary holds `max_lines` lines."""
    repl = list(config_replacements.items()) if isinstance(config_replacements, dict) else list(config_replacements or [])
    known = {str(w).lower() for w, _ in repl}
    have = {str(w).strip().lower() for w in config_words or []}
    size = len(repl) + len(config_words or [])
    out = {"replacements": [], "words": []}
    for pair in pairs or []:
        wrong, right = (str(x).strip() for x in pair)
        if not wrong or not right or "=>" in wrong + right or wrong.startswith("#") or wrong.lower() in known:
            continue
        if size >= max_lines:
            break
        out["replacements"].append([wrong, right])
        known.add(wrong.lower())
        size += 1
        if name_like(right) and right.lower() not in have and size < max_lines:
            out["words"].append(right)
            have.add(right.lower())
            size += 1
    return out


def _dict_parts(lines):
    """(replacements [[wrong, right]], words) of a dictionary list, comments and blanks left out."""
    repl, words = [], []
    for line in lines:
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        if "=>" in t:
            w, r = (p.strip() for p in t.split("=>", 1))
            if w:
                repl.append([w, r])
        else:
            words.append(t)
    return repl, words


def enabled(cfg):
    """The setting auto_learn (on unless turned off)."""
    return cfg.get("auto_learn", True) is not False


def learned_log(cfg):
    """The learned_log setting as a clean list of {"t", "wrong", "right", "word"}, oldest first."""
    raw = cfg.get("learned_log")
    out = []
    for e in raw if isinstance(raw, list) else []:
        if isinstance(e, dict) and isinstance(e.get("wrong"), str) and isinstance(e.get("right"), str) \
                and isinstance(e.get("t"), (int, float)) and not isinstance(e.get("t"), bool):
            out.append({"t": float(e["t"]), "wrong": e["wrong"], "right": e["right"], "word": e.get("word") is True})
    return out[-LEARNED_LOG_MAX:]


def apply_learned(cfg, pairs, now=None):
    """The settings to save after learning `pairs`: {"dictionary", "learned_log"}, and the pairs really added.
    ({}, []) when nothing is new. `cfg` is not changed."""
    lines = [x for x in cfg.get("dictionary") or [] if isinstance(x, str)]
    repl, words = _dict_parts(lines)
    add = learn(repl, words, pairs)
    if not add["replacements"]:
        return {}, []
    now = time.time() if now is None else now
    new_lines = lines + ["%s => %s" % (w, r) for w, r in add["replacements"]] + add["words"]
    log = learned_log(cfg)
    for i, (w, r) in enumerate(add["replacements"]):
        log.append({"t": round(now + i / 1000.0, 3), "wrong": w, "right": r, "word": r in add["words"]})
    return {"dictionary": new_lines, "learned_log": log[-LEARNED_LOG_MAX:]}, add["replacements"]


def remove_learned(cfg, t):
    """The settings to save after the "Recently learned" entry made at `t` is removed: its replacement and the word it
    added leave the dictionary (when still there), and the entry leaves the log. `cfg` is not changed."""
    log = learned_log(cfg)
    entry = next((e for e in log if abs(e["t"] - float(t)) < 0.0005), None)
    lines = [x for x in cfg.get("dictionary") or [] if isinstance(x, str)]
    if entry is None:
        return {"dictionary": lines, "learned_log": log}
    for i, line in enumerate(lines):
        if "=>" in line and [p.strip() for p in line.split("=>", 1)] == [entry["wrong"], entry["right"]]:
            lines = lines[:i] + lines[i + 1:]
            break
    if entry["word"]:
        for i, line in enumerate(lines):
            if "=>" not in line and line.strip() == entry["right"]:
                lines = lines[:i] + lines[i + 1:]
                break
    return {"dictionary": lines, "learned_log": [e for e in log if e is not entry]}


# ------------------------------------------------------------------ the watch after Vox types

class Watch:
    """Watches one field for a while after Vox typed into it. Not thread safe: the caller holds a lock.

    arm() when Vox typed `inserted` into `app`; observe(app, text) with the field's whole text whenever it may have
    changed (or None when it cannot be read); end() when the watch must stop. Each returns the corrections found then, each
    pair once per watch. The watch ends by itself when AUTO_LEARN_WINDOW_S pass, the app changes, the field is emptied
    (sent), the typed text is gone from it or the field shrinks to under half of it. A text is analysed once it has stayed
    the same for SETTLE_S, and the last snapshot (the latest text that still held the typed text) is analysed once more when
    the watch ends, so a fix made just before pressing Send still counts. The snapshot is kept in memory only."""

    def __init__(self, clock=time.monotonic, window=AUTO_LEARN_WINDOW_S, settle=SETTLE_S):
        self.clock, self.window, self.settle = clock, window, settle
        self.app = None
        self._reset()

    def _reset(self):
        self.armed = False
        self.inserted = ""
        self.armed_at = 0.0
        self._snapshot = None
        self._changed_at = 0.0
        self._dirty = False
        self._reported = set()

    def arm(self, app, inserted):
        """Start watching `app` for fixes of `inserted`; a watch still running ends first (its corrections are returned)."""
        out = self.end()
        if inserted and inserted.strip():
            self.armed, self.app, self.inserted, self.armed_at = True, app, inserted, self.clock()
        return out

    def end(self):
        """Stop watching: the last snapshot is analysed once more and then dropped."""
        out = self._analyse() if self.armed and self._dirty else []
        self._reset()
        return out

    def is_armed(self):
        return self.armed and self.clock() - self.armed_at <= self.window

    def observe(self, app, current):
        if not self.armed:
            return []
        now = self.clock()
        if now - self.armed_at > self.window or app != self.app:
            return self.end()
        text = current if isinstance(current, str) else ""
        if not text.strip() or 2 * len(text.strip()) < len(self.inserted.strip()) or len(text) > MAX_TEXT \
                or locate(self.inserted, text) is None:
            return self.end()   # sent, cleared, moved away or unreadable: the last snapshot still counts
        out = []
        if text != self._snapshot:
            if self._dirty and now - self._changed_at >= self.settle:
                out = self._analyse()   # the text that was quiet until now
            self._snapshot, self._changed_at, self._dirty = text, now, True
        elif self._dirty and now - self._changed_at >= self.settle:
            out = self._analyse()
        return out

    def _analyse(self):
        self._dirty = False
        out = []
        for wrong, right in detect(self.inserted, self._snapshot or ""):
            if wrong.lower() not in self._reported:
                self._reported.add(wrong.lower())
                out.append([wrong, right])
        return out
