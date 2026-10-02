package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

/** Plain-Java checks for AutoLearn and AutoLearnWatch, mirroring tests/test_autolearn.py. The shared cases are in spec/golden.txt. */
public final class AutoLearnTest {
    private static int checks;

    private static void check(String name, boolean ok) {
        checks++;
        if (!ok) {
            System.err.println("FAIL " + name);
            System.exit(1);
        }
    }

    private static String show(List<String[]> pairs) {
        StringBuilder sb = new StringBuilder();
        for (String[] p : pairs) sb.append(sb.length() == 0 ? "" : ";").append(p[0]).append("=>").append(p[1]);
        return sb.toString();
    }

    private static void eq(String name, String expected, String actual) {
        checks++;
        if (!expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static List<String[]> pairs(String... wr) {
        List<String[]> out = new ArrayList<>();
        for (int i = 0; i + 1 < wr.length; i += 2) out.add(new String[] {wr[i], wr[i + 1]});
        return out;
    }

    /** A clock the test moves by hand (milliseconds). */
    private static final long[] NOW = {100_000};

    private static final String TYPED = "send it to Minhaj today";
    private static final String FIXED = "Hello. send it to Minhajuddin today";

    private static AutoLearnWatch watch() {
        NOW[0] = 100_000;
        AutoLearnWatch w = new AutoLearnWatch(() -> NOW[0]);
        w.arm("app", TYPED);
        return w;
    }

    public static void main(String[] args) {
        // detect
        eq("typo", "Minhaj=>Minhajuddin", show(AutoLearn.detect(TYPED, "send it to Minhajuddin today")));
        eq("text around", "you vrag=>Yuvraj", show(AutoLearn.detect("ask you vrag about it", "Dear team, please ask Yuvraj about it. Thanks!")));
        eq("first word", "Minhaj=>Minhajuddin", show(AutoLearn.detect("Minhaj said hi", "Hi Minhaj. Minhajuddin said hi")));
        eq("rewrite", "", show(AutoLearn.detect("I will send it today", "I will ship it tomorrow")));
        eq("capitals only", "", show(AutoLearn.detect("ping yuvraj about it", "ping Yuvraj about it")));
        eq("stop words", "", show(AutoLearn.detect("we need to go", "we need too go")));
        check("gone", AutoLearn.locate(TYPED, "something else entirely") == null);
        eq("much shorter", "", show(AutoLearn.detect("one two three four five six seven", "one 2 3 4 5 6 seven")));
        check("three pairs", AutoLearn.detect("alpha one beta one gamma one delta one epsilon one zeta",
                "alpha onee beta onne gamma oone delta onee epsilon onne zeta").size() == 3);
        eq("no-break spaces", "a|b|c|d", String.join("|", AutoLearn.tokens("a b c  d")));
        eq("no-break space in the field", "Minhaj=>Minhajuddin", show(AutoLearn.detect(TYPED, "send it to Minhajuddin today")));
        check("empty", AutoLearn.detect("", "").isEmpty() && AutoLearn.detect(null, null).isEmpty() && AutoLearn.detect("x", "").isEmpty());

        // rules
        check("recieve", AutoLearn.looksLikeFix("recieve", "receive"));
        check("nite", AutoLearn.looksLikeFix("nite", "night"));
        check("identifier", AutoLearn.looksLikeFix("user underscore id", "user_id"));
        check("send ship", !AutoLearn.looksLikeFix("send", "ship"));
        check("case only", !AutoLearn.looksLikeFix("vox", "Vox"));
        check("punctuation only", !AutoLearn.looksLikeFix("e-mail", "email"));
        check("stop word", !AutoLearn.looksLikeFix("to", "too"));
        eq("soundex Robert", "R163", AutoLearn.soundex("Robert"));
        eq("soundex Rupert", "R163", AutoLearn.soundex("Rupert"));
        eq("soundex Ashcraft", "A261", AutoLearn.soundex("Ashcraft"));
        eq("soundex Tymczak", "T522", AutoLearn.soundex("Tymczak"));
        eq("soundex digits", "", AutoLearn.soundex("123"));
        check("osa", AutoLearn.osa("teh", "the") == 1 && AutoLearn.osa("kitten", "sitting") == 3 && AutoLearn.osa("", "abc") == 3);
        check("name like", AutoLearn.nameLike("Yuvraj") && AutoLearn.nameLike("user_id") && AutoLearn.nameLike("v2") && AutoLearn.nameLike("React.js")
                && !AutoLearn.nameLike("hello") && !AutoLearn.nameLike("Groq Cloud"));
        check("plain", AutoLearn.plain("you vrag") && !AutoLearn.plain("Yuvraj") && !AutoLearn.plain("v2"));

        // learn
        AutoLearn.Learned l = AutoLearn.learn(new ArrayList<String[]>(), new ArrayList<String>(), pairs("you vrag", "Yuvraj"));
        eq("learn", "you vrag=>Yuvraj / Yuvraj", show(l.replacements) + " / " + String.join("|", l.words));
        l = AutoLearn.learn(pairs("Minhaj", "Minhajuddin"), new ArrayList<String>(), pairs("minhaj", "Minhaj Uddin"));
        check("known wrong", l.replacements.isEmpty() && l.words.isEmpty());
        l = AutoLearn.learn(new ArrayList<String[]>(), Arrays.asList("yuvraj"), pairs("you vrag", "Yuvraj"));
        check("word there", l.words.isEmpty() && l.replacements.size() == 1);
        List<String[]> full = new ArrayList<>();
        for (int i = 0; i < AutoLearn.MAX_DICTIONARY_LINES; i++) full.add(new String[] {"w" + i, "r" + i});
        check("cap", AutoLearn.learn(full, new ArrayList<String>(), pairs("you vrag", "Yuvraj")).replacements.isEmpty());
        l = AutoLearn.learn(full.subList(1, full.size()), new ArrayList<String>(), pairs("you vrag", "Yuvraj"));
        check("one line left", l.replacements.size() == 1 && l.words.isEmpty());
        l = AutoLearn.learn(new ArrayList<String[]>(), new ArrayList<String>(), pairs("a=>b", "c", "#x", "y", "", "z"));
        check("lines that would not read back", l.replacements.isEmpty());

        // the learned log
        AutoLearn.Applied ap = AutoLearn.applyLearned("# One term per line. Use  wrong => right  to force a replacement.\nLoomXR\nvox => Vox\n",
                "[]", pairs("you vrag", "Yuvraj", "recieve", "receive"), 1000.0);
        eq("applied dictionary", "# One term per line. Use  wrong => right  to force a replacement.\nLoomXR\nvox => Vox\nyou vrag => Yuvraj\nrecieve => receive\nYuvraj\n",
                ap.dictionary);
        List<Map<String, Object>> log = AutoLearn.learnedLog(ap.log);
        check("log", log.size() == 2 && log.get(0).get("wrong").equals("you vrag") && Boolean.TRUE.equals(log.get(0).get("word"))
                && Boolean.FALSE.equals(log.get(1).get("word")) && Math.abs((Double) log.get(1).get("t") - 1000.001) < 1e-9);
        String[] rm = AutoLearn.removeLearned(ap.dictionary, ap.log, 1000.0);
        eq("removed", "# One term per line. Use  wrong => right  to force a replacement.\nLoomXR\nvox => Vox\nrecieve => receive\n", rm[0]);
        check("removed from the log", AutoLearn.learnedLog(rm[1]).size() == 1);
        ap = AutoLearn.applyLearned("vox => Vox", "[]", pairs("vox", "VOX"), 1.0);
        check("nothing new", ap.added.isEmpty() && ap.dictionary.equals("vox => Vox"));
        ap = AutoLearn.applyLearned("Yuvraj", "", pairs("you vrag", "Yuvraj"), 5.0);
        eq("word kept", "Yuvraj\nyou vrag => Yuvraj\n", ap.dictionary);
        eq("word kept after remove", "Yuvraj\n", AutoLearn.removeLearned(ap.dictionary, ap.log, 5.0)[0]);
        StringBuilder big = new StringBuilder("[");
        for (int i = 0; i < 30; i++) big.append(i == 0 ? "" : ",").append("{\"t\":").append(i).append(",\"wrong\":\"w").append(i).append("\",\"right\":\"r\"}");
        big.append(",\"junk\",{\"t\":true,\"wrong\":\"a\",\"right\":\"b\"}]");
        log = AutoLearn.learnedLog(big.toString());
        check("last twenty", log.size() == AutoLearn.LEARNED_LOG_MAX && log.get(0).get("wrong").equals("w10") && log.get(19).get("wrong").equals("w29"));
        check("bad log", AutoLearn.learnedLog("nope").isEmpty() && AutoLearn.learnedLog(null).isEmpty() && AutoLearn.learnedLog("{}").isEmpty());

        // the watch
        AutoLearnWatch w = watch();
        check("first look", w.observe("app", "Hello. " + TYPED).isEmpty());
        NOW[0] += 2000;
        check("just changed", w.observe("app", FIXED).isEmpty());
        NOW[0] += 1000;
        check("1 s quiet", w.observe("app", FIXED).isEmpty());
        NOW[0] += 600;
        eq("settled", "Minhaj=>Minhajuddin", show(w.observe("app", FIXED)));
        NOW[0] += 5000;
        check("once", w.observe("app", FIXED).isEmpty() && w.isArmed());

        w = watch();
        w.observe("app", FIXED);
        NOW[0] += 1600;
        eq("change after quiet", "Minhaj=>Minhajuddin", show(w.observe("app", FIXED + " more")));

        w = watch();
        NOW[0] += 179_000;
        w.observe("app", TYPED);
        check("179 s", w.isArmed());
        NOW[0] += 2000;
        check("181 s", !w.isArmed() && w.observe("app", TYPED).isEmpty() && w.app() == null);
        check("window", AutoLearnWatch.AUTO_LEARN_WINDOW_S == 180 && AutoLearnWatch.SETTLE_MS == 1500);

        w = watch();
        w.observe("app", TYPED);
        NOW[0] += 3000;
        w.observe("app", FIXED);
        NOW[0] += 400;
        eq("sent within the debounce", "Minhaj=>Minhajuddin", show(w.observe("app", "")));
        check("sent ends", !w.isArmed());

        w = watch();
        w.observe("app", FIXED);
        NOW[0] += 200;
        eq("span replaced", "Minhaj=>Minhajuddin", show(w.observe("app", "a whole new message is being written here")));
        check("span replaced ends", w.app() == null);

        w = watch();
        w.observe("app", TYPED);
        check("shrunk", w.observe("app", "send it").isEmpty() && w.app() == null);

        w = watch();
        w.observe("app", FIXED);
        eq("app changed", "Minhaj=>Minhajuddin", show(w.observe("other", "anything")));
        check("app changed ends", w.app() == null && w.observe("app", FIXED).isEmpty());

        w = watch();
        check("unreadable", w.observe("app", null).isEmpty() && w.app() == null);

        w = watch();
        w.observe("app", FIXED);
        eq("re-arm flushes", "Minhaj=>Minhajuddin", show(w.arm("app", "and then call Ada")));
        check("re-armed", "app".equals(w.app()));
        w.observe("app", FIXED + " and then call Adah");
        NOW[0] += 2000;
        eq("new text", "Ada=>Adah", show(w.observe("app", FIXED + " and then call Adah")));

        w = new AutoLearnWatch(() -> NOW[0]);
        check("unarmed", w.observe("app", "x").isEmpty() && !w.isArmed() && w.arm("app", "  ").isEmpty() && w.app() == null);

        System.out.println("OK: " + checks + " checks passed");
    }
}
