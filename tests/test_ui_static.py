"""Static checks for the two UI pages (windows/ui/index.html and android/assets/index.html).

Nothing here runs a browser. The pages are plain text, so a few regexes (and `ast` for the Python bridge) can catch the
mistakes that otherwise only show up when someone taps a button: a script looking up an id the markup does not have
(`get-key` vs `key-get`), two elements sharing an id, and a page calling a bridge method that does not exist.

The checks work on text, so they have limits. They do not see ids or calls built from variables (see DYNAMIC_LOOKUPS
for the ones that exist today), and the Windows check only follows `pywebview.api.NAME` and `api().NAME`.
"""
import ast
import os
import re
from collections import Counter

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
PAGES = {
    "windows": os.path.join(ROOT, "windows", "ui", "index.html"),
    "android": os.path.join(ROOT, "android", "assets", "index.html"),
}
UI_APP = os.path.join(ROOT, "windows", "ui_app.py")
MAIN_ACTIVITY = os.path.join(ROOT, "android", "src", "com", "minhaj", "vox", "MainActivity.java")

# Lookups whose id is computed at runtime, so no regex can read it. Key: the exact text of the call's argument.
# Value: every id that call can resolve to; each must exist in the page. A computed lookup that is not listed here
# fails test_every_computed_lookup_is_listed, so a new one has to be added on purpose, with its ids.
DYNAMIC_LOOKUPS = {
    "windows": {
        # refreshModels() and testRole(): `role` is "stt" or "llm".
        'role + "-status"': ["stt-status", "llm-status"],
        'role + "-models"': ["stt-models", "llm-models"],
        '"test-" + role': ["test-stt", "test-llm"],
        # The two `for (const ... of [["stt-url", ...], ...])` loops over the per-role override fields.
        "id": ["stt-url", "stt-key", "llm-url", "llm-key"],
        'id + "-status"': ["stt-url-status", "llm-url-status"],
    },
    "android": {
        'role + "-status"': ["stt-status", "llm-status"],
        'role + "-models"': ["stt-models", "llm-models"],
        '"test-" + role': ["test-stt", "test-llm"],
        # `id` inside helpers: chips(list, id) gets "words" and "people"; the onkeydown loop gets "w-in" and "p-in";
        # the per-role override loops get the four stt/llm url and key fields. (setVal(id, ...) is also checked by
        # its literal call sites below, so its callers do not need to be listed.)
        "id": ["words", "people", "w-in", "p-in", "stt-url", "stt-key", "llm-url", "llm-key"],
        'id + "-status"': ["stt-url-status", "llm-url-status"],
        # "w-in" -> "w-add", "p-in" -> "p-add" (the Enter key clicks the matching Add button).
        'id.replace("in", "add")': ["w-add", "p-add"],
    },
}

# The page's own helpers that take an element id first: `$` is its getElementById shorthand, `setVal` fills a form
# field unless it has focus. Computed (non-literal) calls are only collected for these two, because the one
# getElementById(id) with a variable is the body of the `$` helper itself.
HELPER_CALL = re.compile(r"(?<![\w$.])(?:\$|setVal)\(")
GET_BY_ID_CALL = re.compile(r"\.getElementById\(")
LITERAL = re.compile(r"""(["'`])([^"'`$\\]*)\1""")   # a plain string; a template with ${...} is computed
ID_ATTR = re.compile(r"""(?<![\w-])id\s*=\s*(["'])([^"']*)\1""")
SELECTOR_CALL = re.compile(r"""querySelector(?:All)?\(\s*(["'`])(.*?)\1""")
SELECTOR_ID = re.compile(r"#([A-Za-z_][\w-]*)")
STYLE_OR_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------- page text helpers ----------

def first_argument(text, open_paren):
    """Source text of the first argument of the call whose "(" is at `open_paren` (strings and nesting respected)."""
    depth, quote, i, start = 0, None, open_paren, open_paren + 1
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return text[start:i].strip()
        elif c == "," and depth == 1:
            return text[start:i].strip()
        i += 1
    return text[start:].strip()


