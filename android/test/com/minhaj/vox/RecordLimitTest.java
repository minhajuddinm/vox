package com.minhaj.vox;

/** Plain-Java checks for RecordLimit: how long a recording may run, and what is said before and at the limit. Exits non-zero on failure. */
public final class RecordLimitTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) {
        // the PC app's limits (windows/engine.py: MAX_SECONDS, three times that for a note)
        eq("a dictation runs 6 minutes", 360, RecordLimit.seconds(false));
        eq("a voice note runs 18 minutes", 1080, RecordLimit.seconds(true));
        eq("the byte limit is 16 kHz, 16-bit mono", 360L * 32000, RecordLimit.maxBytes(false));
        eq("the note byte limit", 1080L * 32000, RecordLimit.maxBytes(true));
        // a warning half a minute before
        eq("the warning comes 30 s before the end", (1080L - 30) * 32000, RecordLimit.warnBytes(true));
        eq("the dictation warning", (360L - 30) * 32000, RecordLimit.warnBytes(false));
        eq("note warning", "This voice note stops in 30 seconds (18-minute limit).", RecordLimit.warning(true));
        eq("dictation warning", "This dictation stops in 30 seconds (6-minute limit).", RecordLimit.warning(false));
        eq("note limit reached", "The voice note reached the 18-minute limit: Vox is saving it. Start a new note to go on.", RecordLimit.reached(true));
        eq("dictation limit reached", "The dictation reached the 6-minute limit: Vox is sending it.", RecordLimit.reached(false));
        // an 18-minute note is over the single-upload limit, so it is sent in pieces (StreamingStt.inPieces)
        eq("a long note needs pieces", true, StreamingStt.needsPieces(RecordLimit.maxBytes(true)));
        eq("a full dictation fits in one upload", false, StreamingStt.needsPieces(RecordLimit.maxBytes(false)));

        System.out.println("OK: " + checks + " checks passed");
    }
}
