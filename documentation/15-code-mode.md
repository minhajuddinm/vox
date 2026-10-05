# 15. Code mode (Windows)

Spoken formatters and symbols for code editors and terminals: say `camel case user name` and Vox types `userName`; say `open paren` and it types `(`. **Windows only**; the Android app keeps its prose rules. Code: `windows/codemode.py` (pure), wired in `windows/vox_core.py:process_text`. Tests: `tests/test_codemode.py` (a row for every formatter and symbol, the interactions, which apps, the pipeline, and that the table below and the window's help box list everything in `codemode.FORMATTERS` and `codemode.SYMBOLS`). Never run with a real microphone or a real editor: the tests feed text as Whisper writes it.

## When it is on

| Setting (`config.json`) | Default | Effect |
|---|---|---|
| `code_mode` | `auto` | `auto`: on in a code app; `off`: code apps are typed into like any other app. Settings, Voice & audio, the Code mode card. |
| `code_apps` | 21 editors and terminals | Program names (exe, case ignored) that count as code apps: `Code.exe`, `Code - Insiders.exe`, `cursor.exe`, `windsurf.exe`, `devenv.exe`, `idea64.exe`, `pycharm64.exe`, `webstorm64.exe`, `clion64.exe`, `rider64.exe`, `sublime_text.exe`, `notepad++.exe`, `WindowsTerminal.exe`, `wt.exe`, `cmd.exe`, `powershell.exe`, `pwsh.exe`, `conhost.exe`, `alacritty.exe`, `wezterm-gui.exe`, `mintty.exe`. Edited as a comma-separated box. |
| per-app style `code` | none | Styles page, Per app: "Code (spoken symbols)" makes any app a code app (also when it is not in `code_apps`). |
| `code_cleanup` | `rules` | `rules`: no AI cleanup in a code app, so code is never rewritten; `llm`: the AI cleanup runs with the style `code` (prompt: keep identifiers, symbols and casing exactly as spoken, keep spoken symbol and formatter names, never add prose, quotes or a final period; no lists, no blank lines), then the rules below. An app whose style is `raw` (the default for `code.exe` and `windowsterminal.exe`) never goes to the AI either way. |

In a code app the order is: AI cleanup (only with `llm`) and its fidelity guard, then the formatters and symbols, then the dictionary's replacements and spellings, then the snippets. "Lists and paragraphs" never runs in a code app, and "new line" is handled by the symbol table instead of the spoken-command rule; "new paragraph" (not a symbol) still becomes a blank line when the AI cleanup did not run.

## Formatters

A formatter takes the words that follow it, lowercase, joined its way, until a stop: a spoken symbol, the word `then` (dropped), punctuation after a word (Whisper's comma or full stop, dropped), a line break, or the end. Apostrophes are dropped (`don't` becomes `dont`), digits and letters of any script are kept (`camel case café menu` gives `caféMenu`). A formatter with no words after it is left as said. Whisper sometimes writes `camelcase`, `snakecase`, `pascalcase` or `kebabcase` as one word; those work too.

| Say | Types | Example |
|---|---|---|
| camel case | words joined | `camel case user name` gives `userName` |
| snake case | words joined | `snake case max retries` gives `max_retries` |
| pascal case | words joined | `pascal case http client` gives `HttpClient` |
| kebab case | words joined | `kebab case my component` gives `my-component` |
| constant case | words joined | `constant case api key` gives `API_KEY` |
| dotted case | words joined | `dotted case foo bar` gives `foo.bar` |
| all caps | words joined | `all caps hello` gives `HELLO` |
| no space | words joined | `no space foo bar` gives `foobar` |

## Symbols

Whole words only, case ignored: `dotted`, `tabby` or `starboard` are never touched. Words that are also everyday English are only typed as symbols in code (see "Code or prose" below). The longest spoken form wins (`equals equals` before `equals`, `double colon` before `colon`, `single quote` before `quote`). "Space before" / "Space after" say whether the space between the symbol and its neighbour is kept; quotes pair up: the first one of a kind opens (no space after it), the next one closes (no space before it).

| Say | Types | Space before | Space after |
|---|---|---|---|
| open paren | `(` | no | no |
| open parenthesis | `(` | no | no |
| close paren | `)` | no | kept |
| close parenthesis | `)` | no | kept |
| open bracket | `[` | no | no |
| close bracket | `]` | no | kept |
| open brace | `{` | kept | no |
| close brace | `}` | no | kept |
| open angle | `<` | no | no |
| close angle | `>` | no | kept |
| equals | `=` | kept | kept |
| equals equals | `==` | kept | kept |
| not equals | `!=` | kept | kept |
| less than | `<` | kept | kept |
| greater than | `>` | kept | kept |
| plus equals | `+=` | kept | kept |
| arrow | `->` | kept | kept |
| fat arrow | `=>` | kept | kept |
| dot | `.` | no | no |
| comma | `,` | no | kept |
| semicolon | `;` | no | kept |
| colon | `:` | no | kept |
| double colon | `::` | no | no |
| underscore | `_` | no | no |
| dash | `-` | kept | no |
| slash | `/` | no | no |
| backslash | `\` | no | no |
| quote | `"` | pairs (opens, then closes) | pairs |
| single quote | `'` | pairs (opens, then closes) | pairs |
| backtick | `` ` `` | pairs (opens, then closes) | pairs |
| pipe | `\|` | kept | kept |
| ampersand | `&` | kept | kept |
| hash | `#` | kept | no |
| at sign | `@` | no | no |
| dollar sign | `$` | kept | no |
| percent | `%` | no | kept |
| star | `*` | kept | kept |
| plus | `+` | kept | kept |
| minus | `-` | kept | kept |
| tilde | `~` | kept | no |
| new line | line break (in a terminal a space: Vox never presses Enter there, see `paste.terminal_text`) | no | no |
| tab | tab | no | no |

Examples (from the tests): `print open paren quote hello quote close paren` types `print("hello")`; `def snake case load config open paren path close paren colon` types `def load_config(path):`; `git commit dash m quote fix the build quote` types `git commit -m "fix the build"`; `cd tilde slash projects` types `cd ~/projects`.

## Code or prose

Code apps are also where people write commit messages, chat in a terminal or type a comment, so a spoken symbol that is also an everyday word stays a word unless the dictation is code. These are the ambiguous ones (`codemode.AMBIGUOUS`): equals, arrow, dot, dash, slash, quote, single quote, pipe, hash, percent, star, plus, minus, tab, less than, greater than. One of them becomes its symbol when:

- the dictation is code: it starts with a command name (`codemode.COMMANDS`: git, ls, cd, cat, grep, npm, pip, python, node, docker, ssh, curl, echo, rm, mkdir, sudo, cargo, ... but not everyday words such as go or head), or it has a formatter or a spoken symbol that is not ambiguous and not plain punctuation (open paren, underscore, tilde, at sign, equals equals...); or
- a word next to it looks like code: one letter other than a and I (`x dot y`), a digit (`total equals 5`), a character other than letters and apostrophes (`log.txt`), a capital inside the word (`getUser`), or another symbol (`dash dash verbose`, `cd dot dot`); or
- it is a quote that closes one opened before.

And never right after an article or possessive (the, an, this, that, my, your, one, another, each, every, some, any, no...): `add the dot env file` keeps `dot`. Comma, colon, semicolon and new line are always typed as symbols but do not by themselves make a dictation code.

| Said in a code app | Typed |
|---|---|
| add a quote from the ceo | add a quote from the ceo |
| take a hash of the file | take a hash of the file |
| it took less than a minute | it took less than a minute |
| he gave it a five star review | he gave it a five star review |
| ls dash la | ls -la |
| cat log dot txt pipe grep error | cat log.txt \| grep error |
| git commit dash m quote add the dot env file quote | git commit -m "add the dot env file" |
| x dot y | x.y |

The formatters always apply, so `no space` and `all caps` inside a sentence (`there is no space left`) are still read as formatters.

## Whisper's own punctuation and capitals

Only when the text had a formatter or symbol (otherwise it is typed as Whisper wrote it): punctuation Whisper put right after a spoken symbol (`open paren,`) is dropped, a full stop at the very end is dropped, and a first word written as `Console` or `If` (one capital, then lowercase) starts lowercase.

## What it does not do

- **Variable names from the file you are editing**: out of scope; Vox does not read the file, and only the app name goes to the cleanup server with the text ([09-security-privacy.md](09-security-privacy.md)). Code mode itself is detected from the foreground program's file name only. Note that **Learn from my corrections** (on by default) also runs in code apps: for up to 3 minutes after a paste it reads the focused editor control's text through UI Automation (up to 20,000 characters, in memory only) to learn the words you fix, nothing else. Identifiers you put in the Dictionary are spelled as saved, also in code apps (the dictionary runs after the formatters; for example `use memo => useMemo`).
- Number words are not turned into digits (Whisper usually writes digits already).
- No per-language command sets, no "reformat that", no editing commands.
- No spoken "end" for a formatter: say a symbol or `then`, or pause so Whisper writes a comma.
