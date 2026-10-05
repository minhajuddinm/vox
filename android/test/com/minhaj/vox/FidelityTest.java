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

    private static String withoutBlock(String[] words, int start, int n) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < words.length; i++) if (i < start || i >= start + n) sb.append(words[i]).append(' ');
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

        // big numbers, "and" and "point" inside a number, "p m", spoken "at" / "dot"
        eq("recall two hundred", 1.0, Fidelity.wordRecall("it costs two hundred dollars", "It costs $200."));
        eq("recall two thousand twenty six", 1.0, Fidelity.wordRecall("two thousand twenty six", "2026"));
        eq("recall hundred and five", 1.0, Fidelity.wordRecall("one hundred and five degrees", "105°"));
        eq("recall 1,250", 1.0, Fidelity.wordRecall("one thousand two hundred and fifty", "1,250"));
        eq("recall a hundred a thousand", 1.0, Fidelity.wordRecall("a hundred and a thousand", "100 and 1000"));
        eq("recall point", 1.0, Fidelity.wordRecall("three point five liters", "3.5 liters"));
        eq("recall p m", 1.0, Fidelity.wordRecall("three thirty p m", "3:30 PM"));
        eq("recall a m", 1.0, Fidelity.wordRecall("seven a m", "7 a.m."));
        eq("recall wrong number", 0.0, Fidelity.wordRecall("two hundred", "300"));
        eq("recall wrong decimal", 0.0, Fidelity.wordRecall("three point five", "3.6"));
        // ordinals, scale words, half past N
        eq("recall ordinal", 1.0, Fidelity.wordRecall("the twenty first of march", "the 21st of March"));
        eq("recall ordinal written plain", 1.0, Fidelity.wordRecall("the twenty first of march", "the 21 of March"));
        eq("recall ordinals", 1.0, Fidelity.wordRecall("the twenty-second and the thirtieth", "the 22nd and the 30th"));
        eq("recall ordinal suffixes", 1.0, Fidelity.wordRecall("the third eleventh twelfth thirteenth", "the 3rd 11th 12th 13th"));
        eq("recall million", 1.0, Fidelity.wordRecall("five million two hundred thousand", "5,200,000"));
        eq("recall crore lakh", 1.0, Fidelity.wordRecall("two crore fifty lakh and five", "2,50,00,005"));
        eq("recall a billion", 1.0, Fidelity.wordRecall("a billion", "1,000,000,000"));
        eq("recall half past", 1.0, Fidelity.wordRecall("half past three", "3:30"));
        eq("recall rupees Rs", 1.0, Fidelity.wordRecall("five lakh rupees", "Rs. 5,00,000"));
        eq("recall rupees bare", 0.5, Fidelity.wordRecall("five lakh rupees", "5,00,000"));
        eq("recall scale out of order", 0.5, Fidelity.wordRecall("two thousand million", "2000"));
        eq("recall wrong lakh", 0.0, Fidelity.wordRecall("five lakh twenty thousand", "5,00,000"));
        eq("recall and is a word", true, Fidelity.wordRecall("salt and pepper", "salt pepper") < 1.0);
        eq("recall and then some", true, Fidelity.wordRecall("one hundred and then some", "100 then some") < 1.0);
        eq("recall email", 1.0, Fidelity.wordRecall("mail john at gmail dot com", "Mail john@gmail.com."));
        eq("recall www", 1.0, Fidelity.wordRecall("see www dot example dot org", "See www.example.org."));
        eq("recall plain at", true, Fidelity.wordRecall("meet me at noon", "Meet me noon.") < 1.0);
        eq("recall full stop is not dot", true, Fidelity.wordRecall("a dot on the page", "A on the page.") < 1.0);
        eq("recall dot said and written", 1.0, Fidelity.wordRecall("a dot on the page", "A dot. On the page."));
        String[][] common = {
                {"it costs two hundred dollars for the big one", "It costs $200 for the big one."},
                {"two thousand twenty six", "2026"},
                {"one hundred and five degrees", "105°"},
                {"john at gmail dot com", "john@gmail.com"},
                {"three point five liters", "3.5 liters"},
                {"three thirty p m", "3:30 PM"},
        };
        for (String[] pair : common) {
            eq("common light " + pair[0], true, Fidelity.ok(pair[0], pair[1], "light"));
            eq("common standard " + pair[0], true, Fidelity.ok(pair[0], pair[1], "standard"));
        }

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

        // Light also loses at most 12 words whatever the percentage (twin of the Python tests)
        String raw1k = longText(1000);
        String[] w1k = punctuate(raw1k).split("\\s+");
        eq("light cap 12 passes", true, Fidelity.ok(raw1k, withoutBlock(w1k, 400, 12), "light"));
        eq("light cap 13 fails", false, Fidelity.ok(raw1k, withoutBlock(w1k, 400, 13), "light"));
        eq("light cap 25 fails", false, Fidelity.ok(raw1k, withoutBlock(w1k, 400, 25), "light"));
        eq("light cap 25 recall alone passes", true, Fidelity.wordRecall(raw1k, withoutBlock(w1k, 400, 25)) >= 0.97);
        eq("light cap 25 looksValid", false, ApiClient.looksValid(raw1k, withoutBlock(w1k, 400, 25), "light"));
        eq("standard 25 passes", true, Fidelity.ok(raw1k, withoutBlock(w1k, 400, 25), "standard"));
        eq("standard 200 fails", false, Fidelity.ok(raw1k, withoutBlock(w1k, 400, 200), "standard"));
        String noisy = raw1k.replaceAll(" and ", " um and ");
        eq("noises are not counted", true, Fidelity.ok(noisy, punctuate(raw1k), "light"));

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

        eq("cleanStrength null is light", "light", Fidelity.cleanStrength(null));
        eq("cleanStrength standard", "standard", Fidelity.cleanStrength("standard"));
        eq("fallbackText null", "", ApiClient.fallbackText(null));
        eq("fallbackText capitalises after a spoken line break", "One\nTwo.", ApiClient.fallbackText("one new line two"));
        eq("fallbackText null style counts as neutral", "Hello there.", ApiClient.fallbackText("um hello there", null, null));
        eq("fallbackText raw style keeps the words", "Um hello there", ApiClient.fallbackText("um hello there", "raw", "light"));

        System.out.println("OK: " + checks + " fidelity checks passed");
    }
}
