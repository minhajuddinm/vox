package com.minhaj.vox;

import java.math.BigInteger;
import java.text.Normalizer;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The fidelity guard: rejects a cleanup that lost or changed the speaker's words (a summary, a rewrite, a dropped clause,
 * an answer, padding, a prompt echo). Pure Java, no Android classes. Twin of fidelity_check (guard v2), word_recall and
 * word_tokens in windows/vox_core.py; the golden rows of kinds fidelity, guard, lcs, pkey, tokens and recall in
 * spec/golden.txt keep the two equal. Integer arithmetic in the decision.
 */
final class Fidelity {
    private Fidelity() {}

    /** Pure noises: may be missing from the cleaned text even in Light strength. */
    static final Set<String> NOISES = new HashSet<>(Arrays.asList("um", "uh", "er", "erm", "ah", "hmm", "hm", "uhm"));
    /**
     * The guard reads drawn-out noises the way the rules layer drops them (umm, uhh, hmmm, ahh, errm): letters only. Not
     * "err" ("to err is human"): the rules layer keeps it too.
     */
    private static final Pattern NOISE_WORD = Pattern.compile("(?:u+m+|u+h+m*|e+r(?:r*m+)?|a+h+|h+m+)");

    /** True for a pure noise word (lowercase): um, umm, uh, uhh, uhm, er, erm, ah, ahh, hm, hmm, hmmm. Twin: is_noise. */
    static boolean isNoise(String word) {
        return word != null && NOISE_WORD.matcher(word).matches();
    }
    /** Fillers and filler phrases (a space inside): not expected in the cleaned text in Standard strength. */
    static final Set<String> FILLERS = new HashSet<>(Arrays.asList(
            "um", "uh", "er", "erm", "ah", "hmm", "like", "basically", "you know", "i mean", "sort of", "kind of"));

    /**
     * Spoken commands (see the prompt): "new line", "new paragraph" and the punctuation names become breaks and symbols; one
     * counts as kept only while the cleaned text has its symbol left for it.
     */
    private static final Map<String, Character> COMMAND_PHRASES = new HashMap<>();
    private static final Map<String, Character> COMMAND_WORDS = new HashMap<>();
    private static final Set<String> CURRENCY_WORDS = new HashSet<>(Arrays.asList(
            "dollar", "dollars", "euro", "euros", "pound", "pounds", "rupee", "rupees"));
    /** "five dollars and fifty cents" = "$5.50". */
    private static final Set<String> SUBUNITS = new HashSet<>(Arrays.asList("cent", "cents", "paise", "paisa", "pence"));

    private static final Map<String, Integer> UNITS = new HashMap<>();
    private static final Map<String, Integer> TENS = new HashMap<>();
    private static final Map<String, Integer> SCALES = new HashMap<>();
    private static final Map<String, Integer> ORDINALS = new HashMap<>();
    private static final Map<String, String> SYMBOL_WORDS = new HashMap<>();

