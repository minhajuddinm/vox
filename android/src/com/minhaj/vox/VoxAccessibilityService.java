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
 * Owns the floating bubble (an accessibility overlay, so no "draw over apps" permission is needed),
 * tracks the focused text field, and inserts the final text into it.
 */
public class VoxAccessibilityService extends AccessibilityService implements DictationService.Listener {
    public static volatile VoxAccessibilityService instance;

    private final Handler main = new Handler(Looper.getMainLooper());
    private WindowManager wm;
    private BubbleView bubble;
    private WindowManager.LayoutParams lp;
    private boolean bubbleShown;

    private AccessibilityNodeInfo editNode;
    private String editPkg;

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        DictationService.setListener(this);
        wm = (WindowManager) getSystemService(WINDOW_SERVICE);
        createBubble();
        refreshVisibility();
    }

    @Override
    public boolean onUnbind(Intent intent) {
        removeBubble();
        instance = null;
        DictationService.setListener(null);
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        removeBubble();
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
        if (bubble == null) return;
        boolean busy = DictationService.instance != null
                && DictationService.instance.getState() != DictationService.IDLE;
        boolean want = busy || !new Prefs(this).onlyWhenTyping() || editNode != null;
        try {
            if (want && !bubbleShown) { wm.addView(bubble, lp); bubbleShown = true; }
            else if (!want && bubbleShown) { wm.removeView(bubble); bubbleShown = false; }
        } catch (Exception e) {
            bubbleShown = false; // the window manager refused (service going away, overlay revoked)
        }
    }

    // ------------------------------------------------------------- bubble

    private void createBubble() {
        DisplayMetrics dm = getResources().getDisplayMetrics();
        int size = (int) (60 * dm.density);
        bubble = new BubbleView(this);
        lp = new WindowManager.LayoutParams(size, size,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.TOP | Gravity.START;
        Prefs p = new Prefs(this);
        lp.x = p.bubbleX() >= 0 ? p.bubbleX() : dm.widthPixels - size;
        lp.y = p.bubbleY() >= 0 ? p.bubbleY() : (int) (dm.heightPixels * 0.35);

        final int slop = ViewConfiguration.get(this).getScaledTouchSlop();
        final int longPress = ViewConfiguration.getLongPressTimeout() + 150;
        bubble.setOnTouchListener(new BubbleTouch(slop, longPress));
    }

    private final class BubbleTouch implements View.OnTouchListener {
            private final int slop;
            private final int longPress;
            BubbleTouch(int slop, int longPress) { this.slop = slop; this.longPress = longPress; }
            float downX, downY;
            int startX, startY;
            boolean dragging, longFired;
            final Runnable onLong = () -> { longFired = true; onBubbleLongPress(); };

            @Override
            public boolean onTouch(View v, MotionEvent ev) {
                switch (ev.getActionMasked()) {
                    case MotionEvent.ACTION_DOWN:
                        downX = ev.getRawX(); downY = ev.getRawY();
                        startX = lp.x; startY = lp.y;
                        dragging = false; longFired = false;
                        main.postDelayed(onLong, longPress);
                        return true;
                    case MotionEvent.ACTION_MOVE:
                        float dx = ev.getRawX() - downX, dy = ev.getRawY() - downY;
                        if (!dragging && Math.hypot(dx, dy) > slop) {
                            dragging = true;
                            main.removeCallbacks(onLong);
                        }
                        if (dragging && bubbleShown) {
                            lp.x = (int) (startX + dx);
                            lp.y = (int) (startY + dy);
                            wm.updateViewLayout(bubble, lp);
                        }
                        return true;
                    case MotionEvent.ACTION_UP:
                        main.removeCallbacks(onLong);
                        if (dragging) snapToEdge();
                        else if (!longFired) onBubbleTap();
                        return true;
                    case MotionEvent.ACTION_CANCEL:
                        main.removeCallbacks(onLong);
                        return true;
                }
                return false;
            }
    }

    private void snapToEdge() {
        DisplayMetrics dm = getResources().getDisplayMetrics();
        int w = lp.width;
        lp.x = (lp.x + w / 2 < dm.widthPixels / 2) ? 0 : dm.widthPixels - w;
        lp.y = Math.max(0, Math.min(lp.y, dm.heightPixels - lp.height));
        if (bubbleShown) wm.updateViewLayout(bubble, lp);
        new Prefs(this).saveBubblePos(lp.x, lp.y);
    }

    private void removeBubble() {
        if (bubble != null && bubbleShown) {
            try { wm.removeView(bubble); } catch (Exception ignored) { }
        }
        bubbleShown = false;
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
            bubble.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
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
        switch (svc.getState()) {
            case DictationService.IDLE:
                if (isPasswordField(editNode)) {
                    toast("Vox does not type into password fields");
                    break;
                }
                bubble.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                svc.startRecording(editPkg, appLabel(editPkg), DictationService.DEST_DICTATION, tapAt);
                break;
            case DictationService.RECORDING:
                bubble.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                svc.stopRecording();
                break;
            default:
                break;
        }
    }

    /** Long press: cancel while busy, otherwise open settings. */
    private void onBubbleLongPress() {
        bubble.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS);
        DictationService svc = DictationService.instance;
        if (svc != null && svc.getState() != DictationService.IDLE) {
            svc.cancel();
            toast("Cancelled");
        } else {
            startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        }
    }

    // ------------------------------------------------------ service events

    @Override
    public void onState(int s) {
        if (bubble != null) bubble.setState(s);
        refreshVisibility();
    }

    @Override
    public void onLevel(float level) {
        if (bubble != null) bubble.setLevel(level);
    }

    @Override
    public void onResult(String text, String targetPkg) {
        boolean typed = insertText(text, targetPkg);
        if (bubble != null) bubble.flash(typed ? BubbleView.SENT : BubbleView.ERROR);   // ERROR: it only reached the clipboard
    }

    @Override
    public void onError(String message) {
        toast(message);
        // A warning that still ends in a result (cleanup fell back to the raw words) is followed by onResult,
        // whose flash replaces this one.
        if (bubble != null) bubble.flash(BubbleView.ERROR);
    }

    // ------------------------------------------------------------ insertion

    static boolean isPasswordField(AccessibilityNodeInfo n) {
        return n != null && n.isPassword();
    }

    /** Types the text into the focused field. False when it did not land there (copied to the clipboard instead, or refused). */
    private boolean insertText(String text, String targetPkg) {
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
        CharSequence nodePkg = node.getPackageName();
        if (targetPkg != null && nodePkg != null && !targetPkg.contentEquals(nodePkg)) {
            // The user switched apps while Vox was working: do not type into the wrong one.
            copyToClipboard(text);
            toast("You switched apps. Dictation copied to clipboard.");
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
