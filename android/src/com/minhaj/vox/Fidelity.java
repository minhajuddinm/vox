package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * The fidelity guard: rejects a cleanup that lost the speaker's words (a summary, a rewrite, a dropped paragraph).
 * Pure Java, no Android classes. Twin of fidelity_ok, word_recall and word_tokens in windows/vox_core.py; the golden rows
 * of kinds fidelity, tokens and recall in spec/golden.txt keep the two equal. Integer arithmetic in the decision.
 */
final class Fidelity {
    private Fidelity() {}

    /** Pure noises: may be missing from the cleaned text even in Light strength. */
    static final Set<String> NOISES = new HashSet<>(Arrays.asList("um", "uh", "er", "erm", "ah", "hmm"));
    /** Fillers and filler phrases (a space inside): not expected in the cleaned text in Standard strength. */
    static final Set<String> FILLERS = new HashSet<>(Arrays.asList(
            "um", "uh", "er", "erm", "ah", "hmm", "like", "you know", "i mean", "sort of", "kind of"));

    /** Light: more raw words than this missing is a lost sentence, whatever the percentage (twin of LIGHT_MAX_MISSING). */
    static final int LIGHT_MAX_MISSING = 12;

    /** Spoken commands (see the prompt): "new line", "new paragraph" and the punctuation names become breaks and symbols. */
    private static final Set<String> COMMAND_PHRASES = new HashSet<>(Arrays.asList("new line", "new paragraph", "question mark"));
    private static final Set<String> COMMAND_WORDS = new HashSet<>(Arrays.asList("comma", "period", "colon"));

    private static final Map<String, Integer> UNITS = new HashMap<>();
    private static final Map<String, Integer> TENS = new HashMap<>();
    private static final Map<String, Integer> SCALES = new HashMap<>();
    private static final Map<String, Integer> ORDINALS = new HashMap<>();
    private static final Map<String, String> SYMBOL_WORDS = new HashMap<>();

    static {
        String[] units = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"};
        for (int i = 0; i < units.length; i++) UNITS.put(units[i], i);
        String[] tens = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"};
        for (int i = 0; i < tens.length; i++) TENS.put(tens[i], (i + 2) * 10);
        SCALES.put("thousand", 1000);
        SCALES.put("lakh", 100000);
        SCALES.put("million", 1000000);
        SCALES.put("crore", 10000000);
        SCALES.put("billion", 1000000000);
        String[] ordinals = {"first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
                "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
                "nineteenth", "twentieth"};
        for (int i = 0; i < ordinals.length; i++) ORDINALS.put(ordinals[i], i + 1);
        ORDINALS.put("thirtieth", 30);
        // words a symbol replaces ("five dollars" -> "$5"): they count as kept when the cleaned text has the symbol ("rupees": or "Rs")
        for (String w : new String[]{"dollar", "dollars"}) SYMBOL_WORDS.put(w, "$");
        for (String w : new String[]{"euro", "euros"}) SYMBOL_WORDS.put(w, "€");
        for (String w : new String[]{"pound", "pounds"}) SYMBOL_WORDS.put(w, "£");
        for (String w : new String[]{"rupee", "rupees"}) SYMBOL_WORDS.put(w, "₹");
        SYMBOL_WORDS.put("percent", "%");
        for (String w : new String[]{"degree", "degrees"}) SYMBOL_WORDS.put(w, "°");
    }

    /** Letters, numbers and marks (Devanagari vowel signs): the characters a word is made of. */
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

