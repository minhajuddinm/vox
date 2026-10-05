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
 * Deterministic cleanup without the AI (the "rules layer"): the text typed when the AI cleanup was wanted but did not give
 * the text (a phrase under cleanup_min_words, an error or timeout, or an answer the fidelity guard rejected). It only
 * removes pure noises (um, uh, er, erm, ah, hmm) and spoken punctuation commands, writes the marks those commands name,
 * and fixes capitals and the final mark for the style; it never adds a word, and in Light strength it drops no other word.
 * Standard strength also takes a typed-value self-correction ("by thursday no wait friday" -> "by friday"): a cue between
 * two different values of the same kind (weekday, month, number or time). Idea of acting only on typed values from
 * whisper-local (MIT); no code copied. Pure Java, no Android classes. Twin of windows/rules_layer.py; the rulelayer and
 * fallback rows of spec/golden.txt keep the two equal. ASCII-only patterns: Hindi and Hinglish words are never touched.
 */
final class RulesLayer {
    private RulesLayer() {}

    private static final String WORD = "A-Za-z0-9_'\u2019\\-";
    private static final String L = "(?<![" + WORD + "])";
    private static final String R = "(?![" + WORD + "])";
    private static final String END = "(?![\\s\\S])";
    private static final String SP = "[ \\t]";
    private static final String OPEN = ".?!,;:\n";

    /** Pure noises; "Er" with a capital only before a comma ("Er Rahul Sharma": the title for an engineer). */
    private static final Pattern NOISE = Pattern.compile("(," + SP + "*)?" + L + "(?:[Uu](?:m+|h+|hm+)|[Ee]rm+|er|Er(?=,)|[Aa]h+|[Hh]m+)" + R
            + "([,.?!;:]?)" + SP + "*");
    private static final Pattern PUNCT = Pattern.compile("(?i)(" + SP + "*,?" + SP + "*)" + L + "(comma|period|full" + SP + "+stop|question"
            + SP + "+mark|exclamation" + SP + "+(?:mark|point))" + R + "([.,?!]?)");
    private static final Map<String, String> MARKS = new HashMap<>();
    static {
        MARKS.put("comma", ",");
        MARKS.put("period", ".");
        MARKS.put("full stop", ".");
        MARKS.put("question mark", "?");
        MARKS.put("exclamation mark", "!");
        MARKS.put("exclamation point", "!");
    }
    /** A punctuation name right after one of these words is a noun ("the trial period", "a comma", "a full stop"). */
    private static final Set<String> NOUN_AFTER = new HashSet<>(Arrays.asList((
            "a an the this that these those my your his her its our their each every any no one per same whole entire first last next "
            + "trial grace notice waiting free time probation billing cooling holding oxford serial big huge small extra missing single "
            + "short long given certain").split(" ")));
    /** "period" / "full stop" with one of these up to 3 words before it in its sentence is the noun ("over a six-month period"). */
    private static final Set<String> NOUN_NEAR = new HashSet<>(Arrays.asList((
            "a an the this that these those my your our his her their its each every per over during for in of").split(" ")));
    private static final Pattern PREV_WORD = Pattern.compile("([A-Za-z]+)" + END);
    private static final Pattern NEXT_WORD = Pattern.compile(SP + "*([A-Za-z]+)");

    private static final String DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday";
    private static final String MONTHS = "january|february|march|april|june|july|august|september|october|november|december";
    private static final String NUMW = "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
            + "sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakh|million|"
            + "crore|billion";
    private static final String AMPM = "(?:a\\.m\\.|p\\.m\\.|a" + SP + "?m|p" + SP + "?m)";
    private static final String NUM = "(?:[0-9]+(?:[:.,][0-9]+)*|(?:" + NUMW + ")(?:" + SP + "+(?:" + NUMW + "))*)(?:" + SP + "*" + AMPM + ")?";
    private static final String VALUE = "(" + DAYS + "|" + MONTHS + "|" + NUM + ")";
    private static final String STRONG_CUE = "(?:no[,.]?" + SP + "+wait|wait[,.]?" + SP + "+no|no[,.]?" + SP + "+no|nahi[,.]?" + SP
            + "+nahi|i" + SP + "+mean)";
    /** A weak cue (sorry, actually) only inside the sentence: "Call me at five. Sorry, six is better." is no correction. */
    private static final Pattern CORRECTION = Pattern.compile("(?i)" + L + VALUE + "(?:,?" + SP + "+(?:" + STRONG_CUE + "|sorry|actually)|\\."
            + SP + "+" + STRONG_CUE + ")[,.]?" + SP + "+" + VALUE + R);
    private static final Set<String> DAY_SET = new HashSet<>(Arrays.asList(DAYS.split("\\|")));
    private static final Set<String> MONTH_SET = new HashSet<>(Arrays.asList(MONTHS.split("\\|")));

