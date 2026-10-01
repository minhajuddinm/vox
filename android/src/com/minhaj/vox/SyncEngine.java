package com.minhaj.vox;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * One sync run with a relay: send what changed on this phone, fetch what changed elsewhere, then merge the profile.
 * A port of windows/sync.py (sync_once, sync_profile): the same order, the same rules (the newer {@code updated_at}
 * wins, deletes travel as markers, the relay's sequence number is the cursor, the profile is merged field by field
 * with ProfileMerge) and the same words for the errors. Pure Java over SyncStore, RelayApi and SyncConfig, with plain
 * maps (no org.json, no Android), so every rule is tested off-device against an in-memory store and relay.
 *
 * Offline first: the notes always work locally, and a run that fails changes nothing it cannot repeat. A note stays
 * {@code dirty} until the relay has accepted that exact version, so a run that stops (no network, the app killed) is
 * picked up by the next one, and a version is sent again only when it is not yet on the relay. One run uses one
 * engine on one thread at a time.
 */
final class SyncEngine {
    /** Notes or delete markers asked for in one {@code /changes} request. */
    static final int PULL_LIMIT = 200;

    /** How often the profile is read again after another device wrote first (412). */
    static final int PROFILE_ATTEMPTS = 3;

    static final String PROFILE_BUSY = "The profile keeps changing on the relay; it will be tried again later.";

    /** Meta flag: this device has put key fields on the relay's profile, so it may take them off when keys are switched off. */
    static final String KEYS_SENT = "profile_keys_sent";

    private final SyncStore store;
    private final RelayApi api;
    private final SyncConfig cfg;

    SyncEngine(SyncStore store, RelayApi api, SyncConfig cfg) {
        this.store = store;
        this.api = api;
        this.cfg = cfg;
    }

    /** The relay address as the sync state is tied to it: no trailing slash, scheme and host in lower case. */
    static String originOf(String url) {
        String u = Endpoint.normalize(url);
        int scheme = u.indexOf("://");
        if (scheme < 0) return u.toLowerCase(java.util.Locale.ROOT);
        int slash = u.indexOf('/', scheme + 3);
        if (slash < 0) return u.toLowerCase(java.util.Locale.ROOT);
        return u.substring(0, slash).toLowerCase(java.util.Locale.ROOT) + u.substring(slash);
    }

    /**
     * The sync state (cursor, profile version and snapshot, which notes the relay has) describes one relay. When the
     * app is pointed at another address, that state is wrong for the new relay: start from zero and send everything,
     * notes and delete markers, so the new relay gets all of it. An install with no address saved yet (an update)
     * keeps its state and just records the address. The address is saved last, so a run that stops half way repeats this.
     */
    private void followRelay() {
        String origin = originOf(cfg.relayUrl());
        if (origin.isEmpty()) return;
        String saved = store.getMeta("relay_origin", "");
        if (origin.equals(saved)) return;
        if (!saved.isEmpty()) {
            store.setMeta("relay_cursor", "0");
            store.setMeta("profile_version", "0");
            store.setMeta("profile_snapshot", "{}");
            store.setMeta(KEYS_SENT, "");
            store.markAllDirty();
        }
        store.setMeta("relay_origin", origin);
    }

    /** What has happened so far in a run, kept outside the try block so a failure can still report it. */
    private static final class Run {
        int pushed;
        int pulled;
        String profile = "";
        /** What the relay said about each note it refuses for good. */
        final List<String> refused = new ArrayList<>();
    }

    /**
     * Sends local changes, then fetches remote ones, then syncs the profile. Never throws: every failure is a message
     * in the result. A note that the relay refuses for good (a 4xx that would come back every time) is skipped for this
     * run and reported; it does not stop the other notes, the pull or the profile. Anything else that goes wrong (the
     * network, the token, a 5xx, a rate limit) stops the run, keeps what was already done and is reported.
     */
    SyncResult syncOnce() {
        Run run = new Run();
        try {
            followRelay();
            push(run);
            pull(run);
            run.profile = syncProfile();
        } catch (RelayApi.RelayError e) {
            return new SyncResult(run.pushed, run.pulled, e.message, "");
        } catch (RuntimeException e) {
            return new SyncResult(run.pushed, run.pulled, "Sync failed: " + e.getClass().getSimpleName(), "");
        }
        String error = "";
        if (!run.refused.isEmpty()) {
            int n = run.refused.size();
            error = n + (n == 1 ? " note" : " notes") + " could not be sent: " + run.refused.get(0);
        }
        return new SyncResult(run.pushed, run.pulled, error, run.profile);
    }

    // ------------------------------------------------------------------ notes: push

    private static String versionKey(Note n) {
        return n.id + "|" + n.updatedAt;
    }

