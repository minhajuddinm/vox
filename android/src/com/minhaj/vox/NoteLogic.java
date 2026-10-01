package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.GregorianCalendar;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.TimeZone;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Rules for voice notes that the phone and the Windows app (windows/notes.py) must apply identically, so a note
 * looks and searches the same on both and a sync merges the same way. Pure Java (no android.* or org.json) so the
 * off-device tests can run it. The title, search and merge rules are pinned by the title, ftsq and remotewins
 * rows of spec/golden.txt, which tests/test_parity.py runs against notes.py and ParityTest runs against this class.
 */
final class NoteLogic {
    private NoteLogic() { }

    /** Notes or delete markers sent to the relay in one request. */
    static final int PUSH_BATCH = 100;

    /** Tags kept per note. The relay keeps at most as many (relay.py MAX_TAGS). */
    static final int MAX_TAGS = 20;

    private static final int TITLE_WORDS = 7;

    /** What notes.search hands to FTS5: a word is a run of letters, digits and underscores (Python's \w). */
    private static final Pattern WORD = Pattern.compile("[\\p{L}\\p{N}_]+");

    /** The title of a note saved without one: its first 7 words, plus "..." when there are more (notes.auto_title). */
    static String autoTitle(String text) {
        List<String> words = words(text);
        String title = String.join(" ", words.subList(0, Math.min(words.size(), TITLE_WORDS)));
        return words.size() > TITLE_WORDS ? title + "..." : title;
    }

    /**
     * True when a note received from the relay replaces the local copy (notes.apply_remote): a note this device
     * never had is taken unless it is a delete marker, and a note this device has is replaced only by a strictly
     * newer version (a tie keeps the local one).
     */
    static boolean remoteWins(boolean hasLocal, double localUpdated, double remoteUpdated, boolean remoteDeleted) {
        if (!hasLocal) return !remoteDeleted;
        return remoteUpdated > localUpdated;
    }

    /**
     * The words of the search box text (notes._words): runs of letters, digits and underscores, in order. The store
     * matches each one (as a word start with FTS5, or anywhere in the title or text with LIKE). Empty for null.
     */
    static List<String> searchWords(String q) {
        List<String> out = new ArrayList<>();
        if (q == null) return out;
        Matcher m = WORD.matcher(q);
        while (m.find()) out.add(m.group());
        return out;
    }

    /**
     * The FTS5 MATCH string for the search box text (notes.fts_query): each word as a quoted prefix token,
     * {@code "tok"*}, joined by spaces, so every word must start some word of the note. "" when there is no word.
     */
    static String ftsQuery(String q) {
        StringBuilder out = new StringBuilder();
        for (String w : searchWords(q)) {
            if (out.length() > 0) out.append(' ');
            out.append('"').append(w).append("\"*");
        }
        return out.toString();
    }

    /**
     * Tags as stored: double quotes removed, then trimmed, no empty ones, no repeats (the first stays), at most
     * {@link #MAX_TAGS}. A new list. Same rule as notes._tags.
     */
    static List<String> cleanTags(List<String> tags) {
        LinkedHashSet<String> out = new LinkedHashSet<>();
        if (tags != null) {
            for (String t : tags) {
                String s = strip(t == null ? null : t.replace("\"", ""));
                if (!s.isEmpty()) out.add(s);
                if (out.size() == MAX_TAGS) break;
            }
        }
        return new ArrayList<>(out);
    }

    /**
     * Python's str.isspace(), which is what str.split() and str.strip() use. Not Character.isWhitespace: that one
     * leaves out the no-break spaces (U+00A0, U+2007, U+202F) and U+0085, so a title would differ between platforms.
     */
    private static boolean isSpace(char c) {
        return (c >= 0x09 && c <= 0x0D) || (c >= 0x1C && c <= 0x20) || c == 0x85 || c == 0xA0 || c == 0x1680
                || (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 || c == 0x202F || c == 0x205F
                || c == 0x3000;
    }

    /** Python's {@code text.split()}: the runs of non-space characters. */
    private static List<String> words(String text) {
        List<String> out = new ArrayList<>();
        if (text == null) return out;
        int start = -1;
        for (int i = 0; i < text.length(); i++) {
            if (isSpace(text.charAt(i))) {
                if (start >= 0) out.add(text.substring(start, i));
                start = -1;
            } else if (start < 0) {
                start = i;
            }
        }
        if (start >= 0) out.add(text.substring(start));
        return out;
    }

    /** The longest device name, in code points (sync.device_name cuts at 60 characters). */
    static final int MAX_DEVICE_NAME = 60;

    /** What a phone calls itself when nothing else is known. */
    static final String DEFAULT_DEVICE_NAME = "android-phone";

    /**
     * The name this phone shows on the relay and puts on the notes it records (sync.device_name): the name the user
     * typed, else the phone model, else "android-phone"; trimmed the Python way, then cut at 60 code points (not
     * UTF-16 units, so a pair of surrogates is never split) and not trimmed again.
     */
    static String deviceName(String typed, String model) {
        String name = strip(typed);
        if (name.isEmpty()) name = strip(model);
        if (name.isEmpty()) name = DEFAULT_DEVICE_NAME;
        if (name.codePointCount(0, name.length()) > MAX_DEVICE_NAME) {
            name = name.substring(0, name.offsetByCodePoints(0, MAX_DEVICE_NAME));
        }
        return name;
    }

    /**
     * The "created since" bound of the Voice notes period filter, in Unix seconds: "today" is the start of today in
     * {@code tz}, "week" is 7 days before {@code nowSecs} and "month" 30 days before it. Null (no bound) for "all"
     * and anything else. The same periods as Api.notes_list in windows/ui_app.py.
     */
    static Double periodStart(String period, double nowSecs, TimeZone tz) {
        if ("week".equals(period)) return nowSecs - 7 * 86400;
        if ("month".equals(period)) return nowSecs - 30 * 86400;
        if (!"today".equals(period)) return null;
        Calendar c = new GregorianCalendar(tz);   // not getInstance: a locale can pick another calendar system
        c.setTimeInMillis((long) Math.floor(nowSecs * 1000));
        c.set(Calendar.HOUR_OF_DAY, 0);
        c.set(Calendar.MINUTE, 0);
        c.set(Calendar.SECOND, 0);
        c.set(Calendar.MILLISECOND, 0);
        return c.getTimeInMillis() / 1000.0;
    }

    /** What a start request (the trampoline's start intent) does for the service state: see {@link #startAction}. */
    static final int START = 0, STOP = 1, BUSY = 2;

    /**
     * What a request to start recording does. {@code state} is DictationService IDLE (0), RECORDING (1) or PROCESSING
     * (2). Idle: START. A note is being recorded and another note was asked for: STOP (the same button stops it).
     * Anything else: BUSY (say so, change nothing).
     */
    static int startAction(int state, boolean noteJob, boolean wantNote) {
        if (state == 0) return START;
        if (state == 1 && noteJob && wantNote) return STOP;
        return BUSY;
    }

    /** Python's {@code s.strip()}; null counts as "". The store saves the text and the title of a note this way. */
    static String strip(String s) {
        if (s == null) return "";
        int from = 0, to = s.length();
        while (from < to && isSpace(s.charAt(from))) from++;
        while (to > from && isSpace(s.charAt(to - 1))) to--;
        return s.substring(from, to);
    }
}
