package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * The merge of the profile that follows the user from device to device (About you, dictionary, people, ...), the
 * same rule as windows/sync.py (PROFILE_FIELDS, PROFILE_KEY_FIELDS, merge3), so the phone and the PC settle a
 * conflict the same way. Pure Java (no android.* or org.json) so the off-device tests can run it. The merge rule
 * and the two field lists are pinned by the merge3 and profilefields rows of spec/golden.txt, which
 * tests/test_parity.py runs against sync.py and ParityTest runs against this class.
 */
final class ProfileMerge {
    private ProfileMerge() { }

    /** Settings that always travel (sync.PROFILE_FIELDS). Read-only, in the order of the Python tuple. */
    static final Set<String> SHARED_FIELDS = Collections.unmodifiableSet(new LinkedHashSet<>(Arrays.asList(
            "user_context", "dictionary", "people", "default_style", "cleanup", "language", "my_cleanup_rules", "snippets")));

    /**
     * Provider settings and API keys, which travel only when the user switched on relay sync of keys
     * (sync.PROFILE_KEY_FIELDS). Read-only, in the order of the Python tuple.
     */
    static final Set<String> KEY_FIELDS = Collections.unmodifiableSet(new LinkedHashSet<>(Arrays.asList(
            "provider", "base_url", "stt_base_url", "llm_base_url", "stt_model", "llm_model", "llm_reasoning",
            "api_key", "stt_api_key", "llm_api_key")));

    /**
     * One field, three-way (sync.merge3): the side that changed since {@code base} wins; if both changed it
     * differently the relay's ({@code remote}) value wins. null means the field is absent on that side, and a null
     * result means the field is dropped (a removal is a change like any other). The values are compared with
     * {@code equals}, which agrees with Python's {@code ==} for strings, booleans and lists of them; convert numbers
     * to one type first, since an Integer 1 is not equal to a Long 1.
     * A list (dictionary, people) or a map (snippets) changed on both sides merges item by item instead
     * ({@link #mergeItems}), so a word learned here while the PC added another keeps both. One more exception: a field
     * with no base (this device's first sync) whose relay value is blank ("" or an empty list) keeps the local value when
     * that is not blank, because the blank is only the other device's default.
     */
    static Object merge3(Object base, Object local, Object remote) {
        if (base == null && local != null && remote != null && isBlank(remote) && !isBlank(local)) return local;
        return mergeOne(base, local, remote);
    }

    private static Object mergeOne(Object base, Object local, Object remote) {
        if (Objects.equals(local, remote) || Objects.equals(remote, base)) return local;
        if (Objects.equals(local, base)) return remote;
        return mergeItems(base, local, remote);
    }

    /**
     * Both sides changed a list or a map: an item (a map's key) added on either side is kept, one removed on either side
     * goes, and a map key changed on both takes the relay's value; the relay's order first, then this device's additions.
     * Anything else (different types) is the relay's value. Twin of _merge_items in windows/sync.py.
     */
    @SuppressWarnings("unchecked")
    static Object mergeItems(Object base, Object local, Object remote) {
        if (local instanceof List && remote instanceof List && (base == null || base instanceof List)) {
            Set<Object> b = new HashSet<>(base == null ? Collections.emptyList() : (List<Object>) base);
            Set<Object> l = new HashSet<>((List<Object>) local), r = new HashSet<>((List<Object>) remote);
            List<Object> out = new ArrayList<>();
            Set<Object> seen = new HashSet<>();
            for (Object x : (List<Object>) remote) {
                if (kept(x, b, l, r)) { out.add(x); seen.add(x); }
            }
            for (Object x : (List<Object>) local) {
                if (!r.contains(x) && !seen.contains(x) && kept(x, b, l, r)) { out.add(x); seen.add(x); }
            }
            return out;
        }
        if (local instanceof Map && remote instanceof Map && (base == null || base instanceof Map)) {
            Map<Object, Object> b = base == null ? Collections.emptyMap() : (Map<Object, Object>) base;
            Map<Object, Object> l = (Map<Object, Object>) local, r = (Map<Object, Object>) remote;
            List<Object> keys = new ArrayList<>(r.keySet());
            for (Object k : l.keySet()) if (!r.containsKey(k)) keys.add(k);
            Map<Object, Object> out = new LinkedHashMap<>();
            for (Object k : keys) {
                Object v = mergeOne(b.get(k), l.get(k), r.get(k));
                if (v != null) out.put(k, v);
            }
            return out;
        }
        return remote;
    }

    private static boolean kept(Object x, Set<Object> b, Set<Object> l, Set<Object> r) {
        return (Boolean) mergeOne(b.contains(x), l.contains(x), r.contains(x));
    }

    private static boolean isBlank(Object v) {
        return "".equals(v) || (v instanceof java.util.List && ((java.util.List<?>) v).isEmpty())
                || (v instanceof Map && ((Map<?, ?>) v).isEmpty());   // an empty snippets map
    }

    /**
     * The merged profile: {@link #merge3} for each field of {@code fields}, on the maps {@code base} (the snapshot
     * stored at the last sync), {@code local} and {@code remote}. Fields outside {@code fields} are ignored on
     * all three sides, so the result holds only those (a new map; the inputs are not changed). A null map counts
     * as empty and a null value as absent. The caller decides when to merge at all: sync_profile takes the local
     * profile as it is when the relay has none yet or has not changed since the last sync.
     */
    static Map<String, Object> mergeProfile(Map<String, ?> base, Map<String, ?> local, Map<String, ?> remote,
                                            Set<String> fields) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (String k : fields) {
            Object v = merge3(get(base, k), get(local, k), get(remote, k));
            if (v != null) out.put(k, v);
        }
        return out;
    }

    /**
     * The settings to write after a sync merge (AND-15): each field of {@code received} (merged from {@code seen}, this
     * phone's settings when the run read them) put onto {@code current} (the settings now, read under the learn lock).
     * A field that changed here meanwhile (a word learned while the run was in flight) is merged again with the
     * received value, item by item ({@code merge3(seen, current, received)}: both words stay), instead of being
     * overwritten. Windows reaches the same end by merging again when the file changed (sync_profile's take).
     */
    static Map<String, Object> onto(Map<String, ?> seen, Map<String, ?> current, Map<String, ?> received) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (received == null) return out;
        for (Map.Entry<String, ?> e : received.entrySet()) {
            Object was = get(seen, e.getKey()), now = get(current, e.getKey());
            Object v = Objects.equals(now, was) ? e.getValue() : mergeOne(was, now, e.getValue());
            if (v != null) out.put(e.getKey(), v);
        }
        return out;
    }

    private static Object get(Map<String, ?> m, String key) {
        return m == null ? null : m.get(key);
    }
}
