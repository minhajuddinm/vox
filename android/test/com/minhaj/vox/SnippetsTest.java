package com.minhaj.vox;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Plain-Java checks for the snippets beyond the shared golden rows (kind snippets): the caps of Snippets.clean, the stored
 * form ProfileMap reads and writes, and the profile field. The same checks are in tests/test_snippets.py for Python.
 */
public final class SnippetsTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static String rep(char c, int n) {
        return new String(new char[n]).replace('\0', c);
    }

    public static void main(String[] args) {
        Map<String, Object> raw = new LinkedHashMap<>();
        raw.put(" my  email ", "a@b.c");
        raw.put("", "x");
        raw.put("blank", "");
        raw.put("num", 5L);
        raw.put("!!", "x");
        raw.put("My Email", "dup");
        raw.put("sig", rep('s', 3000));
        Map<String, String> want = new LinkedHashMap<>();
        want.put("my email", "a@b.c");
        want.put("sig", rep('s', Snippets.MAX_EXPANSION));
        eq("clean keeps text pairs and cuts", want, Snippets.clean(raw));

        Map<String, String> many = new LinkedHashMap<>();
        for (int i = 0; i < 80; i++) many.put("t" + i, "x");
        eq("at most 50", 50, Snippets.clean(many).size());
        Map<String, String> big = new LinkedHashMap<>();
        for (int i = 0; i < 20; i++) big.put("t" + i, rep('y', 2000));
        int total = 0;
        for (String v : Snippets.clean(big).values()) total += v.length();
        eq("total cap", true, total <= Snippets.MAX_TOTAL);
        Map<String, String> longTrigger = new LinkedHashMap<>();
        longTrigger.put(rep('t', 101), "x");
        eq("trigger cap", 0, Snippets.clean(longTrigger).size());
        eq("null is none", 0, Snippets.clean(null).size());
        eq("a list is none", 0, Snippets.clean(java.util.Arrays.asList("a")).size());
        Map<String, String> crlf = new LinkedHashMap<>();
        crlf.put("a", "x\r\ny");
        eq("line breaks", "x\ny", Snippets.clean(crlf).get("a"));
        // a code point beyond the BMP counts once, as in Python
        Map<String, String> emoji = new LinkedHashMap<>();
        emoji.put("e", rep('a', 1999) + "😀😀");
        eq("cut in code points", rep('a', 1999) + "😀", Snippets.clean(emoji).get("e"));

        // the stored form: JSON text on the phone, a map in the profile
        eq("stored unreadable", 0, ProfileMap.snippetsOf("not json").size());
        eq("stored empty", 0, ProfileMap.snippetsOf("").size());
        eq("stored list", 0, ProfileMap.snippetsOf("[1]").size());
        eq("stored map", "me@example.com", ProfileMap.snippetsOf("{\"my email\":\"me@example.com\",\"n\":3}").get("my email"));
        Map<String, Object> stored = new LinkedHashMap<>();
        stored.put("snippets", "{\"my email\":\"me@example.com\"}");
        Map<String, Object> profile = ProfileMap.toProfile(stored);
        Map<String, String> expect = new LinkedHashMap<>();
        expect.put("my email", "me@example.com");
        eq("to profile", expect, profile.get("snippets"));
        eq("back to stored", "{\"my email\":\"me@example.com\"}", ProfileMap.toStored(profile).get("snippets"));
        eq("shared field", true, ProfileMerge.SHARED_FIELDS.contains("snippets"));
        Map<String, Object> local = new LinkedHashMap<>(expect);
        eq("a blank relay map keeps the local snippets", local, ProfileMerge.merge3(null, local, new LinkedHashMap<String, Object>()));

        eq("apply", "Send it to me@example.com please.", Snippets.apply("Send it to my email please.", expect));
        eq("apply null", null, Snippets.apply(null, expect));
        System.out.println("OK: " + checks + " checks");
    }
}