    private static final Pattern I = Pattern.compile(L + "i(?=[ \\t\\n,;:!?)\"]|['\u2019](?:m|ll|ve|d)" + R + "|\\.(?![A-Za-z0-9])|" + END + ")");
    private static final Pattern NAMES = Pattern.compile(L + "(monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|"
            + "april|june|july|august|september|october|november|december)" + R);   // "may" and "march" are also verbs
    private static final Pattern SENTENCE_START = Pattern.compile("(^|[.!?][ \\t]+|\\n[ \\t]*)(\\p{L})");
    private static final Pattern LOWER_START = Pattern.compile("(^|[.!?][ \\t]+|\\n[ \\t]*)([A-Z])(?=[a-z]+(?:['\u2019][a-z]+)?" + R + ")");
    private static final Set<String> QUESTION = new HashSet<>(Arrays.asList(
            "what why how where who whose which is are can did does kya kab kahan kaun kaise kyun".split(" ")));
    private static final String ONLY_MARKS = " \t\n.,?!;:-";

    /** A capital letter at the start and after each sentence end or line break; the rest as it is. */
    static String capitals(String text) {
        Matcher m = SENTENCE_START.matcher(text == null ? "" : text);
        StringBuffer sb = new StringBuffer();
        while (m.find()) m.appendReplacement(sb, Matcher.quoteReplacement(m.group(1) + m.group(2).toUpperCase(Locale.ROOT)));
        return m.appendTail(sb).toString();
    }

    /**
     * The rules layer on a transcript whose spoken line breaks are already applied. style: neutral, formal, casual or
     * very_casual (anything else counts as neutral); strength "standard" also takes typed-value self-corrections. Filler-only
     * input gives "" (spoken line breaks alone give those breaks). Twin of rules_cleanup in windows/rules_layer.py.
     */
    static String clean(String text, String style, String strength) {
        String t = dropNoises(text == null ? "" : text);
        if (Fidelity.cleanStrength(strength).equals("standard")) t = corrections(t);
        t = spokenMarks(t);
        boolean veryCasual = "very_casual".equals(style);
        if (!veryCasual) {
            t = I.matcher(t).replaceAll("I");
            Matcher m = NAMES.matcher(t);
            StringBuffer sb = new StringBuffer();
            while (m.find()) m.appendReplacement(sb, Matcher.quoteReplacement(Character.toUpperCase(m.group(1).charAt(0)) + m.group(1).substring(1)));
            t = m.appendTail(sb).toString();
        }
        t = tidy(t);
        boolean onlyMarks = true;
        for (int i = 0; i < t.length() && onlyMarks; i++) onlyMarks = ONLY_MARKS.indexOf(t.charAt(i)) >= 0;
        if (onlyMarks) {   // "new line" / "new paragraph" alone still types the break
            StringBuilder breaks = new StringBuilder();
            for (int i = 0; i < t.length(); i++) if (t.charAt(i) == '\n') breaks.append('\n');
            return breaks.toString();
        }
        if (veryCasual) {   // like a text message: no capitals added, sentence capitals undone, no final period
            Matcher m = LOWER_START.matcher(t);
            StringBuffer sb = new StringBuffer();
            while (m.find()) m.appendReplacement(sb, Matcher.quoteReplacement(m.group(1) + m.group(2).toLowerCase(Locale.ROOT)));
            t = m.appendTail(sb).toString();
            return t.endsWith(".") && !t.endsWith("..") ? t.substring(0, t.length() - 1) : t;
        }
        return finalMark(capitals(t), style);
    }

    private static String rstrip(String s, String chars) {
        int e = s.length();
        while (e > 0 && chars.indexOf(s.charAt(e - 1)) >= 0) e--;
        return s.substring(0, e);
    }

    private static String lstrip(String s, String chars) {
        int b = 0;
        while (b < s.length() && chars.indexOf(s.charAt(b)) >= 0) b++;
        return s.substring(b);
    }

    private static boolean isOpen(String prefix) {
        return prefix.isEmpty() || OPEN.indexOf(prefix.charAt(prefix.length() - 1)) >= 0;
    }

    private static String dropNoises(String t) {
        Matcher m = NOISE.matcher(t);
        StringBuffer sb = new StringBuffer();
        while (m.find()) {
            boolean comma = m.group(1) != null;
            String mark = m.group(2);
            boolean open = isOpen(rstrip(t.substring(0, m.start()), " \t"));
            String rest = t.substring(m.end());
            boolean atEnd = rest.isEmpty() || rest.charAt(0) == '\n';
            String rep;
            if (!mark.isEmpty() && !mark.equals(",")) rep = open ? "" : mark + " ";
            else if (mark.equals(",") || !atEnd) rep = comma && !open && !atEnd ? ", " : " ";
            else rep = "";
            m.appendReplacement(sb, Matcher.quoteReplacement(rep));
        }
        return m.appendTail(sb).toString();
    }