    /**
     * The words of a text: lowercase, punctuation and bullet markers dropped, apostrophes kept inside words (a curly one
     * counts as a straight one), digits kept. Numbers are not merged here (see wordRecall).
     */
    static List<String> wordTokens(String text) {
        String s = (text == null ? "" : text).toLowerCase(Locale.ROOT);
        int[] cps = new int[s.codePointCount(0, s.length())];   // no String.codePoints(): needs API 24
        for (int i = 0, k = 0; i < s.length(); k++) {
            cps[k] = s.codePointAt(i);
            i += Character.charCount(cps[k]);
        }
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        for (int i = 0; i < cps.length; i++) {
            int ch = cps[i];
            if (isWordChar(ch)) {
                cur.appendCodePoint(ch);
            } else if ((ch == '\'' || ch == 0x2019) && cur.length() > 0 && i + 1 < cps.length && isWordChar(cps[i + 1])) {
                cur.append('\'');
            } else if (cur.length() > 0) {
                out.add(cur.toString());
                cur.setLength(0);
            }
        }
        if (cur.length() > 0) out.add(cur.toString());
        return out;
    }

    private static final Pattern ORDINAL_SUFFIX = Pattern.compile("^([0-9]+)(?:st|nd|rd|th)$");

    private static boolean allDigits(String t) {
        if (t.isEmpty()) return false;
        for (int i = 0; i < t.length(); i++) {
            char c = t.charAt(i);
            if (c < '0' || c > '9') return false;
        }
        return true;
    }

    /** {value, next index} of a spoken number below a hundred at i ("twenty five", "fourteen", "six"), or null. */
    private static int[] tensUnits(List<String> tokens, int i, boolean allowZero) {
        if (i >= tokens.size()) return null;
        String t = tokens.get(i);
        if (TENS.containsKey(t)) {
            int v = TENS.get(t);
            if (i + 1 < tokens.size()) {
                Integer u = UNITS.get(tokens.get(i + 1));
                if (u != null && u >= 1 && u <= 9) return new int[]{v + u, i + 2};
            }
            return new int[]{v, i + 1};
        }
        Integer u = UNITS.get(t);
        if (u != null && (allowZero || u > 0)) return new int[]{u, i + 1};
        return null;
    }

    /** {value, next index} of a spoken number below a thousand at i: "N hundred [and] M", "a hundred", or below a hundred. */
    private static int[] hundreds(List<String> tokens, int i) {
        if (i + 1 < tokens.size() && tokens.get(i + 1).equals("hundred")) {
            String t = tokens.get(i);
            Integer u = UNITS.get(t);
            if (t.equals("a") || (u != null && u > 0)) {
                int v = 100 * (t.equals("a") ? 1 : u);
                int j = i + 2 < tokens.size() && tokens.get(i + 2).equals("and") ? i + 3 : i + 2;
                int[] rest = tensUnits(tokens, j, false);
                return rest != null ? new int[]{v + rest[0], rest[1]} : new int[]{v, i + 2};
            }
        }
        return tensUnits(tokens, i, true);
    }

    /**
     * {value, next index} of a spoken number at i: "two thousand twenty six", "one hundred and five", "a thousand",
     * "five million two hundred thousand", "two crore fifty lakh" (a smaller scale word after a larger one). The value is a
     * long: "three billion" does not fit an int.
     */
    private static long[] spokenNumber(List<String> tokens, int i) {
        int n = tokens.size();
        long total = 0;
        int k = i;
        long limit = Long.MAX_VALUE;
        while (true) {
            int j = total > 0 && k < n && tokens.get(k).equals("and") ? k + 1 : k;
            int[] g;
            if (total == 0 && j + 1 < n && tokens.get(j).equals("a") && SCALES.containsKey(tokens.get(j + 1))) {
                g = new int[]{1, j + 1};
            } else {
                g = hundreds(tokens, j);
            }
            if (g == null) break;
            j = g[1];
            Integer scale = j < n ? SCALES.get(tokens.get(j)) : null;
            if (scale != null && scale < limit) {
                total += (long) g[0] * scale;
                k = j + 1;
                limit = scale;
            } else {
                if (total == 0 || g[0] > 0) {
                    total += g[0];
                    k = j;
                }
                break;
            }
        }
        return k > i ? new long[]{total, k} : null;
    }

