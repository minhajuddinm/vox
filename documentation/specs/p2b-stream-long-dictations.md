# Spec P2b: send long recordings in pieces while speaking (Windows)

Status: Implemented on branch `feat/stream-long-dictation`. Date: 2026-09-30. Decision record: [0024](../decisions/0024-stream-long-dictations-in-pieces.md). Follows [p2a](p2a-keydown-warmup.md).

## Goal
For a long dictation, most of the speech is already text when the user releases the key.

## Design
- `vox_core.Segmenter` (pure): `feed(pcm)` returns finished pieces, `rest()` the remainder; pieces plus rest equal the audio fed, whatever the block sizes.
- `windows/streaming.py` `StreamingStt`: queue, worker thread, `finish()` returns the joined text or None (fall back to the whole recording).
- `vox_core.transcribe(cfg, wav, context)` puts the end of the previous text at the end of Whisper's prompt (600 characters at most); `process_text` is the second half of `process_detailed`.
- Engine: creates the streamer in `start`, feeds it from `_audio`, hands it to `_process`, cancels it on cancel, short or silent recordings and microphone errors. Setting `stream_stt` and a Settings switch.

## Not in this step
Android; streaming APIs; skipping cleanup for short phrases; measuring the real saving; a per-piece Retry.

## Done when
`tests/test_streaming.py` and the engine tests pass; a long dictation on a real network ends sooner than before and reads the same (not checked yet).
