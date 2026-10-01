package com.minhaj.vox;

/** Plain-Java checks for NoteBubbleLogic: when the note bubble is on screen and how its timer reads. Exits non-zero on failure. */
public final class NoteBubbleLogicTest {
    private static void eq(String name, Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) {
        // visible(persistent switch, note recording, note being saved)
        eq("nothing going on, switch off", false, NoteBubbleLogic.visible(false, false, false));
        eq("nothing going on, switch on", true, NoteBubbleLogic.visible(true, false, false));
        eq("recording a note, switch off", true, NoteBubbleLogic.visible(false, true, false));
        eq("saving a note, switch off", true, NoteBubbleLogic.visible(false, false, true));
        eq("recording a note, switch on", true, NoteBubbleLogic.visible(true, true, false));
        eq("saving a note, switch on", true, NoteBubbleLogic.visible(true, false, true));

        // the timer: m:ss, then h:mm:ss; whole seconds, rounded down; never negative
        eq("zero", "0:00", NoteBubbleLogic.timer(0));
        eq("under a second", "0:00", NoteBubbleLogic.timer(999));
        eq("seven seconds", "0:07", NoteBubbleLogic.timer(7400));
        eq("one minute", "1:00", NoteBubbleLogic.timer(60000));
        eq("12 min 5 s", "12:05", NoteBubbleLogic.timer(725000));
        eq("59 min 59 s", "59:59", NoteBubbleLogic.timer(3599999));
        eq("one hour", "1:00:00", NoteBubbleLogic.timer(3600000));
        eq("1 h 2 min 3 s", "1:02:03", NoteBubbleLogic.timer(3723000));
        eq("negative is zero", "0:00", NoteBubbleLogic.timer(-5));
        System.out.println("NoteBubbleLogicTest ok");
    }
}
