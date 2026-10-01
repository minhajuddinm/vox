package com.minhaj.vox;

import android.accessibilityservice.AccessibilityService;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.DisplayMetrics;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Toast;

/**
 * Owns the floating bubbles (accessibility overlays, so no "draw over apps" permission is needed): the mic bubble,
 * which tracks the focused text field and inserts the final text into it, and the optional voice note bubble, which
 * is always on screen while "note_bubble" is on and starts or stops a note.
 */
public class VoxAccessibilityService extends AccessibilityService implements DictationService.Listener {
    public static volatile VoxAccessibilityService instance;

    private final Handler main = new Handler(Looper.getMainLooper());
    private WindowManager wm;
    /** The mic bubble: on screen near a focused text field (always, when "only_typing" is off). */
    private Floating dictation;
    /** The voice note bubble: on screen whenever "note_bubble" is on, whatever is focused. */
    private Floating noteBubble;

    private AccessibilityNodeInfo editNode;
    private String editPkg;

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        DictationService.setListener(this);
        wm = (WindowManager) getSystemService(WINDOW_SERVICE);
        createBubbles();
        refreshVisibility();
        NoteEntry.applySettings(this);   // puts the "Record note" notification back after a reboot
    }

    @Override
    public boolean onUnbind(Intent intent) {
        removeBubbles();
        instance = null;
        DictationService.setListener(null);
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        removeBubbles();
        instance = null;
        DictationService.setListener(null);
        super.onDestroy();
    }

    @Override public void onInterrupt() { }

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
        if (dictation == null) return;
        DictationService svc = DictationService.instance;
        // A note being recorded or sent is the note bubble's business: it must not bring the mic bubble up.
        boolean busy = svc != null && svc.getState() != DictationService.IDLE && !svc.isNoteJob();
        Prefs p = new Prefs(this);
        dictation.setVisible(busy || !p.onlyWhenTyping() || editNode != null);
        noteBubble.setVisible(p.noteBubble());   // independent of the focused field and of only_typing
    }

    // ------------------------------------------------------------- bubble

    /** One floating bubble: its view, its place on screen, and whether it is currently added to the window manager. */
    private final class Floating {
        final BubbleView view;
        final WindowManager.LayoutParams lp;
        final boolean note;
        boolean shown;

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
            Prefs p = new Prefs(VoxAccessibilityService.this);
            int x = note ? p.noteBubbleX() : p.bubbleX();
            int y = note ? p.noteBubbleY() : p.bubbleY();
            // default spots: the right edge, the note bubble lower down so the two do not sit on top of each other
            lp.x = x >= 0 ? x : dm.widthPixels - size;
            lp.y = y >= 0 ? y : (int) (dm.heightPixels * (note ? 0.55 : 0.35));

            final int slop = ViewConfiguration.get(VoxAccessibilityService.this).getScaledTouchSlop();
            final int longPress = ViewConfiguration.getLongPressTimeout() + 150;
            view.setOnTouchListener(new BubbleTouch(this, slop, longPress));
        }

        void setVisible(boolean want) {
            try {
                if (want && !shown) { wm.addView(view, lp); shown = true; }
                else if (!want && shown) { wm.removeView(view); shown = false; }
            } catch (Exception e) {
                shown = false; // the window manager refused (service going away, overlay revoked)
            }
        }

        void remove() {
            if (shown) {
                try { wm.removeView(view); } catch (Exception ignored) { }
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
        DisplayMetrics dm = getResources().getDisplayMetrics();
        int w = f.lp.width;
        f.lp.x = (f.lp.x + w / 2 < dm.widthPixels / 2) ? 0 : dm.widthPixels - w;
        f.lp.y = Math.max(0, Math.min(f.lp.y, dm.heightPixels - f.lp.height));
        if (f.shown) wm.updateViewLayout(f.view, f.lp);
        f.savePosition();
    }

    private void removeBubbles() {
        if (dictation != null) dictation.remove();
        if (noteBubble != null) noteBubble.remove();
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
        insertText(text, targetPkg);
    }

    @Override
    public void onError(String message) {
        toast(message);
    }

    // ------------------------------------------------------------ insertion

    static boolean isPasswordField(AccessibilityNodeInfo n) {
        return n != null && n.isPassword();
    }

    private void insertText(String text, String targetPkg) {
        AccessibilityNodeInfo node = null;
        try { node = findFocus(AccessibilityNodeInfo.FOCUS_INPUT); } catch (Exception ignored) { }
        if (node == null || !node.isEditable()) {
            node = editNode;
            if (node != null && !node.refresh()) node = null;
        }
        if (node == null) {
            copyToClipboard(text);
            toast("No text field found. Copied to clipboard.");
            return;
        }
        if (isPasswordField(node)) {
            toast("Vox does not type into password fields");
            return;
        }
        CharSequence nodePkg = node.getPackageName();
        if (targetPkg != null && nodePkg != null && !targetPkg.contentEquals(nodePkg)) {
            // The user switched apps while Vox was working: do not type into the wrong one.
            copyToClipboard(text);
            toast("You switched apps. Dictation copied to clipboard.");
            return;
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
            return;
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
