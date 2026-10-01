package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Which buttons the foreground notification shows, and its hint line, as pure logic (no android.*). Android shows at
 * most {@link #MAX_ACTIONS} actions and silently drops the rest, so the choice is made here, in one place.
 */
public final class NotificationActions {
    public static final int MAX_ACTIONS = 3;
    public static final String STOP = "Stop", RETRY = "Retry", CLEAR = "Clear", TURN_OFF = "Turn off";

    private NotificationActions() { }

    /**
     * The buttons, in order. "Stop" while a voice note is being recorded; "Retry" and "Clear" while any recording is
     * unsent; "Turn off" only when there is still room (never a fourth button). With a note recording AND unsent
     * recordings the three buttons are Stop, Retry, Clear: the service is then turned off from the app.
     */
    public static List<String> choose(boolean recording, int unsentCount) {
        List<String> out = new ArrayList<>();
        if (recording) out.add(STOP);
        if (unsentCount > 0) {
            out.add(RETRY);
            out.add(CLEAR);
        }
        if (out.size() < MAX_ACTIONS) out.add(TURN_OFF);
        return Collections.unmodifiableList(out);
    }

    /** The second line of the notification when recordings are unsent: with several, Retry's order is said. */
    public static String retryHint(int unsentCount) {
        return unsentCount > 1 ? "Retry sends the oldest first" : "Tap Retry to send it again";
    }

    /** The same with the number of recordings that are parked after {@link PendingQueue#MAX_RETRIES} failed retries. */
    public static String retryHint(int unsentCount, int stuckCount) {
        if (stuckCount <= 0) return retryHint(unsentCount);
        String tries = "Stuck after " + PendingQueue.MAX_RETRIES + " tries. Tap Clear to remove ";
        if (stuckCount >= unsentCount) return tries + (unsentCount == 1 ? "it" : "them");
        return retryHint(unsentCount) + ". " + stuckCount + " stuck after " + PendingQueue.MAX_RETRIES + " tries";
    }

    /**
     * A Retry that reached a service which is not running as the foreground service (a stale button) sends nothing and
     * must not leave a background service behind; the others do the same for Stop and Clear.
     */
    public static boolean retryIsStale(boolean isForegroundInstance) { return !isForegroundInstance; }
}
