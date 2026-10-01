package com.minhaj.vox;

import android.accessibilityservice.AccessibilityService;
import android.content.ClipData;
import android.content.BroadcastReceiver;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Toast;

import java.io.File;

/**
 * Owns the floating bubbles (accessibility overlays, so no "draw over apps" permission is needed): the mic bubble,
 * which tracks the focused text field and inserts the final text into it, and the optional voice note bubble, which
 * is always on screen while "note_bubble" is on and starts or stops a note.
 *
 * Keeping the bubble there: {@link BubbleLogic} holds the rules (visibility, position clamp, what to do about a bubble
 * whose window is gone). They are applied on every window change, on screen on, unlock, rotation and when the service
 * connects, and by a watchdog every 30 seconds while the service is alive. A saved position is clamped to the current
 * screen. "Always show the bubble" (always_show_bubble) ignores only_typing.
 */
public class VoxAccessibilityService extends AccessibilityService
        implements DictationService.Listener, DictationService.NoteListener {
    public static volatile VoxAccessibilityService instance;

    private final Handler main = new Handler(Looper.getMainLooper());
    private WindowManager wm;
    /** The mic bubble: on screen near a focused text field (always, when "only_typing" is off). */
    private Floating dictation;
    /** The voice note bubble: on screen whenever "note_bubble" is on, whatever is focused. */
    private Floating noteBubble;

    private AccessibilityNodeInfo editNode;
    private String editPkg;
    /** Screen on/off, unlock and app changes: logged for the bubble diagnostics card (see OverlayDiag) and used to put the bubble back. */
    private BroadcastReceiver diagReceiver;
    /** True from onServiceConnected to onUnbind: no bubble is added outside that time. */
    private boolean serviceReady;
    private boolean screenOn = true;
    /** Looks at the bubbles every BubbleLogic.WATCHDOG_MS while the service is alive. */
    private final Runnable watchdog = new Runnable() {
        @Override
        public void run() {
            if (!serviceReady) return;
            try {
                screenOn = isScreenOn();   // in case a broadcast was missed
                refreshVisibility(false, "watchdog");
            } catch (Exception e) {
                Log.w("vox", "bubble watchdog: " + e.getClass().getSimpleName());
            }
            main.postDelayed(this, BubbleLogic.WATCHDOG_MS);
        }
    };

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        diag(OverlayDiag.SERVICE_CONNECTED, "");
        serviceReady = true;
        screenOn = isScreenOn();
        registerDiagReceiver();
        DictationService.setListener(this);
        DictationService.setNoteListener(this);
        wm = (WindowManager) getSystemService(WINDOW_SERVICE);
        createBubbles();
        refreshVisibility(true, "service connected");
        main.removeCallbacks(watchdog);
        main.postDelayed(watchdog, BubbleLogic.WATCHDOG_MS);
        NoteEntry.applySettings(this);   // puts the "Record note" notification back after a reboot
    }

    private boolean isScreenOn() {
        try {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            return pm == null || pm.isInteractive();
        } catch (Exception e) {
            return true;
        }
    }

    @Override
    public boolean onUnbind(Intent intent) {
        diag(OverlayDiag.SERVICE_UNBOUND, "");
        stopWatching();
        removeBubbles("service unbound");
        instance = null;
        DictationService.setListener(null);
        DictationService.setNoteListener(null);
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        diag(OverlayDiag.SERVICE_DESTROYED, "");
        stopWatching();
        removeBubbles("service destroyed");
        instance = null;
        DictationService.setListener(null);
        DictationService.setNoteListener(null);
        super.onDestroy();
    }

    @Override
    public void onInterrupt() {
        diag(OverlayDiag.SERVICE_INTERRUPTED, "");
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        DisplayMetrics dm = getResources().getDisplayMetrics();
        diag(OverlayDiag.CONFIG_CHANGE, (newConfig.orientation == Configuration.ORIENTATION_LANDSCAPE ? "landscape " : "portrait ")
                + dm.widthPixels + "x" + dm.heightPixels);
        // the saved position may lie outside the new screen: clamp it, then put the bubbles back
        if (dictation != null) dictation.reposition();
        if (noteBubble != null) noteBubble.reposition();
        refreshVisibility(true, "screen change");
    }

    /** Ends the watchdog and the receiver; the caller removes the bubbles. */
    private void stopWatching() {
        serviceReady = false;
        main.removeCallbacks(watchdog);
        unregisterDiagReceiver();
    }

    // ------------------------------------------------------------- diagnostics

    /** Records one event for the Settings "Bubble diagnostics" card and logcat (tag vox). Never throws. */
    private void diag(String kind, String detail) {
        try {
            OverlayDiag.shared(new File(getFilesDir(), OverlayDiag.FILE_NAME)).record(System.currentTimeMillis(), kind, detail);
        } catch (Exception ignored) { }
        Log.i("vox", "bubble: " + kind + (detail.isEmpty() ? "" : " " + detail));
    }

    private void registerDiagReceiver() {
        if (diagReceiver != null) return;
        diagReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent i) {
                String a = i == null ? null : i.getAction();
                if (a == null) return;
                switch (a) {
                    case Intent.ACTION_SCREEN_ON:
                        screenOn = true;
                        diag(OverlayDiag.SCREEN_ON, "");
                        if (dictation != null) dictation.reposition();
                        if (noteBubble != null) noteBubble.reposition();
                        refreshVisibility(true, "screen on");
                        break;
                    case Intent.ACTION_SCREEN_OFF:
                        screenOn = false;
                        diag(OverlayDiag.SCREEN_OFF, "");
                        refreshVisibility(false, "screen off");
                        break;
                    case Intent.ACTION_USER_PRESENT:
                        diag(OverlayDiag.USER_PRESENT, "");
                        refreshVisibility(true, "unlock");
                        break;
                    // no package name on purpose: the log must not list what is installed
                    default:
                        diag(OverlayDiag.PACKAGE_CHANGE, "");
                        refreshVisibility(false, "app change");
                        break;
                }
            }
        };
        try {
            IntentFilter screen = new IntentFilter();
            screen.addAction(Intent.ACTION_SCREEN_ON);
            screen.addAction(Intent.ACTION_SCREEN_OFF);
            screen.addAction(Intent.ACTION_USER_PRESENT);
            IntentFilter pkg = new IntentFilter();
            pkg.addAction(Intent.ACTION_PACKAGE_ADDED);
            pkg.addAction(Intent.ACTION_PACKAGE_REPLACED);
            pkg.addAction(Intent.ACTION_PACKAGE_REMOVED);
            pkg.addDataScheme("package");
            if (Build.VERSION.SDK_INT >= 33) {
                registerReceiver(diagReceiver, screen, Context.RECEIVER_NOT_EXPORTED);
                registerReceiver(diagReceiver, pkg, Context.RECEIVER_NOT_EXPORTED);
            } else {
                registerReceiver(diagReceiver, screen);
                registerReceiver(diagReceiver, pkg);
            }
        } catch (Exception e) {
            diagReceiver = null;   // the bubble still works: the window events and the watchdog cover for it
        }
    }

    private void unregisterDiagReceiver() {
        BroadcastReceiver r = diagReceiver;
        diagReceiver = null;
        if (r != null) {
            try { unregisterReceiver(r); } catch (Exception ignored) { }
        }
    }

    // ------------------------------------------------------------- events

    @Override
    public void onAccessibilityEvent(AccessibilityEvent e) {
        int type = e.getEventType();
        CharSequence cls = e.getClassName();
        if (cls != null && cls.toString().endsWith("TrampolineActivity")) return;
        if (type == AccessibilityEvent.TYPE_VIEW_FOCUSED
                || type == AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED
                || type == AccessibilityEvent.TYPE_VIEW_CLICKED) {
            AccessibilityNodeInfo src = e.getSource();
            if (src != null && src.isEditable()) {
                setEditNode(src);
            } else if (type == AccessibilityEvent.TYPE_VIEW_FOCUSED) {
                checkFocus();
            }
        } else if (type == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            checkFocus();
        }
    }

    @SuppressWarnings("deprecation")
    private void releaseEditNode() {
        AccessibilityNodeInfo old = editNode;
        editNode = null;
        if (old != null && Build.VERSION.SDK_INT < 33) {
            try { old.recycle(); } catch (Exception ignored) { }
        }
    }

    private void setEditNode(AccessibilityNodeInfo n) {
        if (editNode != null && editNode != n) releaseEditNode();
        editNode = n;
        CharSequence p = n.getPackageName();
        if (p != null) editPkg = p.toString();
        refreshVisibility();
    }

    private void checkFocus() {
        AccessibilityNodeInfo f = null;
        try { f = findFocus(AccessibilityNodeInfo.FOCUS_INPUT); } catch (Exception ignored) { }
        if (f != null && f.isEditable()) setEditNode(f);
        else { releaseEditNode(); refreshVisibility(); }
    }

    public void refreshVisibility() {
        refreshVisibility(false, "");
    }

    /** Puts both bubbles right: added when wanted and missing, put back when their window is gone, removed when not wanted. */
    private void refreshVisibility(boolean rebuild, String why) {
        if (dictation == null || noteBubble == null) return;
        DictationService svc = DictationService.instance;
        // A note being recorded or sent is the note bubble's business: it must not bring the mic bubble up.
        boolean busy = svc != null && svc.getState() != DictationService.IDLE && !svc.isNoteJob();
        Prefs p = new Prefs(this);
        boolean onlyTyping = p.onlyWhenTyping(), always = p.alwaysShowBubble(), field = editNode != null;
        boolean wantMic = busy || BubbleLogic.shouldShow(onlyTyping, always, field, screenOn, serviceReady);
        dictation.apply(wantMic, OverlayDiag.micKind(wantMic, busy, onlyTyping, field, always, screenOn),
                OverlayDiag.micReason(busy, onlyTyping, field, always, screenOn), rebuild, why);
        boolean noteOn = p.noteBubble();   // independent of the focused field and of only_typing
        boolean wantNote = noteOn && serviceReady && screenOn;
        noteBubble.apply(wantNote, wantNote ? OverlayDiag.OVERLAY_ADD : OverlayDiag.OVERLAY_REMOVE,
                !noteOn ? "voice note bubble is off" : !screenOn ? "screen is off" : "voice note bubble is on", rebuild, why);
    }

    /** The size of the screen in the current orientation. */
    private DisplayMetrics screen() {
        return getResources().getDisplayMetrics();
    }

    // ------------------------------------------------------------- bubble

    /** One floating bubble: its view, its place on screen, and whether it is currently added to the window manager. */
    private final class Floating {
        final BubbleView view;
        final WindowManager.LayoutParams lp;
        final boolean note;
        boolean shown;
        /** When the overlay was last added: a view is not attached to its window until its first frame, so a fresh one is not "gone". */
        long addedAt;

        Floating(boolean note) {
            this.note = note;
            DisplayMetrics dm = getResources().getDisplayMetrics();
            int size = (int) (60 * dm.density);
            view = new BubbleView(VoxAccessibilityService.this, note);
            lp = new WindowManager.LayoutParams(size, size,
                    WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                            | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                    PixelFormat.TRANSLUCENT);
            lp.gravity = Gravity.TOP | Gravity.START;
            place();

            final int slop = ViewConfiguration.get(VoxAccessibilityService.this).getScaledTouchSlop();
            final int longPress = ViewConfiguration.getLongPressTimeout() + 150;
            view.setOnTouchListener(new BubbleTouch(this, slop, longPress));
        }

        /**
         * Takes the saved position (or the default spot) and keeps it on the current screen. The clamped value is not
         * written back, so turning the phone back restores where the user left the bubble.
         */
        void place() {
            DisplayMetrics dm = screen();
            Prefs p = new Prefs(VoxAccessibilityService.this);
            int x = note ? p.noteBubbleX() : p.bubbleX();
            int y = note ? p.noteBubbleY() : p.bubbleY();
            // default spots: the right edge, the note bubble lower down so the two do not sit on top of each other
            if (x < 0) x = dm.widthPixels - lp.width;
            if (y < 0) y = (int) (dm.heightPixels * (note ? 0.55 : 0.35));
            int[] xy = BubbleLogic.clamp(x, y, dm.widthPixels, dm.heightPixels, lp.width, lp.height);
            lp.x = xy[0];
            lp.y = xy[1];
        }

        /** The screen changed: move the bubble to where it belongs on the new one. */
        void reposition() {
            place();
            if (!shown) return;
            try { wm.updateViewLayout(view, lp); } catch (Exception ignored) { }
        }

        private String what(String reason) {
            return (note ? "note bubble, " : "mic bubble, ") + reason;
        }

        /**
         * Does what BubbleLogic.action says about this bubble. {@code kind} and {@code reason} go to the diagnostics
         * log; {@code rebuild} takes a wanted, shown bubble off and puts it back (after an unlock, a rotation, the
         * screen coming on) and {@code why} names the event that asked.
         */
        void apply(boolean want, String kind, String reason, boolean rebuild, String why) {
            boolean fresh = SystemClock.elapsedRealtime() - addedAt < 2000;
            boolean attached = view.isAttachedToWindow() || fresh;
            String act = BubbleLogic.action(want, shown, attached);
            if (act.equals(BubbleLogic.NONE) && want && rebuild) act = BubbleLogic.REPAIR;
            if (act.equals(BubbleLogic.NONE)) return;
            if (act.equals(BubbleLogic.REMOVE)) {
                setVisible(false, kind, reason);
            } else if (act.equals(BubbleLogic.ADD)) {
                setVisible(true, kind, reason);
            } else {   // REPAIR: the window is gone, or the event says to start fresh
                boolean gone = !attached;
                try { wm.removeView(view); } catch (Exception ignored) { }
                shown = false;
                String by = why == null || why.isEmpty() ? "" : why;
                setVisible(true, gone ? OverlayDiag.WATCHDOG_REPAIR : kind,
                        gone ? "window was gone" + (by.isEmpty() ? "" : ", found after " + by)
                                : reason + (by.isEmpty() ? "" : ", refreshed after " + by));
            }
        }

        /** Adds or removes the overlay when that changes what is on screen; {@code kind} and {@code reason} go to the diagnostics log. */
        void setVisible(boolean want, String kind, String reason) {
            if (want == shown) return;
            try {
                if (want) { place(); wm.addView(view, lp); addedAt = SystemClock.elapsedRealtime(); } else wm.removeView(view);
                shown = want;
                diag(kind, what(reason));
            } catch (Exception e) {
                shown = false; // the window manager refused (service going away, overlay revoked)
                String why = " (" + e.getClass().getSimpleName() + ")";
                if (want) diag(OverlayDiag.OVERLAY_FAILED, what(reason) + why);
                else diag(OverlayDiag.OVERLAY_REMOVE, what(reason + ", window already gone" + why));
            }
        }

        void remove(String reason) {
            if (shown) {
                try { wm.removeView(view); } catch (Exception ignored) { }
                diag(OverlayDiag.OVERLAY_REMOVE, what(reason));
            }
            shown = false;
        }

        void savePosition() {
            Prefs p = new Prefs(VoxAccessibilityService.this);
            if (note) p.saveNoteBubblePos(lp.x, lp.y);
            else p.saveBubblePos(lp.x, lp.y);
        }
    }

    private void createBubbles() {
        dictation = new Floating(false);
        noteBubble = new Floating(true);
    }

    private final class BubbleTouch implements View.OnTouchListener {
            private final Floating f;
            private final int slop;
            private final int longPress;
            float downX, downY;
            int startX, startY;
            boolean dragging, longFired;
            final Runnable onLong;
            BubbleTouch(Floating f, int slop, int longPress) {
                this.f = f;
                this.slop = slop;
                this.longPress = longPress;
                this.onLong = () -> { longFired = true; onBubbleLongPress(f); };
            }

            @Override
            public boolean onTouch(View v, MotionEvent ev) {
                switch (ev.getActionMasked()) {
                    case MotionEvent.ACTION_DOWN:
                        downX = ev.getRawX(); downY = ev.getRawY();
                        startX = f.lp.x; startY = f.lp.y;
                        dragging = false; longFired = false;
                        main.postDelayed(onLong, longPress);
                        DictationService.warm(VoxAccessibilityService.this);   // open the server connections now: the tap that follows finds them ready
                        return true;
                    case MotionEvent.ACTION_MOVE:
                        float dx = ev.getRawX() - downX, dy = ev.getRawY() - downY;
                        if (!dragging && Math.hypot(dx, dy) > slop) {
                            dragging = true;
                            main.removeCallbacks(onLong);
                        }
                        if (dragging && f.shown) {
                            f.lp.x = (int) (startX + dx);
                            f.lp.y = (int) (startY + dy);
                            wm.updateViewLayout(f.view, f.lp);
                        }
                        return true;
                    case MotionEvent.ACTION_UP:
                        main.removeCallbacks(onLong);
                        if (dragging) snapToEdge(f);
                        else if (!longFired) { if (f.note) onNoteBubbleTap(); else onBubbleTap(); }
                        return true;
                    case MotionEvent.ACTION_CANCEL:
                        main.removeCallbacks(onLong);
                        return true;
                }
                return false;
            }
    }

    private void snapToEdge(Floating f) {
        DisplayMetrics dm = screen();
        int w = f.lp.width;
        int x = (f.lp.x + w / 2 < dm.widthPixels / 2) ? 0 : dm.widthPixels - w;
        int[] xy = BubbleLogic.clamp(x, f.lp.y, dm.widthPixels, dm.heightPixels, w, f.lp.height);
        f.lp.x = xy[0];
        f.lp.y = xy[1];
        if (f.shown) wm.updateViewLayout(f.view, f.lp);
        f.savePosition();
    }

    private void removeBubbles(String reason) {
        if (dictation != null) dictation.remove(reason);
        if (noteBubble != null) noteBubble.remove(reason);
    }

    private void onBubbleTap() {
        final long tapAt = SystemClock.elapsedRealtime(); // for the "tap->recording" log in DictationService
        DictationService svc = DictationService.instance;
        if (svc == null) {
            if (isPasswordField(editNode)) {
                toast("Vox does not type into password fields");
                return;
            }
            // The mic service can only start from a visible activity. Flash a transparent one: it passes these
            // extras on, the service starts recording itself as soon as it is in the foreground, then closes it.
            dictation.view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
            Intent i = new Intent(this, TrampolineActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_ANIMATION)
                    .putExtra(DictationService.EXTRA_START, true)
                    .putExtra(DictationService.EXTRA_PKG, editPkg)
                    .putExtra(DictationService.EXTRA_LABEL, appLabel(editPkg))
                    .putExtra(DictationService.EXTRA_DEST, DictationService.DEST_DICTATION)
                    .putExtra(DictationService.EXTRA_TAP_AT, tapAt);
            startActivity(i);
            return;
        }
        if (svc.getState() != DictationService.IDLE && svc.isNoteJob()) {
            toast("Vox is busy with a voice note");   // this bubble shows no sign of it, so a tap must not end the note
            return;
        }
        switch (svc.getState()) {
            case DictationService.IDLE:
                if (isPasswordField(editNode)) {
                    toast("Vox does not type into password fields");
                    break;
                }
                dictation.view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                svc.startRecording(editPkg, appLabel(editPkg), DictationService.DEST_DICTATION, tapAt);
                break;
            case DictationService.RECORDING:
                dictation.view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                svc.stopRecording();
                break;
            default:
                break;
        }
    }

    /**
     * Tap on the voice note bubble: start a note, or finish the one being recorded. Unlike dictation it does not care
     * about the focused field (a note is not typed anywhere), so a password field does not matter.
     */
    private void onNoteBubbleTap() {
        final long tapAt = SystemClock.elapsedRealtime();
        DictationService svc = DictationService.instance;
        noteBubble.view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
        if (svc == null) {
            // Same as the dictation bubble: the microphone service can only start from a visible activity.
            startActivity(NoteEntry.startIntent(this).putExtra(DictationService.EXTRA_TAP_AT, tapAt));
            return;
        }
        switch (svc.getState()) {
            case DictationService.IDLE:
                svc.startRecording(null, "", DictationService.DEST_NOTE, tapAt);
                break;
            case DictationService.RECORDING:
                if (svc.isNoteJob()) svc.stopRecording();
                else toast("Finish the dictation first");
                break;
            default:
                break;
        }
    }

    /** Long press: cancel this bubble's own job while it is busy, otherwise open settings. */
    private void onBubbleLongPress(Floating f) {
        f.view.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS);
        DictationService svc = DictationService.instance;
        if (svc != null && svc.getState() != DictationService.IDLE && svc.isNoteJob() == f.note) {
            svc.cancel();
            toast("Cancelled");
        } else {
            startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        }
    }

    // ------------------------------------------------------ service events

    /** True when the job in progress is a voice note (then the note bubble shows it and the mic bubble stays idle). */
    private static boolean noteJob() {
        DictationService svc = DictationService.instance;
        return svc != null && svc.isNoteJob();
    }

    @Override
    public void onState(int s) {
        boolean note = noteJob();
        if (dictation != null) dictation.view.setState(note ? DictationService.IDLE : s);
        if (noteBubble != null) noteBubble.view.setState(note ? s : DictationService.IDLE);
        refreshVisibility();
    }

    @Override
    public void onLevel(float level) {
        Floating f = noteJob() ? noteBubble : dictation;
        if (f != null) f.view.setLevel(level);
    }

    @Override
    public void onResult(String text, String targetPkg) {
        boolean typed = insertText(text, targetPkg);
        // Only dictations end here (a note is saved, not typed), so this is the mic bubble.
        if (dictation != null) dictation.view.flash(typed ? BubbleView.SENT : BubbleView.ERROR);   // ERROR: it only reached the clipboard
    }

    /** A voice note was saved: the note bubble (not the mic bubble) shows the green check. A failed save arrives as onError. */
    @Override
    public void onNoteSaved(String id, String title) {
        if (noteBubble != null) noteBubble.view.flash(BubbleView.SENT);
    }

    @Override
    public void onError(String message) {
        toast(message);
        // A warning that still ends in a result (cleanup fell back to the raw words) is followed by onResult,
        // whose flash replaces this one. A failed voice note flashes the note bubble, the one that shows that job.
        Floating f = noteJob() ? noteBubble : dictation;
        if (f != null) f.view.flash(BubbleView.ERROR);
    }

    // ------------------------------------------------------------ insertion

    static boolean isPasswordField(AccessibilityNodeInfo n) {
        return n != null && n.isPassword();
    }

    /** Types the text into the focused field. False when it did not land there (copied to the clipboard instead, or refused). */
    private boolean insertText(String text, String targetPkg) {
        // A restored dictation (its app is unknown: pkg "") is never typed, whatever the focused field reports.
        if (InsertGuard.check(targetPkg, null) == InsertGuard.NO_TARGET) {
            copyToClipboard(text);
            toast(InsertGuard.message(InsertGuard.NO_TARGET));
            return false;
        }
        AccessibilityNodeInfo node = null;
        try { node = findFocus(AccessibilityNodeInfo.FOCUS_INPUT); } catch (Exception ignored) { }
        if (node == null || !node.isEditable()) {
            node = editNode;
            if (node != null && !node.refresh()) node = null;
        }
        if (node == null) {
            copyToClipboard(text);
            toast("No text field found. Copied to clipboard.");
            return false;
        }
        if (isPasswordField(node)) {
            toast("Vox does not type into password fields");
            return false;
        }
        int verdict = InsertGuard.check(targetPkg, node.getPackageName());
        if (verdict != InsertGuard.TYPE) {
            // The user switched apps while Vox was working: do not type into the wrong one.
            copyToClipboard(text);
            toast(InsertGuard.message(verdict));
            return false;
        }

        CharSequence curCs = node.getText();
        String cur = curCs == null ? "" : curCs.toString();
        if (Build.VERSION.SDK_INT >= 26) {
            // Empty fields often report their placeholder ("Message", "Search") as their text.
            CharSequence hint = node.getHintText();
            if (node.isShowingHintText()
                    || (hint != null && cur.trim().equalsIgnoreCase(hint.toString().trim()))) {
                cur = "";
            }
        }
        int s = node.getTextSelectionStart();
        int e = node.getTextSelectionEnd();
        if (s < 0 || e < 0 || s > cur.length() || e > cur.length()) { s = cur.length(); e = s; }
        if (s > e) { int t = s; s = e; e = t; }

        String ins = text;
        if (s > 0 && !Character.isWhitespace(cur.charAt(s - 1)) && !ins.isEmpty()
                && ".,!?;:)".indexOf(ins.charAt(0)) < 0) {
            ins = " " + ins;
        }
        String next = cur.substring(0, s) + ins + cur.substring(e);

        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, next);
        boolean ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        if (ok) {
            Bundle sel = new Bundle();
            int caret = s + ins.length();
            sel.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, caret);
            sel.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, caret);
            node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, sel);
            return true;
        }

        // Fallback: paste through the clipboard, then restore what was there.
        ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        final ClipData old = cm.getPrimaryClip();
        cm.setPrimaryClip(ClipData.newPlainText("Vox", ins));
        boolean pasted = node.performAction(AccessibilityNodeInfo.ACTION_PASTE);
        if (pasted) {
            main.postDelayed(() -> {
                try { if (old != null) cm.setPrimaryClip(old); } catch (Exception ignored) { }
            }, 800);
        } else {
            toast("This app blocked typing. Text copied to clipboard.");
        }
        return pasted;
    }

    private void copyToClipboard(String t) {
        ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        cm.setPrimaryClip(ClipData.newPlainText("Vox", t));
    }

    private String appLabel(String pkg) {
        if (pkg == null) return "";
        try {
            PackageManager pm = getPackageManager();
            ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
            return pm.getApplicationLabel(ai).toString();
        } catch (Exception e) {
            return pkg;
        }
    }

    private void toast(String m) {
        main.post(() -> Toast.makeText(this, m, Toast.LENGTH_SHORT).show());
    }
}
