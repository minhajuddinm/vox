package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Reads the dictionary text (one entry per line, "wrong => right" for replacements). Same rules as
 * dictionary_terms and replacements in windows/vox_core.py; spec/golden.txt keeps the two in step.
 */
final class Terms {
    private Terms() { }

    /** Names and terms the speech and cleanup models should spell correctly, without duplicates. */
    static List<String> terms(String peopleRaw, String dictionaryRaw) {
        List<String> out = new ArrayList<>();
        for (String line : peopleRaw.split("\n")) {
            String l = ApiClient.pyStrip(line);   // Python's strip(): also no-break spaces
            if (!l.isEmpty() && !l.startsWith("#")) add(out, l);
        }
        for (String line : dictionaryRaw.split("\n")) {
            String l = ApiClient.pyStrip(line);   // Python's strip(): also no-break spaces
            if (l.isEmpty() || l.startsWith("#")) continue;
            if (l.contains("=>")) {
                String right = ApiClient.pyStrip(l.substring(l.indexOf("=>") + 2));
                if (!right.isEmpty()) add(out, right);
            } else {
                add(out, l);
            }
        }
        return out;
    }

    /** Forced replacements from lines of the form "wrong => right". A later line for the same word wins. */
    static Map<String, String> replacements(String dictionaryRaw) {
        Map<String, String> out = new LinkedHashMap<>();
        for (String line : dictionaryRaw.split("\n")) {
            String l = ApiClient.pyStrip(line);   // Python's strip(): also no-break spaces
            if (l.startsWith("#") || !l.contains("=>")) continue;
            String wrong = ApiClient.pyStrip(l.substring(0, l.indexOf("=>")));
            String right = ApiClient.pyStrip(l.substring(l.indexOf("=>") + 2));
            if (!wrong.isEmpty()) out.put(wrong, right);
        }
        return out;
    }

    /** Shortest term the fuzzy pass works on (and the shortest word it changes). */
    static final int FUZZY_MIN_LEN = 5;

    /** Shortest term that also fixes a spelling one letter off (shorter names sit next to real words: Alice, alike). */
    static final int FUZZY_NEAR_MIN_LEN = 7;

    /** Ordinary English words the fuzzy pass never touches (the same list as COMMON_WORDS in windows/vox_core.py). */
    private static final String COMMON =
        "about above after again agree alone along already always among another answer anyone anything around " +
        "asked asking based basic beach because become before began begin being below better between black " +
        "blank board bring broke brown build built bunch cause chain chair change charge check child choice " +
        "class clean clear click clock close cloud coffee color could count cover crash cross daily dance " +
        "dates delay doing doubt dozen draft drive early earth eight email empty enjoy enough entire equal " +
        "error event every exact extra faces fault field fifth final first fixed flash floor focus force " +
        "found frame fresh front fruit funny given glass going grace grand grant great green group guess " +
        "guide happy heard heart heavy hello house human ideas image issue items large later laugh layer " +
        "learn least leave level light likely limit local logic looks lower lunch maybe means might money " +
        "month mouse mouth movie music needs never night noise north noted notes novel number offer often " +
        "older order other paper party peace phone piece place plain plane plant point power press price " +
        "pride print prior prize proof proud quick quiet quite radio raise range rapid reach ready right " +
        "rough round route royal salad sales scale scene score sense serve seven shall shape share sharp " +
        "sheet shift short shown sight simple since sleep slice slide small smart smile solid solve sorry " +
        "sound south space speak speed spend split spoke sport stack staff stage stand start state still " +
        "stock stone stood store storm story study stuff style sugar super sweet table taken taste teach " +
        "thank their theme there these thing think third those three threw throw tight times title today " +
        "token total touch tough tower track trade train treat trend trial tried truck truly trust truth " +
        "twice under union until upper urban usage usual value video visit voice waste watch water wheel " +
        "where which while white whole whose woman women world worry worse worth would write wrong yield " +
        "young yours " +
        "acute adapt admit adopt adult agent alarm album alert alive allow alter ample angle angry apart " +
        "apply argue arise aware awful basis begun bible blame blind block blood bonus boost brain " +
        "brand bread break brief broad brush buyer cable carry catch cease chart chase cheap chief civil " +
        "claim clause clauses climb coach curve cycle dealt decker depth dirty docket drama dream dress drink " +
        "drove eager enter essay exist fancy fiber fight flame flank fleet flesh fluid frank fraud fully " +
        "giant glory goggle guard guest guilty habit handy harsh hence hotel humor ideal imply index " +
        "inner input intro jelly joint judge knife known label lemon linux loose lotion lucky magic " +
        "major march match mayor media metal minor minus mixed model motel motion nation noble nurse " +
        "occur ocean opera outer owner panic pause phase photo pilot pitch pixel plate plenty polite potion " +
        "pound prime queen quest quote rally reply rider rival robot rocker rocky rural scope serum shack " +
        "shade shark shelf shell shine shirt shock shoot skill sleek slick slicker slope smoke snack snake " +
        "solar spare spark spell spice spine spite sprint steam steel steep stern stick stiff stove strap " +
        "straw stride strike strip strive stroke strong swing sword teeth tenth thick thumb " +
        "tiger tired toast toggle topic torch trace tribe trick trunk tutor twist ultra uncle unite unity " +
        "upset vague valid vital vowel wagon weird whale wheat wider wound wrist youth ";
    private static final Set<String> COMMON_WORDS = new HashSet<>(Arrays.asList(COMMON.trim().split(" ")));