    static {
        COMMAND_PHRASES.put("new line", '\n');
        COMMAND_PHRASES.put("new paragraph", '\n');
        COMMAND_PHRASES.put("question mark", '?');
        COMMAND_WORDS.put("comma", ',');
        COMMAND_WORDS.put("period", '.');
        COMMAND_WORDS.put("colon", ':');
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
     * so the plain written date "May 3" matches "may third". "oh" or "o" between two single digits is 0 ("one oh four" =
     * "104").
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
            } else if ((t.equals("oh") || t.equals("o")) && i > 0 && i < n - 1 && singleDigit(tokens.get(i - 1))
                    && singleDigit(tokens.get(i + 1))) {
                t = "0";   // "one oh four" = "104"
                i++;
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

    private static boolean singleDigit(String t) {
        Integer u = UNITS.get(t);
        return (u != null && u <= 9) || (t.length() == 1 && t.charAt(0) >= '0' && t.charAt(0) <= '9');
    }

    /**
     * Spoken commands are not words to keep: the cleanup turns them into line breaks and punctuation. Each one is let go
     * only while cleaned has its symbol (or a line break) left for it, so "put a comma here" -> "Put a here." misses one.
     */
    private static List<String> withoutCommands(List<String> tokens, String cleaned) {
        Map<Character, Integer> left = new HashMap<>();
        for (char sym : new char[]{'\n', '?', ',', '.', ':'}) left.put(sym, count(cleaned, sym));
        List<String> out = new ArrayList<>();
        int i = 0;
        while (i < tokens.size()) {
            Character sym = i + 1 < tokens.size() ? COMMAND_PHRASES.get(tokens.get(i) + " " + tokens.get(i + 1)) : null;
            Character word = COMMAND_WORDS.get(tokens.get(i));
            if (sym != null && left.get(sym) > 0) {
                left.put(sym, left.get(sym) - 1);
                i += 2;
            } else if (word != null && left.get(word) > 0) {
                left.put(word, left.get(word) - 1);
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

    private static boolean symbolKept(String t, String cleaned, List<String> cWords) {
        return cleaned.contains(SYMBOL_WORDS.get(t)) || (t.startsWith("rupee") && cWords.contains("rs"));
    }

    private static boolean numberWord(String t) {
        return allDigits(t) || UNITS.containsKey(t) || TENS.containsKey(t);
    }

    /**
     * Positions of the "and" and the cent word of "N dollars [and] M cents" when cleaned has the currency symbol and not
     * the cent word ("$5.50"): they are part of the written amount.
     */
    private static Set<Integer> moneyWords(List<String> tokens, String cleaned, List<String> cWords) {
        Set<Integer> out = new HashSet<>();
        for (int i = 0; i < tokens.size(); i++) {
            if (!SUBUNITS.contains(tokens.get(i)) || cWords.contains(tokens.get(i))) continue;
            int j = i - 1;
            while (j >= 0 && numberWord(tokens.get(j))) j--;
            int k = j >= 0 && tokens.get(j).equals("and") ? j - 1 : j;
            if (j < i - 1 && k >= 0 && CURRENCY_WORDS.contains(tokens.get(k)) && symbolKept(tokens.get(k), cleaned, cWords)) {
                out.add(i);
                if (k != j) out.add(j);
            }
        }
        return out;
    }

    private static List<String> rawTokens(String raw, String cleaned) {
        List<String> cWords = wordTokens(cleaned);
        int ats = count(cleaned, '@');   // spoken "at" / "dot" are kept when cleaned has the symbol
        int dots = innerDots(cleaned);
        List<String> toks = withoutCommands(wordTokens(raw), cleaned);
        Set<Integer> money = moneyWords(toks, cleaned, cWords);
        List<String> r = new ArrayList<>();
        for (int i = 0; i < toks.size(); i++) {
            String t = toks.get(i);
            if (money.contains(i) || (SYMBOL_WORDS.containsKey(t) && symbolKept(t, cleaned, cWords))) continue;
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

    /** The guard's answer for one cleanup (twin of vox_core.Verdict). */
    static final class Verdict {
        /** The cleaned text may be used. */
        final boolean ok;
        /** The answer is the empty result (blank or EMPTY) and only words the strength may drop were said: type nothing. */
        final boolean empty;
        /** Why (ok, empty, finish_reason, missing 3 > 1, ...): never a dictated word, so it may be logged. */
        final String reason;

        Verdict(boolean ok, boolean empty, String reason) {
            this.ok = ok;
            this.empty = empty;
            this.reason = reason;
        }
    }

    // ------------------------------------------------------- fidelity guard v2
    // Design: D1 section 3 of the 2026-10-05 review (cleanup-quality round). Every rule is a twin of one in
    // vox_core.fidelity_check. Explicit character classes and ASCII digits, so both languages read a text the same way.

    private static final String WS = " \t\n\r\f\u000B";
    private static final String[] NO_SYM = new String[0];
    private static final Set<String> FILLER_1 = new HashSet<>();   // like, basically (Standard)
    private static final Set<String> FILLER_2 = new HashSet<>();   // you know, i mean, sort of, kind of
    /** Negations; a negative contraction counts as one ("don't" and "do not": the same count, different words). */
    private static final Set<String> NEG = set("not", "no", "never", "nothing", "none", "nobody", "nowhere", "neither",
            "nor", "without", "nahi", "nahin", "नहीं", "मत", "dont", "doesnt", "didnt", "cant",
            "cannot", "wont", "wouldnt", "shouldnt", "couldnt", "isnt", "arent", "wasnt", "werent", "havent", "hasnt",
            "hadnt", "mustnt", "neednt", "aint");
    private static final Set<String> MONTHS = set("january", "february", "march", "april", "may", "june", "july",
            "august", "september", "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
            "sep", "sept", "oct", "nov", "dec");
    private static final Set<String> WEEKDAYS = set("monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
            "sunday");
    /** Never a one-word "fix" of another word: negations, days, months, pronouns, opposites. */
    private static final Set<String> PROTECTED = set("yes", "he", "she", "they", "we", "you", "i", "him", "her", "them",
            "us", "before", "after", "more", "less", "first", "last", "left", "right");
    private static final Set<String> CONNECTORS = set("and", "but", "because", "so", "or", "then", "although", "while",
            "if");
    private static final Set<String> FREE_INS = set("a", "an", "the", "to", "of", "is", "are", "and", "it", "that", "in",
            "for", "on", "at", "i");
    /** Words so common that one of them after a weak cue says nothing about a restart (vox_core._RESTART_COMMON). */
    private static final Set<String> RESTART_COMMON = new HashSet<>(FREE_INS);
    static {
        RESTART_COMMON.addAll(Arrays.asList("we", "you", "he", "she", "they", "my", "your", "this", "so", "but", "was", "be", "will"));
    }
    private static final Map<String, Integer> SCALE_ZEROS = new HashMap<>();
    /** Spoken commands: the symbols one of them may become in the cleaned text, between its neighbouring words. */
    private static final Map<String, String[]> COMMANDS = new HashMap<>();
    private static final Set<String> LIST_CUES = set("point", "number", "item", "step", "bullet");
    /** Self-correction cues (Standard): the words just before one may be replaced by the words after it. */
    private static final Set<String> CUES_CLAUSE = set("scratch that", "forget that", "delete that", "strike that");
    private static final Set<String> CUES_2 = set("no wait", "wait no", "i mean", "i meant", "or rather", "make that",
            "nahi nahi", "no no", "sorry i");
    private static final Set<String> CUES_1 = set("actually", "sorry", "matlab", "rather");
    private static final Set<String> CUE_TAILS = set("make it", "make that", "change it", "change that", "it to",
            "that to", "lets say");
    /** One spelling for two; a contraction is not one ("don't" -> "do not" changes the speaker's words, both ways). */
    private static final Map<String, String> SPELLINGS = new HashMap<>();
    /** Pieces of the cleanup prompt: in an answer they are an echo of the instructions, unless the speaker said them. */
    private static final String[] SCAFFOLD = {"<about_speaker", "about_speaker>", "my_cleanup_rules", "spell these names",
            "never talking to you", "the text will be typed into", "examples (the output", "rules:\n", "output:\n",
            "input:", "keep fillers such as", "drop only pure noises", "you are a transcript",
            "speech-to-text transcript inside", "standard written form", "about the speaker", "terms (spell", "\nstyle:",
            "\nlayout:", "\napp:"};
    private static final String[] PREAMBLES = {"sure", "certainly", "here is", "here's", "here are", "output", "cleaned",
            "cleaned text", "cleaned transcript", "transcript", "result", "formatted text"};
    private static final Map<String, String> CUR_ABBR = new HashMap<>();
    private static final String[] INFLECT = {"s", "es", "ed", "d", "ing"};
    /** lcsPairs looks at most this many tokens (plus the length difference) off the diagonal. */
    static final int LCS_BAND = 30;
    private static final String[][] PKEY_SUBS = {{"sch", "sk"}, {"ph", "f"}, {"gh", "g"}, {"ck", "k"}, {"th", "t"},
            {"dh", "d"}, {"bh", "b"}, {"kh", "k"}, {"sh", "s"}, {"ch", "c"}, {"q", "k"}, {"x", "ks"}, {"z", "s"}, {"w", "v"}};

    static {
        for (String f : FILLERS) {
            if (f.indexOf(' ') >= 0) FILLER_2.add(f);
            else if (!NOISES.contains(f)) FILLER_1.add(f);
        }
        PROTECTED.addAll(NEG);
        PROTECTED.addAll(MONTHS);
        PROTECTED.addAll(WEEKDAYS);
        SCALE_ZEROS.put("thousand", 3);
        SCALE_ZEROS.put("lakh", 5);
        SCALE_ZEROS.put("lakhs", 5);
        SCALE_ZEROS.put("million", 6);
        SCALE_ZEROS.put("crore", 7);
        SCALE_ZEROS.put("crores", 7);
        SCALE_ZEROS.put("billion", 9);
        COMMANDS.put("new paragraph", new String[]{"\n"});
        COMMANDS.put("new line", new String[]{"\n"});
        COMMANDS.put("question mark", new String[]{"?"});
        COMMANDS.put("exclamation mark", new String[]{"!"});
        COMMANDS.put("exclamation point", new String[]{"!"});
        COMMANDS.put("full stop", new String[]{"."});
        COMMANDS.put("period", new String[]{"."});
        COMMANDS.put("comma", new String[]{","});
        COMMANDS.put("colon", new String[]{":"});
        COMMANDS.put("semicolon", new String[]{";"});
        COMMANDS.put("slash", new String[]{"/"});
        COMMANDS.put("dash", new String[]{"-", "–", "—"});
        COMMANDS.put("hyphen", new String[]{"-"});
        SPELLINGS.put("okay", "ok");
        SPELLINGS.put("alright", "all right");
        CUR_ABBR.put("rs", "₹");
        CUR_ABBR.put("inr", "₹");
        CUR_ABBR.put("usd", "$");
        CUR_ABBR.put("eur", "€");
        CUR_ABBR.put("gbp", "£");
    }

    private static Set<String> set(String... items) {
        return new HashSet<>(Arrays.asList(items));
    }

    /** A guard token: normalised text, span, kind, the symbols that stand for it, optional, digits, command words. */
    private static final class Tok {
        final String t;
        final int s;
        final int e;
        String kind;
        String[] sym = NO_SYM;
        boolean opt;
        final String num;
        int[] cue;
        String say = "";

        Tok(String t, int s, int e, String kind, String num) {
            this.t = t;
            this.s = s;
            this.e = e;
            this.kind = kind;
            this.num = num;
        }

        Tok(String t, int s, int e) {
            this(t, s, e, "word", "");
        }
    }

    /** A word of a text and where it is (start and end offsets). */
    static final class Span {
        final String t;
        final int s;
        final int e;

        Span(String t, int s, int e) {
            this.t = t;
            this.s = s;
            this.e = e;
        }
    }

    private static boolean isDigit(char c) {
        return c >= '0' && c <= '9';
    }

    /** tok cut at letter/digit boundaries ("q3" = q, 3); a digit part keeps the , . : inside it (2,500). */
    private static List<String> digitParts(String tok) {
        List<String> out = new ArrayList<>();
        int i = 0;
        int n = tok.length();
        while (i < n) {
            boolean digit = isDigit(tok.charAt(i));
            int j = i + 1;
            while (j < n) {
                char c = tok.charAt(j);
                boolean same = digit ? isDigit(c) || c == ',' || c == '.' || c == ':' : !isDigit(c);
                if (!same) break;
                j++;
            }
            out.add(tok.substring(i, j));
            i = j;
        }
        return out;
    }

    /**
     * The words of text with their spans: runs of letters, digits and marks, lowercase; an apostrophe inside a word is
     * dropped (what's = whats); , . : between two digits stay inside (2,500, 9:15, 5.50); letters and digits are split
     * (q3 = q 3) except an ordinal (3rd). Twin: vox_core.guard_split.
     */
    static List<Span> split(String text) {
        String s = text == null ? "" : text;
        List<Span> out = new ArrayList<>();
        int i = 0;
        int n = s.length();
        while (i < n) {
            int cp = s.codePointAt(i);
            if (!isWordChar(cp)) {
                i += Character.charCount(cp);
                continue;
            }
            int j = i;
            StringBuilder buf = new StringBuilder();
            while (j < n) {
                int ch = s.codePointAt(j);
                int w = Character.charCount(ch);
                if (isWordChar(ch)) {
                    buf.appendCodePoint(ch);
                } else if ((ch == '\'' || ch == 0x2019) && buf.length() > 0 && j + w < n && isWordChar(s.codePointAt(j + w))) {
                    // dropped: what's = whats
                } else if ((ch == ',' || ch == '.' || ch == ':') && buf.length() > 0 && isDigit(buf.charAt(buf.length() - 1))
                        && j + 1 < n && isDigit(s.charAt(j + 1))) {
                    buf.append((char) ch);
                } else {
                    break;
                }
                j += w;
            }
            String tok = buf.toString().toLowerCase(Locale.ROOT);
            List<String> parts = ORDINAL_SUFFIX.matcher(tok).matches() ? Arrays.asList(tok) : digitParts(tok);
            for (String part : parts) out.add(new Span(part, i, j));
            i = j;
        }
        return out;
    }

    private static boolean isNum(String t) {
        if (t.isEmpty() || !isDigit(t.charAt(0))) return false;
        for (int i = 0; i < t.length(); i++) {
            char c = t.charAt(i);
            if (!isDigit(c) && c != ',' && c != '.' && c != ':') return false;
        }
        return true;
    }

    private static String digitsOf(String t) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < t.length(); i++) if (isDigit(t.charAt(i))) sb.append(t.charAt(i));
        return sb.toString();
    }

    private static String nfkc(String text) {
        return Normalizer.normalize(text == null ? "" : text, Normalizer.Form.NFKC);
    }

    /**
     * The tokens of an NFKC text: okay = ok, alright = all right, numbers as digit strings ("2,500" = 2500, "2.5 million"
     * = 25000000, twenty five = 25, third = 3, half past three = 330, quarter past three = 315, quarter to four = 345,
     * "one oh four" = 1 0 4), "point" between numbers a decimal point, "a m" = am, and a date "3 march" or "3 of march"
     * written as "march 3" (the "of" and a "the" before it may go).
     */
    private static List<Tok> tokenize(String s) {
        List<Tok> raw = new ArrayList<>();
        for (Span sp : split(s)) {
            String exp = SPELLINGS.get(sp.t);
            if (exp == null) {
                raw.add(new Tok(sp.t, sp.s, sp.e));
            } else {
                for (String part : exp.split(" ")) raw.add(new Tok(part, sp.s, sp.e));
            }
        }
        List<String> words = new ArrayList<>();
        for (Tok t : raw) words.add(t.t);
        List<Tok> out = new ArrayList<>();
        int i = 0;
        int n = raw.size();
        while (i < n) {
            Tok t = raw.get(i);
            String w = words.get(i);
            String next = i + 1 < n ? words.get(i + 1) : null;
            Tok last = out.isEmpty() ? null : out.get(out.size() - 1);
            if (w.equals("quarter") && i + 2 < n && (next.equals("past") || next.equals("to"))) {
                Object[] nt = numberToken(words, i + 2);
                if (nt != null && allDigits((String) nt[0])) {
                    BigInteger h = new BigInteger((String) nt[0]);
                    String val = next.equals("past") ? h + "15"
                            : (h.equals(BigInteger.ONE) ? "12" : h.subtract(BigInteger.ONE).toString()) + "45";
                    int end = (Integer) nt[1];
                    out.add(new Tok(val, t.s, raw.get(end - 1).e, "number", val));
                    i = end;
                    continue;
                }
            }
            Matcher m = ORDINAL_SUFFIX.matcher(w);
            if (m.matches()) {
                out.add(new Tok(m.group(1), t.s, t.e, "number", m.group(1)));
                i++;
                continue;
            }
            if (isNum(w)) {
                String d = digitsOf(w);
                if (next != null && SCALE_ZEROS.containsKey(next)) {
                    StringBuilder z = new StringBuilder(d);
                    for (int k = 0; k < SCALE_ZEROS.get(next); k++) z.append('0');
                    d = z.toString();
                    out.add(new Tok(d, t.s, raw.get(i + 1).e, "number", d));
                    i += 2;
                    continue;
                }
                out.add(new Tok(d, t.s, t.e, "number", d));
                i++;
                continue;
            }
            Integer unit = next == null ? null : UNITS.get(next);
            if ((w.equals("oh") || w.equals("o")) && last != null && last.kind.equals("number") && last.num.length() == 1
                    && unit != null && unit < 10) {
                out.add(new Tok("0", t.s, t.e, "number", "0"));
                i++;
                continue;
            }
            if (w.equals("point") && last != null && last.kind.equals("number") && next != null
                    && (isNum(next) || UNITS.containsKey(next))) {
                out.add(new Tok("point", t.s, t.e, "decimal", ""));
                i++;
                continue;
            }
            if ((w.equals("a") || w.equals("p")) && "m".equals(next)) {
                out.add(new Tok(w + "m", t.s, raw.get(i + 1).e));
                i += 2;
                continue;
            }
            Object[] nt = null;
            if (!w.equals("a") || (next != null && (next.equals("hundred") || SCALE_ZEROS.containsKey(next)))) {
                nt = numberToken(words, i);
            }
            if (nt != null) {
                String d = ORDINAL_SUFFIX.matcher((String) nt[0]).replaceFirst("$1");
                if (allDigits(d)) {
                    int end = (Integer) nt[1];
                    out.add(new Tok(d, t.s, raw.get(end - 1).e, "number", d));
                    i = end;
                    continue;
                }
            }
            out.add(new Tok(w, t.s, t.e));
            i++;
        }
        int k = 0;
        while (k + 1 < out.size()) {
            Tok a = out.get(k);
            Tok b = out.get(k + 1);
            if (a.kind.equals("number") && a.num.length() <= 2 && MONTHS.contains(b.t)) {
                out.set(k, b);
                out.set(k + 1, a);
                k += 2;
                continue;
            }
            if (a.kind.equals("number") && a.num.length() <= 2 && b.t.equals("of") && k + 2 < out.size()
                    && MONTHS.contains(out.get(k + 2).t)) {
                b.kind = "datefill";
                out.set(k, out.get(k + 2));
                out.set(k + 1, a);
                out.set(k + 2, b);
                if (k > 0 && out.get(k - 1).t.equals("the")) out.get(k - 1).kind = "datefill";
                k += 3;
                continue;
            }
            k++;
        }
        return out;
    }

    /**
     * Kinds of the raw tokens: noises, spoken commands, symbol words (five dollars [and] fifty cents = $5.50, "to" between
     * numbers, at, dot), list cues; in Standard also fillers, immediate repeats and self-correction windows.
     */
    private static List<Tok> mark(List<Tok> toks, boolean standard) {
        int n = toks.size();
        int i = 0;
        while (i < n) {
            Tok t = toks.get(i);
            String two = i + 1 < n ? t.t + " " + toks.get(i + 1).t : null;
            if (!t.kind.equals("word")) {
                i++;
                continue;
            }
            if (isNoise(t.t)) {
                t.kind = "noise";
            } else if (two != null && COMMANDS.containsKey(two)) {
                Tok u = toks.get(i + 1);
                t.kind = u.kind = "command";
                t.sym = u.sym = COMMANDS.get(two);
                t.say = u.say = two;
                i += 2;
                continue;
            } else if (COMMANDS.containsKey(t.t)) {
                t.kind = "command";
                t.sym = COMMANDS.get(t.t);
                t.say = t.t;
            } else if (SYMBOL_WORDS.containsKey(t.t)) {
                t.kind = "symbol";
                t.sym = t.t.startsWith("rupee") ? new String[]{SYMBOL_WORDS.get(t.t), "rs"} : new String[]{SYMBOL_WORDS.get(t.t)};
                int j = i + 1 < n && toks.get(i + 1).t.equals("and") ? i + 2 : i + 1;
                if (j + 1 < n && toks.get(j).kind.equals("number") && SUBUNITS.contains(toks.get(j + 1).t)) {   // [and] fifty cents
                    for (int q = i + 1; q < j + 2; q++) {
                        if (q != j) {
                            toks.get(q).kind = "symbol";
                            toks.get(q).sym = t.sym;
                        }
                    }
                }
            } else if (t.t.equals("to") && i > 0 && i < n - 1 && toks.get(i - 1).kind.equals("number")
                    && toks.get(i + 1).kind.equals("number")) {
                t.kind = "command";
                t.sym = new String[]{"-", "–", ":"};
                t.say = "to";
            } else if (t.t.equals("at") || t.t.equals("dot")) {
                t.kind = "symbol";
                t.sym = new String[]{t.t.equals("at") ? "@" : "."};
            } else if (LIST_CUES.contains(t.t) && (t.t.equals("bullet") || (i + 1 < n && toks.get(i + 1).kind.equals("number")))) {
                t.kind = "listcue";
            } else if (standard && FILLER_1.contains(t.t)) {
                t.kind = "filler";
            } else if (standard && two != null && FILLER_2.contains(two)) {
                t.kind = toks.get(i + 1).kind = "filler";
                i += 2;
                continue;
            } else if (standard && i > 0 && toks.get(i - 1).t.equals(t.t)) {
                t.kind = "repeat";
            }
            i++;
        }
        if (standard) corrections(toks);
        return toks;
    }

    private static boolean typedValue(Tok t) {
        return t.kind.equals("number") || WEEKDAYS.contains(t.t) || MONTHS.contains(t.t);
    }

    /** True when a self-correction cue word or phrase starts at token k. */
    private static boolean isCue(List<Tok> toks, int k) {
        String two = k + 1 < toks.size() ? toks.get(k).t + " " + toks.get(k + 1).t : null;
        return (two != null && (CUES_CLAUSE.contains(two) || CUES_2.contains(two))) || CUES_1.contains(toks.get(k).t);
    }

    /**
     * Standard: a self-correction cue ("no wait", "actually", "scratch that", a bare "no" between two typed values) and up
     * to 6 tokens before it (15 for "scratch that", 3 for a bare "no") may be missing; checked later. A weak cue (actually,
     * sorry, rather, matlab, "sorry i": also everyday words) opens the window only when the words around it look like a
     * repair: a typed value before it and in the 6 tokens after it, the first word after it repeating a word of the window
     * (a restart; not a common word, RESTART_COMMON), a tail ("make it"), or another cue up to the first word after it.
     * The first two words after it repeating two words in a row of the window are a restart from there: the window then
     * starts at that run.
     */
    private static void corrections(List<Tok> toks) {
        int n = toks.size();
        for (int i = 0; i < n; i++) {
            String w = toks.get(i).t;
            String two = i + 1 < n ? w + " " + toks.get(i + 1).t : null;
            int cueLen;
            int back;
            boolean weak = false;
            if (two != null && CUES_CLAUSE.contains(two)) {
                cueLen = 2;
                back = 15;
            } else if (two != null && CUES_2.contains(two)) {
                cueLen = 2;
                back = 6;
                weak = two.equals("sorry i");
            } else if (CUES_1.contains(w)) {
                cueLen = 1;
                back = 6;
                weak = true;
            } else if (w.equals("no") && i + 1 < n && typedValue(toks.get(i + 1)) && typedBefore(toks, i)) {
                cueLen = 1;
                back = 3;
            } else {
                continue;
            }
            boolean tail = false;
            while (i + cueLen + 1 < n && CUE_TAILS.contains(toks.get(i + cueLen).t + " " + toks.get(i + cueLen + 1).t)) {
                cueLen += 2;   // "actually make it thursday", "sorry change that to friday"
                tail = true;
            }
            if (i + cueLen >= n) continue;
            int start = i;
            while (start > 0 && i - start < back && !toks.get(start - 1).kind.equals("command") && !toks.get(start - 1).opt) {
                start--;
            }
            int after = i + cueLen;
            if (weak && !tail) {
                int nxt = after;
                for (int k = after; k < n; k++) {
                    if (!toks.get(k).kind.equals("noise") && !toks.get(k).kind.equals("filler")) {
                        nxt = k;
                        break;
                    }
                }
                boolean before = false;
                boolean later = false;
                boolean restart = false;
                boolean chain = false;
                for (int k = start; k < i; k++) {
                    if (typedValue(toks.get(k))) before = true;
                    if (toks.get(k).t.equals(toks.get(nxt).t) && !RESTART_COMMON.contains(toks.get(nxt).t)) restart = true;
                }
                for (int k = after; k < Math.min(n, after + 6); k++) if (typedValue(toks.get(k))) later = true;
                for (int k = i + 1; k < Math.min(n, nxt + 1); k++) if (isCue(toks, k)) chain = true;
                if (!((before && later) || restart || chain)) {
                    // the first two words after the cue begin a run of the window ("we should take the bus actually we
                    // should walk"): a restart from that run on, so only the run may be missing ("please send" stays)
                    int run = -1;
                    for (int s = start; s < i - 1 && nxt + 1 < n; s++) {
                        if (toks.get(s).t.equals(toks.get(nxt).t) && toks.get(s + 1).t.equals(toks.get(nxt + 1).t)) {
                            run = s;
                            break;
                        }
                    }
                    if (run < 0) continue;   // an everyday "actually" / "sorry": ordinary words
                    start = run;
                }
            }
            for (int k = start; k < i + cueLen; k++) toks.get(k).opt = true;
            toks.get(i).cue = new int[]{start, i, i + cueLen};
        }
    }

    private static boolean typedBefore(List<Tok> toks, int i) {
        for (int k = Math.max(0, i - 3); k < i; k++) if (typedValue(toks.get(k))) return true;
        return false;
    }

    /**
     * The aligned {i, j} index pairs of a longest common subsequence of two token lists, ascending. The common suffix is
     * taken first (a repeated word binds to its later copy, the repair after a correction), then the common prefix; the
     * rest is a dynamic programme inside the band |i - j*N/M| <= band + |N - M| (cells outside it count as 0). Walking
     * back: a match when the tokens are equal and on an optimal path, else a step back in a when that keeps the score,
     * else in b. Twin: vox_core.lcs_pairs.
     */
    static List<int[]> lcsPairs(List<String> a, List<String> b) {
        int n = a.size();
        int m = b.size();
        int s = 0;
        while (s < n && s < m && a.get(n - 1 - s).equals(b.get(m - 1 - s))) s++;
        int p = 0;
        while (p < n - s && p < m - s && a.get(p).equals(b.get(p))) p++;
        int bigN = n - s - p;
        int bigM = m - s - p;
        int w = LCS_BAND + Math.abs(bigN - bigM);
        int[] los = new int[bigN + 1];
        int[][] rows = new int[bigN + 1][];
        rows[0] = new int[0];
        for (int i = 1; i <= bigN; i++) {
            int jc = (int) ((long) i * bigM / bigN);
            int lo = Math.max(1, jc - w);
            int hi = Math.min(bigM, jc + w);
            los[i] = lo;
            int[] cur = new int[Math.max(0, hi - lo + 1)];
            String ai = a.get(p + i - 1);
            for (int j = lo; j <= hi; j++) {
                int v;
                if (ai.equals(b.get(p + j - 1))) {
                    v = at(rows, los, i - 1, j - 1) + 1;
                } else {
                    int up = at(rows, los, i - 1, j);
                    int left = j > lo ? cur[j - lo - 1] : 0;
                    v = up >= left ? up : left;
                }
                cur[j - lo] = v;
            }
            rows[i] = cur;
        }
        List<int[]> mid = new ArrayList<>();
        int i = bigN;
        int j = bigM;
        while (i > 0 && j > 0) {
            if (a.get(p + i - 1).equals(b.get(p + j - 1)) && at(rows, los, i, j) == at(rows, los, i - 1, j - 1) + 1) {
                mid.add(new int[]{p + i - 1, p + j - 1});
                i--;
                j--;
            } else if (at(rows, los, i - 1, j) >= at(rows, los, i, j - 1)) {
                i--;
            } else {
                j--;
            }
        }
        List<int[]> out = new ArrayList<>();
        for (int k = 0; k < p; k++) out.add(new int[]{k, k});
        for (int k = mid.size() - 1; k >= 0; k--) out.add(mid.get(k));
        for (int k = 0; k < s; k++) out.add(new int[]{n - s + k, m - s + k});
        return out;
    }

    private static int at(int[][] rows, int[] los, int i, int j) {
        int k = j - los[i];
        return k >= 0 && k < rows[i].length ? rows[i][k] : 0;
    }

    private static int[] codePoints(String s) {
        int[] out = new int[s.codePointCount(0, s.length())];
        for (int i = 0, k = 0; i < s.length(); k++) {
            out[k] = s.codePointAt(i);
            i += Character.charCount(out[k]);
        }
        return out;
    }

    private static int lev(int[] a, int[] b) {
        if (Arrays.equals(a, b)) return 0;
        int[] prev = new int[b.length + 1];
        for (int j = 0; j <= b.length; j++) prev[j] = j;
        for (int i = 1; i <= a.length; i++) {
            int[] cur = new int[b.length + 1];
            cur[0] = i;
            for (int j = 1; j <= b.length; j++) {
                cur[j] = Math.min(Math.min(prev[j] + 1, cur[j - 1] + 1), prev[j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1));
            }
            prev = cur;
        }
        return prev[b.length];
    }

    /** A small phonetic key (Metaphone-like, for Indian English too: aspirates dropped, v and w the same). Twin: vox_core.pkey. */
    static String pkey(String word) {
        String low = (word == null ? "" : word).toLowerCase(Locale.ROOT);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < low.length(); i++) {
            char c = low.charAt(i);
            if (c >= 'a' && c <= 'z') sb.append(c);
        }
        String w = sb.toString();
        if (w.isEmpty()) return "";
        for (String[] xy : PKEY_SUBS) w = w.replace(xy[0], xy[1]);
        StringBuilder c = new StringBuilder();
        for (int i = 0; i < w.length(); i++) {
            char ch = w.charAt(i);
            if (ch == 'c') c.append(i + 1 < w.length() && "eiy".indexOf(w.charAt(i + 1)) >= 0 ? 's' : 'k');
            else c.append(ch);
        }
        w = c.toString();
        StringBuilder rest = new StringBuilder();
        for (int i = 1; i < w.length(); i++) {
            char ch = w.charAt(i);
            if ("aeiouyh".indexOf(ch) < 0 && (rest.length() == 0 || rest.charAt(rest.length() - 1) != ch)) rest.append(ch);
        }
        String out = ("aeiouy".indexOf(w.charAt(0)) >= 0 ? "A" : String.valueOf(Character.toUpperCase(w.charAt(0))))
                + rest.toString().toUpperCase(Locale.ROOT);
        return out.length() > 8 ? out.substring(0, 8) : out;
    }

    /** A one-word spelling or grammar fix (never of a number, negation, day, month or pronoun). */
    private static boolean similar(String x, String y, Tok rt, Tok ct) {
        if (!rt.kind.equals("word") || !ct.kind.equals("word") || PROTECTED.contains(x) || PROTECTED.contains(y)) return false;
        for (String s : INFLECT) if (x.equals(y + s) || y.equals(x + s)) return true;
        int[] a = codePoints(x);
        int[] b = codePoints(y);
        if (a.length >= 4 && b.length >= 4 && lev(a, b) <= (Math.min(a.length, b.length) >= 6 ? 2 : 1)) return true;
        return a.length >= 3 && b.length >= 3 && pkey(x).equals(pkey(y));
    }

    /** Status per raw token, status per cleaned token ("eq", "fix", "moved", "comp" or null) and the aligned pairs. */
    private static final class Alignment {
        String[] rs;
        String[] cs;
        List<int[]> pairs;
    }

    /**
     * Compounds first (tail scale = tailscale, can not = cannot, and the reverse), then lcsPairs (without the spoken
     * commands whose words the cleaned text does not have: they became symbols), then inside each gap such a command
     * equal to a cleaned word or a similar word (a fix), then equal words out of order (moved).
     */
    private static Alignment align(List<Tok> r, List<Tok> c) {
        int nr = r.size();
        int nc = c.size();
        String[] rs = new String[nr];
        String[] cs = new String[nc];
        String[] rw = new String[nr];
        String[] cw = new String[nc];
        Set<String> rset = new HashSet<>();
        Set<String> cset = new HashSet<>();
        Set<String> said = new HashSet<>();   // a command kept as words
        for (int i = 0; i < nr; i++) {
            rw[i] = r.get(i).t;
            rset.add(rw[i]);
        }
        for (int j = 0; j < nc; j++) {
            cw[j] = c.get(j).t;
            cset.add(cw[j]);
            said.add(cw[j]);
            if (j + 1 < nc) said.add(cw[j] + " " + c.get(j + 1).t);
        }
        for (int i = 0; i < nr; i++) {
            for (int k = 3; k >= 2; k--) {
                if (i + k > nr) continue;
                boolean free = true;
                for (int q = i; q < i + k; q++) if (rs[q] != null) free = false;
                if (!free) continue;
                StringBuilder sb = new StringBuilder();
                for (int q = i; q < i + k; q++) sb.append(rw[q]);
                String joined = sb.toString();
                if (cset.contains(joined) && !rset.contains(joined)) {
                    for (int q = i; q < i + k; q++) rs[q] = "comp";
                    rw[i] = joined;
                    for (int q = i + 1; q < i + k; q++) rw[q] = "\0";
                    break;
                }
            }
        }
        for (int j = 0; j < nc; j++) {
            for (int k = 3; k >= 2; k--) {
                if (j + k > nc) continue;
                boolean whole = true;
                StringBuilder sb = new StringBuilder();
                for (int q = j; q < j + k; q++) {
                    if (cw[q].equals("\0")) whole = false;
                    sb.append(cw[q]);
                }
                String joined = sb.toString();
                if (whole && rset.contains(joined) && !cset.contains(joined)) {
                    cw[j] = joined;
                    for (int q = j + 1; q < j + k; q++) {
                        cw[q] = "\0";
                        cs[q] = "comp";
                    }
                    break;
                }
            }
        }
        List<Integer> ri = new ArrayList<>();
        List<Integer> ci = new ArrayList<>();
        List<String> a = new ArrayList<>();
        List<String> b = new ArrayList<>();
        for (int i = 0; i < nr; i++) {
            if (!rw[i].equals("\0") && (!r.get(i).kind.equals("command") || said.contains(r.get(i).say))) {
                ri.add(i);
                a.add(rw[i]);
            }
        }
        for (int j = 0; j < nc; j++) {
            if (!cw[j].equals("\0")) {
                ci.add(j);
                b.add(cw[j]);
            }
        }
        List<int[]> pairs = new ArrayList<>();
        for (int[] xy : lcsPairs(a, b)) {
            int i = ri.get(xy[0]);
            int j = ci.get(xy[1]);
            if (rs[i] == null) rs[i] = "eq";
            cs[j] = "eq";
            pairs.add(new int[]{i, j});
        }
        List<int[]> bounds = new ArrayList<>();
        bounds.add(new int[]{-1, -1});
        bounds.addAll(pairs);
        bounds.add(new int[]{nr, nc});
        for (int g = 0; g + 1 < bounds.size(); g++) {
            int i0 = bounds.get(g)[0];
            int j0 = bounds.get(g)[1];
            int i1 = bounds.get(g + 1)[0];
            int j1 = bounds.get(g + 1)[1];
            Set<Integer> used = new HashSet<>();
            for (int i = i0 + 1; i < i1; i++) {
                if (rw[i].equals("\0")) continue;
                boolean cmd = r.get(i).kind.equals("command") && !said.contains(r.get(i).say);
                for (int j = j0 + 1; j < j1; j++) {
                    if (cw[j].equals("\0") || used.contains(j)) continue;
                    if (cmd ? cw[j].equals(rw[i]) : similar(rw[i], cw[j], r.get(i), c.get(j))) {
                        rs[i] = cs[j] = cmd ? "eq" : "fix";
                        used.add(j);
                        pairs.add(new int[]{i, j});
                        break;
                    }
                }
            }
        }
        Map<String, ArrayDeque<Integer>> left = new HashMap<>();
        for (int j = 0; j < nc; j++) {
            if (cs[j] == null && !cw[j].equals("\0")) {
                ArrayDeque<Integer> q = left.get(cw[j]);
                if (q == null) left.put(cw[j], q = new ArrayDeque<>());
                q.add(j);
            }
        }
        for (int i = 0; i < nr; i++) {
            ArrayDeque<Integer> q = left.get(rw[i]);
            if (rs[i] == null && !rw[i].equals("\0") && q != null && !q.isEmpty()) {
                int j = q.poll();
                rs[i] = cs[j] = "moved";
            }
        }
        for (int i = 0; i < nr; i++) if (rw[i].equals("\0")) rs[i] = "comp";
        pairs.sort((x, y) -> x[0] != y[0] ? Integer.compare(x[0], y[0]) : Integer.compare(x[1], y[1]));
        Alignment al = new Alignment();
        al.rs = rs;
        al.cs = cs;
        al.pairs = pairs;
        return al;
    }

    /**
     * The number of list-marker lines ("1." / "1)" / "-" / "*" / bullet, then a space); the start offsets of the numbered
     * markers' digits go to pos.
     */
    private static int listMarkers(String text, Set<Integer> pos) {
        int count = 0;
        int i = 0;
        int n = text.length();
        while (true) {
            int j = i;
            while (j < n && " \t\r\f\u000B".indexOf(text.charAt(j)) >= 0) j++;
            int k = j;
            while (k < n && isDigit(text.charAt(k))) k++;
            if (k > j && k + 1 < n && (text.charAt(k) == '.' || text.charAt(k) == ')') && WS.indexOf(text.charAt(k + 1)) >= 0) {
                count++;
                pos.add(j);
            } else if (k == j && j + 1 < n && "-*•".indexOf(text.charAt(j)) >= 0 && WS.indexOf(text.charAt(j + 1)) >= 0) {
                count++;
            }
            int nl = text.indexOf('\n', i);
            if (nl < 0) return count;
            i = nl + 1;
        }
    }

    /** Offsets just after each sentence end: a run of . ! ? followed by white space or the end, and each line break. */
    private static List<Integer> sentenceEnds(String text) {
        List<Integer> out = new ArrayList<>();
        int i = 0;
        int n = text.length();
        while (i < n) {
            char ch = text.charAt(i);
            if (ch == '.' || ch == '!' || ch == '?') {
                int j = i;
                while (j < n && ".!?".indexOf(text.charAt(j)) >= 0) j++;
                if (j == n || WS.indexOf(text.charAt(j)) >= 0) out.add(j);
                i = j;
            } else {
                if (ch == '\n') out.add(i + 1);
                i++;
            }
        }
        return out;
    }

    /**
     * The first word (apostrophe dropped) of a preamble such as "Sure, here is the text:" at the start of text, else null:
     * one of PREAMBLES as a whole word, then at most 40 characters without a line break or colon, then a colon.
     */
    private static String preambleWord(String text) {
        String low = text.toLowerCase(Locale.ROOT);
        for (String p : PREAMBLES) {
            if (!low.startsWith(p)) continue;
            if (low.length() > p.length()) {
                int cp = low.codePointAt(p.length());
                if (cp == '_' || isWordChar(cp)) continue;
            }
            int k = p.length();
            int count = 0;
            while (k < low.length() && count <= 40 && low.charAt(k) != '\n' && low.charAt(k) != ':') {
                k += Character.charCount(low.codePointAt(k));
                count++;
            }
            if (k < low.length() && low.charAt(k) == ':' && count <= 40) return p.split(" ")[0].replace("'", "");
        }
        return null;
    }

    /** True when every spoken word is one the strength lets the cleanup drop: pure noises, and fillers in Standard. */
    private static boolean fillerOnly(String raw, boolean standard) {
        for (Tok t : mark(tokenize(nfkc(raw)), standard)) {
            if (!t.kind.equals("noise") && !t.kind.equals("filler")) return false;
        }
        return true;
    }

    /** The cleaned text between the partners of the nearest aligned raw tokens around raw token i. */
    private static String cGap(int i, int nr, Map<Integer, Integer> rawToC, List<Tok> ct, String csrc) {
        Integer p = null;
        for (int k = i - 1; k >= 0 && p == null; k--) p = rawToC.get(k);
        Integer q = null;
        for (int k = i + 1; k < nr && q == null; k++) q = rawToC.get(k);
        int a = p != null ? ct.get(p).e : 0;
        int b = q != null ? ct.get(q).s : csrc.length();
        return a < b ? csrc.substring(a, b) : "";
    }

    private static boolean digitsFit(String cnum, List<String> nums, List<Boolean> opts) {
        int len = cnum.length();
        boolean[] states = new boolean[len + 1];
        states[0] = true;
        for (int x = 0; x < nums.size(); x++) {
            String d = nums.get(x);
            boolean[] next = new boolean[len + 1];
            boolean any = false;
            for (int p = 0; p <= len; p++) {
                if (!states[p]) continue;
                if (cnum.startsWith(d, p)) {
                    next[p + d.length()] = true;
                    any = true;
                }
                if (opts.get(x)) {
                    next[p] = true;
                    any = true;
                }
            }
            states = next;
            if (!any) return false;
        }
        return states[len];
    }

    private static Verdict reject(String reason) {
        return new Verdict(false, false, reason);
    }

    /**
     * The fidelity guard (twin of vox_core.fidelity_check): the verdict for a cleanup answer of the transcript raw. terms:
     * the dictionary's spellings (one may never go missing); repl: its "wrong => right" pairs, applied to both sides
     * first. Light keeps every word but noises, spoken commands and number/symbol formatting; Standard may also drop
     * fillers, repeats, false starts and resolve self-corrections ("thursday no wait friday" = "Friday"). Neither may
     * answer, pad, reorder, change a number or a negation, or drop content.
     */
    static Verdict check(String raw, String cleaned, String strength, String finishReason, List<String> terms,
                         Map<String, String> repl) {
        boolean standard = cleanStrength(strength).equals("standard");
        String finish = finishReason == null ? "" : finishReason.toLowerCase(Locale.ROOT);
        if (finish.equals("length") || finish.equals("content_filter")) return reject("finish_reason");
        String rawText = raw == null ? "" : raw;
        String c = cleaned == null ? "" : cleaned.trim();
        int dots = c.length();
        while (dots > 0 && c.charAt(dots - 1) == '.') dots--;
        if (c.isEmpty() || c.substring(0, dots).equals("EMPTY")) {
            return fillerOnly(rawText, standard) ? new Verdict(true, true, "empty") : reject("empty");
        }
        String low = c.toLowerCase(Locale.ROOT);
        Set<String> rawWords = new HashSet<>();
        for (Span sp : split(rawText.toLowerCase(Locale.ROOT))) rawWords.add(sp.t);
        for (String m : SCAFFOLD) {
            if (!low.contains(m)) continue;
            for (Span sp : split(m)) {
                if (!rawWords.contains(sp.t)) return reject("scaffold echo: " + m.trim());
            }
        }
        String first = preambleWord(c);
        if (first != null && !rawWords.contains(first)) return reject("preamble");
        if (5L * c.length() > 8L * rawText.length() + 200) return reject("too long");   // 1.6 times plus 40: an answer
        if (repl != null && !repl.isEmpty()) {
            rawText = ApiClient.applyReplacements(rawText, repl);
            c = ApiClient.applyReplacements(c, repl);
        }
        List<Tok> r = mark(tokenize(nfkc(rawText)), standard);
        String csrc = nfkc(c);
        List<Tok> ct = tokenize(csrc);
        Alignment al = align(r, ct);
        String[] rs = al.rs;
        String[] cs = al.cs;
        Map<Integer, Integer> rawToC = new HashMap<>();
        for (int[] p : al.pairs) rawToC.put(p[0], p[1]);
        int atLeft = count(csrc, '@');
        int dotLeft = innerDots(csrc);
        Set<Integer> markerPos = new HashSet<>();
        int markers = listMarkers(csrc, markerPos);
        boolean hasRs = false;
        for (Tok t : ct) if (t.t.equals("rs")) hasRs = true;
        int nr = r.size();
        for (Tok t : r) {   // a correction counts only when the word after the cue is kept and the words gone end at the cue
            if (t.cue == null) continue;
            int start = t.cue[0];
            int cue = t.cue[1];
            int after = t.cue[2];
            int nxt = -1;
            for (int k = after; k < nr; k++) {
                if (!r.get(k).kind.equals("noise") && !r.get(k).kind.equals("filler")) {
                    nxt = k;
                    break;
                }
            }
            boolean valid = nxt >= 0 && rs[nxt] != null;
            if (valid) {
                int gone = -1;
                for (int k = start; k < cue; k++) {
                    if (rs[k] == null) {
                        gone = k;
                        break;
                    }
                }
                if (gone >= 0) for (int k = gone; k < cue; k++) if (rs[k] != null) valid = false;
            }
            if (!valid) for (int k = start; k < after; k++) r.get(k).opt = false;
        }
        List<Integer> missing = new ArrayList<>();
        int run = 0;
        int longest = 0;
        int nReq = 0;
        for (int i = 0; i < nr; i++) {
            Tok t = r.get(i);
            boolean free = t.kind.equals("noise") || t.kind.equals("filler") || t.kind.equals("repeat")
                    || t.kind.equals("decimal") || t.kind.equals("datefill") || t.kind.equals("number");   // numbers: below
            if (t.kind.equals("command")) {
                free = false;
                if (rs[i] == null) {
                    String gap = cGap(i, nr, rawToC, ct, csrc);
                    for (String x : t.sym) if (gap.contains(x)) free = true;
                }
            } else if (t.kind.equals("symbol") && (t.t.equals("at") || t.t.equals("dot"))) {
                if (rs[i] == null && (t.t.equals("at") ? atLeft : dotLeft) > 0) {
                    if (t.t.equals("at")) atLeft--;
                    else dotLeft--;
                    free = true;
                }
            } else if (t.kind.equals("symbol")) {
                free = rs[i] == null && (csrc.contains(t.sym[0]) || (Arrays.asList(t.sym).contains("rs") && hasRs));
            } else if (t.kind.equals("listcue")) {
                free = rs[i] == null && markers > 0;
            }
            if (free || t.opt) continue;
            nReq++;
            if (rs[i] == null) {
                missing.add(i);
                run++;
                longest = Math.max(longest, run);
            } else {
                run = 0;
            }
        }
        if (standard && !missing.isEmpty()) {   // a dropped run that restarts with its own first word is a false start
            List<Integer> keep = new ArrayList<>();
            int k = 0;
            while (k < missing.size()) {
                int j = k;
                while (j + 1 < missing.size() && missing.get(j + 1) == missing.get(j) + 1) j++;
                int a = missing.get(k);
                int b = missing.get(j);
                boolean restart = b - a + 1 <= 4 && b + 1 < nr && r.get(b + 1).t.equals(r.get(a).t) && rs[b + 1] != null;
                for (int q = a; q <= b && restart; q++) if (CONNECTORS.contains(r.get(q).t)) restart = false;
                if (!restart) keep.addAll(missing.subList(k, j + 1));
                k = j + 1;
            }
            missing = keep;
            longest = 0;
            run = 0;
            int prev = -1;
            for (int i : missing) {
                boolean joined = prev >= 0;
                for (int q = prev + 1; joined && q < i; q++) {
                    Tok t = r.get(q);
                    if (!(t.kind.equals("noise") || t.kind.equals("filler") || t.kind.equals("repeat") || t.opt)) joined = false;
                }
                run = joined ? run + 1 : 1;
                longest = Math.max(longest, run);
                prev = i;
            }
        }
        Set<String> critical = new HashSet<>(WEEKDAYS);
        critical.addAll(MONTHS);
        if (terms != null) for (String x : terms) critical.add(x.toLowerCase(Locale.ROOT));
        for (int i : missing) if (critical.contains(r.get(i).t)) return reject("critical word dropped");
        Set<String> spokenCur = new HashSet<>();
        for (Tok t : r) if (SYMBOL_WORDS.containsKey(t.t)) spokenCur.add(SYMBOL_WORDS.get(t.t));
        Set<Integer> ins = new HashSet<>();
        int freeIns = 0;
        for (int j = 0; j < ct.size(); j++) {
            Tok t = ct.get(j);
            if (cs[j] != null || !t.kind.equals("word")) continue;
            if (FREE_INS.contains(t.t)) {
                freeIns++;
            } else {
                String abbr = CUR_ABBR.get(t.t);
                if (abbr == null || !spokenCur.contains(abbr)) ins.add(j);
            }
        }
        int fixes = 0;
        int moved = 0;
        for (String x : rs) {
            if ("fix".equals(x)) fixes++;
            if ("moved".equals(x)) moved++;
        }
        List<String> nums = new ArrayList<>();
        List<Boolean> opts = new ArrayList<>();
        for (Tok t : r) {
            if (t.kind.equals("number")) {
                nums.add(t.num);
                opts.add(t.opt);
            }
        }
        StringBuilder withMarkers = new StringBuilder();
        StringBuilder without = new StringBuilder();
        for (Tok t : ct) {
            if (!t.kind.equals("number")) continue;
            withMarkers.append(t.num);
            if (!markerPos.contains(t.s)) without.append(t.num);
        }
        if (!digitsFit(withMarkers.toString(), nums, opts) && !digitsFit(without.toString(), nums, opts)) {
            return reject("numbers changed");
        }
        // negations that may go: a cue's own words, or in a window whose repair says one again or that "scratch that"
        // deletes ("i do not think i mean i think" keeps its "not")
        boolean[] negFree = new boolean[nr];
        for (Tok t : r) {
            if (t.cue == null || !r.get(t.cue[1]).opt) continue;
            int start = t.cue[0];
            int cue = t.cue[1];
            int after = t.cue[2];
            boolean clause = cue + 1 < nr && CUES_CLAUSE.contains(r.get(cue).t + " " + r.get(cue + 1).t);
            boolean again = false;
            for (int k = after; k < Math.min(nr, after + 6); k++) if (NEG.contains(r.get(k).t)) again = true;
            for (int k = start; k < after; k++) if (NEG.contains(r.get(k).t) && (k >= cue || clause || again)) negFree[k] = true;
        }
        int reqNeg = 0;
        int allNeg = 0;
        int cNeg = 0;
        for (int k = 0; k < nr; k++) {
            if (NEG.contains(r.get(k).t)) {
                allNeg++;
                if (!negFree[k]) reqNeg++;
            }
        }
        for (Tok t : ct) if (NEG.contains(t.t)) cNeg++;
        if (cNeg < reqNeg || cNeg > allNeg) return reject("negation changed");
        List<Integer> ends = sentenceEnds(csrc);
        TreeMap<Integer, List<Integer>> sentences = new TreeMap<>();
        for (int j = 0; j < ct.size(); j++) {
            int s = ct.get(j).s;
            int lo = 0;
            int hi = ends.size();
            while (lo < hi) {   // the number of sentence ends at or before s (bisect_right)
                int mid = (lo + hi) >>> 1;
                if (ends.get(mid) <= s) lo = mid + 1;
                else hi = mid;
            }
            List<Integer> idx = sentences.get(lo);
            if (idx == null) sentences.put(lo, idx = new ArrayList<>());
            idx.add(j);
        }
        for (List<Integer> idx : sentences.values()) {   // a sentence that is half new words: an answer, a sign-off
            int added = 0;
            boolean hasIns = false;
            for (int j : idx) {
                if (cs[j] == null && (ct.get(j).kind.equals("word") || ct.get(j).kind.equals("number"))) added++;
                if (ins.contains(j)) hasIns = true;
            }
            if (hasIns && added * 2 >= idx.size()) return reject("added sentence");
        }
        int n = Math.max(nReq, 1);
        String[] names = {"missing", "run", "ins", "free", "fixes", "moved"};
        int[] got = {missing.size(), longest, ins.size(), freeIns, fixes, moved};
        int[] most = standard ? new int[]{1 + n / 15, 2, n / 40, 1 + n / 10, 1 + n / 10, n / 40}
                : new int[]{n / 33, 1, n / 50, 1 + n / 20, 1 + n / 12, n / 50};
        for (int k = 0; k < names.length; k++) {
            if (got[k] > most[k]) return reject(names[k] + " " + got[k] + " > " + most[k]);
        }
        return new Verdict(true, false, "ok");
    }

    /** True when the cleanup kept the spoken words: {@link #check} without a dictionary (twin of fidelity_ok). */
    static boolean ok(String raw, String cleaned, String strength) {
        return check(raw, cleaned, strength, "", null, null).ok;
    }
}