    private void push(Run run) throws RelayApi.RelayError {
        // Every version handled in this run (sent, or refused for good) is remembered, so none is tried twice in one
        // run: a refused note stays dirty and would otherwise be handed back by dirtyNotes() for ever. dirtyNotes() is
        // asked for that many more notes so the refused ones (the only handled ones that stay dirty) cannot fill the
        // batch and hide the rest. A sent note is marked synced and no longer comes back, so it needs no extra room.
        Set<String> handled = new HashSet<>();
        int parked = 0;
        while (true) {
            List<Note> batch = new ArrayList<>();
            for (Note n : store.dirtyNotes(NoteLogic.PUSH_BATCH + parked)) {
                if (handled.contains(versionKey(n))) continue;
                batch.add(n);
                if (batch.size() == NoteLogic.PUSH_BATCH) break;
            }
            if (batch.isEmpty()) return;
            for (Note n : batch) {
                handled.add(versionKey(n));
                Map<String, Object> out;
                try {
                    out = api.putNote(toWire(n));
                } catch (RelayApi.RelayError e) {
                    if (!e.permanent()) throw e;   // the relay, the token or the network is the problem, not this note
                    run.refused.add(e.message);
                    parked++;
                    continue;
                }
                Map<String, Object> stored = asMap(out == null ? null : out.get("note"));
                Object applied = out == null ? null : out.get("applied");
                if (stored == null || !(applied instanceof Boolean)) {
                    throw new RelayApi.RelayError(0, RelayApi.RelayError.NOT_A_RELAY);
                }
                long seq = numberOf(stored.get("seq"));
                if ((Boolean) applied) {
                    store.markSynced(n.id, n.updatedAt, seq);
                    run.pushed++;
                    continue;
                }
                Note remote = fromWire(stored);
                if (remote == null) throw new RelayApi.RelayError(0, RelayApi.RelayError.NOT_A_RELAY);
                if (store.applyRemote(remote)) {
                    run.pulled++;   // the relay already has a newer version: take it
                } else {
                    store.markSynced(n.id, n.updatedAt, seq);   // the relay has this exact version
                }
            }
        }
    }

    // ------------------------------------------------------------------ notes: pull

    private void pull(Run run) throws RelayApi.RelayError {
        long cursor = parseLong(store.getMeta("relay_cursor", "0"));
        while (true) {
            RelayApi.Changes page = api.changes(cursor, PULL_LIMIT);
            for (Map<String, Object> wire : page.notes) {
                Note n = fromWire(wire);
                if (n != null && store.applyRemote(n)) run.pulled++;
            }
            boolean moved = page.next > cursor;
            if (moved) {
                cursor = page.next;
                store.setMeta("relay_cursor", String.valueOf(cursor));   // after each page: a failure later keeps this page
            }
            // a relay that says "more" without moving the cursor would keep this loop busy for ever
            if (!page.more || !moved) return;
        }
    }

    // ------------------------------------------------------------------ the profile

    private static Set<String> profileFields(boolean withKeys) {
        Set<String> fields = new LinkedHashSet<>(ProfileMerge.SHARED_FIELDS);
        if (withKeys) fields.addAll(ProfileMerge.KEY_FIELDS);
        return fields;
    }

    /**
     * Two-way sync of the shared settings with the relay's profile document (sync.sync_profile). Returns "", "sent",
     * "received" or "both". The relay refuses a stale write (If-Match), so a race with another device is read again
     * and merged, not lost. Keys and provider settings travel only while the switch is on. They are taken off the
     * relay's document only on this device's own on-to-off change (it had sent keys, KEYS_SENT, and they are now
     * off); a device that never sent keys leaves other devices' keys alone, so two devices do not undo each other.
     */
    private String syncProfile() throws RelayApi.RelayError {
        boolean receivedAny = false;   // settings written here in any attempt: a retry sees them as local, so remember them
        for (int attempt = 0; attempt < PROFILE_ATTEMPTS; attempt++) {
            boolean keysOn = cfg.syncKeys();
            Set<String> fields = profileFields(keysOn);
            RelayApi.Profile remote = api.getProfile();
            long version = remote.version;
            Map<String, Object> data = remote.data == null ? new LinkedHashMap<String, Object>() : remote.data;
            long baseVersion = parseLong(store.getMeta("profile_version", "0"));
            Map<String, Object> base = snapshot();
            Map<String, Object> local = pick(cfg.readProfile(), fields);
            Map<String, Object> remoteShared = ProfileMap.accept(data, fields);
            // local wins when the relay has no profile yet or has not changed since this phone last synced it
            Map<String, Object> merged = version == 0 || version == baseVersion
                    ? local : ProfileMerge.mergeProfile(base, local, remoteShared, fields);
            Map<String, Object> received = new LinkedHashMap<>();
            for (Map.Entry<String, Object> e : merged.entrySet()) {
                if (!Objects.equals(local.get(e.getKey()), e.getValue())) received.put(e.getKey(), e.getValue());
            }
            if (!received.isEmpty()) {
                cfg.writeProfile(received);
                receivedAny = true;
            }
            // Keys are taken off the relay only on this device's own on-to-off switch (it sent keys, now they are off).
            // A device that never sent keys leaves other devices' keys alone, or two devices would undo each other for ever.
            boolean relayHasKeys = false;
            for (String k : ProfileMerge.KEY_FIELDS) if (data.containsKey(k)) relayHasKeys = true;
            boolean sentKeys = "1".equals(store.getMeta(KEYS_SENT, ""));
            boolean staleKeys = !keysOn && sentKeys && relayHasKeys;
            if (!keysOn && sentKeys && !relayHasKeys) store.setMeta(KEYS_SENT, "");   // someone else took them off already
            if (merged.equals(remoteShared) && !staleKeys) {
                if (keysOn) store.setMeta(KEYS_SENT, "1");
                remember(version, merged);
                return receivedAny ? "received" : "";
            }
            Map<String, Object> doc = new LinkedHashMap<>();   // keep the fields other devices added, their keys too
            for (Map.Entry<String, Object> e : data.entrySet()) {
                if (!staleKeys || !ProfileMerge.KEY_FIELDS.contains(e.getKey())) doc.put(e.getKey(), e.getValue());
            }
            doc.putAll(merged);
            try {
                RelayApi.Profile out = api.putProfile(version, doc);
                if (keysOn) store.setMeta(KEYS_SENT, "1");
                else if (staleKeys) store.setMeta(KEYS_SENT, "");
                remember(out.version, merged);
                return receivedAny ? "both" : "sent";
            } catch (RelayApi.RelayError e) {
                if (e.status != 412) throw e;
                // someone wrote in between: look again
            }
        }
        throw new RelayApi.RelayError(0, PROFILE_BUSY);
    }

