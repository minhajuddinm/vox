package com.minhaj.vox;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Snippets: a trigger phrase the user says ("my email") becomes the text saved for it. Twin of windows/snippets.py (golden
 * rows "snippets"). Applied after the AI cleanup, so the saved text never goes to the cleanup server, and after the lists ({@link #layout}), so the list pass never re-formats it.
 * Whole phrase, case ignored, any run of spaces between its words; the longest trigger wins and the text put in is not
 * looked at again. The setting travels with the synced profile (ProfileMap). Pure Java (no android.*).
 */
final class Snippets {
    private Snippets() { }

    static final int MAX_SNIPPETS = 50;      // snippets used (the first ones)
    static final int MAX_EXPANSION = 2000;   // characters (code points) of one saved text
    static final int MAX_TRIGGER = 100;      // characters of one trigger phrase
    /** Bytes of all triggers and saved texts as the relay stores them (wireSize): the relay keeps at most 64,000 bytes of profile. */
    static final int MAX_TOTAL = 20000;

    private static final Pattern SPACES = Pattern.compile("[ \\t\\r\\n]+");

    private static boolean hasWordChar(String s) {
        for (int i = 0; i < s.length(); ) {
            int cp = s.codePointAt(i);
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
                    i += Character.charCount(cp);
            }
        }
        return false;
    }

    /**
     * Bytes s takes in the relay's profile: JSON with ASCII escapes, as relay.py measures it (a letter outside ASCII is 6
     * bytes, an emoji 12, a line break, tab, quote or backslash 2). Twin of wire_size in windows/snippets.py.
     */
    static int wireSize(String s) {
        int n = 0;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\' || c == '\n' || c == '\r' || c == '\t' || c == '\b' || c == '\f') n += 2;
            else if (c >= ' ' && c <= '~') n += 1;
            else n += 6;
        }
        return n;
    }

    private static int cps(String s) {
        return s.codePointCount(0, s.length());
    }

    private static String trimSpaces(String s) {
        int a = 0, b = s.length();
        while (a < b && s.charAt(a) == ' ') a++;
        while (b > a && s.charAt(b - 1) == ' ') b--;
        return s.substring(a, b);
    }

    private static boolean blank(String s) {
        for (int i = 0; i < s.length(); i++) if (" \t\n".indexOf(s.charAt(i)) < 0) return false;
        return true;
    }

    /**
     * The setting made safe (twin of clean_snippets): only text triggers with text, a trigger's spaces made single and
     * trimmed (at most MAX_TRIGGER characters, at least one letter or digit), line breaks as \n, each text cut at
     * MAX_EXPANSION characters, a repeated trigger (case ignored) dropped, at most MAX_SNIPPETS snippets and MAX_TOTAL
     * bytes of triggers and texts as the relay stores them (a snippet that would go over is left out). Order kept.
     * Anything that is not a map gives none.
     */
    static Map<String, String> clean(Object value) {
        Map<String, String> out = new LinkedHashMap<>();
        if (!(value instanceof Map)) return out;
        Set<String> seen = new HashSet<>();
        int total = 0;
        for (Map.Entry<?, ?> e : ((Map<?, ?>) value).entrySet()) {
            if (out.size() >= MAX_SNIPPETS) break;
            if (!(e.getKey() instanceof String) || !(e.getValue() instanceof String)) continue;
            String t = trimSpaces(SPACES.matcher((String) e.getKey()).replaceAll(" "));
            String x = ((String) e.getValue()).replace("\r\n", "\n").replace('\r', '\n');
            if (cps(x) > MAX_EXPANSION) x = x.substring(0, x.offsetByCodePoints(0, MAX_EXPANSION));
            String key = t.toLowerCase(Locale.ROOT);
            if (t.isEmpty() || cps(t) > MAX_TRIGGER || !hasWordChar(t) || blank(x) || seen.contains(key)) continue;
            int size = wireSize(t) + wireSize(x);
            if (total + size > MAX_TOTAL) continue;
            seen.add(key);
            out.put(t, x);
            total += size;
        }
        return out;
    }

    /**
     * Lists from spoken cues (Structure.format), then the snippets: last, so a saved text is never re-formatted by the list
     * pass (TXT-12: its line breaks and list markers stay as saved). Twin of vox_core.apply_layout (golden rows "layout").
     */
    static String layout(String text, Object snippets, String mode, String style) {
        return apply(Structure.format(text, mode, style), snippets);
    }

    /** Text with every trigger phrase replaced by its saved text (twin of apply_snippets). */
    static String apply(String text, Object value) {
        Map<String, String> snips = clean(value);
        if (text == null || text.isEmpty() || snips.isEmpty()) return text;
        List<Map.Entry<String, String>> items = new ArrayList<>(snips.entrySet());
        java.util.Collections.sort(items, (a, b) -> cps(b.getKey()) - cps(a.getKey()));   // stable: equal lengths keep their order
        StringBuilder alts = new StringBuilder();
        for (Map.Entry<String, String> it : items) {
            if (alts.length() > 0) alts.append('|');
            alts.append('(');
            String[] words = it.getKey().split(" ");
            for (int i = 0; i < words.length; i++) {
                if (i > 0) alts.append("[ \\t\\r\\n]+");
                alts.append(Pattern.quote(words[i]));
            }
            alts.append(')');
        }
        // a word's combining marks are part of it: a trigger never ends inside a Hindi word (करें is not कर)
        Matcher m = Pattern.compile("(?iu)(?<!" + ApiClient.WORD_CHAR + ")(?:" + alts + ")(?!" + ApiClient.WORD_CHAR + ")").matcher(text);
        StringBuffer sb = new StringBuffer();
        while (m.find()) {
            int g = 1;
            while (m.group(g) == null) g++;
            m.appendReplacement(sb, Matcher.quoteReplacement(items.get(g - 1).getValue()));
        }
        return m.appendTail(sb).toString();
    }
}
