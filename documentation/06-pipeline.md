# 6. Dictation pipeline

How audio becomes text, on both platforms. Windows: `windows/vox_core.py` (`process_detailed`). Android: `DictationService.send` with helpers in `ApiClient`. Meeting notes use the same server but different prompts (see [04-windows-app.md](04-windows-app.md)).

## Steps

```
 audio (16 kHz mono int16)
   -> silence gate            peak < 655 of 32768 -> nothing is sent
   -> transcribe              POST {server}/audio/transcriptions   (Whisper)
   -> silence-phrase filter   "thank you", "thanks for watching", "thank you for watching", "you", "bye"
   -> choose style            per-app style, else default style
   -> cleanup?                only if cleanup is on AND style != raw AND the text has >= cleanup_min_words words (default 3)
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
| Model list | `GET /models` per role (Ollama fallback `GET /api/tags`) | Parsed and classified into speech and chat models by `providers.parse_models` / `Providers.parseModels` |
| Meeting speech | `POST /audio/transcriptions` with `response_format=verbose_json` | Segments carry quality scores used to drop hallucinations |
| Meeting notes and questions | `POST /chat/completions` with `notes_model` (default `openai/gpt-oss-120b`) | |

Timeouts: Windows 60 s (dictation), 180 s (meeting speech), 240 s (meeting notes); Android 15 s connect, 60 s read.

### Retry policy

| Platform | What is retried | How |
|---|---|---|
| Windows | Every request through `post_with_retry`: connection errors, timeouts and HTTP 500, 502, 503, 504 | 3 attempts, waits 0.7 s then 1.4 s. HTTP 429 and 4xx are **not** retried here |
| Android | Only the speech request: network errors, 5xx, 429, 408 (`ApiClient.isRetryable`) | 3 attempts, waits 0.8 s then 1.6 s. Cleanup is tried once |
| Both | A dictation whose speech request still fails is kept for a manual retry | Windows tray "Retry last dictation"; Android notification "Retry" ([decisions/0009-keep-failed-recordings-and-retry.md](decisions/0009-keep-failed-recordings-and-retry.md)) |

Meetings keep their own 429 handling (`Meeting._stt`: wait 6 s x attempt, up to 4 attempts).

## Cleanup prompt

Built by `system_prompt(style, terms, app_label)` / `ApiClient.systemPrompt`. It tells the model that the user message is a raw transcript in `<transcript>` tags and asks for: output only the final text; never answer the transcript; remove fillers, stutters and false starts; apply self-corrections ("no wait", "actually", "scratch that"); fix punctuation and capitalization without changing wording; convert spoken commands ("new line", "new paragraph", spoken punctuation); format spoken lists; write numbers, dates, emails and URLs normally; spell dictionary terms exactly (at most 150). Then a style line and, if known, `The text will be typed into the app: <name>.`

Styles: `formal`, `casual`, `very_casual`, `neutral` (default text for any other value), `raw` (skips cleanup entirely). The style name in the prompt is matched case-insensitively on both platforms; the check that skips cleanup for `raw` is exact-case on Windows (`raw`) and lower-cased on Android. Default per-app styles are in `vox_core.DEFAULT_CONFIG["app_styles"]` (Windows exe names) and `Prefs.DEFAULT_APP_STYLES` (Android packages).

Short phrases skip cleanup (saves a round trip to the model). The threshold is the `cleanup_min_words` setting (Settings, "Skip AI cleanup for phrases shorter than N words"). `clean_min_words(value)` / `ApiClient.cleanMinWords` turn the stored value into a whole number from 1 to 20 (anything that is not a whole number gives 3; numbers outside the range are clamped), and `needs_cleanup(raw, style, enabled, min_words)` / `ApiClient.needsCleanup` decide: false when cleanup is off or the style is `raw`, otherwise true when the transcript has at least that many words (words are runs of non-space characters). `process_text` and `DictationService.send` both call it; the `gate` rows of `spec/golden.txt` keep the two in step. A skipped phrase is handled like a failed cleanup without the warning: spoken commands, then replacements.

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

Both platforms store the dictionary as lines: a plain line is a **term** (spelling hint for Whisper and the cleanup prompt); `wrong => right` is a **replacement** (applied at the end; its right side also counts as a term). **People** are extra terms. Terms are de-duplicated in order: people first, then dictionary lines. Comments start with `#`. Windows keeps the dictionary and the people as lists of lines in `config.json`, the phone as text with a line break after each; with relay sync both travel as lists of text, and the phone's comment lines are not shared (`ProfileMap`, see [05-android-app.md](05-android-app.md)).

