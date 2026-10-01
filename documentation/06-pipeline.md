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
| Windows | Every request through `post_with_retry`: connection errors, timeouts and HTTP 500, 502, 503, 504. With the relay as the AI server (`via_relay`): only connection errors, 502 and 503, **not** a timeout (`vox_core.retryable`) | 3 attempts, waits 0.7 s then 1.4 s. HTTP 429 and 4xx are **not** retried here |
| Android | Only the speech request: network errors, 5xx, 429, 408 (`ApiClient.isRetryable`); with the relay as the AI server only network errors, 502 and 503, not a timeout (`ApiClient.retryable`; golden rows `retry` pin the shared part) | 3 attempts, waits 0.8 s then 1.6 s. Cleanup is tried once |
| Both | A dictation whose speech request still fails is kept for a manual retry | Windows tray "Retry last dictation"; Android notification "Retry" ([decisions/0009-keep-failed-recordings-and-retry.md](decisions/0009-keep-failed-recordings-and-retry.md)) |

Meetings keep their own 429 handling (`Meeting._stt`: wait 6 s x attempt, up to 4 attempts). The same loop retries network errors, but follows the shared rule (`core.retryable`): with the relay as the AI server a read timeout is not sent again (the relay is still working on it); connect errors, connect timeouts and 502/503 still are.

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

