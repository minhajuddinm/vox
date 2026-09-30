# 0024. Long dictations are transcribed in pieces while the user speaks

Status: Accepted
Date: 2026-09-30

## Context

The wait after releasing the key is upload plus transcription plus cleanup. For a long dictation the first two grow with the length of the recording, and all of that audio could have been sent while the user was still talking. Research (2026-09-30): Groq transcribes very fast, so for short recordings the network dominates and chunking saves little; for long ones the upload and transcription of the whole recording is a real part of the wait. Streaming protocols (WebSocket) are not OpenAI-compatible and are out of scope. Cleanup must see the whole text (lists, self-corrections), so it still runs once at the end.

## Decision

- While recording, the microphone callback only queues audio (`StreamingStt.feed`). A worker thread cuts it at pauses (`vox_core.Segmenter`: not before 12 s, at the first pause of 0.6 s after that, or at the last quiet moment before 28 s), transcribes each piece with the end of the previous text (150 characters) as context, and keeps the texts in order.
- On release only the last piece is left. The engine then runs the rest of the pipeline on the joined text (`process_text`): silence phrases, style, cleanup once, spoken commands, replacements.
- It is only a shortcut: if nothing was cut (recordings under about 13 s), or any piece failed, or it took too long, `finish()` returns None and the engine transcribes the whole recording exactly as before. The whole recording is always kept for Retry.
- Setting `stream_stt` (default on) switches it off.

## Consequences

- Long dictations are ready sooner (less audio to send at the end). Not measured on a real network.
- More requests: about one per 12 to 28 seconds of speech, which counts against provider rate limits (Groq's free tier: 20 speech requests a minute).
- Text from separate pieces can start a sentence with a capital letter at a cut (the cleanup fixes it; with the `raw` style it stays).
- A cut inside a word is possible when someone speaks 28 seconds without any quiet moment; the previous text as context helps Whisper recover.
- Windows only; Android is not changed.

## Alternatives considered

- Streaming APIs (WebSocket, SSE from `/audio/transcriptions`): provider-specific or only for finished files.
- Cutting at fixed lengths: cuts words.
- Streaming the cleanup answer into the app: needs validation of the whole answer first ([0013](0013-raw-fallback-when-cleanup-fails.md)).