    /** {token, next index} for the spoken number at i, or null: "twenty five" = "25", "twenty first" = "21st", "half past three" = "330" (3:30). */
    private static Object[] numberToken(List<String> tokens, int i) {
        String t = tokens.get(i);
        int n = tokens.size();
        if (t.equals("half") && i + 2 < n && tokens.get(i + 1).equals("past")) {
            long[] num = spokenNumber(tokens, i + 2);
            return num == null ? null : new Object[]{num[0] + "30", (int) num[1]};
        }
        Integer v = ORDINALS.get(t);
        int k = i + 1;
        if (v == null && (t.equals("twenty") || t.equals("thirty")) && i + 1 < n) {
            Integer u = ORDINALS.get(tokens.get(i + 1));
            if (u != null && u < 10) {
                v = TENS.get(t) + u;
                k = i + 2;
            }
        }
        if (v != null) {
            int d = v % 10;
            String suffix = (v > 10 && v < 14) || d > 3 || d == 0 ? "th" : d == 1 ? "st" : d == 2 ? "nd" : "rd";
            return new Object[]{v + suffix, k};
        }
        long[] num = spokenNumber(tokens, i);
        return num == null ? null : new Object[]{String.valueOf(num[0]), (int) num[1]};
    }

    /**
     * Spoken numbers become digits, and runs of digit words or digit groups join into one token, so "twenty five" = "25",
     * "one hundred and five" = "105", "two thousand twenty six" = "2026", "a hundred" = "100", "five five five one two" =
     * "55512" = "555-12" and "twenty twenty six" = "2026"; "five million" = "5000000", "five lakh" = "500000"; ordinals are
     * "21st" ("twenty first"), "half past three" = "330" (3:30). "point" between two numbers is the decimal point ("three
     * point five" = "3.5" = "35") and "p m" / "a m" are "pm" / "am". An ordinal's suffix is dropped last ("21st" = "21"),
     * so the plain written date "May 3" matches "may third".
     */
    private static List<String> mergeNumbers(List<String> tokens) {
        List<String> out = new ArrayList<>();
        int i = 0;
        int n = tokens.size();
        while (i < n) {
            String t = tokens.get(i);
            Object[] num = numberToken(tokens, i);
            if (num != null) {
                t = (String) num[0];
                i = (Integer) num[1];
            } else if (t.equals("point") && !out.isEmpty() && allDigits(out.get(out.size() - 1)) && i + 1 < n
                    && (allDigits(tokens.get(i + 1)) || UNITS.containsKey(tokens.get(i + 1)))) {
                i++;
                continue;
            } else if ((t.equals("a") || t.equals("p")) && i + 1 < n && tokens.get(i + 1).equals("m")) {
                t = t + "m";
                i += 2;
            } else {
                i++;
            }
            if (allDigits(t) && !out.isEmpty() && allDigits(out.get(out.size() - 1))) {
                out.set(out.size() - 1, out.get(out.size() - 1) + t);
            } else {
                out.add(t);
            }
        }
        for (int j = 0; j < out.size(); j++) out.set(j, ORDINAL_SUFFIX.matcher(out.get(j)).replaceFirst("$1"));
        return out;
    }

    /** Spoken commands are not words to keep: the cleanup turns them into line breaks and punctuation. */
    private static List<String> withoutCommands(List<String> tokens) {
        List<String> out = new ArrayList<>();
        int i = 0;
        while (i < tokens.size()) {
            if (i + 1 < tokens.size() && COMMAND_PHRASES.contains(tokens.get(i) + " " + tokens.get(i + 1))) {
                i += 2;
            } else if (COMMAND_WORDS.contains(tokens.get(i))) {
                i++;
            } else {
                out.add(tokens.get(i));
                i++;
            }
        }
        return out;
    }

    /** How many dots in text sit between two word characters (gmail.com, 3.5): not a full stop. */
    private static int innerDots(String text) {
        int n = 0;
        int prev = -1;
        int i = 0;
        while (i < text.length()) {
            int cp = text.codePointAt(i);
            int next = i + Character.charCount(cp);
            if (cp == '.' && prev >= 0 && next < text.length() && isWordChar(prev) && isWordChar(text.codePointAt(next))) n++;
            prev = cp;
            i = next;
        }
        return n;
    }