    /** True for a lowercase word on the COMMON list (AutoLearn's ordinary words use it too). */
    static boolean isCommonWord(String lower) {
        return COMMON_WORDS.contains(lower);
    }
    private static final Pattern WORD = Pattern.compile(ApiClient.WORD_CHAR + "+");   // with its combining marks

    private static final String ADDRESS_GLUE = ".@/\\";

    private static boolean wordOrMark(int cp) {
        if (cp == '_') return true;
        switch (Character.getType(cp)) {
            case Character.UPPERCASE_LETTER: case Character.LOWERCASE_LETTER: case Character.TITLECASE_LETTER:
            case Character.MODIFIER_LETTER: case Character.OTHER_LETTER:
            case Character.NON_SPACING_MARK: case Character.ENCLOSING_MARK: case Character.COMBINING_SPACING_MARK:
            case Character.DECIMAL_DIGIT_NUMBER: case Character.LETTER_NUMBER: case Character.OTHER_NUMBER:
                return true;
            default:
                return false;
        }
    }

    /**
     * True when text[start, end) is joined to another word by . @ / or a backslash on either side (an email, a web or file
     * address, code such as ai.predict): a dictionary spelling never changes it. Twin of in_address in windows/vox_core.py.
     */
    static boolean inAddress(String text, int start, int end) {
        return (start >= 2 && ADDRESS_GLUE.indexOf(text.charAt(start - 1)) >= 0 && wordOrMark(text.codePointBefore(start - 1)))
                || (end + 1 < text.length() && ADDRESS_GLUE.indexOf(text.charAt(end)) >= 0 && wordOrMark(text.codePointAt(end + 1)));
    }

    /**
     * Puts the dictionary's spelling on words that are the same word in another case or one letter off. Same rules as
     * fuzzy_dictionary in windows/vox_core.py: only terms that are one word of five letters or more take part; a word of
     * that length is changed when it equals a term ignoring case, or, for a term of seven letters or more only, is one
     * edit from exactly one such term with the same first letter; never an ordinary English word or one with a digit or
     * underscore. spec/golden.txt (fuzzydict) keeps the two in step.
     */
    static String fuzzy(String text, List<String> terms) {
        Map<String, String> byLower = new LinkedHashMap<>();
        for (String t : terms) {
            t = ApiClient.pyStrip(t);
            if (t.length() >= FUZZY_MIN_LEN && isAlpha(t)) byLower.putIfAbsent(t.toLowerCase(Locale.ROOT), t);
        }
        if (byLower.isEmpty() || text.isEmpty()) return text;
        Matcher m = WORD.matcher(text);
        StringBuffer sb = new StringBuffer();
        while (m.find()) {
            String w = m.group();
            m.appendReplacement(sb, Matcher.quoteReplacement(inAddress(text, m.start(), m.end()) ? w : fixWord(w, byLower)));
        }
        return m.appendTail(sb).toString();
    }

