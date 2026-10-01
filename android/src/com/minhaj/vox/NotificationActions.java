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
}
