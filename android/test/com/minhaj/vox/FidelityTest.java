package com.minhaj.vox;

import java.util.Arrays;
import java.util.Random;

/**
 * Plain-Java checks for the fidelity guard beyond the shared golden rows (spec/golden.txt kinds fidelity, tokens, recall):
 * property-style and long-text cases, the same ones tests/test_cleanup_fidelity.py runs for the Python twin.
 */
public final class FidelityTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static final String[] SENTENCES = {
        "so yesterday I went to the market and bought some apples and bananas",
        "then I came home and cooked dinner for the whole family",
        "we all sat down together and talked about the trip we are planning for the summer",
        "my sister said she would book the tickets if we agree on the dates",
        "I told her that the second week of june works best for me and for the kids",
        "after that we looked at a few hotels near the beach and compared the prices",
        "the cheapest one had no breakfast so we kept looking for something better",
        "in the end we picked the small place with the garden and the old stone wall",
    };

    private static String longText(int words) {
        StringBuilder sb = new StringBuilder();
        int n = 0;
        for (int i = 0; n < words; i++) {
            for (String w : SENTENCES[i % SENTENCES.length].split(" ")) {
                if (n >= words) break;
                if (n > 0) sb.append(' ');
                sb.append(w);
                n++;
            }
        }
        return sb.toString();
    }

    /** What a good Light cleanup does to a long text: capitals, commas, full stops and paragraph breaks only. */
    private static String punctuate(String text) {
        String[] words = text.split(" ");
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < words.length; i++) {
            String w = words[i];
            if (i % 14 == 0) w = Character.toUpperCase(w.charAt(0)) + w.substring(1);
            sb.append(w);
            if (i % 14 == 13) {
                sb.append(i % 56 == 55 ? ".\n\n" : ". ");
            } else {
                sb.append(i % 6 == 5 ? ", " : " ");
            }
        }
        return sb.toString();
    }

    public static void main(String[] args) {
        // tokens
        eq("tokens basic", Arrays.asList("hello", "world", "it's", "5pm"), Fidelity.wordTokens("Hello, World! It's 5pm."));
        eq("tokens null", 0, Fidelity.wordTokens(null).size());
        eq("tokens bullets", Arrays.asList("one", "two", "three"), Fidelity.wordTokens("- one\n- two\n\n  three"));
        eq("tokens curly apostrophe", Arrays.asList("don't"), Fidelity.wordTokens("don’t"));
        eq("tokens quoted", Arrays.asList("quoted"), Fidelity.wordTokens("'quoted'"));

        // recall
        eq("recall empty raw", 1.0, Fidelity.wordRecall("", "whatever"));
        eq("recall nothing kept", 0.0, Fidelity.wordRecall("one two", ""));
        eq("recall multiset", 0.5, Fidelity.wordRecall("the the the cat", "the cat"));
        eq("recall order ignored", 1.0, Fidelity.wordRecall("red green blue", "blue red green"));
        eq("recall number words", 1.0, Fidelity.wordRecall("I have twenty-five apples", "I have 25 apples."));
        eq("recall symbol covers dollars", 1.0, Fidelity.wordRecall("five dollars", "$5"));
        eq("recall no symbol", 0.5, Fidelity.wordRecall("five dollars", "5"));

        // adding only whitespace and bullet markers never lowers recall
        String[] vocab = {"alpha", "beta", "um", "gamma", "twenty", "five", "don't", "the", "the", "x1"};
        String[] gaps = {" ", "\n", "\n\n", "\n- ", "  ", "\t", "\n* ", "\n• "};
        for (int seed = 0; seed < 20; seed++) {
            Random rnd = new Random(seed);
            int n = 1 + rnd.nextInt(60);
            StringBuilder raw = new StringBuilder();
            StringBuilder marked = new StringBuilder();
            for (int i = 0; i < n; i++) {
                String w = vocab[rnd.nextInt(vocab.length)];
                raw.append(i == 0 ? "" : " ").append(w);
                marked.append(gaps[rnd.nextInt(gaps.length)]).append(w);
            }
            eq("markers keep recall " + seed, true, Fidelity.wordRecall(raw.toString(), marked.toString()) >= 1.0);
            eq("identical is perfect " + seed, true, Fidelity.ok(raw.toString(), raw.toString(), "light")
                    && Fidelity.ok(raw.toString(), raw.toString(), "standard"));
        }

        // long dictation (1500 words)
        String raw = longText(1500);
        String good = punctuate(raw);
        eq("long good light", true, Fidelity.ok(raw, good, "light"));
        eq("long good standard", true, Fidelity.ok(raw, good, "standard"));
        String[] gw = good.split("\\s+");
        StringBuilder tenth = new StringBuilder();
        for (int i = 0; i < 150; i++) tenth.append(gw[i]).append(' ');
        eq("long summary light", false, Fidelity.ok(raw, tenth.toString(), "light"));
        eq("long summary standard", false, Fidelity.ok(raw, tenth.toString(), "standard"));
        StringBuilder missingChunk = new StringBuilder();
        for (int i = 0; i < gw.length; i++) if (i < 600 || i >= 750) missingChunk.append(gw[i]).append(' ');
        eq("long missing chunk", false, Fidelity.ok(raw, missingChunk.toString(), "light"));

        // looksValid keeps the old rules and adds the guard
        String r60 = longText(60);
        eq("looksValid good", true, ApiClient.looksValid(r60, punctuate(r60)));
        eq("looksValid summary", false, ApiClient.looksValid(r60, "I went to the market and cooked dinner."));
        String raw2 = "so um I like you know really want to go to the beach this weekend you know";
        String c2 = "I really want to go to the beach this weekend.";
        eq("looksValid default light", false, ApiClient.looksValid(raw2, c2));
        eq("looksValid standard", true, ApiClient.looksValid(raw2, c2, "standard"));
        eq("looksValid answer sized", false, ApiClient.looksValid("what is the capital of france",
                "what is the capital of france " + new String(new char[200]).replace('\0', 'x'), "standard"));
        eq("looksValid unknown strength is strict", false, ApiClient.looksValid(raw2, c2, "banana"));
        eq("looksValid null strength is strict", false, ApiClient.looksValid(raw2, c2, null));

        System.out.println("OK: " + checks + " fidelity checks passed");
    }
}
