package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

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

    /** Spoken commands (see the prompt): "new line", "new paragraph" and the punctuation names become breaks and symbols. */
    private static final Set<String> COMMAND_PHRASES = new HashSet<>(Arrays.asList("new line", "new paragraph", "question mark"));
    private static final Set<String> COMMAND_WORDS = new HashSet<>(Arrays.asList("comma", "period", "colon"));

    private static final Map<String, Integer> UNITS = new HashMap<>();
    private static final Map<String, Integer> TENS = new HashMap<>();
    private static final Map<String, String> SYMBOL_WORDS = new HashMap<>();

    static {
        String[] units = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"};
        for (int i = 0; i < units.length; i++) UNITS.put(units[i], i);
        String[] tens = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"};
        for (int i = 0; i < tens.length; i++) TENS.put(tens[i], (i + 2) * 10);
        // words a symbol replaces ("five dollars" -> "$5"): they count as kept when the cleaned text has the symbol
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

    private static boolean allDigits(String t) {
        if (t.isEmpty()) return false;
        for (int i = 0; i < t.length(); i++) {
            char c = t.charAt(i);
            if (c < '0' || c > '9') return false;
        }
        return true;
    }

    /**
     * Spoken numbers (zero to a hundred) become digits, and runs of digit words or digit groups join into one token, so
     * "twenty five" = "25", "five five five one two" = "55512" = "555-12" and "twenty twenty six" = "2026".
     */
    private static List<String> mergeNumbers(List<String> tokens) {
        List<String> out = new ArrayList<>();
        int i = 0;
        while (i < tokens.size()) {
            String t = tokens.get(i);
            if (TENS.containsKey(t)) {
                int v = TENS.get(t);
                if (i + 1 < tokens.size()) {
                    Integer u = UNITS.get(tokens.get(i + 1));
                    if (u != null && u >= 1 && u <= 9) {
                        v += u;
                        i++;
                    }
                }
                t = String.valueOf(v);
            } else if (UNITS.containsKey(t)) {
                t = String.valueOf(UNITS.get(t));
            } else if (t.equals("hundred") && !out.isEmpty() && out.get(out.size() - 1).equals("1")) {
                out.set(out.size() - 1, "100");
                i++;
                continue;
            }
            if (allDigits(t) && !out.isEmpty() && allDigits(out.get(out.size() - 1))) {
                out.set(out.size() - 1, out.get(out.size() - 1) + t);
            } else {
                out.add(t);
            }
            i++;
        }
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

    private static List<String> rawTokens(String raw, String cleaned) {
        List<String> r = new ArrayList<>();
        for (String t : withoutCommands(wordTokens(raw))) {
            String sym = SYMBOL_WORDS.get(t);
            if (sym != null && cleaned.contains(sym)) continue;
            r.add(t);
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

    /**
     * True when the cleanup kept enough of the spoken words. Light (anything but "standard"): only pure noises (um, uh,
     * er...) may be missing; at least 97% of the words must be there and the text must not be shorter than 90% of the
     * words minus one. Standard: fillers, filler phrases and immediate repeats are not expected; 85% of the rest must be
     * there and the text at least 60% as long. Under four words the length rule is skipped.
     */
    static boolean ok(String raw, String cleaned, String strength) {
        if (cleaned == null || cleaned.trim().isEmpty()) return false;
        boolean standard = strength != null && strength.trim().toLowerCase(Locale.ROOT).equals("standard");
        List<String> r = dropFillers(rawTokens(raw, cleaned), standard);
        List<String> c = mergeNumbers(wordTokens(cleaned));
        if ((long) matched(r, c) * 100 < (long) (standard ? 85 : 97) * r.size()) return false;
        if (r.size() < 4) return true;
        return standard ? c.size() * 10 >= 6 * r.size() : c.size() * 10 + 10 >= 9 * r.size();
    }
}
