package com.minhaj.vox;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
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

    /** Tags as stored: trimmed, no empty ones, no repeats (the first stays), at most {@link #MAX_TAGS}. A new list. */
    static List<String> cleanTags(List<String> tags) {
        LinkedHashSet<String> out = new LinkedHashSet<>();
        if (tags != null) {
            for (String t : tags) {
                String s = strip(t);
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

    /** Python's {@code s.strip()}; null counts as "". The store saves the text and the title of a note this way. */
    static String strip(String s) {
        if (s == null) return "";
        int from = 0, to = s.length();
        while (from < to && isSpace(s.charAt(from))) from++;
        while (to > from && isSpace(s.charAt(to - 1))) to--;
        return s.substring(from, to);
    }
}