    private void remember(long version, Map<String, Object> merged) {
        store.setMeta("profile_version", String.valueOf(version));
        store.setMeta("profile_snapshot", PlainJson.stringify(merged));
    }

    /** The shared settings as of the last profile sync; nothing when none was saved or the saved text is damaged. */
    private Map<String, Object> snapshot() {
        try {
            Map<String, Object> m = asMap(PlainJson.parse(store.getMeta("profile_snapshot", "{}")));
            if (m != null) return m;
        } catch (IllegalArgumentException damaged) {
            // counts as nothing saved: the relay's values win, which loses nothing that is not also on the relay
        }
        return new LinkedHashMap<>();
    }

    private static Map<String, Object> pick(Map<String, Object> all, Set<String> fields) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (all == null) return out;
        for (String k : fields) {
            Object v = all.get(k);
            if (v != null) out.put(k, v);
        }
        return out;
    }

    // ------------------------------------------------------------------ the wire form of a note

    /** A local note as the relay stores it (sync.wire): the eleven fields of the note, without dirty and seq. */
    static Map<String, Object> toWire(Note n) {
        Map<String, Object> w = new LinkedHashMap<>();
        w.put("id", n.id);
        w.put("source", n.source);
        w.put("title", n.title);
        w.put("text", n.text);
        w.put("raw", n.raw);
        w.put("created_at", n.createdAt);
        w.put("updated_at", n.updatedAt);
        w.put("secs", n.secs);
        w.put("device", n.device);
        w.put("tags", new ArrayList<>(n.tags));
        w.put("deleted", n.deleted);
        return w;
    }

    /**
     * A note (or delete marker) from the relay as a Note that is not dirty, or null when it has no usable id. The
     * relay is trusted to speak its own protocol, but not to get every field right: a field of the wrong type counts as
     * empty, and only text tags are kept.
     */
    static Note fromWire(Map<String, Object> w) {
        if (w == null || !(w.get("id") instanceof String) || ((String) w.get("id")).isEmpty()) return null;
        Note n = new Note();
        n.id = (String) w.get("id");
        String source = text(w.get("source"));
        n.source = source.isEmpty() ? Note.SOURCE_NOTE : source;
        n.title = text(w.get("title"));
        n.text = text(w.get("text"));
        n.raw = text(w.get("raw"));
        n.createdAt = decimal(w.get("created_at"));
        n.updatedAt = decimal(w.get("updated_at"));
        n.secs = decimal(w.get("secs"));
        n.device = text(w.get("device"));
        if (w.get("tags") instanceof List) {
            for (Object t : (List<?>) w.get("tags")) if (t instanceof String) n.tags.add((String) t);
        }
        n.deleted = Boolean.TRUE.equals(w.get("deleted"));
        n.dirty = false;
        n.seq = numberOf(w.get("seq"));
        return n;
    }

    private static String text(Object o) {
        return o instanceof String ? (String) o : "";
    }

    private static double decimal(Object o) {
        return o instanceof Number ? ((Number) o).doubleValue() : 0.0;
    }

    private static long numberOf(Object o) {
        return o instanceof Number ? ((Number) o).longValue() : 0L;
    }

    private static long parseLong(String s) {
        try {
            return Long.parseLong(s == null ? "" : s.trim());
        } catch (NumberFormatException e) {
            return 0L;
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object o) {
        return o instanceof Map ? (Map<String, Object>) o : null;
    }
}
