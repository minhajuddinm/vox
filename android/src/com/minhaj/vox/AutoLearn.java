package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Learn from my corrections: the corrections a user made in text Vox just typed, and what they add to the dictionary.
 * Same rules as windows/autolearn.py (detect, learn and the learned log); spec/golden.txt (kinds autocorrect and autolearn)
 * keeps the two in step. Pure Java, tested off-device. The watch that decides when to look is {@link AutoLearnWatch}.
 */
final class AutoLearn {
    static final int MAX_PAIRS = 3;
    static final int MAX_WORDS = 3;
    static final int MAX_SHIFT = 3;
    static final int MAX_TOKENS = 1500;
    /** Characters of a field that are read at all. */
    static final int MAX_TEXT = 20000;
    /** Nothing is learned into a dictionary this big (well inside the relay's 64 KB profile). */
    static final int MAX_DICTIONARY_LINES = 1000;
    /** Entries kept in learned_log (the "Recently learned" list). */
    static final int LEARNED_LOG_MAX = 20;
    private static final String EDGE = ".,;:!?\"'()[]{}";
    private static final Set<String> STOP_WORDS =
            new HashSet<>(Arrays.asList("the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on"));

    private AutoLearn() { }

    // ------------------------------------------------------------------ small text helpers

    static boolean isSpace(int c) {
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == 0x0B) return true;
        int t = Character.getType(c);
        return t == Character.SPACE_SEPARATOR || t == Character.LINE_SEPARATOR || t == Character.PARAGRAPH_SEPARATOR;
    }

    /** Words of a text split on any space, including the no-break spaces some apps put in their fields. */
    static List<String> tokens(String text) {
        List<String> out = new ArrayList<>();
        if (text == null) return out;
        StringBuilder cur = new StringBuilder();
        for (int i = 0; i < text.length(); ) {
            int c = text.codePointAt(i);
            i += Character.charCount(c);
            if (isSpace(c)) {
                if (cur.length() > 0) { out.add(cur.toString()); cur.setLength(0); }
            } else {
                cur.appendCodePoint(c);
            }
        }
        if (cur.length() > 0) out.add(cur.toString());
        return out;
    }

    private static String lower(String s) { return s.toLowerCase(Locale.ROOT); }

    private static boolean isWordChar(int c) {
        switch (Character.getType(c)) {
            case Character.UPPERCASE_LETTER: case Character.LOWERCASE_LETTER: case Character.TITLECASE_LETTER:
            case Character.MODIFIER_LETTER: case Character.OTHER_LETTER:
            case Character.NON_SPACING_MARK: case Character.ENCLOSING_MARK: case Character.COMBINING_SPACING_MARK:
            case Character.DECIMAL_DIGIT_NUMBER: case Character.LETTER_NUMBER: case Character.OTHER_NUMBER:
                return true;
            default:
                return false;
        }
    }

    /** The words' letters, marks and digits, lowercased: what is left when case and punctuation are ignored. */
    static String wordChars(String s) {
        StringBuilder sb = new StringBuilder();
        for (String t : tokens(s)) {
            if (sb.length() > 0) sb.append(' ');
            String l = lower(t);
            for (int i = 0; i < l.length(); ) {
                int c = l.codePointAt(i);
                i += Character.charCount(c);
                if (isWordChar(c)) sb.appendCodePoint(c);
            }
        }
        return sb.toString();
    }

    private static boolean special(int c) {
        return Character.isUpperCase(c) || Character.isDigit(c) || c == '_' || c == '.';
    }

    /** One word with a capital, a digit, an underscore or a dot: a name, brand or code identifier. */
    static boolean nameLike(String s) {
        if (s == null || s.isEmpty()) return false;
        boolean any = false;
        for (int i = 0; i < s.length(); ) {
            int c = s.codePointAt(i);
            i += Character.charCount(c);
            if (isSpace(c)) return false;
            if (special(c)) any = true;
        }
        return any;
    }

    /** Ordinary lowercase words (no capital, digit, underscore or dot). */
    static boolean plain(String s) {
        if (s == null || s.isEmpty()) return false;
        for (int i = 0; i < s.length(); ) {
            int c = s.codePointAt(i);
            i += Character.charCount(c);
            if (special(c)) return false;
        }
        return true;
    }

    private static int[] cps(String s) { return s.codePoints().toArray(); }

    /** Edit distance counting a swap of two neighbouring letters as one edit (optimal string alignment). */
    static int osa(String x, String y) {
        int[] a = cps(x), b = cps(y);
        int n = a.length, m = b.length;
        int[][] d = new int[n + 1][m + 1];
        for (int i = 0; i <= n; i++) d[i][0] = i;
        for (int j = 0; j <= m; j++) d[0][j] = j;
        for (int i = 1; i <= n; i++) {
            for (int j = 1; j <= m; j++) {
                int cost = a[i - 1] == b[j - 1] ? 0 : 1;
                d[i][j] = Math.min(Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1), d[i - 1][j - 1] + cost);
                if (i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
            }
        }
        return d[n][m];
    }

    private static char soundexCode(char c) {
        if ("bfpv".indexOf(c) >= 0) return '1';
        if ("cgjkqsxz".indexOf(c) >= 0) return '2';
        if (c == 'd' || c == 't') return '3';
        if (c == 'l') return '4';
        if (c == 'm' || c == 'n') return '5';
        if (c == 'r') return '6';
        return 0;
    }

    /** American Soundex of the a-z letters of s ("" when it has none). */
    static String soundex(String s) {
        StringBuilder letters = new StringBuilder();
        for (char c : lower(s).toCharArray()) if (c >= 'a' && c <= 'z') letters.append(c);
        if (letters.length() == 0) return "";
        StringBuilder out = new StringBuilder().append(Character.toUpperCase(letters.charAt(0)));
        char prev = soundexCode(letters.charAt(0));
        for (int i = 1; i < letters.length(); i++) {
            char c = letters.charAt(i);
            if (c == 'h' || c == 'w') continue;
            char code = soundexCode(c);
            if (code != 0 && code != prev) {
                out.append(code);
                if (out.length() == 4) break;
            }
            prev = code;
        }
        while (out.length() < 4) out.append('0');
        return out.toString();
    }

    /** True when wrong -> right looks like a correction of a misheard or misspelled word, not a rewrite. */
    static boolean looksLikeFix(String wrong, String right) {
        if (wrong == null || right == null || wrong.isEmpty() || right.isEmpty() || wordChars(wrong).equals(wordChars(right))) return false;
        String a = lower(wrong), b = lower(right);
        boolean allStop = true;
        for (String t : tokens(a)) if (!STOP_WORDS.contains(t)) allStop = false;
        if (allStop) return false;   // "to => too" would change every "to" from now on
        int dist = osa(a, b);
        int la = a.codePointCount(0, a.length()), lb = b.codePointCount(0, b.length()), longest = Math.max(la, lb);
        if (2 * dist <= longest) return true;
        if (a.codePointAt(0) == b.codePointAt(0) && Math.abs(la - lb) <= 1 && 3 * dist <= 2 * longest) return true;
        String sa = soundex(a);
        if (!sa.isEmpty() && sa.equals(soundex(b))) return true;
        return nameLike(right) && plain(wrong) && 3 * dist <= 2 * longest;
    }

    // ------------------------------------------------------------------ finding the typed text again

    private static String key(String t) {
        int s = 0, e = t.length();
        while (s < e && EDGE.indexOf(t.charAt(s)) >= 0) s++;
        while (e > s && EDGE.indexOf(t.charAt(e - 1)) >= 0) e--;
        return t.substring(s, e);
    }

    private static List<String> keys(String text) {
        List<String> out = new ArrayList<>();
        for (String t : tokens(text)) out.add(key(t));
        return out;
    }

    private static List<Integer> hits(List<String> keys, List<String> sub) {
        List<Integer> out = new ArrayList<>();
        for (int p = 0; p + sub.size() <= keys.size(); p++) if (keys.subList(p, p + sub.size()).equals(sub)) out.add(p);
        return out;
    }

    /** (shift, size) to try: two-word anchors first (from the very end inwards), then single words. */
    private static List<int[]> anchorOrder(int n) {
        List<int[]> out = new ArrayList<>();
        for (int k = 2; k >= 1; k--) for (int i = 0; i < Math.min(MAX_SHIFT, n); i++) if (i + k <= n) out.add(new int[] {i, k});
        return out;
    }

    /** Where the typed text is now: {start, end} word positions in tokens(current), end exclusive, or null. Same rules as locate in autolearn.py. */
    static int[] locate(String inserted, String current) {
        List<String> ins = keys(inserted), cur = keys(current);
        int n = ins.size(), m = cur.size();
        if (n == 0 || m == 0 || n > MAX_TOKENS) return null;
        List<int[]> starts = new ArrayList<>(), ends = new ArrayList<>();
        for (int[] ik : anchorOrder(n)) {
            int i = ik[0], k = ik[1];
            List<Integer> h = hits(cur, ins.subList(i, i + k));
            if (h.isEmpty()) continue;
            for (int p : h) starts.add(new int[] {Math.max(0, p - i), p});
            break;
        }
        for (int[] jk : anchorOrder(n)) {
            int j = jk[0], k = jk[1];
            List<Integer> h = hits(cur, ins.subList(n - j - k, n - j));
            if (h.isEmpty()) continue;
            for (int q : h) ends.add(new int[] {Math.min(m, q + k + j), q + k});
            break;
        }
        int[] best = null;   // {diff, -s, e, s}
        for (int[] st : starts) {
            for (int[] en : ends) {
                int s = st[0], p = st[1], e = en[0], qe = en[1];
                if (qe <= p || e <= s) continue;
                int[] rank = {Math.abs((e - s) - n), -s, e, s};
                if (best == null || rank[0] < best[0] || (rank[0] == best[0] && (rank[1] < best[1] || (rank[1] == best[1] && rank[2] < best[2])))) best = rank;
            }
        }
        return best == null ? null : new int[] {best[3], best[2]};
    }

    private static String join(List<String> t) {
        StringBuilder sb = new StringBuilder();
        for (String x : t) { if (sb.length() > 0) sb.append(' '); sb.append(x); }
        return sb.toString();
    }

    private static int len(String s) { return s.codePointCount(0, s.length()); }

    /** [wrong, right] pairs (at most MAX_PAIRS): the words the user corrected in the text Vox typed, seen in the field's whole text now. */
    static List<String[]> detect(String inserted, String current) {
        List<String[]> out = new ArrayList<>();
        int[] span = locate(inserted, current);
        if (span == null) return out;
        String insText = join(tokens(inserted));
        String edited = join(tokens(current).subList(span[0], span[1]));
        if (span[1] - span[0] > MAX_TOKENS || 5 * Math.abs(len(edited) - len(insText)) > 2 * len(insText)) return out;
        for (String[] p : Corrections.suggest(insText, edited, MAX_WORDS)) {
            if (!looksLikeFix(p[0], p[1])) continue;
            boolean dup = false;
            for (String[] o : out) if (o[0].equals(p[0]) && o[1].equals(p[1])) dup = true;
            if (!dup) out.add(new String[] {p[0], p[1]});
            if (out.size() == MAX_PAIRS) break;
        }
        return out;
    }

    // ------------------------------------------------------------------ what to add to the dictionary

    /** What learn() adds: replacements as [wrong, right] and words. */
    static final class Learned {
        final List<String[]> replacements = new ArrayList<>();
        final List<String> words = new ArrayList<>();
    }

    static Learned learn(List<String[]> replacements, List<String> words, List<String[]> pairs) {
        return learn(replacements, words, pairs, MAX_DICTIONARY_LINES);
    }

    /**
     * What to add for pairs. A wrong word that already has a replacement (any case) is skipped; a name-like right word is
     * also added as a word unless it is there already. Nothing is added once the dictionary holds maxLines lines.
     */
    static Learned learn(List<String[]> replacements, List<String> words, List<String[]> pairs, int maxLines) {
        Learned out = new Learned();
        Set<String> known = new HashSet<>(), have = new HashSet<>();
        for (String[] r : replacements) known.add(lower(r[0]));
        for (String w : words) have.add(lower(w.trim()));
        int size = replacements.size() + words.size();
        for (String[] p : pairs) {
            String wrong = p[0] == null ? "" : p[0].trim(), right = p.length < 2 || p[1] == null ? "" : p[1].trim();
            if (wrong.isEmpty() || right.isEmpty() || (wrong + right).contains("=>") || wrong.startsWith("#") || known.contains(lower(wrong))) continue;
            if (size >= maxLines) break;
            out.replacements.add(new String[] {wrong, right});
            known.add(lower(wrong));
            size++;
            if (nameLike(right) && !have.contains(lower(right)) && size < maxLines) {
                out.words.add(right);
                have.add(lower(right));
                size++;
            }
        }
        return out;
    }

    /** The replacements ([wrong, right]) and words of a stored dictionary text, comments and blank lines left out. */
    static List<String[]> dictReplacements(String raw) {
        List<String[]> out = new ArrayList<>();
        for (String line : (raw == null ? "" : raw).split("\n")) {
            String t = line.trim();
            if (t.isEmpty() || t.startsWith("#") || !t.contains("=>")) continue;
            String w = t.substring(0, t.indexOf("=>")).trim();
            if (!w.isEmpty()) out.add(new String[] {w, t.substring(t.indexOf("=>") + 2).trim()});
        }
        return out;
    }

    static List<String> dictWords(String raw) {
        List<String> out = new ArrayList<>();
        for (String line : (raw == null ? "" : raw).split("\n")) {
            String t = line.trim();
            if (!t.isEmpty() && !t.startsWith("#") && !t.contains("=>")) out.add(t);
        }
        return out;
    }

    // ------------------------------------------------------------------ the learned log ("Recently learned")

    /** The stored learned_log JSON as a clean list of {t, wrong, right, word}, oldest first, at most LEARNED_LOG_MAX. */
    @SuppressWarnings("unchecked")
    static List<Map<String, Object>> learnedLog(String json) {
        List<Map<String, Object>> out = new ArrayList<>();
        Object v;
        try { v = PlainJson.parse(json == null || json.isEmpty() ? "[]" : json); } catch (RuntimeException e) { return out; }
        if (!(v instanceof List)) return out;
        for (Object o : (List<Object>) v) {
            if (!(o instanceof Map)) continue;
            Map<String, Object> e = (Map<String, Object>) o;
            Object t = e.get("t"), w = e.get("wrong"), r = e.get("right");
            if (!(t instanceof Number) || !(w instanceof String) || !(r instanceof String)) continue;
            out.add(entry(((Number) t).doubleValue(), (String) w, (String) r, Boolean.TRUE.equals(e.get("word"))));
        }
        return out.size() > LEARNED_LOG_MAX ? new ArrayList<>(out.subList(out.size() - LEARNED_LOG_MAX, out.size())) : out;
    }

    private static Map<String, Object> entry(double t, String wrong, String right, boolean word) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("t", t);
        m.put("wrong", wrong);
        m.put("right", right);
        m.put("word", word);
        return m;
    }

    /** The new dictionary text and learned_log after learning; added is empty (and nothing changes) when nothing is new. */
    static final class Applied {
        String dictionary;
        String log;
        final List<String[]> added = new ArrayList<>();
    }

    static Applied applyLearned(String dictRaw, String logJson, List<String[]> pairs, double now) {
        Applied out = new Applied();
        String raw = dictRaw == null ? "" : dictRaw;
        out.dictionary = raw;
        List<Map<String, Object>> log = learnedLog(logJson);
        out.log = PlainJson.stringify(log);
        Learned add = learn(dictReplacements(raw), dictWords(raw), pairs);
        if (add.replacements.isEmpty()) return out;
        StringBuilder sb = new StringBuilder(raw);
        if (sb.length() > 0 && sb.charAt(sb.length() - 1) != '\n') sb.append('\n');
        for (String[] r : add.replacements) sb.append(r[0]).append(" => ").append(r[1]).append('\n');
        for (String w : add.words) sb.append(w).append('\n');
        out.dictionary = sb.toString();
        for (int i = 0; i < add.replacements.size(); i++) {
            String[] r = add.replacements.get(i);
            log.add(entry(Math.round((now + i / 1000.0) * 1000) / 1000.0, r[0], r[1], add.words.contains(r[1])));
            out.added.add(r);
        }
        if (log.size() > LEARNED_LOG_MAX) log = new ArrayList<>(log.subList(log.size() - LEARNED_LOG_MAX, log.size()));
        out.log = PlainJson.stringify(log);
        return out;
    }

    /** The dictionary text and learned_log after the entry made at t is removed, with the replacement and word it added. */
    static String[] removeLearned(String dictRaw, String logJson, double t) {
        List<Map<String, Object>> log = learnedLog(logJson);
        Map<String, Object> hit = null;
        for (Map<String, Object> e : log) if (Math.abs((Double) e.get("t") - t) < 0.0005) { hit = e; break; }
        String raw = dictRaw == null ? "" : dictRaw;
        if (hit == null) return new String[] {raw, PlainJson.stringify(log)};
        List<String> lines = new ArrayList<>(Arrays.asList(raw.split("\n", -1)));
        for (int i = 0; i < lines.size(); i++) {
            String l = lines.get(i);
            if (l.contains("=>") && l.substring(0, l.indexOf("=>")).trim().equals(hit.get("wrong"))
                    && l.substring(l.indexOf("=>") + 2).trim().equals(hit.get("right"))) { lines.remove(i); break; }
        }
        if (Boolean.TRUE.equals(hit.get("word"))) {
            for (int i = 0; i < lines.size(); i++) {
                if (!lines.get(i).contains("=>") && lines.get(i).trim().equals(hit.get("right"))) { lines.remove(i); break; }
            }
        }
        log.remove(hit);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < lines.size(); i++) { if (i > 0) sb.append('\n'); sb.append(lines.get(i)); }
        return new String[] {sb.toString(), PlainJson.stringify(log)};
    }
}
