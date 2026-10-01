# Spec P9h: choosing the microphone on Android

Status: Implemented on branch `fix/medium-round`, **not run on a phone**. Date: 2026-10-01. Source: Yuvraj's request "also add mic choosing option"; Windows already has the picker (`windows/audio_devices.py`, setting `input_device`).

## Goal
The Android recorder always used the phone's default input (`VOICE_RECOGNITION` source). With a wired headset or a USB microphone connected, the user could not say which one Vox listens to. (Bluetooth is not offered yet, see Honest limits.) Settings, Voice & audio now has a **Microphone** row, worded like the Windows one.

## What was built
- **The row** (`id="mic-device"`, a select): "Phone default" plus the input devices Android reports now (`AudioManager.getDevices(GET_DEVICES_INPUTS)`: built-in, wired headset, USB device or headset; Bluetooth SCO, Bluetooth LE headset, hearing aid and the output-only types are left out). Each is named by its product name and a short type word ("Pixel 7 (built-in)", "Dock (USB)"); a second identical label gets " 2". Description: "Which microphone Vox listens to. The phone's own microphone unless you pick one. If dictation says it heard nothing, try another." The list is asked again every time Settings is drawn (devices come and go) and the select is rebuilt only when the list changed.
- **Storage:** `Prefs.micDevice()` / `setMicDevice` under the key `mic_device`, a key made of the device type and product name (`MicChoice.key`, for example `11|Dock`), never the numeric device id, which Android changes at every connect. Empty means the default. It is per phone and is not one of the profile fields that travel through the relay (`ProfileMap` does not list it).
- **The recorder:** `DictationService.startRecording` reads the key once when a recording starts; after the `AudioRecord` is created and before it starts, `preferMic` calls `setPreferredDevice` with the chosen device when it is connected now (`MicChoice.pick`). When it is not connected the default stays and the toast "Chosen microphone not connected, using the phone's" is shown once for that choice (not before every dictation; it shows again after another choice or after the device was found again). Voice notes use the same path.
- **Pure logic** (`MicChoice.java`, listed in `android/testsrc.list`, no `android.*`; the type numbers are those of `AudioDeviceInfo`): `key`, `label`, `labelOfKey`, `isMic`, `usable`, `options` (each key once, each label once), `pick` (null for an empty, unknown, not connected or Bluetooth key) and `shouldWarn`. `MicChoiceTest` has 46 checks.
- **Bridge:** `MainActivity.Bridge.getMics()` answers `{current, options: [{key, label}]}` (a saved choice that is not connected is listed as "... (not connected)" so the select still shows it) and `setMic(key)` saves the key. The page keeps the `V.NAME` style of the other calls. `tests/test_ui_static.py` checks the row, the wording against the Windows page, the two bridge methods and that the recorder uses `setPreferredDevice`.
- **Permissions:** none added. Listing devices needs none; no Bluetooth permission is requested, so Vox only lists and prefers.
- **Bluetooth is not offered (final review, Android medium 3).** `setPreferredDevice` only routes the stream; the Bluetooth capture link (SCO, or LE Audio) is normally opened for communication audio by `AudioManager.setCommunicationDevice` (Android 12+) or `startBluetoothSco` (needs `MODIFY_AUDIO_SETTINGS`, which the app does not declare). Without it a Bluetooth recording can be silent (the app then throws it away as "did not hear anything") or quietly come from the phone. So `MicChoice.isMic` offers only built-in, wired and USB devices, and `pick` never returns a Bluetooth device. A Bluetooth choice saved by an earlier version is read as "Phone default" (`MicChoice.usable`), with no notice and no "not connected" entry. Offering Bluetooth again needs `setCommunicationDevice` before `startRecording` (cleared afterwards) and a check of `getRoutedDevice()` after it, tested on a phone.

## Honest limits
- `setPreferredDevice` is a request: Android may still route elsewhere. Bluetooth microphones are left out for that reason (see above); wired and USB ones are routed by it.
- A device that is connected after a recording started is used from the next recording.
- Windows meeting notes ignore the Windows picker too ([12](../12-known-issues-and-roadmap.md)); the Android row applies to dictation and voice notes.

## Device checklist (for a tester; none of this has been run)
1. Settings, Voice & audio, Microphone: with nothing plugged in, the list shows "Phone default" and the phone's built-in microphone. Pick the built-in one, dictate: text arrives. Pick "Phone default" again: the toast says "Using the phone's own microphone".
2. Plug in a wired headset (or a USB microphone) and open Settings again: it is listed with its name and "(wired)" or "(USB)". Choose it, dictate with the phone far away and the headset microphone close: does the text match what the headset heard? Does the level bar move?
3. Connect a Bluetooth headset and open Settings: it is not listed (Bluetooth is not offered yet). If an earlier version saved a Bluetooth choice, the row shows "Phone default" and dictation uses the phone's microphone with no notice.
4. Choose the headset, then unplug or switch it off and dictate: the toast "Chosen microphone not connected, using the phone's" appears once, the dictation works with the phone's microphone, and a second dictation shows no toast. Reconnect it: it is used again without choosing it again.
5. Close and reopen the app, then restart the phone: the choice is still shown. Check the relay profile on another device: the microphone choice did not arrive there.
6. A second built-in microphone (some phones list two under one name): each appears once, a duplicate label ends in " 2".

## Not verified
Nothing ran on a phone. Verified off device only: the pure logic by `MicChoiceTest`, the page text and bridge names by `tests/test_ui_static.py`, and that all Android sources compile against `android.jar` (`javatest compile`).
