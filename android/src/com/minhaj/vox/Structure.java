package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Lists from spoken cues: the twin of format_structure in windows/structure.py (golden rows "structure"). A list is made
 * only from cues the speaker says, never from commas: ordinals in sequence (first, second; firstly; first of all), introduced
 * numbers (point/item/step/number one, then two or point two), Hindi and Hinglish ordinals (pehla, doosra, teesra, also in
 * Devanagari) and bullet cues (bullet, bullet point, new bullet, next bullet, next point, next item). The cue words are
 * removed and every other word is kept, in order. Pure Java (no android.*), so the off-device tests run it. The paragraph
 * breaks at pauses of the Windows app need segment times, which this app does not ask for.
 */
final class Structure {
    private Structure() { }

    static final String OFF = "off", AUTO = "auto", LISTS = "lists";

    private static final Map<String, Integer> ORD = new HashMap<>();
    private static final Map<String, Integer> HINDI = new HashMap<>();
    private static final Map<String, Integer> NUMBERS = new HashMap<>();
    private static final Set<String> INTROS = new HashSet<>(Arrays.asList("point", "item", "step", "number"));
    private static final Set<String> BE = new HashSet<>(Arrays.asList("are", "is", "were"));
    private static final String[][] BULLETS_ANYWHERE = {{"bullet", "point"}, {"new", "bullet"}, {"next", "bullet"}};
    private static final String[][] BULLETS_AT_CLAUSE = {{"next", "point"}, {"next", "item"}};
    private static final String CLAUSE_PUNCT = ".,;:!?\u0964";   // \u0964 = the Devanagari danda
    private static final String SENTENCE_PUNCT = ".!?\u0964";
    private static final String SPACE = " \t\r\n";
    private static final String LEAD_STRIP = SPACE + ",.;:-\u2013\u2014\u0964";
    private static final int ANYWHERE = 0, CLAUSE = 1, SENTENCE = 2;

    static {
        String[] ord = {"first firstly", "second secondly", "third thirdly", "fourth fourthly", "fifth fifthly",
            "sixth sixthly", "seventh", "eighth", "ninth", "tenth"};
        for (int n = 1; n <= ord.length; n++) for (String w : ord[n - 1].split(" ")) ORD.put(w, n);
        String[] hindi = {
            "pehla pehli pahla pahli \u092a\u0939\u0932\u093e \u092a\u0939\u0932\u0940",
            "doosra doosri dusra dusri \u0926\u0942\u0938\u0930\u093e \u0926\u0942\u0938\u0930\u0940",
            "teesra teesri tisra tisri \u0924\u0940\u0938\u0930\u093e \u0924\u0940\u0938\u0930\u0940",
            "chautha chauthi \u091a\u094c\u0925\u093e \u091a\u094c\u0925\u0940",
            "paanchva paanchvan panchva \u092a\u093e\u0901\u091a\u0935\u093e\u0901 \u092a\u093e\u0902\u091a\u0935\u093e\u0902"};
        for (int n = 1; n <= hindi.length; n++) for (String w : hindi[n - 1].split(" ")) HINDI.put(w, n);
        String[] nums = "one two three four five six seven eight nine ten".split(" ");
        for (int n = 1; n <= nums.length; n++) {
            NUMBERS.put(nums[n - 1], n);
            NUMBERS.put(String.valueOf(n), n);
        }
    }

    private static final Pattern ALREADY_LIST = Pattern.compile("(?md)^[ \\t]*(?:[-*\u2022]|[0-9]+[.)])[ \\t]+[^ \\t\\r\\n]");
    private static final Pattern SENTENCE_INSIDE = Pattern.compile("[.!?।][ \\t\\r\\n]");
    private static final Pattern NEWLINES =Pattern.compile("[ \\t\\r\\n]*\\n[ \\t\\r\\n]*");
    private static final Pattern SENTENCE_END = Pattern.compile("[.!?\u0964][\"'\u201d\u2019)\\]]*[ \\t\\r\\n]+(?=[^ \\t\\r\\n])");

    /** The "Lists and paragraphs" setting as off, auto or lists; unset or anything else is auto (twin of structure_mode). */
    static String mode(String value) {
        String v = value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
        return v.equals(OFF) || v.equals(LISTS) ? v : AUTO;
    }

    private static boolean isWordChar(int cp) {
        switch (Character.getType(cp)) {
            case Character.UPPERCASE_LETTER:
            case Character.LOWERCASE_LETTER:
            case Character.TITLECASE_LETTER:
            case Character.MODIFIER_LETTER:
            case Character.OTHER_LETTER:
            case Character.DECIMAL_DIGIT_NUMBER:
            case Character.LETTER_NUMBER:
            case Character.OTHER_NUMBER:
            case Character.NON_SPACING_MARK:
            case Character.COMBINING_SPACING_MARK:
            case Character.ENCLOSING_MARK:
                return true;
            default:
                return false;
        }
    }