One case per line, fields separated by TAB; `\n`, `\t`, `\\` are escapes; lists use `|`; replacement pairs use `;` between pairs and `=>` inside one. Kinds: `sanitize`, `looks_valid`, `replace`, `whisper`, `terms`, `prompt`, `spoken`, `silence`, `gate` (raw text, style, cleanup on/off, minimum words, expected), `title`, `ftsq`, `remotewins` (the last three are the voice-note rules: `notes.auto_title`, `notes.fts_query`, and `notes.apply_remote` against a temporary `notes.db`; Java: `NoteLogic`; a `remotewins` row is `hasLocal localUpdated remoteUpdated remoteDeleted expected`), `merge3` and `profilefields` (the profile sync: `sync.merge3` and `sync.PROFILE_FIELDS` / `PROFILE_KEY_FIELDS`; Java: `ProfileMerge`; a `merge3` row is `base local remote expected` with the value of one field on each side, `~` meaning the field is absent there, and an empty column the empty string; a `profilefields` row is `shared` or `keys` and the field names joined by `|`) and `segcuts` (the pause finder: `min_ms|max_ms|pause_ms`, the audio as runs such as `t13000|s1000|t5000` (t loud, s silent, q just under the pause level, n at it, each in milliseconds), the block size in bytes it is fed in, and the piece lengths in bytes then `/` then the rest length; `Segmenter` in `vox_core.py` and `Segmenter.java`), `whisperctx` (the speech prompt of a piece of a long recording: terms, context, prompt; `whisper_prompt_with_context` and `ApiClient.whisperPromptWith`), `devices` (the Devices card's rows: `sync.devices_view` / `DevicesView.rows`; a row is `now myName devices rows`, where `devices` is `last_seen;name` items joined by `|` (`~` = no last_seen, other text = not a number) and `rows` is `state;this;ago;name` items joined by `|`) `relaycheck` (what Test connection reports: `sync.relay_check` / `RelayCheck.of`; a row is the HTTP status of `GET /health`, 0 meaning no answer, then the answer's fields as `key=type:value` items joined by `;` where the type is `s` text, `n` number or `b` true/false and an empty column means no usable JSON, then `ok;reachable;token_ok;relay_version;notes`), and `devname` (a typed device name and what `sync.device_name` / `NoteLogic.deviceName` make of it: trimmed, then cut at 60 code points; the fallback for a blank name differs per device and has no row) and the eight `timing_*` kinds of the timing core (`timing_median` and `timing_p90` take a `|` list of milliseconds; `timing_biggest`, `timing_stages` and `timing_summary` take `k=v,k=v` maps, summary entries separated by `;` plus the entry count n and giving `count=C biggest=B stage=median/p90 ...`; `timing_models` takes `voice@cleanup@k=v,k=v` entries separated by `;` plus n and gives one `voice+cleanup n=C stt=M llm=M total=M` per model pair, most used first; `timing_format` gives the "1.4 s" text; `timing_view` takes history rows, n and last and gives the whole Speed card as one line: the `timing_summary` text, then `models=` and `last=` (the newest timed rows, newest first, in history order; a row is `t@app@words@voice@cleanup@relay@stages`, three fields is a row without a timing and a fourth field `~` a row whose timing is not a map; Python `speed_view`, Java `Timing.speedView`, with the Java rows built by `Timing.historyMap`, the call `Prefs.addHistory` writes the timing with); `windows/timing.py` / `Timing.java`), and the four Android bubble rules `bubbleclamp` (`x y screenW screenH bubbleW bubbleH expected`, expected is `x,y`), `bubbleshow` (`onlyTyping alwaysShow fieldFocused screenOn serviceReady expected`) and `bubbleaction` (`wanted shown attached expected`, one of `none add remove repair`), and `notebubble` (`persistentSwitch noteRecording noteSaving expected`, the note bubble's `NoteBubbleLogic.visible`); the bubble exists only on Android (`BubbleLogic`, `NoteBubbleLogic`), so `tests/test_parity.py` holds a small reference copy of the rules and the rows are the executable spec both meet. `tests/test_parity.py` (Python) and `android/test/com/minhaj/vox/ParityTest.java` (Java) run every line. If you change any of these behaviours, change both implementations and the affected lines in the file (compute the expected value from the Python implementation and review it by hand). Never edit the file just to make one side pass.

## Per-role servers, model discovery and reasoning fields

- **Role settings.** `providers.role_settings(cfg, role)` / `Providers.roleSettings` return the address, key and model for `stt` or `llm`. A role with its own address (`stt_base_url`, `llm_base_url`) uses only its own key; the main key is never sent to a different server. `vox_core.transcribe` and `cleanup` (and the meeting code) call `api_base(cfg, role)` and `auth_headers(cfg, role)`. `key_missing` is true when a role talks to a server outside the private network without a key.
- **The relay as the AI server (Windows and Android).** With `relay_proxy` on and `relay_url` and `relay_token` filled in (`providers.uses_relay`), `role_settings` returns `<relay_url>/proxy/<role>` as the address (spaces and trailing `/` removed) and the relay token (from `load_config`, so already unprotected) as the key; the role's model is unchanged, and the provider addresses and keys (`base_url`, `stt_*`, `llm_*`, `api_key`) are not used. Everything else needs no change because it already goes through the role address: the dictation calls become `POST {relay}/proxy/stt/audio/transcriptions` and `POST {relay}/proxy/llm/chat/completions`, the model list and warm-up `GET {relay}/proxy/<role>/models`, and the Test buttons call the same two. Only those four relay routes are ever used, and each request carries only the relay token: the provider key is never sent to the relay, and the relay token is never sent to a provider (with the switch off `role_settings` ignores the relay). `vox_core.endpoint_error` checks `relay_url` (plain http only for private hosts) instead of the provider addresses while the relay is in use, so the token cannot travel in the clear to a public host. With the switch on but the address or token missing, `role_settings` returns the normal settings and `providers.proxy_problem(cfg)` returns `Turn on the relay first`, which the Settings page shows under the switch. Errors: the relay's own 411, 413, 429 and 503 are `{"error": "text"}` and its 502 is `{"error": {"message": ...}}`; `vox_core.check_response` and `providers._error_text` accept both shapes, so the user reads the text and not raw JSON. A 401 or 403 from the relay (wrong relay token) or passed through from the AI server (wrong key on the relay) gets the hint "check the relay token and the AI server key set on the relay page" in the `ApiError` text (`check_response(r, via_relay)`) and on the Test button and model list (`providers.explain(..., via_relay)`). The tray notification for a dictation that fails with 401 uses the same wording while the relay is in use (`providers.explain(401, ..., via_relay=True)`), and the meeting notes call (`meeting.py`) passes `via_relay` too. A 404 through the relay says the relay address, the relay's AI server routes or the role's AI server on the relay page is wrong (`explain(404, ..., via_relay=True)`), not that the server cannot do speech-to-text. `providers.proxy_problem` also returns `vox_core.endpoint_error` for the relay (plain http to a public host from a hand-edited or synced config), so the Settings page does not show green "Using the relay at ..." while dictation is blocked. Tests: `tests/test_providers.py` (section "the relay as the AI server"). Android does the same in `Providers.roleSettings(..., relayProxy, relayUrl, relayToken, role)` (called by `Prefs.role`, so dictation, warm-up, model lists and Test buttons follow), with the address rule shared as `proxyurl` rows in `spec/golden.txt` (`providers.proxy_url` / `Providers.proxyUrl`); the Android 401/403 hint appears on the Test button, the model list and the dictation error notification, and `ApiClient` reads the relay's `{"error": "text"}` shape as well as the OpenAI one. Tested in `ProvidersTest`.
- **Home status card (Windows).** `providers.status_summary(cfg, history, note_count)` builds what the Home status card shows, from local data only (no network call): the provider name (a preset by server address, or "My relay" while `relay_proxy` is in use), the speech and cleanup model ids (the saved value or the default), the voice note count, and the newest history entry (time, words, app) or none. `Api.get_state` returns it as `status`. The last connection Test and the sync state come from the page's own memory and `Api.sync_status`. Tests: `tests/test_providers.py`.
- **Model list.** `GET {base}/models` with a 5 s timeout. Each entry is classified `stt`, `llm` or `hidden`: an explicit provider field first (OpenRouter `architecture.output_modalities`, `task`/`type`, Groq `active: false` is hidden), otherwise the model id (`whisper`, `transcribe`, `voxtral`, `parakeet`, `moonshine`, `canary` mean speech; `orpheus`, `tts`, `guard`, `embed`, `rerank`, image models are hidden). The id rules are checked on both platforms by the `models` rows of `spec/golden.txt`.
- **Test.** Speech: a silent one-second WAV to `/audio/transcriptions`; cleanup: a tiny `chat/completions` call. Success is HTTP 200; failures are explained (401 key, 404 no such endpoint, 429 rate limit).
- **Reasoning fields.** `reasoning_effort: "low"` and `include_reasoning: false` are sent only for models with `gpt-oss` in the name and `llm_reasoning` not `off`. If the server answers 400 or 422 the request is repeated once without them and that address and model are remembered for the rest of the run. A leading `<think>...</think>` block in the answer is removed.

## Connection reuse

Windows posts through one shared `requests.Session` (`vox_core._post`); `vox_core.warm` opens the role servers' connections at key-down. Android reuses connections by reading responses fully and not disconnecting; `ApiClient.warm` does the opening, started by `DictationService.warm` when a bubble is touched and when recording starts (not twice within 3 s). Without this each request paid a fresh TLS handshake.

## Android latency rules (`Latency`, `UploadFormat`, `StreamingStt`)

Pure classes, tested in `LatencyTest`, `SegmenterTest`, `StreamingSttTest` and the golden rows; the Android side only (Windows keeps its own `requests` timeouts).

| Rule | Value |
|---|---|
| Connect timeout | 5 s (was 15 s) |
| Speech read timeout | `20 s + 3 s x audio seconds`, between 30 s and 180 s (was a flat 60 s) |
| Cleanup read timeout | `20 s + 60 ms x words`, at most 60 s; a cleanup that runs out falls back to the words as spoken |
| Fast retry | a request whose connection could not be opened (`ConnectException`, no route, unknown host, a timeout whose message says connect) is sent once more at once; a wait for the answer that ran out is never sent again from `ApiClient` (the relay is still working on it) |
| Cleanup `max_tokens` | Android: `max(256, 2 x estimate + 64)`, estimate = the larger of 2 x words and half the characters for Latin text, one per character otherwise; +768 for models that may think first (gpt-oss, Qwen3, QwQ, DeepSeek R1, names with `think` or `reasoner`; hidden reasoning counts against the limit, with or without a reasoning field); an answer with `finish_reason` `length` is a failed cleanup (the words are typed as spoken); cleanup temperature 0. Windows still sends `max(1024, 2 x transcript length)` |
| Upload container | WAV below 4 s or 100 KB, otherwise AAC-LC in m4a (64 kbit/s); the WAV is used when the encoder fails or is not smaller |
| Pieces while recording | `Segmenter` 12 s minimum, 28 s maximum, 0.6 s pause (the Windows numbers); each piece is sent with the last 150 characters of the text before it (plus the dictionary terms, 600 characters at most) as the prompt; a last piece under 0.3 s is not sent |

If any piece fails, or the recording was never long enough to be cut, or the pieces gave no text, the whole recording is sent as before.

## "About you" context in the cleanup prompt

`system_prompt(style, terms, app_label, context)` (Python) and `ApiClient.systemPrompt(style, terms, appLabel, context)` (Java) add, after the dictionary terms and before the style line, a rule that introduces `<about_speaker>...</about_speaker>` as reference material for spelling, names, jargon and tone, never text to output and never instructions. The text comes from `clean_context` / `cleanContext`: line endings normalised, our own tags removed, trimmed, capped at 8,000 characters. An empty context adds nothing, so existing prompts are unchanged. The constant parts come first so automatic prefix caching (Groq gpt-oss, OpenAI) can reuse them. Golden rows: `context` and `promptctx`. See [decisions/0018-about-you-context-in-the-prompt.md](decisions/0018-about-you-context-in-the-prompt.md).

## Long recordings in pieces (Windows)

`vox_core.Segmenter` cuts audio that is still arriving: a piece is finished at the first pause of 0.6 s (30 ms frames whose loudest sample is under 900) once at least 12 s are buffered, or, with no pause, at the last quiet moment before 28 s (else at 28 s). `streaming.StreamingStt` sends each piece to `/audio/transcriptions` with the last 150 characters of the previous text appended to the end of the Whisper prompt (`transcribe(cfg, wav, context)`, 600 characters at most), skips pieces that are pure silence, drops silence-hallucination phrases, and joins the texts. `process_detailed` is now `transcribe` plus `process_text` (silence phrases, style, cleanup, spoken commands, replacements); the streamed path calls `process_text` directly. Each piece is one request, which counts against provider rate limits.