    private static String spokenMarks(String t) {
        Matcher m = PUNCT.matcher(t);
        StringBuffer sb = new StringBuffer();
        while (m.find()) {
            m.appendReplacement(sb, Matcher.quoteReplacement(markFor(t, m)));
        }
        return m.appendTail(sb).toString();
    }

    private static String markFor(String t, Matcher m) {
        String prefix = rstrip(t.substring(0, m.start()), " \t");
        if (isOpen(prefix)) return m.group(0);   // nothing before it to end: a word, not a command
        Matcher prev = PREV_WORD.matcher(prefix);
        if (prev.find() && NOUN_AFTER.contains(prev.group(1).toLowerCase(Locale.ROOT))) return m.group(0);
        String name = String.join(" ", m.group(2).toLowerCase(Locale.ROOT).trim().split("[ \\t]+"));
        // "the exam period", "a sudden full stop": a comma before it makes it the command
        if ((name.equals("period") || name.equals("full stop")) && m.group(1).indexOf(',') < 0 && nounNear(prefix)) return m.group(0);
        String rest = t.substring(m.end());
        Matcher next = NEXT_WORD.matcher(rest);
        String nxt = next.lookingAt() ? next.group(1).toLowerCase(Locale.ROOT) : "";
        if (name.equals("comma") && (nxt.equals("separated") || nxt.equals("delimited") || nxt.equals("splice"))) return m.group(0);
        String after = lstrip(rest, " \t");
        if (name.equals("period") && !(!m.group(3).isEmpty() || after.isEmpty() || after.charAt(0) == '\n')) return m.group(0);
        return MARKS.get(name) + (!rest.isEmpty() && " \t\n".indexOf(rest.charAt(0)) < 0 ? " " : "");
    }

    /** True when one of NOUN_NEAR is among the last 3 words of prefix's sentence. */
    private static boolean nounNear(String prefix) {
        String[] parts = prefix.split("[.?!\\n]", -1);
        String sentence = lstrip(rstrip(parts[parts.length - 1], " \t"), " \t");
        String[] words = sentence.split("[ \\t]+", -1);
        for (int i = Math.max(0, words.length - 3); i < words.length; i++) {
            if (NOUN_NEAR.contains(words[i].toLowerCase(Locale.ROOT).replaceAll("[^a-z]", ""))) return true;
        }
        return false;
    }

    private static String kind(String value) {
        String v = value.toLowerCase(Locale.ROOT);
        return DAY_SET.contains(v) ? "day" : MONTH_SET.contains(v) ? "month" : "number";
    }

    private static String norm(String value) {
        return String.join(" ", value.toLowerCase(Locale.ROOT).trim().split("[ \\t]+"));
    }

    private static String corrections(String t) {
        Matcher m = CORRECTION.matcher(t);
        StringBuffer sb = new StringBuffer();
        while (m.find()) {
            String a = m.group(1), b = m.group(2);
            String rep = kind(a).equals(kind(b)) && !norm(a).equals(norm(b)) ? b : m.group(0);
            m.appendReplacement(sb, Matcher.quoteReplacement(rep));
        }
        return m.appendTail(sb).toString();
    }

    private static String tidy(String t) {
        t = t.replaceAll("[ \\t]+([,.?!;:])", "$1");
        t = t.replaceAll("[ \\t]{2,}", " ");
        t = t.replaceAll("[ \\t]+\\n", "\n");
        t = t.replaceAll("\\n[ \\t]+", "\n");
        t = t.replaceFirst("^[ \\t,;:]+", "");
        return rstrip(t, " \t,");
    }

    private static List<String> words(String text) {
        List<String> out = new ArrayList<>();
        for (String w : text.split("[ \\t\\n]+")) if (!w.isEmpty()) out.add(w);
        return out;
    }

    private static String finalMark(String t, String style) {
        if (t.isEmpty()) return t;
        char c = t.charAt(t.length() - 1);
        boolean asciiAlnum = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
        if (!asciiAlnum) return t;   // already ends in a mark, or in another script (Devanagari): left as it is
        String[] lines = t.split("\n", -1);
        String[] sentences = lines[lines.length - 1].split("(?<=[.?!])[ \\t]+", -1);
        List<String> w = words(sentences[sentences.length - 1]);
        String first = w.isEmpty() ? "" : w.get(0).toLowerCase(Locale.ROOT).replaceAll("[^a-z]", "");
        String mark = QUESTION.contains(first) && w.size() <= 8 ? "?" : ".";
        if ("casual".equals(style) && mark.equals(".") && words(t).size() <= 12) return t;   // a short casual message has no final period
        return t + mark;
    }
}
