package com.minhaj.vox;

/**
 * The pure rules behind the floating bubble: where it may sit on the screen, whether it is wanted, and what the
 * watchdog does about it. Pure Java (no android.* classes) so the off-device tests and the golden rows in
 * spec/golden.txt (kinds bubbleclamp, bubbleshow, bubbleaction) run it. VoxAccessibilityService is the adapter.
 */
final class BubbleLogic {
    private BubbleLogic() { }

    /** How often the service looks at its own bubble while it is alive (milliseconds). */
    static final long WATCHDOG_MS = 30000L;

    static final String NONE = "none";
    static final String ADD = "add";
    static final String REMOVE = "remove";
    /** The service believes the bubble is on screen but the window is gone (the system dropped it): add it again. */
    static final String REPAIR = "repair";

    /**
     * Keeps a bubble of {@code bw} x {@code bh} fully on a screen of {@code w} x {@code h}: returns {x, y} limited to
     * 0..(w-bw) and 0..(h-bh). A screen smaller than the bubble gives 0. Used after a rotation or a change of screen
     * size, when a saved position can lie outside the new screen.
     */
    static int[] clamp(int x, int y, int w, int h, int bw, int bh) {
        long maxX = Math.max(0L, (long) w - bw);
        long maxY = Math.max(0L, (long) h - bh);
        return new int[]{(int) Math.min(Math.max((long) x, 0L), maxX), (int) Math.min(Math.max((long) y, 0L), maxY)};
    }

    /**
     * Is the mic bubble wanted right now. No bubble while the screen is off or the service is not ready; otherwise
     * "Always show the bubble" wins over "Bubble only while typing", which wants a focused text field. (A dictation in
     * progress also keeps the bubble up: the caller adds that.)
     */
    static boolean shouldShow(boolean onlyTyping, boolean alwaysShow, boolean fieldFocused, boolean screenOn, boolean serviceReady) {
        if (!serviceReady || !screenOn) return false;
        return alwaysShow || !onlyTyping || fieldFocused;
    }

    /** What to do about one bubble: add a wanted one that is missing, add it again when its window is gone, remove one that is not wanted. */
    static String action(boolean wanted, boolean shown, boolean attached) {
        if (wanted) {
            if (!shown) return ADD;
            return attached ? NONE : REPAIR;
        }
        return shown ? REMOVE : NONE;
    }
}
