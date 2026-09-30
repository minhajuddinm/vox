# Spec P4: live voice level

Status: Implemented on branch `feat/live-level`. Date: 2026-09-30.

## Goal
The recording pill (Windows) and bubble (Android) show how loud the voice is, immediately and in the same way on both.

## What existed
Both platforms already computed a level. Windows scaled rms by 12 and drew 11 bars from one number with a sine wobble at 60 frames a second; Android scaled rms by 6 and updated 10 times a second.

## Design
- One curve: `level = 1 - 10^(-30 * max(0, rms - 0.004))` (`vox_core.level_from_rms`, `Pcm.levelFromRms`). Below the floor (a quiet room) the meter shows nothing; normal speech nearly fills it. Held equal by `level` rows in `spec/golden.txt`.
- Windows: `Engine._audio` stores `level_from_rms(rms)`; the overlay samples it every 80 ms into `vox_core.LevelHistory` (11 values) and draws the bars from that history, so they scroll with the real voice (newest on the right); redraw rate 30 frames a second (was 60); the history is cleared when the pill appears.
- Android: the record loop reads 40 ms buffers (was 100 ms), so about 25 level updates a second; `BubbleView.setLevel` rises quickly and falls slowly.

## Not in this step
A bar waveform on the Android bubble (it still grows a circle and ring); a shared calibration for very quiet microphones; drawing without clearing the canvas each frame.

## Done when
Tests and CI pass; on a device the pill bars follow speech on Windows and the bubble reacts smoothly on the phone. Not seen on a screen yet: the Windows overlay cannot be run from the agent's shell.