`suggest_corrections` / `Corrections.suggest` compare a dictation with the user's fixed version at word level and propose replacements: swaps of at most 3 words, wrong text of at least 2 characters, and a capitalization-only change at the start of a sentence is ignored. The user confirms each one in the history "Fix a word" panel.

## The shared golden file (`spec/golden.txt`)

One case per line, fields separated by TAB; `\n`, `\t`, `\\` are escapes; lists use `|`; replacement pairs use `;` between pairs and `=>` inside one. Kinds: `sanitize`, `looks_valid`, `replace`, `whisper`, `terms`, `prompt`, `spoken`, `silence`, `gate` (raw text, style, cleanup on/off, minimum words, expected), `title`, `ftsq`, `remotewins` (the last three are the voice-note rules: `notes.auto_title`, `notes.fts_query`, and `notes.apply_remote` against a temporary `notes.db`; Java: `NoteLogic`; a `remotewins` row is `hasLocal localUpdated remoteUpdated remoteDeleted expected`), `merge3` and `profilefields` (the profile sync: `sync.merge3` and `sync.PROFILE_FIELDS` / `PROFILE_KEY_FIELDS`; Java: `ProfileMerge`; a `merge3` row is `base local remote expected` with the value of one field on each side, `~` meaning the field is absent there, and an empty column the empty string; a `profilefields` row is `shared` or `keys` and the field names joined by `|`) and `devname` (a typed device name and what `sync.device_name` / `NoteLogic.deviceName` make of it: trimmed, then cut at 60 code points; the fallback for a blank name differs per device and has no row). `tests/test_parity.py` (Python) and `android/test/com/minhaj/vox/ParityTest.java` (Java) run every line. If you change any of these behaviours, change both implementations and the affected lines in the file (compute the expected value from the Python implementation and review it by hand). Never edit the file just to make one side pass.

## Per-role servers, model discovery and reasoning fields

