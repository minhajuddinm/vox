package com.minhaj.vox;

import java.util.List;

/** Plain-Java checks for Corrections, mirroring tests/test_suggest_corrections.py. Run by CI. */
public final class CorrectionsTest {
    private static int checks;

    private static String show(List<String[]> pairs) {
        StringBuilder sb = new StringBuilder();
        for (String[] p : pairs) sb.append(sb.length() == 0 ? "" : "; ").append(p[0]).append(" => ").append(p[1]);
        return sb.toString();
    }

    private static void eq(String name, String expected, String original, String edited) {
        eq(name, expected, original, edited, 3);
    }

    private static void eq(String name, String expected, String original, String edited, int max) {
        checks++;
        String got = show(Corrections.suggest(original, edited, max));
        if (!expected.equals(got)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + got + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) {
        eq("single word swap", "Minhaj => Minhajuddin", "send it to Minhaj today", "send it to Minhajuddin today");
        eq("no-break spaces split words (Python split)", "grok => Groq", "we use\u00a0grok\u3000today", "we use\u00a0Groq\u3000today");
        eq("brand spelling mid sentence", "vox => Vox", "we shipped vox to friends", "we shipped Vox to friends");
        eq("capital at start is grammar", "", "send it today", "Send it today");
        eq("capital after full stop is grammar", "", "done. send it today", "done. Send it today");
        eq("two word swap", "grok cloud => Groq Cloud", "ask grok cloud about it", "ask Groq Cloud about it");
        eq("punctuation ignored", "Minhaj => Minhajuddin", "thanks, Minhaj.", "thanks, Minhajuddin.");
        eq("several fixes", "jon => John; acme corp => ACME Corp", "call jon at acme corp", "call John at ACME Corp");
        eq("insertion is not a replacement", "", "send it today", "please send it today");
        eq("deletion is not a replacement", "", "please send it today", "send it today");
        eq("long rewrite skipped", "", "one two three four five", "alpha beta gamma delta epsilon");
        eq("long rewrite allowed with bigger limit", "one two three four five => alpha beta gamma delta epsilon",
                "one two three four five", "alpha beta gamma delta epsilon", 5);
        eq("one letter words skipped", "", "meet at a cafe", "meet at 5 cafe");
        eq("no change", "", "same text", "same text");
        eq("empty", "", "", "");
        eq("null original", "", null, "x");
        eq("no duplicate pairs", "vox => Vox", "vox and vox", "Vox and Vox");
        System.out.println("OK: " + checks + " checks passed");
    }
}
