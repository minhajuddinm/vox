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

        System.out.println("OK: " + checks + " checks passed");
    }
}
