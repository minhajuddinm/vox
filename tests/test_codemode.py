"""Code mode (windows/codemode.py): spoken formatters and symbols for code editors and terminals, Windows only.

Table-driven: every formatter and every spoken symbol has a row, plus the interactions (a formatter followed by symbols),
the gating (only code apps, the per-app style 'code', the code_mode switch), the pipeline (no AI cleanup by default in a
code app, the code-aware prompt when it is chosen) and the help text in the window."""
import os
import re

import pytest

import codemode
import vox_core as core

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.mark.parametrize("spoken,typed", [
    ("camel case user name", "userName"),
    ("snake case max retries", "max_retries"),
    ("pascal case http client", "HttpClient"),
    ("kebab case my component", "my-component"),
    ("constant case api key", "API_KEY"),
    ("dotted case foo bar", "foo.bar"),
    ("all caps hello", "HELLO"),
    ("no space foo bar", "foobar"),
    ("Camel case, user name.", "userName"),          # what Whisper writes: capitals and punctuation
    ("camelcase get user by id", "getUserById"),     # Whisper sometimes joins the two words
    ("snake case don't stop", "dont_stop"),
    ("snake case retry 2 times", "retry_2_times"),
])
def test_every_formatter(spoken, typed):
    assert codemode.format_code(spoken) == typed


SYMBOL_ROWS = [
    ("a open paren b", "a(b"), ("a open parenthesis b", "a(b"), ("a close paren b", "a) b"),
    ("a close parenthesis b", "a) b"), ("a open bracket b", "a[b"), ("a close bracket b", "a] b"),
    ("a open brace b", "a {b"), ("a close brace b", "a} b"), ("a open angle b", "a<b"), ("a close angle b", "a> b"),
    ("a equals b", "a = b"), ("a equals equals b", "a == b"), ("a not equals b", "a != b"),
    ("a less than b", "a < b"), ("a greater than b", "a > b"), ("a plus equals b", "a += b"),
    ("a arrow b", "a -> b"), ("a fat arrow b", "a => b"), ("a dot b", "a.b"), ("a comma b", "a, b"),
    ("a semicolon b", "a; b"), ("a colon b", "a: b"), ("a double colon b", "a::b"), ("a underscore b", "a_b"),
    ("a dash b", "a -b"), ("a slash b", "a/b"), ("a backslash b", "a\\b"), ("a quote b", 'a "b'),
    ("a single quote b", "a 'b"), ("a backtick b", "a `b"), ("a pipe b", "a | b"), ("a ampersand b", "a & b"),
    ("a hash b", "a #b"), ("a at sign b", "a@b"), ("a dollar sign b", "a $b"), ("a percent b", "a% b"),
    ("a star b", "a * b"), ("a plus b", "a + b"), ("a minus b", "a - b"), ("a tilde b", "a ~b"),
    ("a new line b", "a\nb"), ("a tab b", "a\tb"),
]


@pytest.mark.parametrize("spoken,typed", SYMBOL_ROWS)
def test_every_symbol(spoken, typed):
    assert codemode.format_code(spoken) == typed


def test_every_symbol_of_the_table_has_a_row():
    spoken = {s.split(" ", 1)[1].rsplit(" ", 1)[0] for s, _ in SYMBOL_ROWS}
    assert spoken == {s for s, _, _, _ in codemode.SYMBOLS}


@pytest.mark.parametrize("spoken,typed", [
    ("snake case max retries equals 5", "max_retries = 5"),
    ("camel case get user dot name", "getUser.name"),
    ("print open paren quote hello quote close paren", 'print("hello")'),
    ("if open paren x not equals none close paren colon", "if(x != none):"),
    ("def snake case load config open paren path close paren colon", "def load_config(path):"),
    ("Console dot log open paren camel case user name close paren semicolon", "console.log(userName);"),
    ("camel case user then equals 5", "user = 5"),                       # "then" ends a formatter and is dropped
    ("const pascal case http client equals new pascal case http client open paren close paren",
     "const HttpClient = new HttpClient()"),
    ("x equals 5.", "x = 5"),                                           # Whisper's final full stop goes in code
    ("Open paren, close paren.", "()"),                                 # its commas after symbols too
    ("git commit dash m quote fix the build quote", 'git commit -m "fix the build"'),
    ("cd tilde slash projects", "cd ~/projects"),
    ("a new line new line b", "a\n\nb"),
    ("camel case", "camel case"),                                       # a formatter with no words is left as said
    ("Fix the build first.", "Fix the build first."),                   # no code words: untouched
    ("", ""),
])
def test_formatters_and_symbols_together(spoken, typed):
    assert codemode.format_code(spoken) == typed


