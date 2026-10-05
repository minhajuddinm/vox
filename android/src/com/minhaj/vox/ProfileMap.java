package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The settings of this phone as the relay's profile fields, and back, in the encodings the Windows app uses
 * (windows/sync.py PROFILE_FIELDS and PROFILE_KEY_FIELDS; the field names are ProfileMerge's two lists). Windows
 * keeps the dictionary and the people as lists of text, the snippets as a map of text, and everything else as text or a
 * boolean; the phone keeps the two lists as text with one entry per line and the snippets as their JSON text, so those
 * are converted; the other fields are the same on both sides.
 *
 * Two forms of a setting appear here. The <em>stored</em> form is what this phone keeps in its preferences under the
 * same name (text; {@code cleanup} as a boolean; {@code dictionary} and {@code people} as lines). The <em>profile</em>
 * form is what the relay holds (the two lists as Lists of text). Every value is cleaned the same way whichever way it
 * goes, so a setting that goes from this phone to the relay and back is unchanged and a profile never looks changed
 * only because of how a value was written.
 *
 * {@code llm_reasoning} is on the key list but is not a setting of this phone (it always behaves as "auto"), so it is
 * never read or written here; SyncEngine leaves what other devices put there alone. Pure Java (no android.* or
 * org.json) so the off-device tests can run it.
 */
final class ProfileMap {
    private ProfileMap() { }

    /** The styles a dictation can have (Prefs.DEFAULT_APP_STYLES lists the same five). */
    static final Set<String> STYLES = Collections.unmodifiableSet(new LinkedHashSet<>(Arrays.asList(
            "formal", "casual", "very_casual", "neutral", "raw")));

    private static final int NONE = 0, CONTEXT = 1, LINES = 2, FLAG = 3, STYLE = 4, ADDRESS = 5, TEXT = 6, SNIPPETS = 7;

    private static int kindOf(String field) {
        switch (field) {
            case "snippets": return SNIPPETS;   // a map {trigger: text}; stored on the phone as its JSON text
            case "user_context":
            case "my_cleanup_rules": return CONTEXT;   // text kept as it is, like About you
            case "dictionary":
            case "people": return LINES;
            case "cleanup": return FLAG;
            case "default_style": return STYLE;
            case "base_url":
            case "stt_base_url":
            case "llm_base_url": return ADDRESS;
            case "language":
            case "provider":
            case "stt_model":
            case "llm_model":
            case "api_key":
            case "stt_api_key":
            case "llm_api_key": return TEXT;
            default: return NONE;   // llm_reasoning, and any field added later that this phone does not know
        }
    }

    /** The fields this phone takes part in, in the order of the two lists of ProfileMerge. */
    private static final List<String> FIELDS;

    static {
        List<String> all = new ArrayList<>();
        for (String f : ProfileMerge.SHARED_FIELDS) if (kindOf(f) != NONE) all.add(f);
        for (String f : ProfileMerge.KEY_FIELDS) if (kindOf(f) != NONE) all.add(f);
        FIELDS = Collections.unmodifiableList(all);
    }

    // ------------------------------------------------------------------ the two list fields

    /**
     * The entries of a dictionary or people setting as a list: one per line, trimmed, without blank lines and without
     * comment lines (starting with #), which is what the settings pages show. Empty for null.
     */
    static List<String> lines(String raw) {
        List<String> out = new ArrayList<>();
        if (raw == null) return out;
        for (String line : raw.split("\n", -1)) {
            String t = line.trim();
            if (!t.isEmpty() && !t.startsWith("#")) out.add(t);
        }
        return out;
    }

    /** The entries as the text the phone stores: one per line, each followed by a line break. "" for none. */
    static String joinLines(List<String> entries) {
        StringBuilder sb = new StringBuilder();
        if (entries != null) for (String e : entries) sb.append(e).append('\n');
        return sb.toString();
    }

    /**
     * The snippets setting as the phone stores it (the JSON text of {trigger: text}) read back as a map, cleaned
     * (Snippets.clean). Empty for null, unreadable JSON or anything that is not an object.
     */
    static Map<String, String> snippetsOf(String json) {
        if (json == null || json.trim().isEmpty()) return Snippets.clean(null);
        try {
            return Snippets.clean(PlainJson.parse(json));
        } catch (RuntimeException bad) {
            return Snippets.clean(null);
        }
    }

    // ------------------------------------------------------------------ cleaning one value

    /**
     * The value as it is kept on both sides, or null when this phone cannot use it (wrong type, an address that
     * Endpoint refuses, a style that does not exist). {@code v} is in profile form.
     */
    private static Object clean(String field, Object v) {
        switch (kindOf(field)) {
            case CONTEXT:
                return v instanceof String ? v : null;   // kept as typed: a trailing line break is part of the text
            case TEXT:
                return v instanceof String ? ((String) v).trim() : null;
            case FLAG:
                return v instanceof Boolean ? v : null;
            case STYLE: {
                if (!(v instanceof String)) return null;
                String s = ((String) v).trim();
                return STYLES.contains(s) ? s : null;
            }
            case ADDRESS: {
                if (!(v instanceof String)) return null;
                String a = Endpoint.normalize((String) v);
                return Endpoint.error(a) == null ? a : null;
            }
            case SNIPPETS:
                return v instanceof Map ? Snippets.clean(v) : null;   // the same caps on both sides, so it never looks changed
            case LINES: {
                if (!(v instanceof List)) return null;
                StringBuilder sb = new StringBuilder();
                for (Object item : (List<?>) v) {
                    if (!(item instanceof String)) return null;   // half a list is worse than none
                    sb.append((String) item).append('\n');
                }
                return lines(sb.toString());
            }
            default:
                return null;
        }
    }

    // ------------------------------------------------------------------ the three conversions

    /**
     * This phone's settings as relay profile fields. {@code stored} holds the stored form of each setting under its
     * name (Prefs fills in the defaults, so it always hands over all of them); a setting that is missing, or that
     * cannot be shared, is left out. Key fields are included: SyncEngine decides whether they travel.
     */
    static Map<String, Object> toProfile(Map<String, ?> stored) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (stored == null) return out;
        for (String f : FIELDS) {
            Object v = stored.get(f);
            if (kindOf(f) == LINES) v = v instanceof String ? lines((String) v) : null;
            if (kindOf(f) == SNIPPETS) v = v instanceof String ? snippetsOf((String) v) : null;
            Object c = v == null ? null : clean(f, v);
            if (c != null) out.put(f, c);
        }
        return out;
    }

    /**
     * The part of a profile document that this phone takes part in: the fields in {@code fields} that it has a setting
     * for, with a value it can use, cleaned. Everything else (fields of other devices, llm_reasoning, values of the wrong
     * type) is left out as if the relay did not have it. A new map.
     */
    static Map<String, Object> accept(Map<String, ?> profile, Set<String> fields) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (profile == null || fields == null) return out;
        for (String f : FIELDS) {
            if (!fields.contains(f)) continue;
            Object v = profile.get(f);
            Object c = v == null ? null : clean(f, v);
            if (c != null) out.put(f, c);
        }
        return out;
    }

    /**
     * Profile fields as the stored form this phone writes to its preferences ({@code dictionary} and {@code people}
     * as lines, the rest as text or a boolean). Only the fields this phone has a setting for and can use.
     */
    static Map<String, Object> toStored(Map<String, ?> fields) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (fields == null) return out;
        for (String f : FIELDS) {
            Object v = fields.get(f);
            Object c = v == null ? null : clean(f, v);
            if (c == null) continue;
            if (kindOf(f) == LINES) {
                @SuppressWarnings("unchecked")
                List<String> entries = (List<String>) c;
                c = joinLines(entries);
            } else if (kindOf(f) == SNIPPETS) {
                c = PlainJson.stringify(c);
            }
            out.put(f, c);
        }
        return out;
    }

    /**
     * What Prefs.applyReceived writes (AND-15): the settings received from the relay merged onto those stored now
     * ({@code stored}, read under the learn lock) with ProfileMerge.onto and {@code seen} (what the sync run read), in
     * the stored form. A word learned while the run was in flight stays.
     */
    static Map<String, Object> receivedOnto(Map<String, ?> stored, Map<String, ?> received, Map<String, ?> seen) {
        return toStored(ProfileMerge.onto(seen, toProfile(stored), received));
    }
}
