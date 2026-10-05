"""Code mode: spoken formatters and symbols for code editors and terminals (Windows only). Pure: no I/O, no network.

Active when the app being typed into is in the `code_apps` setting (editors and terminals by default) or its per-app style
is "code", unless `code_mode` is "off". Talon-style: a formatter ("camel case", "snake case", ...) formats the words that
follow it, up to a stop (a spoken symbol, the word "then", punctuation after a word, a line break or the end); a spoken
symbol ("open paren", "equals equals", ...) becomes the character. Words are matched whole and ignoring case, never inside
another word. The full table is in documentation/15-code-mode.md and in the window's help box (tests keep all three equal).
"""
import re
import unicodedata

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
_BY_WORDS = sorted(((tuple(s.split()), (t, l, r, s)) for s, t, l, r in SYMBOLS), key=lambda x: -len(x[0]))
# Spoken symbols that are also everyday English ("add a quote", "a five star review", "less than a minute"): they become
# the symbol only in code (see _code_here). The others, and the formatters, always do.
AMBIGUOUS = frozenset({"equals", "arrow", "dot", "dash", "slash", "quote", "single quote", "pipe", "hash", "percent", "star",
                       "plus", "minus", "tab", "less than", "greater than"})
# Spoken punctuation: always a symbol, but no sign that the dictation is code.
PUNCTUATION = frozenset({"comma", "colon", "semicolon", "new line"})
# A dictation that starts with one of these is a command line ("ls dash la", "git commit dash m ...").
COMMANDS = frozenset("""git ls cd cat grep npm npx pnpm yarn pip pip3 python python3 py node deno bun docker kubectl ssh scp
curl wget echo rm mv cp mkdir rmdir chmod chown sudo cargo rustc dotnet gh vim nano sed awk tar unzip conda poetry uv mise
winget choco""".split())   # not go, head, touch...: everyday words at the start of a sentence
_ARTICLES = frozenset("an the this that these those my your our his her their its one another each every some any no".split())
# Words of a sentence, not of code: an AMBIGUOUS symbol next to one of these is no sign of code ("a five star hotel plus a
# spa", "that equals trouble"). Not "this": this.state is code.
_FUNCTION = frozenset("""a an the to of in on at for with from by about into onto over under after before than then as
and or but nor so yet if is are was were be been being am do does did have has had will would can could shall should may
might must not i you he she it we they me him her us them my your our his their its that these those each every some any
no another what which who whom whose there here very too also just""".split())
# A dictation that starts with one of these is a statement: a comparison between two names in it is code
# ("if count greater than limit").
_STATEMENTS = frozenset("if elif while until assert return".split())
_COMPARISONS = frozenset({"equals", "greater than", "less than"})
_FORMATTER_WORDS = sorted([(tuple(k.split()), k) for k in FORMATTERS] + [((a,), k) for a, k in ALIASES.items()],
                          key=lambda x: -len(x[0]))
_CHUNK = re.compile(r"\n|[^\s]+")
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


def _word_char(c):
    return c.isalnum() or c == "_" or unicodedata.category(c)[0] == "M"   # \w, and the vowel signs of a Hindi word


def _split(chunk):
    """A chunk of text as (leading punctuation, the word part, trailing punctuation)."""
    a, b = 0, len(chunk)
    while a < b and not _word_char(chunk[a]):
        a += 1
    while b > a and not _word_char(chunk[b - 1]):
        b -= 1
    return chunk[:a], chunk[a:b], chunk[b:]


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


def _codeish(word):
    """A word that looks like code: one letter other than a and I, a digit, a character that is not a letter, mark or
    apostrophe (my_var, log.txt), or a capital after the first letter (getUser)."""
    if any(c.isdigit() for c in word):
        return True
    if len(word) == 1:
        return word.isalpha() and word.lower() not in ("a", "i")
    return any(not (c.isalpha() or c in "'’" or unicodedata.category(c)[0] == "M") for c in word) \
        or any(c.isupper() for c in word[1:])


def _name(chunks, k, last=False):
    """True when chunks[k] is a plain word that can be a name in code: no punctuation around it (the `last` word of a
    statement may end the sentence), no apostrophe, and not a _FUNCTION word."""
    if k < 0 or k >= len(chunks) or chunks[k] == "\n" or _match(chunks, k, _BY_WORDS):
        return False
    lead, core, trail = _split(chunks[k])
    return bool(core) and not lead and (not trail or (last and set(trail) <= _NOISE)) \
        and "'" not in core and "’" not in core and core.lower() not in _FUNCTION


def _clean_symbol(chunks, i, n):
    """True when the spoken symbol of chunks[i:i+n] has no punctuation before or after it."""
    return not _split(chunks[i])[0] and not _split(chunks[i + n - 1])[2]


