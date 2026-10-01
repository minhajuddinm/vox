# Spec P9b: measure where the time goes, and make Android faster

Status: Implemented on branch `feat/p9b-speed` (v2 part 3, branch B). Date: 2026-10-01. Not run on a phone, in the built exe or against a real server. Decision record: [0031](../decisions/0031-timings-stay-on-the-device.md). Follows [p2a](p2a-keydown-warmup.md) and [p2b](p2b-stream-long-dictations.md). Requests: R8 and "make Android faster" (round 3).

## Goal
1. Every dictation keeps how long each step took, and both apps show it, so speed work and the choice of cleanup model are decided by numbers.
2. Android waits less after the user stops speaking, using what Windows already does (warm connections, pieces sent while recording) plus a smaller upload, a bounded cleanup answer and shorter timeouts.

## Part 1: the timing core and the Speed card

### Design
- `windows/timing.py` (stdlib only) and `android/src/com/minhaj/vox/Timing.java` (pure twin). A `Timing` records marks in monotonic milliseconds: `key_down`, `rec_start`, `key_up`, `stt_start`, `stt_done`, `llm_start`, `llm_done`, `inserted`. An unknown mark name raises an error; a later mark of the same name replaces the earlier one.
- `stages()` gives whole milliseconds, never negative, 0 when a mark is missing: `start` (key down to microphone recording), `rec` (the speaking, not something to speed up), `stt`, `llm` (0 when cleanup was skipped), `insert` (end of cleanup, or of speech to text when cleanup was skipped, to inserted) and `total` (key up to inserted: what the person waits for).
- `summarize(entries, n=50)`: median and p90 of each stage over the newest n entries, the `biggest` stage (among start, stt, llm, insert; a tie goes to the earlier one) and the count. A stage that is 0 in an entry did not run (cleanup skipped) and is left out of that stage's numbers, so skipped cleanups do not pull the cleanup median to 0. `by_model` / `Timing.byModel` groups by voice and cleanup model, most used first. `speed_view` / `Timing.speedView` builds the whole card (summary, models, last N newest first).
- Each history entry gets `timing`: `{stages, stt_model, llm_model, provider, relay}` (format in [07](../07-config-and-data.md)). The Java side writes the same keys in the same order through the pure `Timing.historyMap`, because the offline tests have no `org.json`.
- Windows: `Engine` marks the key and microphone steps, the network steps mark themselves through a per-thread `core.timing_scope` (so `process_detailed` and `process_text` keep their signatures), and `get_speed` returns the card. Android: `DictationService` does the same with `SystemClock.elapsedRealtime`, the bubble touch time is `key_down`, and `Bridge.getSpeed()` returns the card.
- One renderer `speedHtml` in `ui-shared/common.js` draws the Speed card on both Home pages (median and slowest 1 in 10 per stage, the biggest marked, per model, last 10).
- A Retry, a voice note and a dictation with history off are not timed.

### Rules that were open in the plan (decided while building)
Median of an even count averages the two middle values, rounded down; p90 is the ceil(0.9 n)-th smallest value; `format_ms` gives "850 ms" below a second and one decimal from there ("1.5 s"); an entry without a `stages` map is ignored and not counted.