    private static String fixWord(String w, Map<String, String> byLower) {
        String lw = w.toLowerCase(Locale.ROOT);
        if (w.length() < FUZZY_MIN_LEN || !isAlpha(w) || COMMON_WORDS.contains(lw)) return w;
        String exact = byLower.get(lw);
        if (exact != null) return exact;
        Set<String> near = new HashSet<>();
        for (Map.Entry<String, String> e : byLower.entrySet()) {
            String k = e.getKey();
            if (k.length() >= FUZZY_NEAR_MIN_LEN && k.charAt(0) == lw.charAt(0) && oneEdit(lw, k)) near.add(e.getValue());
        }
        return near.size() == 1 ? near.iterator().next() : w;
    }

    private static boolean isAlpha(String s) {
        for (int i = 0; i < s.length(); i++) if (!Character.isLetter(s.charAt(i))) return false;
        return !s.isEmpty();
    }

    /** True when the different strings a and b are one substitution, insertion or deletion apart (not a letter added at the end). */
    private static boolean oneEdit(String a, String b) {
        if (Math.abs(a.length() - b.length()) > 1) return false;
        int i = 0, n = Math.min(a.length(), b.length());
        while (i < n && a.charAt(i) == b.charAt(i)) i++;
        if (a.length() == b.length()) return a.substring(i + 1).equals(b.substring(i + 1));
        String longer = a.length() > b.length() ? a : b, shorter = longer == a ? b : a;
        return i != shorter.length() && longer.substring(i + 1).equals(shorter.substring(i));
    }

    private static void add(List<String> out, String term) {
        if (!out.contains(term)) out.add(term);
    }

    // ------------------------------------------------- dictionary terms for the cleanup prompt
    // Twin of select_terms in windows/vox_core.py (golden rows pickterms, termkey): the cleanup prompt carries only the
    // dictionary terms that occur in the transcript or sound like 1-3 of its words, at most ApiClient.PROMPT_TERMS_MAX.

    private static final Set<String> STOP = new HashSet<>(Arrays.asList(("the and for you are was with that this have from they "
            + "will what when your there their about would could should which where ok okay").split(" ")));
    private static final String[][] KEY_PAIRS = {{"sch", "sk"}, {"ph", "f"}, {"gh", "g"}, {"ck", "k"}, {"th", "t"}, {"dh", "d"},
        {"bh", "b"}, {"kh", "k"}, {"sh", "s"}, {"ch", "c"}, {"q", "k"}, {"x", "ks"}, {"z", "s"}, {"w", "v"}};
    private static final Pattern SOFT_C = Pattern.compile("c(?=[eiy])");

    /**
     * A small Metaphone-like sound key, tuned for Indian English (v/w and the aspirates th, dh, bh, kh merged): only the
     * letters a-z count, a vowel first letter is A, later vowels and h are dropped, doubles collapse, at most 8 letters.
     * Twin of term_key in windows/vox_core.py.
     */
    static String key(String word) {
        String w = word.toLowerCase(Locale.ROOT).replaceAll("[^a-z]", "");
        if (w.isEmpty()) return "";
        for (String[] p : KEY_PAIRS) w = w.replace(p[0], p[1]);
        w = SOFT_C.matcher(w).replaceAll("s").replace("c", "k");
        StringBuilder rest = new StringBuilder();
        for (int i = 1; i < w.length(); i++) {
            char c = w.charAt(i);
            if ("aeiouyh".indexOf(c) >= 0) continue;
            if (rest.length() > 0 && rest.charAt(rest.length() - 1) == c) continue;
            rest.append(c);
        }
        String k = ("aeiouy".indexOf(w.charAt(0)) >= 0 ? "A" : String.valueOf(w.charAt(0)).toUpperCase(Locale.ROOT))
                + rest.toString().toUpperCase(Locale.ROOT);
        return k.length() > 8 ? k.substring(0, 8) : k;
    }