def _statement(chunks):
    """True when two or more AMBIGUOUS spoken symbols each stand between two names (_name), one name between each two:
    "self dot name equals name", "total equals price star quantity", "count equals count plus one". Prose has words such
    as "and", "a" or "is" there ("open a new tab and star the repo", "two plus two is more than three")."""
    i, run, prev_end = 0, 0, -1
    while i < len(chunks):
        sm = _match(chunks, i, _BY_WORDS)
        if not sm or sm[0][3] not in AMBIGUOUS:
            i += 1
            continue
        n = sm[1]
        if _clean_symbol(chunks, i, n) and _name(chunks, i - 1) and _name(chunks, i + n, last=True):
            run = run + 1 if prev_end == i - 1 else 1   # this symbol's left name is the last one's right name
            if run >= 2:
                return True
            prev_end = i + n
        else:
            run, prev_end = 0, -1
        i += n
    return False


def _command(chunks):
    """True when the dictation starts with a command name (COMMANDS) used as one: "git commit", "ls dash la", not "Python is
    great" (a _FUNCTION word after it)."""
    if not chunks or _split(chunks[0])[1].lower() not in COMMANDS:
        return False
    nxt = _split(chunks[1])[1].lower() if len(chunks) > 1 else ""
    return nxt not in _FUNCTION


def _has_code(chunks):
    """True when the dictation is code: it starts with a command (_command), has a formatter or a spoken symbol that is
    neither AMBIGUOUS nor PUNCTUATION ("open paren", "underscore", "tilde"), or is a statement (_statement)."""
    if _command(chunks):
        return True
    for i in range(len(chunks)):
        sm = _match(chunks, i, _BY_WORDS)
        if _match(chunks, i, _FORMATTER_WORDS) or (sm and sm[0][3] not in AMBIGUOUS | PUNCTUATION):
            return True
    return _statement(chunks)


def _code_here(chunks, i, n, out, code, quoted=False):
    """True when the AMBIGUOUS spoken symbol of chunks[i:i+n] is meant as the symbol: the dictation is code (`code`), a
    word next to it looks like code (_codeish) or is a symbol, "dot" stands between two names ("user dot name"), or a
    comparison stands between two names in a statement ("if count greater than limit"). Outside code, and inside quotes
    (`quoted`: a commit message), the word before it must not be an article or possessive (_ARTICLES: "add the dot env
    file" keeps "dot")."""
    before = _split(chunks[i - 1]) if i > 0 and chunks[i - 1] != "\n" else None
    spoken = _match(chunks, i, _BY_WORDS)[0][3]
    this_dot = before and before[1].lower() == "this" and spoken == "dot" and _name(chunks, i + n, last=True)   # this.props
    if before and before[1].lower() in _ARTICLES and not before[2] and (quoted or not code) and not this_dot:
        return False
    if code:
        return True
    if _clean_symbol(chunks, i, n) and _name(chunks, i - 1) and _name(chunks, i + n, last=True) and (
            spoken == "dot" or (spoken in _COMPARISONS and _split(chunks[0])[1].lower() in _STATEMENTS)):
        return True
    left = before is not None and ((out and out[-1][3] in ("sym", "fmt")) or _codeish(before[1]))
    j = i + n
    after = _match(chunks, j, _BY_WORDS) if j < len(chunks) else None   # a code symbol, or the same one ("dash dash")
    right = j < len(chunks) and chunks[j] != "\n" and bool(
        (after and (after[0][3] not in AMBIGUOUS or after[0][3] == _match(chunks, i, _BY_WORDS)[0][3]))
        or _match(chunks, j, _FORMATTER_WORDS) or _codeish(_split(chunks[j])[1]))
    return left or right


def format_code(text):
    """Text with the spoken formatters and symbols applied. Text without any of them comes back unchanged. A spoken
    symbol that is also an everyday word (AMBIGUOUS) is only turned into the symbol in code (_code_here), so "add a quote
    from the ceo" stays as said."""
    chunks = _CHUNK.findall(text or "")
    out = []          # (text, no space before, no space after, kind) with kind "word", "sym" or "fmt"
    open_quotes = set()
    code = _has_code(chunks)
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
                    words += "".join(c if unicodedata.category(c)[0] in "LMN" else " "   # letters of any script (café)
                                     for c in core.lower().replace("'", "").replace("’", "")).split()
                    j += 1
                    if trail:
                        break
            if fm and words:
                out.append((FORMATTERS[name](words), False, False, "fmt"))
                used = True
                i = j
                continue
        sm = _match(chunks, i, _BY_WORDS)
        if sm and sm[0][3] in AMBIGUOUS and not (sm[0][0] in open_quotes and sm[0][1] is None) \
                and not _code_here(chunks, i, sm[1], out, code, bool(open_quotes)):
            sm = None   # an everyday word here, not code (a quote that closes an open one always counts)
        if sm:
            (typed, left, right, _), n = sm
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
