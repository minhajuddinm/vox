package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Plain-Java checks for ProfileMerge beyond the golden rows (merge3 and profilefields in spec/golden.txt, run by
 * ParityTest). Run by CI, exits non-zero on failure.
 */
public final class ProfileMergeTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    /** A map from alternating key, value arguments; a null value stays in the map as a present key. */
    private static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new HashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    private static Set<String> set(String... s) {
        return new LinkedHashSet<>(Arrays.asList(s));
    }

    private static List<String> list(String... s) {
        return new ArrayList<>(Arrays.asList(s));
    }

    public static void main(String[] args) {
        // merge3 on single values (the golden rows cover the string cases, these the other value types)
        eq("lists compare by content, local changed", list("a", "b"),
                ProfileMerge.merge3(list("a"), list("a", "b"), list("a")));
        eq("lists compare by content, remote changed", list("c"),
                ProfileMerge.merge3(list("a"), list("a"), list("c")));
        eq("lists both changed: the relay wins", list("c"),
                ProfileMerge.merge3(list("a"), list("b"), list("c")));
        eq("lists both changed the same way", list("b"),
                ProfileMerge.merge3(list("a"), list("b"), list("b")));
        eq("booleans: local turned it on", true, ProfileMerge.merge3(false, true, false));
        eq("booleans: relay turned it off", false, ProfileMerge.merge3(true, true, false));
        eq("empty string is a value, not absent", "", ProfileMerge.merge3("a", "", "a"));
        eq("absent on all sides", null, ProfileMerge.merge3(null, null, null));
        eq("removed here, changed there: the relay's value", "c", ProfileMerge.merge3("a", null, "c"));
        eq("changed here, removed there: the relay's removal", null, ProfileMerge.merge3("a", "b", null));

        // mergeProfile: the scenario of tests/test_sync_profile.py::test_merge3_takes_the_side_that_changed...
        Set<String> abcd = set("a", "b", "c", "d");
        eq("each side keeps its own change, a clash goes to the relay",
                map("a", 2, "b", 3, "c", 6, "d", 1),
                ProfileMerge.mergeProfile(map("a", 1, "b", 1, "c", 1, "d", 1), map("a", 2, "b", 1, "c", 5, "d", 1),
                        map("a", 1, "b", 3, "c", 6, "d", 1), abcd));
        eq("same value on both sides with no base", map("x", 1),
                ProfileMerge.mergeProfile(map(), map("x", 1), map("x", 1), set("x")));
        eq("only here", map("x", 1), ProfileMerge.mergeProfile(map(), map("x", 1), map(), set("x")));
        eq("only on the relay", map("y", 2), ProfileMerge.mergeProfile(map(), map(), map("y", 2), set("y")));
        eq("removed here, unchanged there", map(),
                ProfileMerge.mergeProfile(map("z", 1), map(), map("z", 1), set("z")));
        eq("removed on the relay, unchanged here", map(),
                ProfileMerge.mergeProfile(map("z", 1), map("z", 1), map(), set("z")));
        eq("removed on both", map(), ProfileMerge.mergeProfile(map("z", 1), map(), map(), set("z")));

        // a device's profile: About you and dictionary edited on different devices
        eq("different fields edited on different devices",
                map("user_context", "B wrote this", "dictionary", list("one", "two"), "people", list("Ada")),
                ProfileMerge.mergeProfile(
                        map("user_context", "original", "dictionary", list("one")),
                        map("user_context", "A wrote this", "dictionary", list("one", "two")),
                        map("user_context", "B wrote this", "dictionary", list("one"), "people", list("Ada")),
                        ProfileMerge.SHARED_FIELDS));

        // only the fields of the set take part: a device-specific setting or a field of another device never merges in
        eq("fields outside the set are dropped from every side", map("user_context", "mine"),
                ProfileMerge.mergeProfile(map("hotkey", "x", "user_context", "old"),
                        map("hotkey", "ctrl", "user_context", "mine", "api_key", "gsk_local"),
                        map("app_styles_android", map("com.whatsapp", "casual"), "user_context", "old", "api_key", "gsk_relay"),
                        ProfileMerge.SHARED_FIELDS));
        eq("keys merge when their fields are in the set", map("api_key", "gsk_relay", "provider", "groq"),
                ProfileMerge.mergeProfile(map("api_key", "gsk_old"), map("api_key", "gsk_local", "provider", "groq"),
                        map("api_key", "gsk_relay"), union(ProfileMerge.SHARED_FIELDS, ProfileMerge.KEY_FIELDS)));
        eq("a key left in the snapshot after keys were switched off is dropped", map("user_context", "u"),
                ProfileMerge.mergeProfile(map("api_key", "gsk", "user_context", "u"), map("user_context", "u"),
                        map("user_context", "u"), ProfileMerge.SHARED_FIELDS));
        eq("empty field set", map(), ProfileMerge.mergeProfile(map("a", 1), map("a", 2), map("a", 3), set()));

        // null maps count as empty; a null value counts as absent
        eq("null maps", map("a", 1), ProfileMerge.mergeProfile(null, map("a", 1), null, set("a")));
        eq("all null", map(), ProfileMerge.mergeProfile(null, null, null, set("a")));
        eq("a null value is a removal", map(),
                ProfileMerge.mergeProfile(map("a", "x"), map("a", null), map("a", "x"), set("a")));
        eq("a null value is not kept when nothing else has the field", map(),
                ProfileMerge.mergeProfile(map(), map("a", null), map(), set("a")));

        // the inputs are not touched and the result is a new map
        Map<String, Object> base = map("a", 1), local = map("a", 2), remote = map("a", 1);
        Map<String, Object> merged = ProfileMerge.mergeProfile(base, local, remote, set("a"));
        merged.put("changed", true);
        eq("base untouched", map("a", 1), base);
        eq("local untouched", map("a", 2), local);
        eq("remote untouched", map("a", 1), remote);

        // the field lists are exactly those of windows/sync.py (PROFILE_FIELDS, PROFILE_KEY_FIELDS); the profilefields
        // golden rows pin the same thing against the Python side
        eq("shared fields", list("user_context", "dictionary", "people", "default_style", "cleanup", "language"),
                new ArrayList<>(ProfileMerge.SHARED_FIELDS));
        eq("key fields", list("provider", "base_url", "stt_base_url", "llm_base_url", "stt_model", "llm_model",
                "llm_reasoning", "api_key", "stt_api_key", "llm_api_key"), new ArrayList<>(ProfileMerge.KEY_FIELDS));
        Set<String> both = new LinkedHashSet<>(ProfileMerge.SHARED_FIELDS);
        both.retainAll(ProfileMerge.KEY_FIELDS);
        eq("the two lists do not overlap", set(), both);
        for (String never : new String[] {"app_styles", "hotkey", "input_device", "relay_token", "relay_url", "your_name"}) {
            eq(never + " never travels", false,
                    ProfileMerge.SHARED_FIELDS.contains(never) || ProfileMerge.KEY_FIELDS.contains(never));
        }
        try {
            ProfileMerge.SHARED_FIELDS.add("hotkey");
            eq("SHARED_FIELDS is read-only", "UnsupportedOperationException", "no exception");
        } catch (UnsupportedOperationException expected) {
            checks++;
        }
        try {
            ProfileMerge.KEY_FIELDS.remove("api_key");
            eq("KEY_FIELDS is read-only", "UnsupportedOperationException", "no exception");
        } catch (UnsupportedOperationException expected) {
            checks++;
        }

        System.out.println("OK: " + checks + " checks passed");
    }

    private static Set<String> union(Set<String> a, Set<String> b) {
        Set<String> out = new LinkedHashSet<>(a);
        out.addAll(b);
        return out;
    }
}