@pytest.mark.parametrize("text", ["the dotted line", "an underscored word", "a commander", "tabby cat", "starboard",
                                  "plussed", "hashtag", "equalsign"])
def test_whole_words_only(text):
    assert codemode.format_code(text) == text


def test_twice_changes_nothing_for_code():
    once = codemode.format_code("camel case user name equals open paren close paren")
    assert codemode.format_code(once) == once


# ------------------------------------------------------------------ which apps

def test_default_code_apps_are_editors_and_terminals():
    apps = {a.lower() for a in core.DEFAULT_CONFIG["code_apps"]}
    for exe in ("code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "devenv.exe", "idea64.exe", "pycharm64.exe",
                "webstorm64.exe", "clion64.exe", "rider64.exe", "sublime_text.exe", "notepad++.exe", "windowsterminal.exe",
                "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "alacritty.exe", "wezterm-gui.exe", "mintty.exe"):
        assert exe in apps
    assert core.DEFAULT_CONFIG["code_mode"] == "auto" and core.DEFAULT_CONFIG["code_cleanup"] == "rules"


@pytest.mark.parametrize("exe,style,cfg,expected", [
    ("Code.exe", "raw", {}, True),
    ("WindowsTerminal.exe", "raw", {}, True),
    ("pwsh.exe", "neutral", {}, True),
    ("notepad.exe", "neutral", {}, False),
    ("notepad.exe", "code", {}, True),                   # the per-app style 'code'
    ("Code.exe", "raw", {"code_mode": "off"}, False),
    ("notepad.exe", "code", {"code_mode": "off"}, False),
    ("myide.exe", "neutral", {"code_apps": ["MyIDE.exe"]}, True),
    ("Code.exe", "raw", {"code_apps": []}, False),
    ("", "neutral", {}, False),
    (None, "neutral", {}, False),
])
def test_is_code_app(exe, style, cfg, expected):
    assert codemode.is_code_app(dict(core.DEFAULT_CONFIG, **cfg), exe, style) is expected


# ------------------------------------------------------------------ the pipeline

def _cfg(**kw):
    return dict(core.DEFAULT_CONFIG, api_key="k", **kw)


def _no_cleanup(monkeypatch):
    monkeypatch.setattr(core, "cleanup", lambda *a: (_ for _ in ()).throw(AssertionError("cleanup must not run")))


def test_a_code_app_gets_the_rules_and_no_ai_cleanup(monkeypatch):
    _no_cleanup(monkeypatch)
    r = core.process_text(_cfg(), "Snake case max retries equals 5.", "pwsh.exe", "pwsh.exe")
    assert r.text == "max_retries = 5" and not r.cleaned and not r.cleanup_error


def test_a_terminal_gets_code_mode_too(monkeypatch):
    _no_cleanup(monkeypatch)
    assert core.process_text(_cfg(), "git commit dash m quote fix quote", "WindowsTerminal.exe", "x").text == 'git commit -m "fix"'


def test_no_change_in_an_ordinary_app(monkeypatch):
    monkeypatch.setattr(core, "cleanup", lambda cfg, raw, style, label: raw)
    r = core.process_text(_cfg(), "camel case user name equals open paren close paren", "notepad.exe", "Notepad")
    assert r.text == "camel case user name equals open paren close paren"


def test_code_mode_off_leaves_a_code_app_alone():
    r = core.process_text(_cfg(code_mode="off", cleanup=False), "camel case user name", "Code.exe", "Code")
    assert r.text == "camel case user name"


def test_no_lists_in_a_code_app():
    r = core.process_text(_cfg(cleanup=False, app_styles={}), "First, milk. Second, eggs.", "Code.exe", "Code")
    assert r.text == "First, milk. Second, eggs."


def test_ai_cleanup_in_a_code_app_when_chosen_gets_a_code_prompt(monkeypatch):
    styles = []
    monkeypatch.setattr(core, "cleanup", lambda cfg, raw, style, label: styles.append(style) or raw)
    r = core.process_text(_cfg(code_cleanup="llm", app_styles={}), "camel case user name equals five", "Code.exe", "Code")
    assert styles == ["code"] and r.text == "userName = five"


def test_the_code_prompt_keeps_code_verbatim_and_flat():
    p = core.system_prompt("code", [], "Code")
    assert "keep identifiers, symbols and casing" in p.lower() and "never add prose" in p.lower()
    assert core.STRUCTURE_BY_STYLE["casual"] in p


def test_a_raw_style_code_app_never_goes_to_the_ai_even_with_llm_chosen(monkeypatch):
    _no_cleanup(monkeypatch)
    assert core.process_text(_cfg(code_cleanup="llm"), "a dot b", "Code.exe", "Code").text == "a.b"   # code.exe is raw by default


def test_dictionary_spellings_still_apply_in_code(monkeypatch):
    _no_cleanup(monkeypatch)
    cfg = _cfg(dictionary=["use memo => useMemo"])
    assert core.process_text(cfg, "use memo open paren close paren", "Code.exe", "Code").text == "useMemo()"


def test_empty_input_in_a_code_app():
    assert core.process_text(_cfg(), "", "Code.exe", "Code").text == ""


# ------------------------------------------------------------------ the help text lists the whole table

def test_the_window_help_lists_every_formatter_and_symbol():
    with open(os.path.join(ROOT, "windows", "ui", "index.html"), encoding="utf-8") as f:
        html = f.read()
    box = re.search(r'<details[^>]*id="code-help".*?</details>', html, re.S)
    assert box, "the code mode help box is missing"
    text = box.group(0).lower()
    for spoken in list(codemode.FORMATTERS) + [s for s, _, _, _ in codemode.SYMBOLS]:
        assert spoken in text, spoken


def test_the_window_has_the_three_settings_and_the_code_style():
    with open(os.path.join(ROOT, "windows", "ui", "index.html"), encoding="utf-8") as f:
        html = f.read()
    for row in ('id="code-mode"', 'id="code-cleanup"', 'id="code-apps"', '["code", "Code (spoken symbols)"]',
                "save({ code_mode: ", "save({ code_cleanup: ", "save({ code_apps: "):
        assert row in html, row


def test_the_docs_page_lists_every_formatter_and_symbol():
    with open(os.path.join(ROOT, "documentation", "15-code-mode.md"), encoding="utf-8") as f:
        text = f.read().lower()
    for spoken in list(codemode.FORMATTERS) + [s for s, _, _, _ in codemode.SYMBOLS]:
        assert "| " + spoken + " |" in text, spoken


# ------------------------------------------------------------------ #63: ordinary English is not code

@pytest.mark.parametrize("text", [
    "add a quote from the ceo",
    "take a hash of the file",
    "Plus, I need to buy milk.",
    "press the tab key twice",
    "open a new tab and star the repo",
    "it took less than a minute",
    "the pipe is leaking, so dash to the store",
    "I'll dot the i's and cross the t's",
    "he gave it a five star review",
    "the slash command is new",
    "ten percent of users said yes",
    "a single quote from him is enough",
    "that equals trouble",
    "shoot the arrow at the target",
    "two plus two is more than three minus one",
    "Go to the slash command page and star it.",
    "Ten percent plus tax, minus the discount.",
])
def test_ordinary_sentences_keep_their_words(text):
    assert codemode.format_code(text) == text


@pytest.mark.parametrize("spoken,typed", [
    ("ls dash la", "ls -la"),                                           # a command name first: code
    ("cat log dot txt pipe grep error", "cat log.txt | grep error"),
    ("npm install dash dash save dev", "npm install --save dev"),       # a symbol next to a symbol
    ("print open paren a dot b close paren", "print(a.b)"),             # a code symbol elsewhere: code
    ('git commit dash m quote add the dot env file quote', 'git commit -m "add the dot env file"'),   # "the dot": prose
    ("x dot y", "x.y"),                                                 # a one-letter name next to it
    ("total equals 5", "total = 5"),                                    # a digit next to it
    ("user underscore id equals my var", "user_id = my var"),
    ("if x less than 10 colon", "if x < 10:"),
    ("dash dash verbose", "--verbose"),                                 # the same symbol twice
])
def test_code_words_still_become_symbols(spoken, typed):
    assert codemode.format_code(spoken) == typed


# ------------------------------------------------------------------ TXT-11

def test_a_formatter_keeps_letters_outside_ascii():
    assert codemode.format_code("camel case café menu") == "caféMenu"
    assert codemode.format_code("snake case नमस्ते दुनिया") == "नमस्ते_दुनिया"


def test_new_paragraph_works_in_a_code_app_without_the_ai(monkeypatch):
    _no_cleanup(monkeypatch)
    r = core.process_text(_cfg(cleanup=False), "first line new paragraph second line", "Code.exe", "Code")
    assert r.text == "first line\n\nsecond line"
