# 6. Dictation pipeline

How audio becomes text, on both platforms. Windows: `windows/vox_core.py` (`process_detailed`). Android: `DictationService.send` with helpers in `GroqClient`. Meeting notes use the same server but different prompts (see [04-windows-app.md](04-windows-app.md)).

## Steps

```
 audio (16 kHz mono int16)
   -> silence gate            peak < 655 of 32768 -> nothing is sent
   -> transcribe              POST {server}/audio/transcriptions   (Whisper)
   -> silence-phrase filter   "thank you", "thanks for watching", "thank you for watching", "you", "bye"
   -> choose style            per-app style, else default style
   -> cleanup?                only if cleanup is on AND style != raw AND the text has >= 3 words
        yes: POST {server}/chat/completions  -> sanitize -> looks_valid guard
        no / failed / rejected: use the raw transcript, then apply_spoken_commands
   -> apply_replacements      dictionary "wrong => right", whole word, case-insensitive
   -> insert                  paste (Windows) / accessibility insert (Android)
   -> history                 unless keep_history is off
```

## Server calls

The server is `base_url` (default `https://api.groq.com/openai/v1`); trailing slashes are ignored. The `Authorization: Bearer <key>` header is sent only when a key is set.

| Call | Request | Notes |
|---|---|---|
| Speech to text | `POST /audio/transcriptions`, multipart: `file` (WAV), `model` (`stt_model`, default `whisper-large-v3-turbo`), `response_format=json`, `temperature=0`, optional `language`, optional `prompt` | `prompt` is the dictionary terms joined by commas, at most about 600 characters (`whisper_prompt`) |
| Cleanup | `POST /chat/completions`, JSON: `model` (`llm_model`, default `openai/gpt-oss-20b`), `temperature=0.2`, `max_tokens=max(1024, 2 x transcript length)`, system prompt + user message `<transcript>\n...\n</transcript>` | For models whose name contains `gpt-oss`: `reasoning_effort="low"`, `include_reasoning=false` |
| Key test | `GET /models` | 200 means the key (and server) work |
| Meeting speech | `POST /audio/transcriptions` with `response_format=verbose_json` | Segments carry quality scores used to drop hallucinations |
| Meeting notes and questions | `POST /chat/completions` with `notes_model` (default `openai/gpt-oss-120b`) | |

Timeouts: Windows 60 s (dictation), 180 s (meeting speech), 240 s (meeting notes); Android 15 s connect, 60 s read.

### Retry policy

| Platform | What is retried | How |
|---|---|---|
| Windows | Every request through `post_with_retry`: connection errors, timeouts and HTTP 500, 502, 503, 504 | 3 attempts, waits 0.7 s then 1.4 s. HTTP 429 and 4xx are **not** retried here |
| Android | Only the speech request: network errors, 5xx, 429, 408 (`GroqClient.isRetryable`) | 3 attempts, waits 0.8 s then 1.6 s. Cleanup is tried once |
| Both | A dictation whose speech request still fails is kept for a manual retry | Windows tray "Retry last dictation"; Android notification "Retry" ([decisions/0009-keep-failed-recordings-and-retry.md](decisions/0009-keep-failed-recordings-and-retry.md)) |

Meetings keep their own 429 handling (`Meeting._stt`: wait 6 s x attempt, up to 4 attempts).

## Cleanup prompt

Built by `system_prompt(style, terms, app_label)` / `GroqClient.systemPrompt`. It tells the model that the user message is a raw transcript in `<transcript>` tags and asks for: output only the final text; never answer the transcript; remove fillers, stutters and false starts; apply self-corrections ("no wait", "actually", "scratch that"); fix punctuation and capitalization without changing wording; convert spoken commands ("new line", "new paragraph", spoken punctuation); format spoken lists; write numbers, dates, emails and URLs normally; spell dictionary terms exactly (at most 150). Then a style line and, if known, `The text will be typed into the app: <name>.`

Styles: `formal`, `casual`, `very_casual`, `neutral` (default text for any other value), `raw` (skips cleanup entirely). The style name in the prompt is matched case-insensitively on both platforms; the check that skips cleanup for `raw` is exact-case on Windows (`raw`) and lower-cased on Android. Default per-app styles are in `vox_core.DEFAULT_CONFIG["app_styles"]` (Windows exe names) and `Prefs.DEFAULT_APP_STYLES` (Android packages).

The app label is the exe name on Windows (for example `slack.exe`) and the app's display name on Android. The window title is never used ([decisions/0006-app-name-only-to-the-model.md](decisions/0006-app-name-only-to-the-model.md)).

## Guards and fallbacks

| Function | Rule |
|---|---|
| `sanitize` | Removes `<think>...</think>`, `<transcript>` tags, trims, and strips one pair of wrapping double quotes |
| `looks_valid(raw, cleaned)` | Cleaned text must be non-blank and at most `1.6 x len(raw) + 40` characters, otherwise the model probably answered the transcript instead of cleaning it |
| `is_silence_hallucination` | Lower-cases, keeps `a-z` and spaces, and compares with the five phrases above |
| `is_silent` / `Pcm.isSilent` | Loudest sample below `SILENCE_PEAK = 655` (about -34 dBFS) |
| `apply_spoken_commands` | Only when cleanup did not produce the text: "new paragraph" -> blank line, "new line" -> line break; a comma/semicolon/colon before the command and any punctuation after it are dropped; `. ? !` before it stay |
| `apply_replacements` | Whole-word, case-insensitive, literal (no regex), `_` counts as part of a word, Unicode case folding |

If cleanup fails or is rejected the raw transcript is used and the user is told ([decisions/0013-raw-fallback-when-cleanup-fails.md](decisions/0013-raw-fallback-when-cleanup-fails.md)).

`vox_core.process_detailed` returns `Result(raw, text, cleaned, cleanup_error)`; `process` returns `(raw, text)`.

## Dictionary

Both platforms store the dictionary as lines: a plain line is a **term** (spelling hint for Whisper and the cleanup prompt); `wrong => right` is a **replacement** (applied at the end; its right side also counts as a term). **People** are extra terms. Terms are de-duplicated in order: people first, then dictionary lines. Comments start with `#`.

`suggest_corrections` / `Corrections.suggest` compare a dictation with the user's fixed version at word level and propose replacements: swaps of at most 3 words, wrong text of at least 2 characters, and a capitalization-only change at the start of a sentence is ignored. The user confirms each one in the history "Fix a word" panel.

## The shared golden file (`spec/golden.txt`)

One case per line, fields separated by TAB; `\n`, `\t`, `\\` are escapes; lists use `|`; replacement pairs use `;` between pairs and `=>` inside one. Kinds: `sanitize`, `looks_valid`, `replace`, `whisper`, `terms`, `prompt`, `spoken`, `silence`. `tests/test_parity.py` (Python) and `android/test/com/minhaj/vox/ParityTest.java` (Java) run every line. If you change any of these behaviours, change both implementations and the affected lines in the file (compute the expected value from the Python implementation and review it by hand). Never edit the file just to make one side pass.