def lookup_calls(html):
    """(literal ids, computed argument texts) for every `$(...)`, `setVal(...)`, getElementById and querySelector call."""
    literals, computed = set(), set()
    for pattern, reports_computed in ((HELPER_CALL, True), (GET_BY_ID_CALL, False)):
        for m in pattern.finditer(html):
            arg = first_argument(html, m.end() - 1)
            lit = LITERAL.fullmatch(arg)
            if lit:
                literals.add(lit.group(2))
            elif reports_computed:
                computed.add(arg)
    for m in SELECTOR_CALL.finditer(html):
        literals.update(SELECTOR_ID.findall(m.group(2)))
    return literals, computed


def defined_ids(html):
    """Every id="..." in the file, markup and the HTML inside script templates alike. Ids with a ${...} part are skipped."""
    return [m.group(2) for m in ID_ATTR.finditer(html) if "${" not in m.group(2)]


def missing_ids(html, dynamic=None):
    literals, _ = lookup_calls(html)
    wanted = literals | {i for ids in (dynamic or {}).values() for i in ids}
    return sorted(wanted - set(defined_ids(html)))


def duplicate_ids(html):
    """Ids used by more than one element in the static markup (scripts and styles removed)."""
    markup = STYLE_OR_SCRIPT.sub("", html)
    return sorted(i for i, n in Counter(defined_ids(markup)).items() if n > 1)


# ---------- the two bridges ----------

