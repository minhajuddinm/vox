package com.minhaj.vox;

import java.util.Arrays;
import java.util.List;

/** Plain-Java checks for NotificationActions and InsertGuard. Run by CI, exits non-zero on failure. */
public final class NotificationActionsTest {
    private static void eq(String name, Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) {
        eq("idle", Arrays.asList("Turn off"), NotificationActions.choose(false, 0));
        eq("note recording", Arrays.asList("Stop", "Turn off"), NotificationActions.choose(true, 0));
        eq("unsent", Arrays.asList("Retry", "Clear", "Turn off"), NotificationActions.choose(false, 2));
        eq("recording and unsent: three, no Turn off", Arrays.asList("Stop", "Retry", "Clear"), NotificationActions.choose(true, 1));
        for (int unsent = 0; unsent <= 6; unsent++) {
            for (int r = 0; r < 2; r++) {
                List<String> a = NotificationActions.choose(r == 1, unsent);
                eq("at most 3 (" + r + "," + unsent + ")", true, a.size() <= NotificationActions.MAX_ACTIONS);
                eq("never empty (" + r + "," + unsent + ")", true, !a.isEmpty());
            }
        }

        eq("hint one", "Tap Retry to send it again", NotificationActions.retryHint(1));
        eq("hint several", "Retry sends the oldest first", NotificationActions.retryHint(3));

        // typing guard
        eq("no target known: type", InsertGuard.TYPE, InsertGuard.check(null, "com.app"));
        eq("same app: type", InsertGuard.TYPE, InsertGuard.check("com.app", "com.app"));
        eq("other app: switched", InsertGuard.SWITCHED_APPS, InsertGuard.check("com.app", "com.other"));
        eq("node reports no package: type (as before)", InsertGuard.TYPE, InsertGuard.check("com.app", null));
        eq("restored dictation, node package set: not typed", InsertGuard.NO_TARGET, InsertGuard.check("", "com.app"));
        eq("restored dictation, node package null: not typed", InsertGuard.NO_TARGET, InsertGuard.check("", null));
        eq("restored text", "Copied; paste it where you want it", InsertGuard.message(InsertGuard.NO_TARGET));
        eq("switched text", "You switched apps. Dictation copied to clipboard.", InsertGuard.message(InsertGuard.SWITCHED_APPS));
        System.out.println("NotificationActions and InsertGuard ok");
    }
}
