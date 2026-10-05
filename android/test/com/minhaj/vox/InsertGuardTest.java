package com.minhaj.vox;

/** Plain-Java checks for InsertGuard: where a finished dictation goes. Exits non-zero on failure. */
public final class InsertGuardTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) {
        // route: a finished dictation is typed when something can type it, copied when nothing can, dropped when it was cancelled
        eq("route with a listener types", InsertGuard.ROUTE_TYPE, InsertGuard.route(true, true));
        eq("route without a listener copies", InsertGuard.ROUTE_CLIPBOARD, InsertGuard.route(true, false));
        eq("route of a cancelled job does nothing", InsertGuard.ROUTE_NONE, InsertGuard.route(false, false));
        eq("route of a cancelled job with a listener does nothing", InsertGuard.ROUTE_NONE, InsertGuard.route(false, true));
        eq("the three routes differ", 3, new java.util.HashSet<Object>(java.util.Arrays.asList(InsertGuard.ROUTE_TYPE, InsertGuard.ROUTE_CLIPBOARD, InsertGuard.ROUTE_NONE)).size());
        eq("the copy message says accessibility is off", true, InsertGuard.noListenerMessage().contains("accessibility is off"));

        // check (existing rule): a restored dictation is never typed, a switch of apps is refused
        eq("check without a target types", InsertGuard.TYPE, InsertGuard.check(null, "a.b"));
        eq("check of a restored dictation", InsertGuard.NO_TARGET, InsertGuard.check("", "a.b"));
        eq("check after switching apps", InsertGuard.SWITCHED_APPS, InsertGuard.check("a.b", "c.d"));
        eq("check in the same app", InsertGuard.TYPE, InsertGuard.check("a.b", "a.b"));

        // cleanupNotice: "Cleanup did not work" is only said while the job is still the current one (a cancel during cleanup says nothing)
        eq("a failed cleanup of a dictation is reported", "Cleanup did not work, so Vox typed your words with basic tidying only", InsertGuard.cleanupNotice(true, true, false));
        eq("a failed cleanup of a note is reported", "Cleanup did not work, so Vox saved your words with basic tidying only", InsertGuard.cleanupNotice(true, true, true));
        eq("no failure, no notice", null, InsertGuard.cleanupNotice(true, false, false));
        eq("a cancelled job says nothing about a failed cleanup", null, InsertGuard.cleanupNotice(false, true, false));
        eq("a cancelled note says nothing about a failed cleanup", null, InsertGuard.cleanupNotice(false, true, true));

        // emptyResult: a dictation that gave no usable words (nothing, or a lone "Thank you.") is never dropped without a word (AND-1)
        eq("an empty dictation says so, in the PC app's words", "Vox heard no usable words in that recording (a lone \"Thank you\" counts as silence). "
                + "Speak a little longer, or check the microphone.", InsertGuard.emptyResult(false));
        eq("an empty note says no note was saved", "Vox did not hear any words, so no note was saved", InsertGuard.emptyResult(true));

        // sendFailed: the action comes first (a toast shows two lines), the server's own text is capped (AND-13)
        eq("a server error keeps the recording, action first", "Recording kept: tap Retry in the notification. API 400: bad file",
                InsertGuard.sendFailed("API 400: bad file"));
        StringBuilder huge = new StringBuilder("API 500: ");
        for (int i = 0; i < 100; i++) huge.append("blah ");
        String capped = InsertGuard.sendFailed(huge.toString());
        eq("a long server text is cut", true, capped.length() <= InsertGuard.KEPT.length() + 1 + InsertGuard.MAX_DETAIL + 3);
        eq("a long server text ends in dots", true, capped.endsWith("..."));
        eq("a network failure keeps the recording, action first", "Recording kept: tap Retry in the notification. Network error: timeout",
                InsertGuard.networkFailed("timeout", "SocketTimeoutException"));
        eq("a network failure without a message", "Recording kept: tap Retry in the notification. Network error: SocketException",
                InsertGuard.networkFailed(null, "SocketException"));
        eq("no message: the class", "Recording kept: tap Retry in the notification. Network error: SocketException",
                InsertGuard.networkFailed("", "SocketException"));

        // crashed: any other failure in a send (a RuntimeException) keeps the recording and says what broke (AND-2)
        eq("an unexpected failure keeps the recording", "Recording kept: tap Retry in the notification. Vox could not send it (IllegalArgumentException).",
                InsertGuard.crashed(new IllegalArgumentException("Bearer gsk_secret")));
        eq("the message never carries the exception's own text (it can hold the key)", false,
                InsertGuard.crashed(new IllegalArgumentException("Bearer gsk_secret")).contains("gsk_secret"));

        // afterPaste: the dictation must not stay on the clipboard once it was pasted (#63, PRV-10). Android 10+ never lets a
        // background service read the old clip, so it is put back only when it could be read; otherwise the clip is cleared.
        eq("a readable old clip is put back", InsertGuard.CLIP_RESTORE, InsertGuard.afterPaste(true));
        eq("an unreadable or empty old clip: the dictation is cleared", InsertGuard.CLIP_CLEAR, InsertGuard.afterPaste(false));
        eq("Android 13+ marks a dictation on the clipboard as sensitive", true, InsertGuard.markSensitive(33));
        eq("older versions have no such flag", false, InsertGuard.markSensitive(32));
        eq("the clear needs Android 9 (API 28)", true, InsertGuard.canClearClip(28) && !InsertGuard.canClearClip(27));

        System.out.println("OK: " + checks + " checks passed");
    }
}
