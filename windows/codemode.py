"""Code mode: spoken formatters and symbols for code editors and terminals (Windows only). Pure: no I/O, no network.

Active when the app being typed into is in the `code_apps` setting (editors and terminals by default) or its per-app style
is "code", unless `code_mode` is "off". Talon-style: a formatter ("camel case", "snake case", ...) formats the words that
follow it, up to a stop (a spoken symbol, the word "then", punctuation after a word, a line break or the end); a spoken
symbol ("open paren", "equals equals", ...) becomes the character. Words are matched whole and ignoring case, never inside
another word. The full table is in documentation/15-code-mode.md and in the window's help box (tests keep all three equal).
"""
import re

CODE_APPS = ["Code.exe", "Code - Insiders.exe", "cursor.exe", "windsurf.exe", "devenv.exe", "idea64.exe", "pycharm64.exe",
             "webstorm64.exe", "clion64.exe", "rider64.exe", "sublime_text.exe", "notepad++.exe", "WindowsTerminal.exe",
             "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "alacritty.exe", "wezterm-gui.exe", "mintty.exe"]


def _camel(ws):
    return ws[0] + "".join(w.capitalize() for w in ws[1:])


FORMATTERS = {   # spoken formatter -> how it joins the lowercase words that follow it
    "camel case": _camel,
    "snake case": "_".join,
    "pascal case": lambda ws: "".join(w.capitalize() for w in ws),
    "kebab case": "-".join,
    "constant case": lambda ws: "_".join(ws).upper(),
    "dotted case": ".".join,
    "all caps": lambda ws: " ".join(ws).upper(),
    "no space": "".join,
}
ALIASES = {"camelcase": "camel case", "snakecase": "snake case", "pascalcase": "pascal case", "kebabcase": "kebab case"}

# (spoken, typed, no space before, no space after). Longer spoken forms win ("equals equals" before "equals").
SYMBOLS = [
    ("open paren", "(", True, True), ("open parenthesis", "(", True, True),
    ("close paren", ")", True, False), ("close parenthesis", ")", True, False),
    ("open bracket", "[", True, True), ("close bracket", "]", True, False),
    ("open brace", "{", False, True), ("close brace", "}", True, False),
    ("open angle", "<", True, True), ("close angle", ">", True, False),
    ("equals", "=", False, False), ("equals equals", "==", False, False), ("not equals", "!=", False, False),
    ("less than", "<", False, False), ("greater than", ">", False, False), ("plus equals", "+=", False, False),
    ("arrow", "->", False, False), ("fat arrow", "=>", False, False),
    ("dot", ".", True, True), ("comma", ",", True, False), ("semicolon", ";", True, False), ("colon", ":", True, False),
    ("double colon", "::", True, True), ("underscore", "_", True, True), ("dash", "-", False, True),
    ("slash", "/", True, True), ("backslash", "\\", True, True),
    ("quote", '"', None, None), ("single quote", "'", None, None), ("backtick", "`", None, None),   # None: opens or closes
    ("pipe", "|", False, False), ("ampersand", "&", False, False), ("hash", "#", False, True),
    ("at sign", "@", True, True), ("dollar sign", "$", False, True), ("percent", "%", True, False),
    ("star", "*", False, False), ("plus", "+", False, False), ("minus", "-", False, False), ("tilde", "~", False, True),
    ("new line", "\n", True, True), ("tab", "\t", True, True),
]
_BY_WORDS = sorted(((tuple(s.split()), (t, l, r)) for s, t, l, r in SYMBOLS), key=lambda x: -len(x[0]))
_FORMATTER_WORDS = sorted([(tuple(k.split()), k) for k in FORMATTERS] + [((a,), k) for a, k in ALIASES.items()],
                          key=lambda x: -len(x[0]))
_CHUNK = re.compile(r"\n|[^\s]+")
_EDGE = re.compile(r"^([^\w]*)(.*?)([^\w]*)$", re.S)
_NOISE = set(",.;:!?")


def code_mode(value):
    """The `code_mode` setting as "auto" or "off" (anything but "off" is auto)."""
    return "off" if str(value or "").strip().lower() == "off" else "auto"


def code_cleanup(value):
    """The `code_cleanup` setting as "rules" (the default: no AI cleanup in code apps) or "llm"."""
    return "llm" if str(value or "").strip().lower() == "llm" else "rules"


def is_code_app(cfg, exe, style):
    """True when code mode applies: it is not switched off, and the app is a code app or its style is "code"."""
    if code_mode(cfg.get("code_mode")) == "off":
        return False
    if (style or "").lower() == "code":
        return True
    apps = cfg.get("code_apps")
    apps = apps if isinstance(apps, list) else []
    return bool(exe) and exe.lower() in {a.strip().lower() for a in apps if isinstance(a, str)}


def _split(chunk):
    """A chunk of text as (leading punctuation, the word part, trailing punctuation)."""
    return _EDGE.match(chunk).groups()


def _match(chunks, i, table):
    """(entry, chunks used) for the longest spoken form in table at chunk i, or None. Words in between carry no punctuation."""
    for words, entry in table:
        n = len(words)
        if i + n > len(chunks):
            continue
        ok = True
        for k, w in enumerate(words):
            lead, core, trail = _split(chunks[i + k])
            if core.lower() != w or (k > 0 and lead) or (k < n - 1 and trail):
                ok = False
                break
        if ok:
            return entry, n
    return None


def format_code(text):
    """Text with the spoken formatters and symbols applied. Text without any of them comes back unchanged."""
    chunks = _CHUNK.findall(text or "")
    out = []          # (text, no space before, no space after, kind) with kind "word", "sym" or "fmt"
    open_quotes = set()
    i, used = 0, False
    while i < len(chunks):
        c = chunks[i]
        if c == "\n":
            out.append(("\n", True, True, "sym"))
            i += 1
            continue
        fm = _match(chunks, i, _FORMATTER_WORDS)
        if fm:
            name, n = fm
            j, words = i + n, []
            if _split(chunks[i + n - 1])[2] and not set(_split(chunks[i + n - 1])[2]) <= _NOISE:
                fm = None   # "camel case)" is not a formatter
            else:
                while j < len(chunks) and chunks[j] != "\n" and not _match(chunks, j, _BY_WORDS):
                    lead, core, trail = _split(chunks[j])
                    if core.lower() == "then" and not lead:
                        j += 1
                        break
                    if lead:
                        break
                    words += [w for w in re.split(r"[^0-9a-z]+", core.lower().replace("'", "").replace("’", "")) if w]
                    j += 1
                    if trail:
                        break
            if fm and words:
                out.append((FORMATTERS[name](words), False, False, "fmt"))
                used = True
                i = j
                continue
        sm = _match(chunks, i, _BY_WORDS)
        if sm:
            (typed, left, right), n = sm
            lead, trail = _split(chunks[i])[0], _split(chunks[i + n - 1])[2]
            if lead:
                out.append((lead, False, True, "word"))
            if left is None:   # a quote: the first one opens (no space after), the next one closes (no space before)
                left, right = (True, False) if typed in open_quotes else (False, True)
                open_quotes ^= {typed}
            out.append((typed, left, right, "sym"))
            if trail and not set(trail) <= _NOISE:
                out.append((trail, True, False, "word"))
            used = True
            i += n
            continue
        out.append((c, False, False, "word"))
        i += 1
    if not used:
        return text
    if out[-1][3] == "word" and out[-1][0].endswith(".") and not out[-1][0].endswith(".."):
        out[-1] = (out[-1][0][:-1],) + out[-1][1:]   # Whisper's full stop at the end: not code
    if out[0][3] == "word" and re.fullmatch(r"[A-Z][a-z0-9]*", out[0][0]):
        out[0] = (out[0][0][0].lower() + out[0][0][1:],) + out[0][1:]   # Whisper's capital at the start ("Console dot log")
    s = ""
    for k, (t, left, _, _) in enumerate(out):
        if k and not left and not out[k - 1][2] and t:
            s += " "
        s += t
    return s
