# 15. Code mode (Windows)

Spoken formatters and symbols for code editors and terminals: say `camel case user name` and Vox types `userName`; say `open paren` and it types `(`. **Windows only**; the Android app keeps its prose rules. Code: `windows/codemode.py` (pure), wired in `windows/vox_core.py:process_text`. Tests: `tests/test_codemode.py` (a row for every formatter and symbol, the interactions, which apps, the pipeline, and that the table below and the window's help box list everything in `codemode.FORMATTERS` and `codemode.SYMBOLS`). Never run with a real microphone or a real editor: the tests feed text as Whisper writes it.

## When it is on

| Setting (`config.json`) | Default | Effect |
|---|---|---|
| `code_mode` | `auto` | `auto`: on in a code app; `off`: code apps are typed into like any other app. Settings, Voice & audio, the Code mode card. |
| `code_apps` | 21 editors and terminals | Program names (exe, case ignored) that count as code apps: `Code.exe`, `Code - Insiders.exe`, `cursor.exe`, `windsurf.exe`, `devenv.exe`, `idea64.exe`, `pycharm64.exe`, `webstorm64.exe`, `clion64.exe`, `rider64.exe`, `sublime_text.exe`, `notepad++.exe`, `WindowsTerminal.exe`, `wt.exe`, `cmd.exe`, `powershell.exe`, `pwsh.exe`, `conhost.exe`, `alacritty.exe`, `wezterm-gui.exe`, `mintty.exe`. Edited as a comma-separated box. |
| per-app style `code` | none | Styles page, Per app: "Code (spoken symbols)" makes any app a code app (also when it is not in `code_apps`). |
| `code_cleanup` | `rules` | `rules`: no AI cleanup in a code app, so code is never rewritten; `llm`: the AI cleanup runs with the style `code` (prompt: keep identifiers, symbols and casing exactly as spoken, keep spoken symbol and formatter names, never add prose, quotes or a final period; no lists, no blank lines), then the rules below. An app whose style is `raw` (the default for `code.exe` and `windowsterminal.exe`) never goes to the AI either way. |

In a code app the order is: AI cleanup (only with `llm`) and its fidelity guard, then the formatters and symbols, then the dictionary's replacements and spellings, then the snippets. "Lists and paragraphs" never runs in a code app, and "new line" is handled by the symbol table instead of the spoken-command rule.

## Formatters

A formatter takes the words that follow it, lowercase, joined its way, until a stop: a spoken symbol, the word `then` (dropped), punctuation after a word (Whisper's comma or full stop, dropped), a line break, or the end. Apostrophes are dropped (`don't` becomes `dont`), digits are kept. A formatter with no words after it is left as said. Whisper sometimes writes `camelcase`, `snakecase`, `pascalcase` or `kebabcase` as one word; those work too.

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

Whole words only, case ignored: `dotted`, `tabby` or `starboard` are never touched. The longest spoken form wins (`equals equals` before `equals`, `double colon` before `colon`, `single quote` before `quote`). "Space before" / "Space after" say whether the space between the symbol and its neighbour is kept; quotes pair up: the first one of a kind opens (no space after it), the next one closes (no space before it).

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
| new line | line break | no | no |
| tab | tab | no | no |

Examples (from the tests): `print open paren quote hello quote close paren` types `print("hello")`; `def snake case load config open paren path close paren colon` types `def load_config(path):`; `git commit dash m quote fix the build quote` types `git commit -m "fix the build"`; `cd tilde slash projects` types `cd ~/projects`.

## Whisper's own punctuation and capitals

Only when the text had a formatter or symbol (otherwise it is typed as Whisper wrote it): punctuation Whisper put right after a spoken symbol (`open paren,`) is dropped, a full stop at the very end is dropped, and a first word written as `Console` or `If` (one capital, then lowercase) starts lowercase.

## What it does not do

- **Variable names from the file you are editing**: out of scope. It would need the editor's screen-reader interface (UI Automation) and reading the file, which Vox does not do ([09-security-privacy.md](09-security-privacy.md): only the app name goes with the text). Identifiers you put in the Dictionary are spelled as saved, also in code apps (the dictionary runs after the formatters; for example `use memo => useMemo`).
- Number words are not turned into digits (Whisper usually writes digits already).
- No per-language command sets, no "reformat that", no editing commands.
- No spoken "end" for a formatter: say a symbol or `then`, or pause so Whisper writes a comma.
