# 0041. An optional warm microphone with a 400 ms ring buffer

Status: Accepted (Windows; tested with fakes, not run with a real microphone)
Date: 2026-10-03

## Context

[0017](0017-warm-connections-not-an-open-microphone.md) decided that the microphone is opened only when the shortcut goes down, because a microphone that is live all the time is a privacy cost. The price is that the first 100 to 300 ms of speech are lost when the user starts talking as he presses the keys (a fast talker; "Known weak spots for fast talkers" in [12](../12-known-issues-and-roadmap.md)). Handy keeps a pre-roll of 450 ms by leaving the stream open.

## Decision

- A new setting `warm_mic` (bool, **default off**, Windows only, Settings, Voice & audio, "Keep the microphone ready") keeps one `sounddevice` input stream open on the chosen microphone. The default behaviour of 0017 is unchanged: with the setting off, nothing is opened early.
- While nobody records, the stream's audio goes only into a ring buffer of the last 400 ms (`windows/warm_mic.py`, `RingBuffer`): in memory, never written to disk, never sent anywhere, overwritten continuously. At key-down the engine takes the held audio and puts it in front of the recording, then the live blocks follow in order; at the end of the recording the stream stays open and the ring fills again.
- The stream is closed, and the ring emptied, when the setting is turned off, when the chosen microphone changes, and when Vox quits. A stream that dies (unplugged) is noticed at the next key-down (the normal path is used) and reopened by the engine's one-second config thread; a failed open is retried at most every 30 s and says so once ("Vox could not keep the microphone ready ...").
- The 400 ms from before the key do not count towards the minimum length of a dictation, so an accidental tap is still ignored.
- Everything stays inside `windows/warm_mic.py` (pure ring buffer and a stream keeper that knows nothing about `sounddevice`); `engine.py` only wires it (`_sync_warm`, `_begin_capture`, `_close_stream`).

## Consequences

- With the setting on, Windows shows the microphone-in-use icon all the time and other programs that want the microphone exclusively may be refused; the setting's text says the first, and the privacy pages say what is and is not kept.
- The first word is no longer clipped, with 400 ms of room before the keys. Not measured on a real PC. The 400 ms (and any key click in it) are sent to the speech server as part of the recording.
- The device list is not rebuilt (`_refresh_audio`) while the warm stream is open, because restarting PortAudio would kill it; a chosen microphone that was missing is found again by the normal path while the warm stream is not open.
- A change of the Windows default microphone while the stream is open on "the default" is not noticed until the setting is toggled or Vox restarts (inferred from how PortAudio opens the default device; not tried).

## Alternatives considered

- On by default: fixes the first word for everyone, but turns on a permanent microphone indicator for people who did not ask for it; 0017's privacy reasoning stands for the default.
- Open the stream at Vox start but pause it when idle: no pre-roll, which is the point.
- A longer ring (1 s or more): catches more, but sends more room noise with every dictation and keeps more audio in memory for no clear gain; 400 ms is the figure the request named (Handy uses 450 ms).
