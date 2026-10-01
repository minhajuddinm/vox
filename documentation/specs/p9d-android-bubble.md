# Spec P9d: the Android bubble that keeps disappearing

Status: tasks D1 (diagnostics) and D2 (watchdog, clamp, Always show the bubble, battery prompt) are built on branch `feat/p9d-bubble`. Not run on a phone. Date: 2026-10-01. Request R5 in the part 3 notes: on his older APK the floating bubble keeps disappearing. No decision record: the choices below are small and easy to reverse.

## Goal
Find out why the bubble disappears, put it back by itself when the system drops it, and let him keep it on screen when he wants it.

## Candidate causes (and what covers each)
| Cause | Covered by |
|---|---|
| "Bubble only while typing" hides it when no text field is focused (by design, surprising) | New switch "Always show the bubble"; the diagnostics log names this rule (`only_typing_hide`) |
| Android or the phone maker stops the accessibility service (battery optimisation, task killers) | Diagnostics card says "Switched on but not running"; battery prompt with a button to Android's battery screen |
| The overlay window is dropped on screen off, unlock or a configuration change | Watchdog: the bubble is removed on screen off and added again on screen on, unlock and rotation; a 30 s check adds back a bubble whose window is gone |
| The bubble was not added again after the service connected or an app changed | `refreshVisibility` runs on service connect, every window change and app install, update or removal |
| The saved position lies outside the screen (rotation, a different display size) | `BubbleLogic.clamp` on add, on rotation and after a drag |

## Design

### Pure rules (`BubbleLogic.java`, golden rows `bubbleclamp`, `bubbleshow`, `bubbleaction`)
- `clamp(x, y, screenW, screenH, bubbleW, bubbleH)` returns `{x, y}` limited to `0..(screenW - bubbleW)` and `0..(screenH - bubbleH)`; a screen smaller than the bubble gives 0. Integer overflow safe.
- `shouldShow(onlyTyping, alwaysShow, fieldFocused, screenOn, serviceReady)`: false when the service is not ready or the screen is off; otherwise true when `alwaysShow`, or `onlyTyping` is off, or a text field is focused. A dictation in progress also keeps the mic bubble up (the service adds that).
- `action(wanted, shown, attached)`: `add` (wanted, not shown), `repair` (wanted, shown, window gone), `remove` (not wanted, shown), else `none`.
- The bubble exists only on Android, so Python has no implementation of its own: `tests/test_parity.py` keeps a short reference copy of the three rules, and both suites run the same rows (13 clamp rows, the 32 combinations of `bubbleshow`, the 8 combinations of `bubbleaction`).

### Service (`VoxAccessibilityService`)
- `refreshVisibility(rebuild, why)` applies the rules to both bubbles. Triggers: every window and focus event (cheap, never rebuilds), screen on (re-clamp, rebuild), screen off (bubbles removed), unlock (rebuild), app installed, updated or removed, `onConfigurationChanged` (clamp, rebuild), `onServiceConnected`, and the watchdog, a main-handler `Runnable` every 30 s while the service is alive (it also re-reads `PowerManager.isInteractive` in case a broadcast was missed).
- "Window gone" is `!view.isAttachedToWindow()`; a view added less than 2 s ago counts as attached because its first frame has not run yet.
- Saved positions are clamped when a bubble is placed. The clamped value is not written back, so rotating the phone back restores where he put it. A drag still snaps to the nearest edge and saves.
- `onUnbind` and `onDestroy` log the reason (`service_unbound`, `service_destroyed`), stop the watchdog and the receiver and remove the bubbles.
- A bubble put back after its window was found gone is logged as `overlay_repaired` ("Bubble put back by the watchdog: mic bubble, window was gone, found after watchdog").

### Settings (Settings, System)
- "Always show the bubble" (`always_show_bubble`, default off, per device, not synced) next to "Bubble only while typing". With it on, tapping the bubble with no text field selected records as usual and the text goes to the clipboard ("No text field found. Copied to clipboard.").
- The Bubble diagnostics card shows, while Vox is not exempt from battery optimisation, a short explanation and an **Open battery settings** button (`Bridge.openBattery`: Android's "Battery optimisation" list, falling back to the app info screen). The "Battery" row above it follows the same state. No new permission is requested: the list screen needs none, and `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` (a direct "allow" dialog) is deliberately not used because Google Play restricts it.

## Tests
- `BubbleLogicTest` (clamp cases, visibility, watchdog action), `OverlayDiagTest` (reasons and kinds with Always show and a dark screen, the watchdog event text), `ParityTest` and `tests/test_parity.py` (the golden rows), `tests/test_ui_static.py` (the new setting and prompt ids, the bridge methods, the service using the pure rules), `android/compile-check.sh` (all sources compile against android.jar).

## Device checklist (for Yuvraj, on the phone; none of this has been run)
1. Settings, System, Bubble diagnostics: Service says "Connected". If it says "Switched on but not running", switch Vox off and on in Accessibility settings and note it.
2. Always show off, "Bubble only while typing" on: open a text field, the bubble appears; leave the field, it goes (log: "Bubble hidden by only-typing"). Turn **Always show the bubble** on: the bubble stays on the home screen and in apps without a text field.
3. Lock the phone, wait 10 s, unlock: the bubble is there (log: Screen off, Bubble removed: screen is off, Screen on, Phone unlocked, Bubble added).
4. Rotate to landscape and back with the bubble dragged to the bottom edge: it stays fully on screen in both and returns to the old place in portrait.
5. Drag the bubble to the far right edge in landscape, rotate to portrait: it is on screen at the right edge.
6. Battery: if the card says Vox may be restricted, tap **Open battery settings**, set Vox to not optimised, come back: the prompt is gone and the line says it is not restricted.
7. Leave the phone idle for 30 minutes with the screen on in another app: the bubble is still there. If it vanished, open the card, tap Copy report and send it: the last events say what happened.
8. Open Settings, System while the voice note bubble is on: both bubbles show; lock and unlock: both come back.
9. Split-screen, a foldable or a floating window: check the bubble stays on screen (the screen size comes from the display metrics of the service, which may not be the window size here).

## Known limits
- Some phone makers kill accessibility services in ways no app can prevent (the card shows it). The watchdog cannot run while the service is dead; only Android can restart it.
- Screen size comes from `getResources().getDisplayMetrics()`, as before; on a foldable, a cut-out or a floating window it may not match the usable area.
- Removing the bubbles on screen off is a guess that it clears stale overlays after sleep; the checklist step 3 tests it.
