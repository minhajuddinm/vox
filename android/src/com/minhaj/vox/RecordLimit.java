package com.minhaj.vox;

/**
 * How long one recording may run, and what is said half a minute before and at the end. The PC app's limits
 * (windows/engine.py: MAX_SECONDS for a dictation, three times that for a hands-free voice note). A note that long is
 * over the speech server's upload limit, so it is sent in pieces (StreamingStt.inPieces). Pure Java, tested by
 * RecordLimitTest.
 */
final class RecordLimit {
    private RecordLimit() { }

    /** A dictation stops itself after this many seconds. */
    static final int MAX_SECONDS = 360;

    /** A voice note may run this many times longer. */
    static final int NOTE_FACTOR = 3;

    /** The warning comes this many seconds before the end. */
    static final int WARN_SECONDS = 30;

    private static final long BYTES_PER_SECOND = 16000L * 2;   // 16 kHz, 16-bit mono

    static int seconds(boolean note) {
        return MAX_SECONDS * (note ? NOTE_FACTOR : 1);
    }

    static long maxBytes(boolean note) {
        return seconds(note) * BYTES_PER_SECOND;
    }

    static long warnBytes(boolean note) {
        return (seconds(note) - WARN_SECONDS) * BYTES_PER_SECOND;
    }

    static String warning(boolean note) {
        return (note ? "This voice note" : "This dictation") + " stops in " + WARN_SECONDS + " seconds (" + seconds(note) / 60 + "-minute limit).";
    }

    static String reached(boolean note) {
        return note ? "The voice note reached the " + seconds(true) / 60 + "-minute limit: Vox is saving it. Start a new note to go on."
                : "The dictation reached the " + seconds(false) / 60 + "-minute limit: Vox is sending it.";
    }
}