    private static int count(String text, char c) {
        int n = 0;
        for (int i = 0; i < text.length(); i++) if (text.charAt(i) == c) n++;
        return n;
    }

    private static List<String> rawTokens(String raw, String cleaned) {
        int ats = count(cleaned, '@');   // spoken "at" / "dot" are kept when cleaned has the symbol
        int dots = innerDots(cleaned);
        List<String> r = new ArrayList<>();
        for (String t : withoutCommands(wordTokens(raw))) {
            String sym = SYMBOL_WORDS.get(t);
            if (sym != null && (cleaned.contains(sym) || (t.startsWith("rupee") && wordTokens(cleaned).contains("rs")))) continue;
            if (t.equals("at") && ats > 0) {
                ats--;
            } else if (t.equals("dot") && dots > 0) {
                dots--;
            } else {
                r.add(t);
            }
        }
        return mergeNumbers(r);
    }

    /** Tokens the cleanup may remove: pure noises always; in Standard also fillers, filler phrases and immediate repeats. */
    private static List<String> dropFillers(List<String> tokens, boolean standard) {
        List<String> out = new ArrayList<>();
        int i = 0;
        while (i < tokens.size()) {
            String t = tokens.get(i);
            if (NOISES.contains(t)) {
                i++;
            } else if (standard && i + 1 < tokens.size() && FILLERS.contains(t + " " + tokens.get(i + 1))) {
                i += 2;
            } else if (standard && (FILLERS.contains(t) || (!out.isEmpty() && out.get(out.size() - 1).equals(t)))) {
                i++;
            } else {
                out.add(t);
                i++;
            }
        }
        return out;
    }

    /** How many tokens of r are in c, counting each token of c once. */
    private static int matched(List<String> r, List<String> c) {
        Map<String, Integer> counts = new HashMap<>();
        for (String t : c) {
            Integer n = counts.get(t);
            counts.put(t, n == null ? 1 : n + 1);
        }
        int n = 0;
        for (String t : r) {
            Integer k = counts.get(t);
            if (k != null && k > 0) {
                counts.put(t, k - 1);
                n++;
            }
        }
        return n;
    }

    /** The share (0..1) of raw's words still in cleaned, order ignored, repeats counted; 1.0 when raw has no words. */
    static double wordRecall(String raw, String cleaned) {
        String c = cleaned == null ? "" : cleaned;
        List<String> r = rawTokens(raw, c);
        if (r.isEmpty()) return 1.0;
        return (double) matched(r, mergeNumbers(wordTokens(c))) / r.size();
    }

    /** The "Cleanup strength" setting as "light" or "standard"; unset or anything else is "light". Twin: clean_strength in windows/vox_core.py. */
    static String cleanStrength(String value) {
        return value != null && value.trim().toLowerCase(Locale.ROOT).equals("standard") ? "standard" : "light";
    }

    /**
     * True when the cleanup kept enough of the spoken words. Light (anything but "standard"): only pure noises (um, uh,
     * er...) may be missing; at least 97% of the words must be there, at most {@link #LIGHT_MAX_MISSING} may be missing
     * in total (97% of a long dictation is a whole paragraph) and the text must not be shorter than 90% of the words minus one. Standard: fillers, filler phrases and immediate repeats are not expected; 85% of the rest must be
     * there and the text at least 60% as long. Under four words the length rule is skipped.
     */
    static boolean ok(String raw, String cleaned, String strength) {
        if (cleaned == null || cleaned.trim().isEmpty()) return false;
        boolean standard = cleanStrength(strength).equals("standard");
        List<String> r = dropFillers(rawTokens(raw, cleaned), standard);
        List<String> c = mergeNumbers(wordTokens(cleaned));
        int kept = matched(r, c);
        if ((long) kept * 100 < (long) (standard ? 85 : 97) * r.size()) return false;
        if (!standard && r.size() - kept > LIGHT_MAX_MISSING) return false;
        if (r.size() < 4) return true;
        return standard ? c.size() * 10 >= 6 * r.size() : c.size() * 10 + 10 >= 9 * r.size();
    }
}
