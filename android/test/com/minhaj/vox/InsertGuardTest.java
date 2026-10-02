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
        eq("a failed cleanup of a dictation is reported", "Cleanup did not work, so Vox typed your words as spoken", InsertGuard.cleanupNotice(true, true, false));
        eq("a failed cleanup of a note is reported", "Cleanup did not work, so Vox saved your words as spoken", InsertGuard.cleanupNotice(true, true, true));
        eq("no failure, no notice", null, InsertGuard.cleanupNotice(true, false, false));
        eq("a cancelled job says nothing about a failed cleanup", null, InsertGuard.cleanupNotice(false, true, false));
        eq("a cancelled note says nothing about a failed cleanup", null, InsertGuard.cleanupNotice(false, true, true));

        System.out.println("OK: " + checks + " checks passed");
    }
}