    /** One word of the text: lowercase, and where it starts and ends. */
    private static final class Word {
        final String w;
        final int start, end;
        Word(String w, int start, int end) { this.w = w; this.start = start; this.end = end; }
    }

    /** A cue: its family (bul, num, bare, ord, hi), number, and where it starts and ends in the text. */
    private static final class Cue {
        final String fam;
        final int n, start, end;
        Cue(String fam, int n, int start, int end) { this.fam = fam; this.n = n; this.start = start; this.end = end; }
    }

    private static List<Word> words(String text) {
        List<Word> out = new ArrayList<>();
        int i = 0, n = text.length();
        while (i < n) {
            int cp = text.codePointAt(i);
            if (!isWordChar(cp)) {
                i += Character.charCount(cp);
                continue;
            }
            int j = i;
            while (j < n) {
                int c = text.codePointAt(j);
                int k = j + Character.charCount(c);
                if (isWordChar(c) || ((c == '\'' || c == 0x2019) && k < n && isWordChar(text.codePointAt(k)))) j = k;
                else break;
            }
            out.add(new Word(text.substring(i, j).toLowerCase(Locale.ROOT), i, j));
            i = j;
        }
        return out;
    }

    private static boolean onlySpace(String s) {
        for (int i = 0; i < s.length(); i++) if (SPACE.indexOf(s.charAt(i)) < 0) return false;
        return true;
    }

    private static boolean starts(String text, int pos, String marks) {
        int i = pos - 1;
        while (i >= 0 && SPACE.indexOf(text.charAt(i)) >= 0) {
            if (text.charAt(i) == '\n') return true;
            i--;
        }
        return i < 0 || marks.indexOf(text.charAt(i)) >= 0;
    }

    private static boolean afterBe(String text, List<Word> ws, int i) {
        return i > 0 && BE.contains(ws.get(i - 1).w) && onlySpace(text.substring(ws.get(i - 1).end, ws.get(i).start));
    }

    private static boolean pair(String[][] pairs, String a, String b) {
        for (String[] p : pairs) if (p[0].equals(a) && p[1].equals(b)) return true;
        return false;
    }

    /** {family index, number, index after the last word, where}; family index: 0 bul, 1 num, 2 bare, 3 ord, 4 hi. null = no cue. */
    private static int[] match(String text, List<Word> ws, int i) {
        String w = ws.get(i).w;
        String nxt = i + 1 < ws.size() && onlySpace(text.substring(ws.get(i).end, ws.get(i + 1).start)) ? ws.get(i + 1).w : null;
        if (nxt != null && pair(BULLETS_ANYWHERE, w, nxt)) return new int[]{0, 0, i + 2, ANYWHERE};
        if (nxt != null && pair(BULLETS_AT_CLAUSE, w, nxt)) return new int[]{0, 0, i + 2, CLAUSE};
        if (w.equals("bullet")) return new int[]{0, 0, i + 1, CLAUSE};
        if (INTROS.contains(w) && nxt != null && NUMBERS.containsKey(nxt)) return new int[]{1, NUMBERS.get(nxt), i + 2, CLAUSE};
        if (w.equals("first") && "of".equals(nxt) && i + 2 < ws.size() && ws.get(i + 2).w.equals("all")
                && onlySpace(text.substring(ws.get(i + 1).end, ws.get(i + 2).start))) return new int[]{3, 1, i + 3, CLAUSE};
        if (ORD.containsKey(w)) return new int[]{3, ORD.get(w), i + 1, CLAUSE};
        if (HINDI.containsKey(w)) return new int[]{4, HINDI.get(w), "point".equals(nxt) ? i + 2 : i + 1, CLAUSE};
        if (NUMBERS.containsKey(w) && NUMBERS.get(w) >= 2) return new int[]{2, NUMBERS.get(w), i + 1, SENTENCE};
        return null;
    }

    private static final String[] FAMILIES = {"bul", "num", "bare", "ord", "hi"};

    private static List<Cue> cues(String text, List<Word> ws) {
        List<Cue> out = new ArrayList<>();
        int i = 0;
        while (i < ws.size()) {
            int[] m = match(text, ws, i);
            if (m != null) {
                String fam = FAMILIES[m[0]];
                boolean ok = m[3] == ANYWHERE || starts(text, ws.get(i).start, m[3] == SENTENCE ? SENTENCE_PUNCT : CLAUSE_PUNCT)
                        || (m[1] == 1 && (fam.equals("ord") || fam.equals("num")) && afterBe(text, ws, i));
                if (ok) {
                    out.add(new Cue(fam, m[1], ws.get(i).start, ws.get(m[2] - 1).end));
                    i = m[2];
                    continue;
                }
            }
            i++;
        }
        return out;
    }