    /** True when the Levenshtein distance of a and b is k or less (stops as soon as it cannot be). */
    static boolean within(String a, String b, int k) {
        if (Math.abs(a.length() - b.length()) > k) return false;
        int[] prev = new int[b.length() + 1], cur = new int[b.length() + 1];
        for (int j = 0; j <= b.length(); j++) prev[j] = j;
        for (int i = 1; i <= a.length(); i++) {
            cur[0] = i;
            int min = i;
            for (int j = 1; j <= b.length(); j++) {
                cur[j] = Math.min(Math.min(prev[j] + 1, cur[j - 1] + 1), prev[j - 1] + (a.charAt(i - 1) == b.charAt(j - 1) ? 0 : 1));
                min = Math.min(min, cur[j]);
            }
            if (min > k) return false;
            int[] t = prev; prev = cur; cur = t;
        }
        return prev[b.length()] <= k;
    }

    /** Only the letters a-z and digits of a text, lowercase. */
    static String plain(String text) {
        return text.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]", "");
    }

    private static boolean letterMarkOrNumber(int cp) {
        return cp != '_' && wordOrMark(cp);
    }

    /** One window of 1 to 5 transcript words: where it starts, how many words, the words joined, its key, all single letters. */
    private static final class Win {
        final int at, n;
        final String joined, key;
        final boolean letters;

        Win(int at, int n, String joined, String key, boolean letters) {
            this.at = at; this.n = n; this.joined = joined; this.key = key; this.letters = letters;
        }
    }

    private static List<String> cachedTerms;
    private static List<String[]> cachedIndex;   // {term, letters, key} of cachedTerms: worked out once per dictionary

    private static synchronized List<String[]> index(List<String> terms) {
        if (!terms.equals(cachedTerms)) {
            List<String[]> idx = new ArrayList<>();
            for (String t : terms) {
                String p = plain(t);
                if (!p.isEmpty()) idx.add(new String[] {t, p, key(p)});
            }
            cachedTerms = new ArrayList<>(terms);
            cachedIndex = idx;
        }
        return cachedIndex;
    }

    /** Where word first occurs in text as a whole word, ignoring case; -1 when it does not. */
    private static int findWord(String text, String word) {
        Matcher m = Pattern.compile(Pattern.quote(word), Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE).matcher(text);
        int from = 0;
        while (from <= text.length() && m.find(from)) {
            int s = m.start(), e = m.end();
            boolean glued = (s > 0 && wordOrMark(text.codePointBefore(s))) || (e < text.length() && wordOrMark(text.codePointAt(e)));
            if (!glued) return s;
            from = s + 1;
        }
        return -1;
    }

    /**
     * The dictionary terms the cleanup prompt needs for this transcript, in the order they come up, at most
     * ApiClient.PROMPT_TERMS_MAX: the right side of each "wrong => right" whose wrong side is in it (repl may be null), and
     * each term that is in it or sounds like 1-3 of its words (up to 5 when they are spelled letters). Same rules as
     * select_terms in windows/vox_core.py.
     */
    static List<String> select(String transcript, List<String> terms, Map<String, String> repl) {
        String text = transcript == null ? "" : transcript;
        Map<String, Integer> hits = new LinkedHashMap<>();
        if (repl != null) {
            for (Map.Entry<String, String> e : repl.entrySet()) {
                String wrong = e.getKey(), right = e.getValue();
                if (wrong == null || wrong.isEmpty() || right == null || right.isEmpty() || hits.containsKey(right)) continue;
                int at = findWord(text, wrong);
                if (at >= 0) hits.put(right, at);
            }
        }
        List<String> words = new ArrayList<>();
        List<Integer> starts = new ArrayList<>();
        int i = 0;
        while (i < text.length()) {
            int cp = text.codePointAt(i);
            if (!letterMarkOrNumber(cp)) { i += Character.charCount(cp); continue; }
            int j = i;
            while (j < text.length()) {
                int c = text.codePointAt(j);
                int next = j + Character.charCount(c);
                if (letterMarkOrNumber(c) || ((c == '\'' || c == '’') && next < text.length() && letterMarkOrNumber(text.codePointAt(next)))) j = next;
                else break;
            }
            int end = j;   // a final 's after an apostrophe is dropped too (Minhaj's is minhaj)
            char ap = j - i > 2 ? text.charAt(j - 2) : ' ';
            if ((ap == '\'' || ap == '’') && (text.charAt(j - 1) == 's' || text.charAt(j - 1) == 'S')) end = j - 2;
            String w = plain(text.substring(i, end));
            if (!w.isEmpty()) { words.add(w); starts.add(i); }
            i = j;
        }
        Map<String, List<Win>> byFirst = new java.util.HashMap<>();
        for (int s = 0; s < words.size(); s++) {
            for (int n = 1; n <= 5 && s + n <= words.size(); n++) {
                int singles = 0;
                StringBuilder sb = new StringBuilder();
                for (int q = s; q < s + n; q++) {
                    sb.append(words.get(q));
                    if (words.get(q).length() == 1) singles++;
                }
                if (n > 3 && singles < n - 1) continue;
                String joined = sb.toString();
                if (STOP.contains(joined) || joined.length() < 3) continue;
                Win win = new Win(starts.get(s), n, joined, key(joined), singles == n);
                String a = joined.substring(0, 1), b = win.key.isEmpty() ? "" : win.key.substring(0, 1).toLowerCase(Locale.ROOT);
                byFirst.computeIfAbsent(a, x -> new ArrayList<>()).add(win);
                if (!b.equals(a)) byFirst.computeIfAbsent(b, x -> new ArrayList<>()).add(win);
            }
        }
        List<Win> none = new ArrayList<>();
        for (String[] t : index(terms == null ? new ArrayList<String>() : terms)) {
            String term = t[0], tl = t[1], tk = t[2];
            if (hits.containsKey(term)) continue;
            if (tl.length() < 3) {   // a short term (AI, Q3) only as a whole word of its own
                int at = words.indexOf(tl);
                if (at >= 0) hits.put(term, starts.get(at));
                continue;
            }
            String a = tl.substring(0, 1), b = tk.isEmpty() ? "" : tk.substring(0, 1).toLowerCase(Locale.ROOT);
            List<Win> cands = byFirst.containsKey(a) ? byFirst.get(a) : none;
            if (!b.equals(a)) {
                java.util.LinkedHashSet<Win> both = new java.util.LinkedHashSet<>(cands);
                if (byFirst.containsKey(b)) both.addAll(byFirst.get(b));
                cands = new ArrayList<>(both);
                java.util.Collections.sort(cands, (x, y) -> x.at != y.at ? Integer.compare(x.at, y.at) : Integer.compare(x.n, y.n));
            }
            for (Win w : cands) {
                boolean nick = w.n == 1 && w.joined.length() >= 5 && tl.startsWith(w.joined);
                if (Math.abs(w.joined.length() - tl.length()) > Math.max(2, tl.length() / 5) && !nick) continue;
                if (w.n > 1 && w.n <= 3 && tl.length() < 6 && !w.letters) continue;
                boolean hit;
                if (w.joined.equals(tl)) {
                    hit = true;
                } else if (w.n == 1) {
                    hit = (tk.length() >= 3 && (w.key.equals(tk) || (tk.length() >= 4 && w.joined.charAt(0) == tl.charAt(0) && within(w.key, tk, 1))))
                            || (tl.length() >= 5 && within(w.joined, tl, Math.max(1, tl.length() / 5)))
                            || (nick && !COMMON_WORDS.contains(w.joined));
                } else {
                    hit = (tl.length() >= 6 && w.key.equals(tk)) || within(w.joined, tl, Math.max(1, tl.length() / 6));
                }
                if (hit) {
                    hits.put(term, w.at);
                    break;
                }
            }
        }
        List<Map.Entry<String, Integer>> sorted = new ArrayList<>(hits.entrySet());
        java.util.Collections.sort(sorted, (x, y) -> Integer.compare(x.getValue(), y.getValue()));   // stable: ties keep their order
        List<String> out = new ArrayList<>();
        for (Map.Entry<String, Integer> e : sorted) {
            if (out.size() >= ApiClient.PROMPT_TERMS_MAX) break;
            out.add(e.getKey());
        }
        return out;
    }
}