## Part 2: Android speed work
| # | Change | Where |
|---|---|---|
| 1 | Connections to the speech and cleanup servers are opened when a bubble is touched and again when recording starts, not twice within 3 s, no key means nothing is sent | `DictationService.warm`, `Latency.shouldWarm`, `ApiClient.warm` |
| 2 | A clip of 4 s or more and at least 100 KB goes up as AAC in m4a (about a quarter of the WAV); the WAV is used for short clips and when the encoder fails or is not smaller; the WAV stays on disk for Retry | `UploadFormat`, `AudioUpload` |
| 3 | Cleanup request: temperature 0, `max_tokens` = `max(256, 2 x estimate + 64)` plus 768 for models that may think first, an answer cut off at the limit (`finish_reason` `length`) is a failed cleanup and the words are typed as spoken | `Latency.maxTokens`, `mayThink`, `cutOff`, `ApiClient.cleanup` |
| 4 | A long recording is cut at pauses while it goes on (the Windows numbers: 12 s minimum, 28 s maximum, 0.6 s pause) and each piece is transcribed at once with the end of the text before it as the prompt; after the stop only the last piece is left. Any failure sends the whole recording as before | `Segmenter`, `StreamingStt`, `whisper_prompt_with_context` |
| 5 | The `AudioRecord` is made on the recording thread, the pending-file scan runs on the worker, the text is posted before the notification is refreshed | `DictationService` |
| 6 | Connect timeout 5 s with one immediate retry on connect failure only (never after a wait that ran out); speech read timeout `20 s + 3 s per audio second` (30 to 180 s); cleanup `20 s + 60 ms per word` (at most 60 s) | `Latency`, `ApiClient` |

### Plan changes (and why)
- The plan's cleanup bound `2x + 64` was too small: hidden reasoning counts against `max_tokens`, so a short answer from gpt-oss, Qwen3 or DeepSeek R1 was cut off inside the think block. Now there is a floor of 256, a 768-token headroom for any model `Latency.mayThink` recognises (a name heuristic), also on the retry without reasoning fields, and a cut-off answer is a failed cleanup instead of silently junk. Windows still sends `max(1024, 2 x length)` and shares no rule with this.
- The "skip cleanup for very short text" item needed no new code: `cleanup_min_words` already does it on both apps.
- The Windows history timing is built by the engine, not through golden rows (`Timing.entry` needs marks); the key order is pinned by a test instead. The Java rows run through `Timing.historyMap`.

## Shared golden rows
`timing_median`, `timing_p90`, `timing_biggest`, `timing_format`, `timing_stages`, `timing_summary`, `timing_models`, `timing_view` (Speed card as one line), `segcuts` (pause finder) and `whisperctx` (speech prompt of a piece). Both `tests/test_parity.py` and `ParityTest` run every row. Formats are in [06](../06-pipeline.md).

## Answers given to the requester
- "Will Rust be faster?" No: the app's own time is about 10 to 30 ms of a 1 to 2 s wait; the wait is network and models. Revisit only if the Speed card shows app-side time matters.
- "Best model for the fastest cleanup": no default changed without evidence. The Speed card shows total, speech and cleanup time per model pair on the user's own network; the benchmark of branch A (`tools/bench_cleanup.py`) measures quality and latency with his own key.

## Needs a phone (or a real network) to prove
Nothing below has run outside unit tests, a fake server and the compile check.
- The m4a encoder (`MediaCodec` and `MediaMuxer`) on real phones, and that Groq, OpenAI-compatible servers and the relay accept the m4a (the relay path was run with a WAV only).
- The recorder started on its own thread on Android 14 and 15, and the real `tap->recording` time.
- Pieces sent while recording against a real server, the 3 s warm-up gap, and the real saving of each item: read it from the Speed card.
- The `mayThink` name list against real thinking models (Ollama Qwen3 or DeepSeek R1, Groq gpt-oss): an unrecognised thinking model gets only the 256 floor.
- The Speed card on a real screen and phone (checked only in a desktop browser with mock bridges), and `get_speed` from the built exe.

## Not in this step
Windows `max_tokens` bound; per-provider or per-model speed advice in the card; a speed history chart; cleanup streaming; on-device speech recognition; sending timings anywhere (see the decision record).

## Done when
`tests/test_timing.py`, `test_timing_pipeline.py`, `test_ui_speed.py`, `test_engine_flash.py`, the parity rows, `TimingTest`, `LatencyTest`, `SegmenterTest`, `StreamingSttTest` and `ParityTest` pass and the Android sources compile (done); a dictation on a real phone fills the Speed card and the pieces, m4a and warm-up show a smaller "after you stop" time than before (not checked yet).
