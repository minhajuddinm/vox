package com.minhaj.vox;

/** Plain-Java checks for BubbleLogic: the position clamp, the visibility rule and the watchdog's decision. Exits non-zero on failure. */
public final class BubbleLogicTest {
    private static void eq(String name, Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    private static String clamp(int x, int y, int w, int h, int bw, int bh) {
        int[] p = BubbleLogic.clamp(x, y, w, h, bw, bh);
        return p[0] + "," + p[1];
    }

    public static void main(String[] args) {
        // a position that is already on screen is kept
        eq("inside", "100,200", clamp(100, 200, 1080, 2400, 180, 180));
        eq("top-left corner", "0,0", clamp(0, 0, 1080, 2400, 180, 180));
        eq("bottom-right corner", "900,2220", clamp(900, 2220, 1080, 2400, 180, 180));
        // saved on a wider (landscape) screen, now portrait: pulled back inside
        eq("too far right", "900,500", clamp(2220, 500, 1080, 2400, 180, 180));
        eq("too far down", "100,2220", clamp(100, 3000, 1080, 2400, 180, 180));
        eq("negative", "0,0", clamp(-50, -1, 1080, 2400, 180, 180));
        eq("the stored 'unset' marker is pulled to the edge, callers handle it before", "0,0", clamp(-1, -1, 1080, 2400, 180, 180));
        // a screen smaller than the bubble: stay at the origin, never a negative position
        eq("screen smaller than the bubble", "0,0", clamp(40, 40, 100, 100, 180, 180));
        eq("screen exactly the bubble", "0,0", clamp(40, 40, 180, 180, 180, 180));
        eq("huge values do not overflow", "900,2220", clamp(Integer.MAX_VALUE, Integer.MAX_VALUE, 1080, 2400, 180, 180));
        eq("tiny values do not overflow", "0,0", clamp(Integer.MIN_VALUE, Integer.MIN_VALUE, 1080, 2400, 180, 180));
        // clamping twice changes nothing
        int[] once = BubbleLogic.clamp(5000, 5000, 1080, 2400, 180, 180);
        int[] twice = BubbleLogic.clamp(once[0], once[1], 1080, 2400, 180, 180);
        eq("idempotent", once[0] + "," + once[1], twice[0] + "," + twice[1]);

        // visibility: only-typing hides it without a focused field; always-show ignores that; no screen or service means no bubble
        eq("only typing, field focused", true, BubbleLogic.shouldShow(true, false, true, true, true));
        eq("only typing, no field", false, BubbleLogic.shouldShow(true, false, false, true, true));
        eq("only typing off, no field", true, BubbleLogic.shouldShow(false, false, false, true, true));
        eq("always show beats only typing", true, BubbleLogic.shouldShow(true, true, false, true, true));
        eq("screen off", false, BubbleLogic.shouldShow(false, true, true, false, true));
        eq("service not ready", false, BubbleLogic.shouldShow(false, true, true, true, false));

        // the watchdog: add what is missing, repair a window the system dropped, remove what is not wanted, otherwise leave it
        eq("wanted, not shown", BubbleLogic.ADD, BubbleLogic.action(true, false, false));
        eq("wanted, shown, attached", BubbleLogic.NONE, BubbleLogic.action(true, true, true));
        eq("wanted, shown, window gone", BubbleLogic.REPAIR, BubbleLogic.action(true, true, false));
        eq("not wanted, shown", BubbleLogic.REMOVE, BubbleLogic.action(false, true, true));
        eq("not wanted, shown, window gone", BubbleLogic.REMOVE, BubbleLogic.action(false, true, false));
        eq("not wanted, not shown", BubbleLogic.NONE, BubbleLogic.action(false, false, false));
        eq("the watchdog runs every 30 seconds", 30000L, BubbleLogic.WATCHDOG_MS);
        System.out.println("BubbleLogicTest ok");
    }
}
