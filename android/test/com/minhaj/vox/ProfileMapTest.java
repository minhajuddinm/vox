package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Plain-Java checks for ProfileMap: the settings of this phone as the relay's profile fields and back, in the same
 * encodings Windows uses (lists for the dictionary and the people, text for the rest). Run by CI, exits non-zero on failure.
 */
public final class ProfileMapTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    /** A map from alternating key, value arguments, in that order. */
    private static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    private static List<Object> list(Object... v) {
        return new ArrayList<Object>(Arrays.asList(v));
    }

    private static Set<String> set(Iterable<String> s) {
        Set<String> out = new LinkedHashSet<>();
        for (String x : s) out.add(x);
        return out;
    }

    private static Set<String> shared() {
        return ProfileMerge.SHARED_FIELDS;
    }

    private static Set<String> everything() {
        Set<String> all = new LinkedHashSet<>(ProfileMerge.SHARED_FIELDS);
        all.addAll(ProfileMerge.KEY_FIELDS);
        return all;
    }

    /** What Prefs hands over: every setting as this phone stores it (text, cleanup as a boolean). */
    private static Map<String, Object> stored() {
        return map("user_context", "I lead Atlas.\nSecond line.", "dictionary", "# One term per line.\nAtlas\nwrong => right\n",
                "people", "Ada\nGrace Hopper\n", "default_style", "casual", "cleanup", false, "language", "en", "my_cleanup_rules", "Write Atlas.\nKeep it flat.",
                "provider", "custom", "base_url", "https://api.example.com/v1", "stt_base_url", "http://100.64.0.7:8000/v1",
                "llm_base_url", "", "stt_model", "whisper-1", "llm_model", "gpt-4o-mini",
                "api_key", "gsk_main", "stt_api_key", "", "llm_api_key", "sk-llm");
    }

    public static void main(String[] args) {
        // lines: the dictionary and people as the list Windows keeps (what the page shows: trimmed, no blanks, no # comments)
        eq("lines split and trimmed", list("a", "b").toString(), ProfileMap.lines("a\nb\n").toString());
        eq("lines drop blanks and comments", Arrays.asList("a", "b => c"), ProfileMap.lines(" a \r\n\r\n# c\n  #d\nb => c\n"));
        eq("lines of null", new ArrayList<String>(), ProfileMap.lines(null));
        eq("lines of empty", new ArrayList<String>(), ProfileMap.lines(""));
        eq("lines of only blanks and a comment", new ArrayList<String>(), ProfileMap.lines("\n \n# header\n"));
        eq("lines keep non-ASCII", Arrays.asList("Zoë", "山田"), ProfileMap.lines("Zoë\n山田"));
        eq("lines keep => and inner spaces", Arrays.asList("new york => New York"), ProfileMap.lines("new york => New York"));
        eq("joinLines", "a\nb\n", ProfileMap.joinLines(Arrays.asList("a", "b")));
        eq("joinLines of none", "", ProfileMap.joinLines(new ArrayList<String>()));
        eq("joinLines of null", "", ProfileMap.joinLines(null));
        eq("lines(joinLines(x)) is x", Arrays.asList("x", "y z"), ProfileMap.lines(ProfileMap.joinLines(Arrays.asList("x", "y z"))));

        // toProfile: this phone's settings as relay fields
        Map<String, Object> p = ProfileMap.toProfile(stored());
        eq("fields, in the order of the shared list then the key list, without llm_reasoning",
                Arrays.asList("user_context", "dictionary", "people", "default_style", "cleanup", "language", "my_cleanup_rules",
                        "provider", "base_url", "stt_base_url", "llm_base_url", "stt_model", "llm_model", "api_key", "stt_api_key", "llm_api_key"),
                new ArrayList<String>(p.keySet()));
        eq("user_context is kept as typed", "I lead Atlas.\nSecond line.", p.get("user_context"));
        eq("dictionary is a list without the comment", Arrays.asList("Atlas", "wrong => right"), p.get("dictionary"));
        eq("people is a list", Arrays.asList("Ada", "Grace Hopper"), p.get("people"));
        eq("default_style", "casual", p.get("default_style"));
        eq("cleanup is a boolean", Boolean.FALSE, p.get("cleanup"));
        eq("language", "en", p.get("language"));
        eq("my_cleanup_rules is kept as it is", "Write Atlas.\nKeep it flat.", p.get("my_cleanup_rules"));
        eq("the rules arrive from the relay as text", "Rule.\n", ProfileMap.toStored(map("my_cleanup_rules", "Rule.\n")).get("my_cleanup_rules"));
        eq("rules that are not text are refused", map(), ProfileMap.accept(map("my_cleanup_rules", 5L), shared()));
        eq("base_url", "https://api.example.com/v1", p.get("base_url"));
        eq("an empty key is present and empty", "", p.get("stt_api_key"));
        eq("api_key", "gsk_main", p.get("api_key"));
        eq("llm_reasoning is not a setting of this phone", false, p.containsKey("llm_reasoning"));

        // a setting this phone did not hand over is simply absent (Prefs always hands over all of them)
        eq("nothing stored, nothing to send", map(), ProfileMap.toProfile(map()));
        eq("null stored map", map(), ProfileMap.toProfile(null));
        Map<String, Object> odd = ProfileMap.toProfile(map("user_context", null, "cleanup", "yes", "dictionary", 5));
        eq("null text counts as absent", false, odd.containsKey("user_context"));
        eq("a cleanup that is not a boolean is absent", false, odd.containsKey("cleanup"));
        eq("a dictionary that is not text is absent", false, odd.containsKey("dictionary"));

        // empty About you: present and empty, not absent (a removal is a change, see ProfileMerge)
        Map<String, Object> blank = ProfileMap.toProfile(map("user_context", "", "dictionary", "", "people", ""));
        eq("empty About you is sent as an empty text", "", blank.get("user_context"));
        eq("empty dictionary is an empty list", new ArrayList<Object>(), blank.get("dictionary"));
        eq("empty people is an empty list", new ArrayList<Object>(), blank.get("people"));
        eq("an empty About you survives accept", "", ProfileMap.accept(blank, shared()).get("user_context"));
        eq("an empty About you is stored as empty text", map("user_context", ""), ProfileMap.toStored(map("user_context", "")));
        eq("clearing About you here reaches the relay, it is not taken for a missing field", map("user_context", ""),
                ProfileMerge.mergeProfile(map("user_context", "x"), map("user_context", ""), map("user_context", "x"), shared()));

        // toStored: relay fields as this phone stores them
        Map<String, Object> s = ProfileMap.toStored(map("user_context", "hello", "dictionary", list("Atlas", "a => b"), "people", list("Ada"),
                "default_style", "formal", "cleanup", true, "language", "de", "base_url", "https://x.example.com/v1"));
        eq("toStored text", "hello", s.get("user_context"));
        eq("toStored dictionary as lines", "Atlas\na => b\n", s.get("dictionary"));
        eq("toStored people as lines", "Ada\n", s.get("people"));
        eq("toStored boolean", Boolean.TRUE, s.get("cleanup"));
        eq("toStored keeps only what it was given", 7, s.size());
        eq("toStored an empty list", map("dictionary", ""), ProfileMap.toStored(map("dictionary", list())));
        eq("toStored ignores what this phone has no setting for", map(), ProfileMap.toStored(map("llm_reasoning", "off", "app_styles", map("a", "b"), "x", 1)));
        eq("toStored null", map(), ProfileMap.toStored(null));

        // round trips: each shared field, phone -> relay -> phone. The phone's form is normalised (no comments, trimmed).
        Map<String, Object> single = new LinkedHashMap<>();
        Object[][] cases = {
                {"user_context", ""}, {"user_context", "About me"}, {"user_context", "  spaces kept  \n"}, {"user_context", "café 😀 \"quoted\""},
                {"dictionary", ""}, {"dictionary", "Atlas\n"}, {"dictionary", "Atlas\nwrong => right\nnew york => New York\n"},
                {"people", ""}, {"people", "Ada\nGrace Hopper\n"}, {"people", "Zoë\n"},
                {"default_style", "neutral"}, {"default_style", "formal"}, {"default_style", "casual"}, {"default_style", "very_casual"}, {"default_style", "raw"},
                {"cleanup", true}, {"cleanup", false}, {"language", ""}, {"language", "en"}, {"language", "hi"},
        };
        for (Object[] c : cases) {
            single.clear();
            single.put((String) c[0], c[1]);
            eq("round trip of " + c[0] + " = " + c[1], single, ProfileMap.toStored(ProfileMap.accept(ProfileMap.toProfile(single), shared())));
        }
        Map<String, Object> all = stored();
        Map<String, Object> back = ProfileMap.toStored(ProfileMap.accept(ProfileMap.toProfile(all), everything()));
        Map<String, Object> expectedBack = new LinkedHashMap<>(all);
        expectedBack.put("dictionary", "Atlas\nwrong => right\n");   // the comment line is not shared
        eq("round trip of every field", expectedBack, back);
        eq("a phone's profile is stable: accept(toProfile(x)) is toProfile(x)", ProfileMap.toProfile(all), ProfileMap.accept(ProfileMap.toProfile(all), everything()));

        // a profile from Windows (what windows/sync.py puts on the relay)
        Map<String, Object> windows = map("user_context", "I lead Atlas.", "dictionary", list("Atlas", "wrong => right"), "people", list("Ada"),
                "default_style", "neutral", "cleanup", true, "language", "");
        Map<String, Object> accepted = ProfileMap.accept(windows, shared());
        eq("a Windows profile is accepted as it is", windows, accepted);
        eq("and stored as lines", map("user_context", "I lead Atlas.", "dictionary", "Atlas\nwrong => right\n", "people", "Ada\n",
                "default_style", "neutral", "cleanup", true, "language", ""), ProfileMap.toStored(accepted));
        eq("its Windows comment lines are not kept", list("Atlas"),
                ProfileMap.accept(map("dictionary", list("# my words", "Atlas", "", "  ")), shared()).get("dictionary"));

        // accept: only the fields asked for, only values this phone can use
        Map<String, Object> doc = map("user_context", "u", "api_key", "secret", "base_url", "https://api.example.com/v1", "llm_reasoning", "off",
                "app_styles_android", map("com.whatsapp", "casual"), "future_field", 1L);
        eq("keys are not taken when only the shared fields are asked for", map("user_context", "u"), ProfileMap.accept(doc, shared()));
        eq("keys are taken when asked for, except llm_reasoning, which this phone has no setting for",
                map("user_context", "u", "base_url", "https://api.example.com/v1", "api_key", "secret"), ProfileMap.accept(doc, everything()));
        eq("fields of other devices are ignored", map(), ProfileMap.accept(map("app_styles_android", map("a", "b"), "future_field", 1L), everything()));
        eq("accept of null", map(), ProfileMap.accept(null, everything()));
        eq("accept returns a new map", false, ProfileMap.accept(windows, shared()) == windows);

        // values of the wrong type are ignored, as if the relay did not have the field
        Map<String, Object> junk = map("user_context", 5L, "dictionary", "Atlas", "people", list("Ada", 7L), "default_style", "weird",
                "cleanup", "true", "language", list("en"));
        eq("wrong types are dropped", map(), ProfileMap.accept(junk, shared()));
        eq("a dictionary entry that is not text drops the whole list, never half of it", false, ProfileMap.accept(junk, shared()).containsKey("people"));
        eq("an unknown style is dropped", false, ProfileMap.accept(map("default_style", ""), shared()).containsKey("default_style"));
        eq("every style of the page is accepted", 5, countAccepted("default_style", "formal", "casual", "very_casual", "neutral", "raw"));
        eq("null values are dropped", map(), ProfileMap.accept(map("user_context", null, "dictionary", null), shared()));

        // normalisation: what the phone would read back from its own settings, so a round trip never looks like a change
        eq("text fields are trimmed, About you is not", map("language", "en", "user_context", " keep me \n"),
                ProfileMap.accept(map("language", " en ", "user_context", " keep me \n"), shared()));
        eq("list entries are trimmed, split at line breaks, and blanks and comments dropped", list("a", "b", "c => d"),
                ProfileMap.accept(map("dictionary", list(" a ", "b\n# no\n", "", "c => d")), shared()).get("dictionary"));
        eq("keys are trimmed", "secret", ProfileMap.accept(map("api_key", " secret\n"), everything()).get("api_key"));

        // addresses: the same rule as the settings page (Endpoint.error), and the same form (no trailing slash)
        eq("an address with a trailing slash is normalised", "https://api.example.com/v1", ProfileMap.accept(map("base_url", "https://api.example.com/v1/"), everything()).get("base_url"));
        eq("a blank role address is fine (it means the main one)", "", ProfileMap.accept(map("stt_base_url", ""), everything()).get("stt_base_url"));
        eq("plain http to a private host is fine", "http://100.64.0.7:8000/v1", ProfileMap.accept(map("llm_base_url", "http://100.64.0.7:8000/v1"), everything()).get("llm_base_url"));
        eq("plain http to a public host is refused", map(), ProfileMap.accept(map("base_url", "http://example.com/v1"), everything()));
        eq("an address that is not http(s) is refused", map(), ProfileMap.accept(map("stt_base_url", "ftp://example.com", "llm_base_url", "example.com"), everything()));
        eq("toStored normalises an address too", map("base_url", "https://x.example.com/v1"), ProfileMap.toStored(map("base_url", "https://x.example.com/v1/")));

        System.out.println("OK: " + checks + " checks passed");
    }

    /** How many of the values are accepted as the field. */
    private static int countAccepted(String field, String... values) {
        int n = 0;
        for (String v : values) if (ProfileMap.accept(map(field, v), shared()).containsKey(field)) n++;
        return n;
    }
}
