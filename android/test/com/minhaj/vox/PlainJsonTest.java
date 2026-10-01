package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Plain-Java checks for PlainJson, the JSON reader and writer the relay sync uses. Run by CI, exits non-zero on failure. */
public final class PlainJsonTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    /** The text is not JSON: parse must say so with an IllegalArgumentException (never another exception). */
    private static void bad(String name, String text) {
        checks++;
        try {
            Object v = PlainJson.parse(text);
            System.err.println("FAIL " + name + ": parsed <" + v + "> from <" + text + ">");
            System.exit(1);
        } catch (IllegalArgumentException expected) {
            // good
        } catch (RuntimeException e) {
            System.err.println("FAIL " + name + ": wrong exception " + e);
            System.exit(1);
        }
    }

    private static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    private static List<Object> list(Object... v) {
        return new ArrayList<>(Arrays.asList(v));
    }

    public static void main(String[] args) {
        // values
        eq("null", null, PlainJson.parse("null"));
        eq("true", true, PlainJson.parse("true"));
        eq("false", false, PlainJson.parse(" false "));
        eq("string", "hi", PlainJson.parse("\"hi\""));
        eq("empty string", "", PlainJson.parse("\"\""));
        eq("empty object", map(), PlainJson.parse("{}"));
        eq("empty array", list(), PlainJson.parse("[ ]"));
        eq("object keeps its members", map("a", 1L, "b", list("x", false, null)), PlainJson.parse("{\"a\": 1, \"b\": [\"x\", false, null]}"));
        eq("object keeps the order of its members", Arrays.asList("b", "a"),
                new ArrayList<Object>(((Map<?, ?>) PlainJson.parse("{\"b\":1,\"a\":2}")).keySet()));
        eq("a repeated key keeps the last value", map("a", 2L), PlainJson.parse("{\"a\":1,\"a\":2}"));

        // numbers: whole numbers are Long, anything else Double, so seq and version compare with equals
        eq("integer is a Long", 42L, PlainJson.parse("42"));
        eq("negative integer", -7L, PlainJson.parse("-7"));
        eq("zero", 0L, PlainJson.parse("0"));
        eq("fraction is a Double", 1.5, PlainJson.parse("1.5"));
        eq("exponent is a Double", 1.790035199E9, PlainJson.parse("1.790035199E9"));
        eq("lower case exponent with sign", 1e-5, PlainJson.parse("1e-5"));
        eq("1.0 stays a Double", 1.0, PlainJson.parse("1.0"));
        eq("a number past the Long range becomes a Double", 1.0E20, PlainJson.parse("100000000000000000000"));
        eq("Long maximum", Long.MAX_VALUE, PlainJson.parse("9223372036854775807"));
        eq("a timestamp survives exactly", 1790035199.1234567, PlainJson.parse("1790035199.1234567"));

        // strings
        eq("escapes", "a\"b\\c/d\b\f\n\r\t", PlainJson.parse("\"a\\\"b\\\\c\\/d\\b\\f\\n\\r\\t\""));
        eq("unicode escape", "\u00e9\u20ac", PlainJson.parse("\"\\u00e9\\u20AC\""));
        eq("surrogate pair escape", "\uD83D\uDE00", PlainJson.parse("\"\\uD83D\\uDE00\""));
        eq("raw non-ASCII text", "caf\u00e9 \u4e2d\u6587", PlainJson.parse("\"caf\u00e9 \u4e2d\u6587\""));

        // what the relay and sync.py really send
        Object changes = PlainJson.parse("{\"notes\": [{\"seq\": 3, \"id\": \"" + "a" + "\", \"updated_at\": 1790035199.5, \"deleted\": false, \"tags\": [\"x\"]}], \"next\": 3, \"more\": false}");
        eq("changes page", map("notes", list(map("seq", 3L, "id", "a", "updated_at", 1790035199.5, "deleted", false, "tags", list("x"))),
                "next", 3L, "more", false), changes);
        eq("error body, string shape", map("error", "request too large"), PlainJson.parse("{\"error\": \"request too large\"}"));
        eq("error body, OpenAI shape", map("error", map("message", "upstream down")), PlainJson.parse("{\"error\":{\"message\":\"upstream down\"}}"));

        // things that are not JSON
        bad("empty", "");
        bad("blank", "   ");
        bad("null reference", null);
        bad("truncated object", "{\"a\": 1");
        bad("truncated string", "\"abc");
        bad("trailing comma in array", "[1,2,]");
        bad("trailing comma in object", "{\"a\":1,}");
        bad("single quotes", "{'a': 1}");
        bad("unquoted key", "{a: 1}");
        bad("garbage after the value", "{} x");
        bad("two values", "1 2");
        bad("html page", "<html><body>Not a relay</body></html>");
        bad("leading zero", "01");
        bad("plus sign", "+1");
        bad("lone minus", "-");
        bad("bare dot", "1.");
        bad("NaN", "NaN");
        bad("Infinity", "Infinity");
        bad("bad escape", "\"\\x\"");
        bad("short unicode escape", "\"\\u12\"");
        bad("raw newline in a string", "\"a\nb\"");
        bad("missing colon", "{\"a\" 1}");
        bad("non-string key", "{1: 2}");
        StringBuilder deep = new StringBuilder();
        for (int i = 0; i < 100000; i++) deep.append('[');
        bad("nesting deep enough to overflow the stack", deep.toString());

        // stringify
        eq("stringify null", "null", PlainJson.stringify(null));
        eq("stringify booleans", "[true,false]", PlainJson.stringify(list(true, false)));
        eq("stringify string", "\"hi\"", PlainJson.stringify("hi"));
        eq("stringify whole numbers", "[1,-2,3]", PlainJson.stringify(list(1, -2L, (short) 3)));
        eq("stringify a fraction", "1.5", PlainJson.stringify(1.5));
        eq("stringify object", "{\"a\":1,\"b\":[\"x\"]}", PlainJson.stringify(map("a", 1, "b", list("x"))));
        eq("stringify empty containers", "{\"a\":{},\"b\":[]}", PlainJson.stringify(map("a", map(), "b", list())));
        eq("stringify escapes", "\"a\\\"b\\\\c\\n\\r\\t\\b\\f\\u0001\"", PlainJson.stringify("a\"b\\c\n\r\t\b\f\u0001"));
        eq("stringify keeps non-ASCII text as it is", "\"caf\u00e9 \u4e2d\"", PlainJson.stringify("caf\u00e9 \u4e2d"));
        eq("stringify null member", "{\"a\":null}", PlainJson.stringify(map("a", null)));
        eq("stringify accepts any List and Map", "{\"k\":[1]}", PlainJson.stringify(map("k", java.util.Collections.singletonList(1))));

        // a double written and read back is the same double, in the forms Java prints (this is what markSynced relies on)
        double[] stamps = {1790035199.1234567, 1.0, 0.1, 1.0E-7, 123456789012.5, 0.0, 1790035199.0, 4.9E-324, 1.7976931348623157E308};
        for (double d : stamps) {
            eq("double " + d + " round trips", d, PlainJson.parse(PlainJson.stringify(d)));
            eq("double " + d + " is written as JSON, not a Java-only form", true, PlainJson.stringify(d).matches("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?"));
        }
        eq("an integer-valued Double comes back as a Double", 1790035199.0, PlainJson.parse(PlainJson.stringify(1790035199.0)));

        // what cannot be written
        Map<Object, Object> numberKey = new LinkedHashMap<>();
        numberKey.put(1, 2);
        for (Object v : new Object[]{Double.NaN, Double.POSITIVE_INFINITY, new Object(), new int[0], numberKey}) {
            checks++;
            try {
                PlainJson.stringify(v);
                System.err.println("FAIL stringify accepted " + v);
                System.exit(1);
            } catch (IllegalArgumentException expected) {
                // good
            }
        }

        // round trips of whole documents
        Map<String, Object> profile = map("user_context", "I lead Atlas.\nLine two \"quoted\"", "dictionary", list("Atlas", "wrong => right"),
                "people", list(), "default_style", "neutral", "cleanup", true, "language", "");
        eq("profile document", profile, PlainJson.parse(PlainJson.stringify(profile)));
        Map<String, Object> wire = map("id", "0123456789abcdef0123456789abcdef", "source", "voice note", "title", "T", "text", "caf\u00e9 \uD83D\uDE00",
                "raw", "", "created_at", 1790035199.25, "updated_at", 1790035200.5, "secs", 3.5, "device", "Pixel 7", "tags", list("a", "b"), "deleted", false);
        eq("note document", wire, PlainJson.parse(PlainJson.stringify(wire)));

        System.out.println("OK: " + checks + " checks passed");
    }
}