    private static List<Cue> sequence(List<Cue> cues, boolean flat) {
        for (int k = 0; k < cues.size(); k++) {
            Cue c = cues.get(k);
            if (c.fam.equals("bare") || (!c.fam.equals("bul") && c.n != 1) || (flat && (c.fam.equals("ord") || c.fam.equals("hi")))) continue;
            List<Cue> chosen = new ArrayList<>();
            chosen.add(c);
            int expected = 2;
            for (Cue d : cues.subList(k + 1, cues.size())) {
                if (c.fam.equals("bul")) {
                    if (d.fam.equals("bul")) chosen.add(d);
                } else if ((d.fam.equals(c.fam) || (c.fam.equals("num") && d.fam.equals("bare"))) && d.n == expected) {
                    chosen.add(d);
                    expected++;
                }
            }
            if (chosen.size() >= 2) return chosen;
        }
        return null;
    }

    private static String lstrip(String s, String chars) {
        int a = 0;
        while (a < s.length() && chars.indexOf(s.charAt(a)) >= 0) a++;
        return s.substring(a);
    }

    private static String rstrip(String s, String chars) {
        int b = s.length();
        while (b > 0 && chars.indexOf(s.charAt(b - 1)) >= 0) b--;
        return s.substring(0, b);
    }

    /** The item with a capital first letter when its first word is all lowercase (an iPhone stays an iPhone). */
    private static String capitalised(String item) {
        int j = 0;
        while (j < item.length() && isWordChar(item.codePointAt(j))) j += Character.charCount(item.codePointAt(j));
        if (j == 0) return item;
        if (j < item.length() && " \t,;:!?".indexOf(item.charAt(j)) < 0) return item;   // a word glued to more (me@example.com, node.js) keeps its case
        int first = item.codePointAt(0);
        if (!Character.isLowerCase(first)) return item;
        for (int i = 0; i < j; ) {
            int cp = item.codePointAt(i);
            if (Character.isUpperCase(cp)) return item;
            i += Character.charCount(cp);
        }
        int len = Character.charCount(first);
        return item.substring(0, len).toUpperCase(Locale.ROOT) + item.substring(len);
    }

    private static String cleanItem(String body, boolean flatCase) {
        String s = rstrip(lstrip(NEWLINES.matcher(body).replaceAll(" "), LEAD_STRIP), SPACE + ",;:");
        if ((s.endsWith(".") || s.endsWith("\u0964")) && !SENTENCE_INSIDE.matcher(s.substring(0, s.length() - 1)).find()) {
            s = rstrip(s.substring(0, s.length() - 1), SPACE);   // one sentence: its full stop goes (the dot of example.com is not a sentence end)
        }
        return flatCase ? s : capitalised(s);
    }

    /**
     * Text with a spoken list written as one: the lead-in, then one item per line ("1. " after ordinals and numbers, "- "
     * after bullet cues), then the rest of the text after a blank line. Off, the raw style, text that already has list
     * markers and text without two cues in sequence come back unchanged; running it twice changes nothing. Casual and very
     * casual styles take only the explicit cues (bullets and point/item/step/number one).
     */
    static String format(String text, String mode, String style) {
        String st = style == null ? "" : style.trim().toLowerCase(Locale.ROOT);
        if (text == null || text.isEmpty() || mode(mode).equals(OFF) || st.equals("raw") || ALREADY_LIST.matcher(text).find()) {
            return text;
        }
        List<Cue> seq = sequence(cues(text, words(text)), st.equals("casual") || st.equals("very_casual"));
        if (seq == null) return text;
        List<String> bodies = new ArrayList<>();
        for (int k = 0; k + 1 < seq.size(); k++) bodies.add(text.substring(seq.get(k).end, seq.get(k + 1).start));
        String last = lstrip(text.substring(seq.get(seq.size() - 1).end), LEAD_STRIP);
        String trailer = "";
        Matcher m = SENTENCE_END.matcher(last);
        if (m.find()) {
            int cut = rstrip(last.substring(0, m.end()), SPACE).length();
            trailer = rstrip(lstrip(last.substring(m.end()), SPACE), SPACE);
            last = last.substring(0, cut);
        }
        bodies.add(last);
        StringBuilder out = new StringBuilder();
        String lead = rstrip(rstrip(lstrip(text.substring(0, seq.get(0).start), SPACE), SPACE), SPACE + ",;-\u2013\u2014");
        if (!lead.isEmpty()) {
            out.append(lead);
            if (".!?:\u0964".indexOf(lead.charAt(lead.length() - 1)) < 0) out.append(':');
        }
        boolean bullets = seq.get(0).fam.equals("bul");
        for (int k = 0; k < bodies.size(); k++) {
            String item = cleanItem(bodies.get(k), st.equals("very_casual"));
            if (item.isEmpty()) return text;
            if (out.length() > 0) out.append('\n');
            out.append(bullets ? "- " : (k + 1) + ". ").append(item);
        }
        if (!trailer.isEmpty()) out.append("\n\n").append(trailer);
        return out.toString();
    }
}
