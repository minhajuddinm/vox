package com.minhaj.vox;

import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
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
            "user_context", "dictionary", "people", "default_style", "cleanup", "language", "my_cleanup_rules")));

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
     */
    static Object merge3(Object base, Object local, Object remote) {
        if (Objects.equals(local, remote)) return local;
        if (Objects.equals(local, base)) return remote;
        if (Objects.equals(remote, base)) return local;
        return remote;
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

    private static Object get(Map<String, ?> m, String key) {
        return m == null ? null : m.get(key);
    }
}
