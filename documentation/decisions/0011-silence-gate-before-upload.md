# 0011. Silence gate before upload

Status: Accepted
Date: 2026-09-29

## Context

Whisper invents phrases ("thank you", "thanks for watching") on silence, and the original code only filtered five exact phrases after paying for the request. Recordings made by accident, or through a dead microphone, were still uploaded and sometimes typed into the user's app.

## Decision

Before any upload, check the loudest sample of the recording. Below `SILENCE_PEAK = 655` (about -34 dBFS, 2 percent of full scale) nothing is sent: Windows shows "Vox did not hear anything (loudest sound N of 32768)…", Android shows a toast. Same threshold and same rule on both platforms (`vox_core.is_silent`, `Pcm.isSilent`). The phrase filter stays as the second line of defence.

## Consequences

- Saves requests and stops phantom text.
- A very quiet speaker or a far microphone could be rejected; the message shows the measured level so the cause is visible, and the threshold is a single constant.
- It exposed a real problem during first use: a headset microphone that delivered digital silence (peak 1). The Windows Microphone setting was added because of it.
- The check only looks at the peak, not at speech versus noise; a loud click passes it. A real voice-activity detector is a possible later step.

## Alternatives considered

- RMS or frame-based voice detection: more code and tuning for the first version.
- Only filter after transcription (status quo): pays for silent requests.