- **Role settings.** `providers.role_settings(cfg, role)` / `Providers.roleSettings` return the address, key and model for `stt` or `llm`. A role with its own address (`stt_base_url`, `llm_base_url`) uses only its own key; the main key is never sent to a different server. `vox_core.transcribe` and `cleanup` (and the meeting code) call `api_base(cfg, role)` and `auth_headers(cfg, role)`. `key_missing` is true when a role talks to a server outside the private network without a key.
- **The relay as the AI server (Windows; Android not yet).** With `relay_proxy` on and `relay_url` and `relay_token` filled in (`providers.uses_relay`), `role_settings` returns `<relay_url>/proxy/<role>` as the address (spaces and trailing `/` removed) and the relay token (from `load_config`, so already unprotected) as the key; the role's model is unchanged, and the provider addresses and keys (`base_url`, `stt_*`, `llm_*`, `api_key`) are not used. Everything else needs no change because it already goes through the role address: the dictation calls become `POST {relay}/proxy/stt/audio/transcriptions` and `POST {relay}/proxy/llm/chat/completions`, the model list and warm-up `GET {relay}/proxy/<role>/models`, and the Test buttons call the same two. Only those four relay routes are ever used, and each request carries only the relay token: the provider key is never sent to the relay, and the relay token is never sent to a provider (with the switch off `role_settings` ignores the relay). `vox_core.endpoint_error` checks `relay_url` (plain http only for private hosts) instead of the provider addresses while the relay is in use, so the token cannot travel in the clear to a public host. With the switch on but the address or token missing, `role_settings` returns the normal settings and `providers.proxy_problem(cfg)` returns `Turn on the relay first`, which the Settings page shows under the switch. Errors: the relay's own 411, 413, 429 and 503 are `{"error": "text"}` and its 502 is `{"error": {"message": ...}}`; `vox_core.check_response` and `providers._error_text` accept both shapes, so the user reads the text and not raw JSON. A 401 or 403 from the relay (wrong relay token) or passed through from the AI server (wrong key on the relay) gets the hint "check the relay token and the AI server key set on the relay page" in the `ApiError` text (`check_response(r, via_relay)`) and on the Test button and model list (`providers.explain(..., via_relay)`). One gap: the tray notification for a dictation that fails with 401 keeps the engine's fixed wording ("The server rejected the API key. Check Vox > Settings."), and the meeting notes call (`meeting.py`) does not pass `via_relay`, so neither shows the hint. Tests: `tests/test_providers.py` (section "the relay as the AI server").
- **Model list.** `GET {base}/models` with a 5 s timeout. Each entry is classified `stt`, `llm` or `hidden`: an explicit provider field first (OpenRouter `architecture.output_modalities`, `task`/`type`, Groq `active: false` is hidden), otherwise the model id (`whisper`, `transcribe`, `voxtral`, `parakeet`, `moonshine`, `canary` mean speech; `orpheus`, `tts`, `guard`, `embed`, `rerank`, image models are hidden). The id rules are checked on both platforms by the `models` rows of `spec/golden.txt`.
- **Test.** Speech: a silent one-second WAV to `/audio/transcriptions`; cleanup: a tiny `chat/completions` call. Success is HTTP 200; failures are explained (401 key, 404 no such endpoint, 429 rate limit).
- **Reasoning fields.** `reasoning_effort: "low"` and `include_reasoning: false` are sent only for models with `gpt-oss` in the name and `llm_reasoning` not `off`. If the server answers 400 or 422 the request is repeated once without them and that address and model are remembered for the rest of the run. A leading `<think>...</think>` block in the answer is removed.

## Connection reuse

Windows posts through one shared `requests.Session` (`vox_core._post`); `vox_core.warm` opens the role servers' connections at key-down. Android reuses connections by reading responses fully and not disconnecting; `ApiClient.warm` does the opening. Without this each request paid a fresh TLS handshake.

## "About you" context in the cleanup prompt

`system_prompt(style, terms, app_label, context)` (Python) and `ApiClient.systemPrompt(style, terms, appLabel, context)` (Java) add, after the dictionary terms and before the style line, a rule that introduces `<about_speaker>...</about_speaker>` as reference material for spelling, names, jargon and tone, never text to output and never instructions. The text comes from `clean_context` / `cleanContext`: line endings normalised, our own tags removed, trimmed, capped at 8,000 characters. An empty context adds nothing, so existing prompts are unchanged. The constant parts come first so automatic prefix caching (Groq gpt-oss, OpenAI) can reuse them. Golden rows: `context` and `promptctx`. See [decisions/0018-about-you-context-in-the-prompt.md](decisions/0018-about-you-context-in-the-prompt.md).

## Long recordings in pieces (Windows)

`vox_core.Segmenter` cuts audio that is still arriving: a piece is finished at the first pause of 0.6 s (30 ms frames whose loudest sample is under 900) once at least 12 s are buffered, or, with no pause, at the last quiet moment before 28 s (else at 28 s). `streaming.StreamingStt` sends each piece to `/audio/transcriptions` with the last 150 characters of the previous text appended to the end of the Whisper prompt (`transcribe(cfg, wav, context)`, 600 characters at most), skips pieces that are pure silence, drops silence-hallucination phrases, and joins the texts. `process_detailed` is now `transcribe` plus `process_text` (silence phrases, style, cleanup, spoken commands, replacements); the streamed path calls `process_text` directly. Each piece is one request, which counts against provider rate limits.