def api_methods(py_source):
    """Public methods of class Api: what pywebview exposes as window.pywebview.api.NAME. Parsed, not imported."""
    cls = next(n for n in ast.parse(py_source).body if isinstance(n, ast.ClassDef) and n.name == "Api")
    return {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")}


def windows_calls(html):
    """Names called on the pywebview bridge: `pywebview.api.NAME` and the page's `api().NAME` shorthand."""
    return set(re.findall(r"(?:pywebview\.api|(?<![\w$.])api\(\))\.([A-Za-z_]\w*)", html))


def js_interface_methods(java_source):
    """Names of the @JavascriptInterface methods: what the WebView exposes as window.Vox.NAME."""
    return set(re.findall(r"@JavascriptInterface\s+public\s+[\w.<>\[\]]+\s+(\w+)\s*\(", java_source))


def android_calls(html):
    """Names called on the WebView bridge: `V.NAME(` (the page's alias, `const V = window.Vox || {...}`) and `Vox.NAME(`."""
    return set(re.findall(r"(?<![\w$])(?:V|Vox)\.([A-Za-z_]\w*)\(", html))


# ---------- the checks, against the real files ----------

@pytest.mark.parametrize("page", list(PAGES))
def test_every_looked_up_id_exists(page):
    html = read(PAGES[page])
    literals, _ = lookup_calls(html)
    assert len(literals) > 20, "the lookup regexes found almost nothing, so they no longer match the page"
    assert missing_ids(html, DYNAMIC_LOOKUPS[page]) == []


@pytest.mark.parametrize("page", list(PAGES))
def test_every_computed_lookup_is_listed(page):
    _, computed = lookup_calls(read(PAGES[page]))
    unlisted = sorted(computed - set(DYNAMIC_LOOKUPS[page]))
    assert unlisted == [], "add these computed lookups to DYNAMIC_LOOKUPS with the ids they can resolve to"


@pytest.mark.parametrize("page", list(PAGES))
def test_no_duplicate_ids(page):
    html = read(PAGES[page])
    assert len(defined_ids(html)) > 20, "the id regex found almost nothing, so it no longer matches the page"
    assert duplicate_ids(html) == []


def test_windows_page_calls_only_api_methods():
    calls = windows_calls(read(PAGES["windows"]))
    assert len(calls) > 20, "the call regex found almost nothing, so it no longer matches the page"
    assert sorted(calls - api_methods(read(UI_APP))) == []


def test_android_page_calls_only_javascript_interface_methods():
    calls = android_calls(read(PAGES["android"]))
    assert len(calls) > 10, "the call regex found almost nothing, so it no longer matches the page"
    assert sorted(calls - js_interface_methods(read(MAIN_ACTIVITY))) == []


@pytest.mark.parametrize("page", list(PAGES))
def test_settings_sections_come_in_the_agreed_order(page):
    html = read(PAGES[page])
    section = re.search(r'<section[^>]*id="settings".*?</section>', html, re.S).group(0)
    assert re.findall(r"<h2[^>]*>(.*?)</h2>", section) == ["AI providers", "Voice &amp; audio", "Privacy", "System"]


@pytest.mark.parametrize("page", list(PAGES))
def test_home_shows_a_status_card_and_no_typing_stats(page):
    html = read(PAGES[page])
    assert 'id="status-card"' in html and 'id="status-test"' in html
    assert 's-saved' not in html and "Time saved" not in html


# ---------- the checkers themselves: they must fire on the mistakes they exist for ----------

def test_checker_catches_a_swapped_id():
    html = '<button id="get-key"></button><script>$("key-get").onclick = f;</script>'
    assert missing_ids(html) == ["key-get"]


@pytest.mark.parametrize("js", [
    "$('x')", '$("x")', 'document.getElementById("x")', "document.getElementById('x')",
    'document.querySelector("#x")', "document.querySelectorAll('#x .row, #y')", 'setVal("x", 1)', '$(`x`)',
])
def test_checker_reads_every_lookup_form(js):
    literals, computed = lookup_calls("<script>" + js + "</script>")
    assert "x" in literals and not computed


def test_checker_reports_computed_lookups_and_ignores_the_helper_definition():
    html = 'const $ = (id) => document.getElementById(id);\n$(role + "-status"); $(id.replace("in", "add")); $(id);'
    literals, computed = lookup_calls(html)
    assert not literals
    assert computed == {'role + "-status"', 'id.replace("in", "add")', "id"}


def test_checker_ignores_data_id_attributes_and_dollar_braces():
    html = '<div data-id="a" id="b"></div><script>x = `<i id="${n}"></i>`; $(`${n}`);</script>'
    assert defined_ids(html) == ["b"]
    assert lookup_calls(html)[0] == set()


def test_checker_catches_duplicate_ids_in_markup_only():
    html = '<i id="a"></i><b id="a"></b><i id="c"></i><script>t = `<i id="c"></i>`;</script>'
    assert duplicate_ids(html) == ["a"]


def test_checker_finds_public_api_methods_only():
    src = "class Api:\n    def get_state(self): pass\n    def _engine(self): pass\n    async def sync_now(self): pass\ndef main(): pass\n"
    assert api_methods(src) == {"get_state", "sync_now"}
    assert windows_calls("await api().get_state(); window.pywebview.api.copy(t); const a = window.pywebview.api;") == {"get_state", "copy"}


def test_checker_finds_annotated_java_methods_only():
    java = "@JavascriptInterface\n        public String state() {}\n        public void helper() {}\n" \
           "@JavascriptInterface public void save(String j) {}"
    assert js_interface_methods(java) == {"state", "save"}
    assert android_calls('V.state(); window.Vox.save(x); DEV.nope(); "Vox. Hi"') == {"state", "save"}


@pytest.mark.parametrize("name", sorted(PAGES))
def test_key_sharing_hint_matches_the_sync_rule(name):
    """Switch-off strips only this device's keys, once; another device that still shares keys puts them back (sync.sync_profile)."""
    with open(PAGES[name], encoding="utf-8") as f:
        text = f.read()
    assert "removes this device's keys from the relay once" in text
    assert "turn it off on every device" in text
    assert "Turning it off removes them from the relay" not in text
