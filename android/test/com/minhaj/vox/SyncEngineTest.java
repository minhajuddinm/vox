package com.minhaj.vox;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Plain-Java checks for SyncEngine against an in-memory notes store and a fake relay that follows the relay's rules
 * (windows tests/test_sync.py and tests/test_sync_profile.py are the model). No org.json, no network, no device.
 * Run by CI, exits non-zero on failure.
 */
public final class SyncEngineTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    private static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    private static List<Object> list(Object... v) {
        return new ArrayList<Object>(Arrays.asList(v));
    }

    private static String id(int n) {
        return String.format("%032x", n);
    }

    private static double num(Object o) {
        return ((Number) o).doubleValue();
    }

    // ------------------------------------------------------------------ fakes

    /** The notes of a phone in memory: the same rules as NotesStore (see its dirtyNotes, markSynced, applyRemote). */
    static class MemStore implements SyncStore {
        final Map<String, Note> notes = new LinkedHashMap<>();
        final Map<String, String> meta = new HashMap<>();
        final List<String> metaLog = new ArrayList<>();
        boolean neverClears;   // a store that cannot clear the flag: the engine must still end the run

        @Override
        public List<Note> dirtyNotes(int limit) {
            List<Note> out = new ArrayList<>();
            for (Note n : notes.values()) if (n.dirty) out.add(n.copy());
            Collections.sort(out, new Comparator<Note>() {
                @Override public int compare(Note a, Note b) { return Double.compare(a.updatedAt, b.updatedAt); }
            });
            return out.size() > limit ? new ArrayList<>(out.subList(0, limit)) : out;
        }

        @Override
        public void markSynced(String id, double sentUpdatedAt, long seq) {
            Note n = notes.get(id);
            if (neverClears || n == null || n.updatedAt != sentUpdatedAt) return;
            n.dirty = false;
            n.seq = seq;
        }

        @Override
        public boolean applyRemote(Note n) {
            if (n == null || n.id == null || n.id.isEmpty()) return false;
            Note local = notes.get(n.id);
            boolean has = local != null;
            if (!NoteLogic.remoteWins(has, has ? local.updatedAt : 0, n.updatedAt, n.deleted)) {
                if (has && local.updatedAt == n.updatedAt) local.seq = n.seq;
                return false;
            }
            Note c = n.copy();
            c.dirty = false;
            if (c.deleted) {
                c.title = "";
                c.text = "";
                c.raw = "";
                c.tags = new ArrayList<>();
            }
            notes.put(c.id, c);
            return true;
        }

        @Override
        public String getMeta(String k, String d) {
            return meta.containsKey(k) ? meta.get(k) : d;
        }

        @Override
        public void setMeta(String k, String v) {
            meta.put(k, v);
            metaLog.add(k + "=" + v);
        }

        @Override
        public void markAllDirty() {
            for (Note n : notes.values()) n.dirty = true;
        }

        Note add(String text, double updatedAt) {
            return add(id(notes.size() + 1), text, updatedAt);
        }

        Note add(String id, String text, double updatedAt) {
            Note n = new Note();
            n.id = id;
            n.title = text;
            n.text = text;
            n.createdAt = updatedAt;
            n.updatedAt = updatedAt;
            n.device = "phone";
            n.dirty = true;
            notes.put(id, n);
            return n;
        }
    }

    /** A relay in memory that follows relay/relay.py: a number for every stored version, the newer write wins, versioned profile. */
    static final class FakeRelay implements RelayApi {
        final Map<String, Map<String, Object>> notes = new LinkedHashMap<>();
        long seq;
        long profileVersion;
        Map<String, Object> profile = new LinkedHashMap<>();
        int pageCap = 200;
        int applied;                          // how many puts were stored
        final List<String> calls = new ArrayList<>();
        RelayError down;                      // every call fails with this
        final Map<String, RelayError> always = new HashMap<>();              // "putNote:<id>" / "changes" / "getProfile" / "putProfile"
        final Map<String, Deque<RelayError>> script = new HashMap<>();       // one outcome per call, null = works
        Runnable beforeNotePut;               // runs at the start of each putNote (another change while a note is being sent)
        Runnable raceOnce;                    // runs at the start of the next putProfile (another device writes first)
        boolean stuck;                        // changes answers "more" forever without moving the cursor

        void script(String call, RelayError... outcomes) {
            Deque<RelayError> q = new ArrayDeque<>();
            for (RelayError e : outcomes) q.add(e == null ? new RelayError(-1, "works") : e);
            script.put(call, q);
        }

        private void gate(String call) throws RelayError {
            if (down != null) throw down;
            if (always.containsKey(call)) throw always.get(call);
            Deque<RelayError> q = script.get(call);
            if (q != null && !q.isEmpty()) {
                RelayError e = q.poll();
                if (e.status != -1) throw e;
            }
        }

        int puts(String id) {
            int n = 0;
            for (String c : calls) if (c.equals("PUT " + id)) n++;
            return n;
        }

        boolean called(String prefix) {
            for (String c : calls) if (c.startsWith(prefix)) return true;
            return false;
        }

        /** A note written by another device, straight into the relay. */
        Map<String, Object> store(String id, String text, double updatedAt, boolean deleted) {
            Map<String, Object> w = map("id", id, "source", "voice note", "title", deleted ? "" : text, "text", deleted ? "" : text, "raw", "",
                    "created_at", updatedAt, "updated_at", updatedAt, "secs", 0.0, "device", "pc", "tags", list(), "deleted", deleted);
            w.put("seq", ++seq);
            notes.put(id, w);
            return w;
        }

        @Override
        public Map<String, Object> putNote(Map<String, Object> wire) throws RelayError {
            String id = (String) wire.get("id");
            calls.add("PUT " + id);
            if (beforeNotePut != null) beforeNotePut.run();
            gate("putNote:" + id);
            Map<String, Object> old = notes.get(id);
            if (old != null) {
                boolean same = true;
                for (String k : wire.keySet()) if (!wire.get(k).equals(old.get(k))) same = false;
                if (num(old.get("updated_at")) > num(wire.get("updated_at")) || same) return answer(old, false);
            }
            Map<String, Object> stored = new LinkedHashMap<>(wire);
            if (Boolean.TRUE.equals(wire.get("deleted"))) {
                stored.put("title", "");
                stored.put("text", "");
                stored.put("raw", "");
                stored.put("tags", list());
            }
            stored.put("seq", ++seq);
            notes.put(id, stored);
            applied++;
            return answer(stored, true);
        }

        private static Map<String, Object> answer(Map<String, Object> note, boolean applied) {
            return map("note", new LinkedHashMap<>(note), "applied", applied);
        }

        @Override
        public Changes changes(long since, int limit) throws RelayError {
            calls.add("GET changes " + since);
            gate("changes");
            if (stuck) return new Changes(new ArrayList<Map<String, Object>>(), since, true);
            List<Map<String, Object>> all = new ArrayList<>();
            for (Map<String, Object> n : notes.values()) if (((Number) n.get("seq")).longValue() > since) all.add(new LinkedHashMap<>(n));
            Collections.sort(all, new Comparator<Map<String, Object>>() {
                @Override public int compare(Map<String, Object> a, Map<String, Object> b) { return Long.compare(((Number) a.get("seq")).longValue(), ((Number) b.get("seq")).longValue()); }
            });
            int cap = Math.min(limit, pageCap);
            List<Map<String, Object>> page = new ArrayList<>(all.subList(0, Math.min(cap, all.size())));
            long next = page.isEmpty() ? since : ((Number) page.get(page.size() - 1).get("seq")).longValue();
            return new Changes(page, next, page.size() == cap);
        }

        boolean noSeq;                        // a relay whose /health does not say its sequence number

        @Override
        public long relaySeq() throws RelayError {
            gate("health");
            return noSeq ? -1 : seq;
        }

        @Override
        public Profile getProfile() throws RelayError {
            calls.add("GET profile");
            gate("getProfile");
            return new Profile(profileVersion, new LinkedHashMap<>(profile));
        }

        @Override
        public Profile putProfile(long ifMatch, Map<String, Object> data) throws RelayError {
            calls.add("PUT profile " + ifMatch);
            if (raceOnce != null) {
                Runnable r = raceOnce;
                raceOnce = null;
                r.run();
            }
            gate("putProfile");
            if (ifMatch != profileVersion) throw new RelayError(412, "The relay answered HTTP 412.");
            profile = new LinkedHashMap<>(data);
            profileVersion++;
            return new Profile(profileVersion, new LinkedHashMap<>(profile));
        }
    }

    /** The settings of this phone, as relay fields (what ProfileMap.toProfile gives), and what the engine wrote into them. */
    static class FakeCfg implements SyncConfig {
        boolean keys;
        String url = "http://relay.test:8787";
        Map<String, Object> profile = map("user_context", "", "dictionary", list(), "people", list(), "default_style", "neutral", "cleanup", true, "language", "",
                "provider", "groq", "base_url", "https://api.groq.com/openai/v1", "stt_base_url", "", "llm_base_url", "", "stt_model", "whisper-large-v3-turbo",
                "llm_model", "openai/gpt-oss-20b", "api_key", "gsk_phone", "stt_api_key", "", "llm_api_key", "");
        final List<Map<String, Object>> writes = new ArrayList<>();
        Runnable meanwhile;   // runs between the read and the write (a word learned while the run is in flight)

        @Override public boolean syncKeys() { return keys; }
        @Override public String relayUrl() { return url; }
        @Override public Map<String, Object> readProfile() { return new LinkedHashMap<>(profile); }
        @Override public void writeProfile(Map<String, Object> received, Map<String, Object> seen) {
            if (meanwhile != null) { meanwhile.run(); meanwhile = null; }
            writes.add(new LinkedHashMap<>(received));
            profile.putAll(ProfileMerge.onto(seen, profile, received));   // what Prefs.applyReceived does, under its lock
        }
    }

    static final class Env {
        final MemStore store;
        final FakeRelay relay;
        final FakeCfg cfg = new FakeCfg();

        Env() { this(new MemStore()); }

        Env(MemStore store) { this(store, new FakeRelay()); }

        /** A second device syncing with the same relay. */
        Env(FakeRelay shared) { this(new MemStore(), shared); }

        Env(MemStore store, FakeRelay relay) {
            this.store = store;
            this.relay = relay;
        }

        SyncResult sync() {
            return new SyncEngine(store, relay, cfg).syncOnce();
        }
    }

    private static RelayApi.RelayError refused(int status) {
        return new RelayApi.RelayError(status, "The relay answered HTTP " + status + " (bad note id).");
    }

    // ------------------------------------------------------------------ tests

    public static void main(String[] args) throws Exception {
        pushing();
        offlineAndKilled();
        refusedNotes();
        stoppingErrors();
        pulling();
        profileSync();
        profileKeys();
        keysDoNotFlap();
        relayChange();
        relayReset();
        keysFlagAcrossUpgradeAndAddress();
        neverThrows();
        loopGuards();
        wireFormat();
        System.out.println("OK: " + checks + " checks passed");
    }

    private static void pushing() {
        Env e = new Env();
        Note n = e.store.add("one", 100.5);
        SyncResult r = e.sync();
        eq("push: one sent", 1, r.pushed);
        eq("push: our own send coming back is not 'pulled'", 0, r.pulled);
        eq("push: no error", "", r.error);
        eq("push: marked synced", false, e.store.notes.get(n.id).dirty);
        eq("push: the relay's number is stored", 1L, e.store.notes.get(n.id).seq);
        eq("push: the relay has the text", "one", e.relay.notes.get(n.id).get("text"));
        eq("push: the cursor is saved", "1", e.store.meta.get("relay_cursor"));
        eq("push: the wire note is what windows/sync.py wire() sends", Arrays.asList("id", "source", "title", "text", "raw", "created_at", "updated_at", "secs",
                "device", "tags", "deleted"), keysOf(e.relay.notes.get(n.id), "seq"));
        eq("push: the times are the same numbers", 100.5, e.relay.notes.get(n.id).get("updated_at"));
        SyncResult quiet = e.sync();
        eq("push: nothing new is quiet", "0/0/", quiet.pushed + "/" + quiet.pulled + "/" + quiet.error);
        eq("push: nothing sent twice", 1, e.relay.puts(n.id));

        // the relay already has a newer version: it is taken, not sent again
        e = new Env();
        Note local = e.store.add("local edit", 100);
        e.relay.store(local.id, "edited on the PC", 200, false);
        r = e.sync();
        eq("not applied, relay newer: nothing pushed", 0, r.pushed);
        eq("not applied, relay newer: taken as a received note", 1, r.pulled);
        eq("not applied, relay newer: local text replaced", "edited on the PC", e.store.notes.get(local.id).text);
        eq("not applied, relay newer: local is clean", false, e.store.notes.get(local.id).dirty);
        eq("not applied, relay newer: sent once, not again", 1, e.relay.puts(local.id));

        // the relay has this exact version (for example after a restored backup): just marked as sent
        e = new Env();
        Note same = e.store.add("same", 100);
        same.device = "pc";   // the relay's copy (made by store()) says the PC recorded it
        e.relay.store(same.id, "same", 100, false);
        r = e.sync();
        eq("not applied, same version: not pushed, not pulled", "0/0", r.pushed + "/" + r.pulled);
        eq("not applied, same version: marked synced with the relay's number", "false/1", e.store.notes.get(same.id).dirty + "/" + e.store.notes.get(same.id).seq);

        // the relay has a newer one of THIS note but our copy is the newer: ours is sent
        e = new Env();
        Note newer = e.store.add("phone is newer", 300);
        e.relay.store(newer.id, "old on the relay", 200, false);
        r = e.sync();
        eq("newer here: pushed", 1, r.pushed);
        eq("newer here: the relay takes it", "phone is newer", e.relay.notes.get(newer.id).get("text"));

        // a delete made here travels as a marker
        e = new Env();
        Note gone = e.store.add("to delete", 100);
        e.sync();
        gone.deleted = true;
        gone.title = "";
        gone.text = "";
        gone.updatedAt = 150;
        gone.dirty = true;
        r = e.sync();
        eq("delete here: marker pushed", 1, r.pushed);
        eq("delete here: the relay keeps a marker without text", "true/", e.relay.notes.get(gone.id).get("deleted") + "/" + e.relay.notes.get(gone.id).get("text"));
    }

    private static List<String> keysOf(Map<String, Object> m, String... except) {
        List<String> out = new ArrayList<>(m.keySet());
        out.removeAll(Arrays.asList(except));
        return out;
    }

    /** Review Focus 1: a note saved while offline, or with the app killed before the sync, is sent once later. */
    private static void offlineAndKilled() {
        Env e = new Env();
        Note n = e.store.add("saved in the lift", 100);
        e.relay.down = new RelayApi.RelayError(0, "Cannot reach the relay (is Tailscale running?): UnknownHostException");
        SyncResult r = e.sync();
        eq("offline: the message says Tailscale", "Cannot reach the relay (is Tailscale running?): UnknownHostException", r.error);
        eq("offline: nothing counted", "0/0", r.pushed + "/" + r.pulled);
        eq("offline: still dirty", true, e.store.notes.get(n.id).dirty);
        eq("offline: nothing written to the store's settings but the relay address", Arrays.asList("relay_origin=http://relay.test:8787"), e.store.metaLog);
        eq("offline: the relay has nothing", 0, e.relay.notes.size());
        e.relay.down = null;
        r = e.sync();   // a new engine, like the next app start
        eq("next run: pushed exactly once", "1/", r.pushed + "/" + r.error);
        eq("next run: one stored version, one number", "1/1", e.relay.notes.size() + "/" + e.relay.seq);
        eq("next run: clean", false, e.store.notes.get(n.id).dirty);
        e.sync();
        e.sync();
        eq("later runs: never sent again", 2, e.relay.puts(n.id));   // the failed try and the one that worked
        eq("later runs: the relay stored it once", 1, e.relay.applied);

        // a failure halfway through: what was sent stays sent, the rest waits
        e = new Env();
        Note a = e.store.add("first", 100), b = e.store.add("second", 200), c = e.store.add("third", 300);
        e.relay.always.put("putNote:" + b.id, new RelayApi.RelayError(0, "Cannot reach the relay (is Tailscale running?): SocketTimeoutException"));
        r = e.sync();
        eq("halfway: one sent", 1, r.pushed);
        eq("halfway: the error is reported", true, r.error.contains("SocketTimeoutException"));
        eq("halfway: first clean, second and third still dirty", "false/true/true", e.store.notes.get(a.id).dirty + "/" + e.store.notes.get(b.id).dirty + "/" + e.store.notes.get(c.id).dirty);
        eq("halfway: the third was not tried", 0, e.relay.puts(c.id));
        e.relay.always.clear();
        r = e.sync();
        eq("halfway: the rest goes next run, the first is not sent again", "2/1", r.pushed + "/" + e.relay.puts(a.id));
    }

    /** Review Focus 2: a note the relay refuses for good does not block the others, the pull or the profile, and is not retried in a loop. */
    private static void refusedNotes() {
        Env e = new Env();
        Note bad = e.store.add(id(99), "the relay refuses this", 1);        // the oldest: the first to be sent
        Note g1 = e.store.add("good one", 100), g2 = e.store.add("good two", 200);
        e.relay.store(id(500), "from the PC", 50, false);
        e.relay.always.put("putNote:" + bad.id, refused(400));
        SyncResult r = e.sync();
        eq("refused: the others are sent", 2, r.pushed);
        eq("refused: the pull still runs: the PC's note is received (our own two coming back are not counted)", 1, r.pulled);
        eq("refused: what the user sees", "1 note could not be sent: The relay answered HTTP 400 (bad note id).", r.error);
        eq("refused: the profile still syncs", "sent", r.profile);
        eq("refused: good notes are on the relay", true, e.relay.notes.containsKey(g1.id) && e.relay.notes.containsKey(g2.id));
        eq("refused: the refused note stays here and dirty, nothing is lost", true, e.store.notes.get(bad.id).dirty);
        eq("refused: the note from the PC arrived", "from the PC", e.store.notes.get(id(500)).text);
        eq("refused: the cursor moved", String.valueOf(e.relay.seq), e.store.meta.get("relay_cursor"));
        eq("refused: tried once in the run", 1, e.relay.puts(bad.id));
        r = e.sync();
        eq("refused: next run tries it once more, and only once", 2, e.relay.puts(bad.id));
        eq("refused: nothing new was sent", 0, r.pushed);
        eq("refused: and it says so again", true, r.error.startsWith("1 note could not be sent: "));

        // the note is fixed (edited, so a new version): it goes through
        e.relay.always.clear();
        bad.updatedAt = 400;
        bad.text = "fixed";
        r = e.sync();
        eq("refused: once accepted it is sent and the error is gone", "1/", r.pushed + "/" + r.error);
        eq("refused: and clean", false, e.store.notes.get(bad.id).dirty);

        // more refused notes than one batch holds: the good one behind them is still reached
        e = new Env();
        for (int i = 0; i < NoteLogic.PUSH_BATCH + 5; i++) {
            Note b = e.store.add(id(1000 + i), "bad " + i, 1 + i);
            e.relay.always.put("putNote:" + b.id, refused(400));
        }
        Note last = e.store.add(id(5), "good, but newer than all the refused ones", 10000);
        r = e.sync();
        eq("many refused: the good note behind them is sent", "1", String.valueOf(r.pushed));
        eq("many refused: counted in the plural", "105 notes could not be sent: The relay answered HTTP 400 (bad note id).", r.error);
        eq("many refused: each one tried once", true, e.relay.puts(id(1000)) == 1 && e.relay.puts(id(1000 + 104)) == 1);
        eq("many refused: the good one is on the relay", true, e.relay.notes.containsKey(last.id));

        // a first sync of many notes reads each dirty row a bounded number of times, not once per batch for ever
        final int[] rows = {0};
        MemStore counting = new MemStore() {
            @Override public List<Note> dirtyNotes(int limit) {
                List<Note> out = super.dirtyNotes(limit);
                rows[0] += out.size();
                return out;
            }
        };
        e = new Env(counting);
        int total = NoteLogic.PUSH_BATCH * 5;
        for (int i = 0; i < total; i++) e.store.add(id(2000 + i), "n" + i, 1 + i);
        r = e.sync();
        eq("many notes: all sent", total, r.pushed);
        eq("many notes: rows read stay linear (about the batch size per batch)", true, rows[0] <= total + 2 * NoteLogic.PUSH_BATCH);

        // the statuses that mean "this note" and those that do not (the same rule as sync.SyncError.permanent)
        for (int status = 400; status < 500; status++) {
            boolean perm = status != 401 && status != 403 && status != 429;
            eq("permanent() for " + status, perm, new RelayApi.RelayError(status, "x").permanent());
        }
        for (int status : new int[]{0, 200, 302, 500, 502, 503}) eq("permanent() for " + status, false, new RelayApi.RelayError(status, "x").permanent());
    }

    /** Problems that are not about one note stop the run and change nothing. */
    private static void stoppingErrors() {
        Env e = new Env();
        Note n = e.store.add("kept", 100);
        e.relay.down = new RelayApi.RelayError(401, "The relay refused the token.");
        SyncResult r = e.sync();
        eq("401: the message", "The relay refused the token.", r.error);
        eq("401: nothing counted", "0/0", r.pushed + "/" + r.pulled);
        eq("401: the note is still dirty", true, e.store.notes.get(n.id).dirty);
        eq("401: no setting changed but the relay address", Arrays.asList("relay_origin=http://relay.test:8787"), e.store.metaLog);
        eq("401: the profile was not touched", 0, e.cfg.writes.size());
        eq("401: the relay was asked once", 1, e.relay.calls.size());

        for (int status : new int[]{403, 429, 500, 502, 503}) {
            e = new Env();
            Note first = e.store.add("first", 100), second = e.store.add("second", 200);
            e.relay.always.put("putNote:" + second.id, new RelayApi.RelayError(status, "The relay answered HTTP " + status + "."));
            r = e.sync();
            eq(status + ": stops the run with the message", "The relay answered HTTP " + status + ".", r.error);
            eq(status + ": what was sent stays sent", "1", String.valueOf(r.pushed));
            eq(status + ": the second is still dirty", true, e.store.notes.get(second.id).dirty);
            eq(status + ": no pull, no profile after a failed push", false, e.relay.called("GET"));
            eq(status + ": the first is clean", false, e.store.notes.get(first.id).dirty);
        }

        // the pull or the profile failing: the notes already sent count, the error is reported
        e = new Env();
        e.store.add("sent", 100);
        e.relay.always.put("changes", new RelayApi.RelayError(0, "Cannot reach the relay (is Tailscale running?): ConnectException"));
        r = e.sync();
        eq("pull fails: pushed is kept in the result", "1", String.valueOf(r.pushed));
        eq("pull fails: error", true, r.error.contains("ConnectException"));
        eq("pull fails: the profile is not tried", false, e.relay.called("GET profile"));
        e = new Env();
        e.store.add("sent", 100);
        e.relay.always.put("getProfile", new RelayApi.RelayError(500, "The relay answered HTTP 500."));
        r = e.sync();
        eq("profile fails: notes are done", "1/The relay answered HTTP 500.", r.pushed + "/" + r.error);
        eq("profile fails: the cursor was saved", "1", e.store.meta.get("relay_cursor"));
    }

    private static void pulling() {
        Env e = new Env();
        e.relay.pageCap = 2;
        for (int i = 1; i <= 5; i++) e.relay.store(id(i), "note " + i, 10 * i, false);
        SyncResult r = e.sync();
        eq("paging: all five arrive", "5/", r.pulled + "/" + r.error);
        eq("paging: three pages, each asked from the last cursor", Arrays.asList("GET changes 0", "GET changes 2", "GET changes 4"), callsStarting(e.relay, "GET changes"));
        eq("paging: the cursor is saved after each page", Arrays.asList("relay_cursor=2", "relay_cursor=4", "relay_cursor=5"), metaLogOf(e.store, "relay_cursor"));
        eq("paging: pulled notes are clean", false, e.store.notes.get(id(3)).dirty);
        eq("paging: pulled notes keep the relay's number", 3L, e.store.notes.get(id(3)).seq);

        // a failure on the second page: the first is kept and the next run goes on from there
        e = new Env();
        e.relay.pageCap = 2;
        for (int i = 1; i <= 5; i++) e.relay.store(id(i), "note " + i, 10 * i, false);
        e.relay.script("changes", null, new RelayApi.RelayError(0, "Cannot reach the relay (is Tailscale running?): SocketTimeoutException"));
        r = e.sync();
        eq("page 2 fails: the first page is applied", "2", String.valueOf(r.pulled));
        eq("page 2 fails: and its cursor saved", "2", e.store.meta.get("relay_cursor"));
        eq("page 2 fails: error reported", true, r.error.contains("SocketTimeoutException"));
        r = e.sync();
        eq("resume: the rest arrives", "3/", r.pulled + "/" + r.error);
        eq("resume: from the saved cursor, not from the start", "GET changes 2", callsStarting(e.relay, "GET changes").get(2));

        // the default page size and a long outbox: 250 notes are sent in batches and our own come back without being counted
        e = new Env();
        for (int i = 1; i <= 250; i++) e.store.add("n" + i, i);
        r = e.sync();
        eq("batches: 250 sent", "250/0/", r.pushed + "/" + r.pulled + "/" + r.error);
        eq("batches: oldest first", true, e.relay.puts(id(1)) == 1 && e.relay.notes.get(id(1)).get("seq").equals(1L) && e.relay.notes.get(id(250)).get("seq").equals(250L));
        eq("batches: pulled in pages of 200", Arrays.asList("GET changes 0", "GET changes 200"), callsStarting(e.relay, "GET changes"));

        // a delete marker from another device removes the note here
        e = new Env();
        Note keep = e.store.add(id(7), "will be deleted elsewhere", 100);
        keep.dirty = false;
        e.relay.store(keep.id, "", 200, true);
        r = e.sync();
        eq("delete marker: applied and counted", "1/", r.pulled + "/" + r.error);
        eq("delete marker: the note is now a marker", "true/", e.store.notes.get(keep.id).deleted + "/" + e.store.notes.get(keep.id).text);
        eq("delete marker: nothing to send back", false, e.store.notes.get(keep.id).dirty);
        eq("delete marker: nothing was sent", 0, e.relay.puts(keep.id));
        e.relay.store(id(8), "", 300, true);   // a delete of a note this phone never had
        r = e.sync();
        eq("delete marker of an unknown note: not counted, not stored", "0/false", r.pulled + "/" + e.store.notes.containsKey(id(8)));

        // an older marker does not delete a newer local edit
        e = new Env();
        Note edited = e.store.add(id(9), "edited after the delete", 500);
        edited.dirty = false;
        e.relay.store(edited.id, "", 300, true);
        e.sync();
        eq("older marker: the local note survives", "edited after the delete", e.store.notes.get(edited.id).text);

        // garbage among the notes does not stop the others
        e = new Env();
        e.relay.store(id(1), "fine", 10, false);
        e.relay.notes.put("x", map("id", 7L, "seq", 99L));
        e.relay.notes.put("y", map("seq", 100L));
        r = e.sync();
        eq("bad wire notes are skipped", "1/", r.pulled + "/" + r.error);
        eq("bad wire notes: the cursor still moves past them", "100", e.store.meta.get("relay_cursor"));

        // a relay that says "more" without moving the cursor cannot keep the run busy forever
        e = new Env();
        e.relay.stuck = true;
        r = e.sync();
        eq("stuck cursor: the run ends", "0/", r.pulled + "/" + r.error);
        eq("stuck cursor: one page asked", 1, callsStarting(e.relay, "GET changes").size());
    }

    private static List<String> callsStarting(FakeRelay relay, String prefix) {
        List<String> out = new ArrayList<>();
        for (String c : relay.calls) if (c.startsWith(prefix)) out.add(c);
        return out;
    }

    private static List<String> metaLogOf(MemStore s, String key) {
        List<String> out = new ArrayList<>();
        for (String l : s.metaLog) if (l.startsWith(key + "=")) out.add(l);
        return out;
    }

    private static final Map<String, Object> SIX = map("user_context", "I lead Atlas.", "dictionary", list("Atlas", "wrong => right"), "people", list("Ada"),
            "default_style", "neutral", "cleanup", true, "language", "");

    private static void profileSync() throws Exception {
        // the relay has no profile yet (version 0): ours goes up as it is
        Env e = new Env();
        e.cfg.profile.putAll(SIX);
        SyncResult r = e.sync();
        eq("profile v0: sent", "sent", r.profile);
        eq("profile v0: the relay has exactly the six shared fields, no keys", SIX, e.relay.profile);
        eq("profile v0: version 1", 1L, e.relay.profileVersion);
        eq("profile v0: nothing was written here", 0, e.cfg.writes.size());
        eq("profile v0: version remembered", "1", e.store.meta.get("profile_version"));
        eq("profile v0: snapshot remembered", SIX, PlainJson.parse(e.store.meta.get("profile_snapshot")));
        eq("profile v0: the first PUT has If-Match 0", Arrays.asList("PUT profile 0"), callsStarting(e.relay, "PUT profile"));
        r = e.sync();
        eq("profile unchanged: quiet", "", r.profile);
        eq("profile unchanged: no second write", 1L, e.relay.profileVersion);
        eq("profile unchanged: no PUT at all", 1, callsStarting(e.relay, "PUT profile").size());

        // a new device receives it
        Env b = new Env();
        b.relay.profile = new LinkedHashMap<>(SIX);
        b.relay.profileVersion = 1;
        r = b.sync();
        eq("new device: received", "received", r.profile);
        eq("new device: only the fields that differ were written", map("user_context", "I lead Atlas.", "dictionary", list("Atlas", "wrong => right"), "people", list("Ada")), b.cfg.writes.get(0));
        eq("new device: no PUT", 0, callsStarting(b.relay, "PUT profile").size());
        eq("new device: version remembered", "1", b.store.meta.get("profile_version"));
        eq("new device: snapshot remembered", SIX, PlainJson.parse(b.store.meta.get("profile_snapshot")));
        eq("new device: next run is quiet", "", b.sync().profile);

        // both devices changed things: each side's own field survives, a field both changed goes to the relay's value
        e = new Env();
        e.cfg.profile.putAll(map("user_context", "original", "dictionary", list("one")));
        e.sync();
        e.cfg.profile.putAll(map("user_context", "A wrote this", "dictionary", list("one", "two")));
        e.relay.profile = map("user_context", "B wrote this", "dictionary", list("one"), "people", list("Ada"), "default_style", "neutral", "cleanup", true, "language", "");
        e.relay.profileVersion = 2;
        r = e.sync();
        eq("both changed: result", "both", r.profile);
        eq("both changed: the relay's value wins the clash", "B wrote this", e.relay.profile.get("user_context"));
        eq("both changed: each side keeps its own field", list("one", "two"), e.relay.profile.get("dictionary"));
        eq("both changed: and the other's", list("Ada"), e.relay.profile.get("people"));
        eq("both changed: this phone took the relay's values (and only those that differ)", map("user_context", "B wrote this", "people", list("Ada")), e.cfg.writes.get(e.cfg.writes.size() - 1));
        eq("both changed: version 3", 3L, e.relay.profileVersion);

        // AND-15: a word learned while the run is in flight (between the read and the write) is not overwritten
        final Env fl = new Env();
        fl.cfg.profile.put("dictionary", list("one"));
        fl.sync();
        fl.relay.profile.put("dictionary", list("one", "from the PC"));
        fl.relay.profileVersion++;
        fl.cfg.meanwhile = new Runnable() {
            public void run() { fl.cfg.profile.put("dictionary", list("one", "learned => Learned")); }
        };
        r = fl.sync();
        eq("in flight: the run took the PC's word", "received", r.profile);
        eq("in flight: the learned word stays, next to the PC's", list("one", "from the PC", "learned => Learned"), fl.cfg.profile.get("dictionary"));
        fl.sync();
        eq("in flight: the next run sends the learned word", list("one", "from the PC", "learned => Learned"), fl.relay.profile.get("dictionary"));

        // one side removed a field: a removal is a change too
        e = new Env();
        e.cfg.profile.put("user_context", "to be cleared");
        e.sync();
        e.cfg.profile.put("user_context", "");
        r = e.sync();
        eq("cleared About you: sent, not taken for missing", "sent", r.profile);
        eq("cleared About you: the relay has the empty text", "", e.relay.profile.get("user_context"));

        // a write that raced with another device: look again and merge, not lose
        final Env race = new Env();
        race.cfg.profile.put("user_context", "mine");
        race.sync();
        race.cfg.profile.put("dictionary", list("mine-too"));
        race.relay.raceOnce = new Runnable() {   // another device saves its People list just before our write
            public void run() {
                race.relay.profile.put("people", list("from another device"));
                race.relay.profileVersion++;
            }
        };
        r = race.sync();
        eq("412: succeeds on the retry", "", r.error);
        eq("412: PUT, look again, PUT with the new version", Arrays.asList("PUT profile 0", "PUT profile 1", "PUT profile 2"), callsStarting(race.relay, "PUT profile"));
        eq("412: our change reached the relay", list("mine-too"), race.relay.profile.get("dictionary"));
        eq("412: the other device's change was kept", list("from another device"), race.relay.profile.get("people"));
        eq("412: and taken here too", list("from another device"), race.cfg.profile.get("people"));
        eq("412: the relay was looked at again", 3, callsStarting(race.relay, "GET profile").size());

        // received on the first try, then the PUT is refused (412): the second try finds those settings already here,
        // but the result must still say "both" so the phone's profile listener is told
        final Env rr = new Env();
        rr.cfg.profile.put("dictionary", list("mine"));
        rr.sync();
        rr.cfg.profile.put("dictionary", list("mine", "mine-too"));
        rr.relay.profile.put("people", list("Ada"));
        rr.relay.profileVersion++;
        rr.relay.raceOnce = new Runnable() {   // another device saves without changing anything we merge
            public void run() { rr.relay.profileVersion++; }
        };
        r = rr.sync();
        eq("412 after receiving: both", "both", r.profile);
        eq("412 after receiving: the received setting was written once", 1, rr.cfg.writes.size());
        eq("412 after receiving: two PUTs", 2, callsStarting(rr.relay, "PUT profile").size() - 1);

        // a relay that never settles: three tries, then a message
        e = new Env();
        e.cfg.profile.put("user_context", "x");
        e.relay.always.put("putProfile", new RelayApi.RelayError(412, "The relay answered HTTP 412."));
        r = e.sync();
        eq("412 forever: three tries", 3, callsStarting(e.relay, "PUT profile").size());
        eq("412 forever: the message of sync.py", "The profile keeps changing on the relay; it will be tried again later.", r.error);
        eq("412 forever: the notes part was done", "0/0", r.pushed + "/" + r.pulled);

        // values the relay holds that this phone cannot use are left alone
        e = new Env();
        e.cfg.profile.putAll(SIX);
        e.relay.profile = map("user_context", 5L, "dictionary", "Atlas", "default_style", "weird", "cleanup", "yes", "future", map("a", 1L));
        e.relay.profileVersion = 3;
        r = e.sync();
        eq("junk on the relay: no error", "", r.error);
        eq("junk on the relay: nothing junk was written here", 0, e.cfg.writes.size());
        eq("junk on the relay: ours replaces the unusable values, the unknown field is kept", "I lead Atlas./true/" + map("a", 1L),
                e.relay.profile.get("user_context") + "/" + e.relay.profile.containsKey("future") + "/" + e.relay.profile.get("future"));

        // a snapshot or version in the store that is damaged counts as nothing saved: the relay's value wins
        e = new Env();
        e.store.meta.put("profile_version", "banana");
        e.store.meta.put("profile_snapshot", "{not json");
        e.cfg.profile.put("user_context", "local");
        e.relay.profile = new LinkedHashMap<String, Object>(SIX);
        e.relay.profileVersion = 4;
        r = e.sync();
        eq("damaged snapshot: no crash", "", r.error);
        eq("damaged snapshot: the relay's value wins the clash", "I lead Atlas.", e.cfg.profile.get("user_context"));
    }

    private static void profileKeys() {
        // keys off: no key field ever goes up, fields added by other devices stay
        Env e = new Env();
        e.cfg.profile.putAll(SIX);
        e.relay.profile = map("app_styles_android", map("com.whatsapp", "casual"));
        e.relay.profileVersion = 1;
        SyncResult r = e.sync();
        eq("keys off: sent", "sent", r.profile);
        eq("keys off: no key on the relay", false, e.relay.profile.containsKey("api_key") || e.relay.profile.containsKey("base_url") || e.relay.profile.containsKey("stt_model"));
        eq("keys off: another device's field kept", map("com.whatsapp", "casual"), e.relay.profile.get("app_styles_android"));

        // switch on: the provider settings and keys go up (llm_reasoning is not this phone's: what another device put there stays)
        e.relay.profile.put("llm_reasoning", "off");
        e.cfg.keys = true;
        r = e.sync();
        eq("keys on: sent", "sent", r.profile);
        eq("keys on: the key is on the relay", "gsk_phone", e.relay.profile.get("api_key"));
        eq("keys on: so is the address and model", "https://api.groq.com/openai/v1/whisper-large-v3-turbo", e.relay.profile.get("base_url") + "/" + e.relay.profile.get("stt_model"));
        eq("keys on: llm_reasoning of another device is not touched", "off", e.relay.profile.get("llm_reasoning"));
        eq("keys on: and is never written here", false, e.cfg.profile.containsKey("llm_reasoning"));
        eq("keys on: quiet afterwards", "", e.sync().profile);

        // another device changes a key: this phone receives it
        Map<String, Object> doc = new LinkedHashMap<>(e.relay.profile);
        doc.put("api_key", "gsk_new");
        doc.put("llm_reasoning", "auto");
        e.relay.profile = doc;
        e.relay.profileVersion++;
        r = e.sync();
        eq("key from another device: received", "received", r.profile);
        eq("key from another device: written here", map("api_key", "gsk_new"), e.cfg.writes.get(e.cfg.writes.size() - 1));
        eq("key from another device: still quiet after", "", e.sync().profile);

        // switch off: the keys leave the relay (llm_reasoning with them), and stay on this phone
        e.cfg.keys = false;
        r = e.sync();
        eq("keys switched off: sent", "sent", r.profile);
        eq("keys switched off: no key field left on the relay", false, e.relay.profile.containsKey("api_key") || e.relay.profile.containsKey("base_url")
                || e.relay.profile.containsKey("stt_model") || e.relay.profile.containsKey("llm_api_key") || e.relay.profile.containsKey("llm_reasoning"));
        eq("keys switched off: the shared fields and another device's field stay", true, e.relay.profile.containsKey("user_context") && e.relay.profile.containsKey("app_styles_android"));
        eq("keys switched off: this phone keeps its key", "gsk_new", e.cfg.profile.get("api_key"));
        eq("keys switched off: quiet afterwards", "", e.sync().profile);

        // keys on the relay, keys off here: they are not taken
        Env off = new Env();
        off.relay.profile = map("user_context", "hello", "api_key", "gsk_other");
        off.relay.profileVersion = 1;
        r = off.sync();
        eq("keys off here: the relay's key is not taken", false, off.cfg.profile.get("api_key").equals("gsk_other"));
        eq("keys off here: but About you is", "hello", off.cfg.profile.get("user_context"));
        eq("keys off here: the relay's key is left alone (this device never sent keys)", "gsk_other", off.relay.profile.get("api_key"));
        eq("keys off here: the key is not in the snapshot", false, ((Map<?, ?>) parse(off.store.meta.get("profile_snapshot"))).containsKey("api_key"));

        // a key that is not an address this phone may use is not taken
        Env bad = new Env();
        bad.cfg.keys = true;
        bad.relay.profile = map("base_url", "http://example.com/v1", "stt_model", "whisper-1");
        bad.relay.profileVersion = 1;
        bad.sync();
        eq("bad address: not taken", "https://api.groq.com/openai/v1", bad.cfg.profile.get("base_url"));
        eq("bad address: the usable model is", "whisper-1", bad.cfg.profile.get("stt_model"));
    }

    /** F6 part 1: a device with keys off must not undo the keys another device put on the relay. */
    private static void keysDoNotFlap() {
        Env pc = new Env();
        pc.cfg.keys = true;
        pc.cfg.profile.put("api_key", "gsk_pc");
        Env phone = new Env(pc.relay);   // keys off, like the phone's default
        pc.sync();
        eq("flap: the PC put its key on the relay", "gsk_pc", pc.relay.profile.get("api_key"));
        for (int round = 1; round <= 4; round++) {
            phone.sync();
            pc.sync();
            eq("flap round " + round + ": the key stays on the relay", "gsk_pc", pc.relay.profile.get("api_key"));
            eq("flap round " + round + ": the phone never writes the key", false, phone.cfg.profile.get("api_key").equals("gsk_pc"));
            eq("flap round " + round + ": the PC is not asked to change its key", true, pc.cfg.writes.isEmpty());
        }
        long version = pc.relay.profileVersion;
        phone.sync();
        pc.sync();
        eq("flap: settled, the relay's profile version no longer moves", version, pc.relay.profileVersion);

        // a setting changed on the phone keeps the key on the relay too
        phone.cfg.profile.put("user_context", "from phone");
        phone.sync();
        eq("flap: the phone's own change is sent", "from phone", pc.relay.profile.get("user_context"));
        eq("flap: and the key is still there", "gsk_pc", pc.relay.profile.get("api_key"));
        pc.sync();
        eq("flap: the PC got the change", "from phone", pc.cfg.profile.get("user_context"));
        eq("flap: the PC still has its key", "gsk_pc", pc.cfg.profile.get("api_key"));

        // the real switch: a device that sent keys and now has them off takes them off the relay, once
        Env two = new Env(pc.relay);
        two.cfg.keys = true;
        two.sync();
        two.cfg.keys = false;
        two.sync();
        eq("flap switch: the keys left the relay", false, pc.relay.profile.containsKey("api_key"));
        long afterStrip = pc.relay.profileVersion;
        two.sync();
        two.sync();
        eq("flap switch: stripped once, then quiet", afterStrip, pc.relay.profileVersion);
        eq("flap switch: the flag is cleared", "", two.store.getMeta("profile_keys_sent", ""));
        // another device puts keys back: this one (keys off, flag cleared) leaves them
        pc.relay.profile.put("api_key", "gsk_again");
        pc.relay.profileVersion++;
        two.sync();
        eq("flap switch: later keys from another device stay", "gsk_again", pc.relay.profile.get("api_key"));
    }

    /** Keys the relay got from this device before the flag existed, or under another spelling of the address, still leave when switched off. */
    private static void keysFlagAcrossUpgradeAndAddress() {
        // an install from before the flag: keys on, no saved address, no flag; its first run stops before the profile step
        Env e = new Env();
        e.cfg.keys = true;
        e.relay.profile = map("user_context", "", "api_key", "gsk_phone", "base_url", "https://api.groq.com/openai/v1");
        e.relay.profileVersion = 1;
        e.relay.always.put("changes", new RelayApi.RelayError(503, "down"));
        e.sync();
        eq("upgrade: the address is recorded", "http://relay.test:8787", e.store.meta.get("relay_origin"));
        eq("upgrade: the keys already on the relay count as ours", "1", e.store.meta.get("profile_keys_sent"));
        e.relay.always.clear();
        e.cfg.keys = false;
        e.sync();
        eq("upgrade: switched off before the first full sync, the keys leave the relay", false,
                e.relay.profile.containsKey("api_key") || e.relay.profile.containsKey("base_url"));

        // a fresh install with keys off does not claim keys another device put there
        Env fresh = new Env();
        fresh.relay.profile = map("user_context", "hello", "api_key", "gsk_other");
        fresh.relay.profileVersion = 1;
        fresh.relay.always.put("changes", new RelayApi.RelayError(503, "down"));
        fresh.sync();
        eq("fresh, keys off: no flag", null, fresh.store.meta.get("profile_keys_sent"));
        fresh.relay.always.clear();
        fresh.sync();
        eq("fresh, keys off: the other device's key stays", "gsk_other", fresh.relay.profile.get("api_key"));

        // the address is rewritten (the same relay) in the same save that switches keys off
        Env same = new Env();
        same.cfg.keys = true;
        same.sync();
        eq("rewrite: keys went up", "gsk_phone", same.relay.profile.get("api_key"));
        eq("rewrite: flag set", "1", same.store.meta.get("profile_keys_sent"));
        same.cfg.url = "http://relay.test:8787/";
        same.store.meta.put("relay_origin", "http://old-spelling.test:8787");
        same.cfg.keys = false;
        same.sync();
        eq("rewrite: the keys leave the relay", false, same.relay.profile.containsKey("api_key"));
        eq("rewrite: the rest of the state was reset", "http://relay.test:8787", same.store.meta.get("relay_origin"));

        // a relay change keeps the flag (and resets the rest) while keys are on
        Env on = new Env();
        on.cfg.keys = true;
        on.sync();
        on.store.meta.put("relay_origin", "http://old-spelling.test:8787");
        on.relay.always.put("changes", new RelayApi.RelayError(503, "down"));
        on.sync();
        eq("relay change, keys on, run stopped early: flag kept", "1", on.store.meta.get("profile_keys_sent"));
        eq("relay change: the profile version was reset", "0", on.store.meta.get("profile_version"));
    }

    /** Issue #63: the relay's data was wiped (or restored from an older backup) at the same address: everything is sent again. */
    private static void relayReset() {
        Env e = new Env();
        Note a = e.store.add("first", 100);
        Note b = e.store.add("second", 200);
        e.sync();
        eq("reset: set up, all clean", false, a.dirty || b.dirty);
        eq("reset: the cursor is the relay's number", "2", e.store.meta.get("relay_cursor"));

        // same address, empty relay (its sequence starts again), with one note another device put there since
        FakeRelay wiped = new FakeRelay();
        wiped.store(id(99), "from the PC after the wipe", 150, false);
        SyncResult r = new SyncEngine(e.store, wiped, e.cfg).syncOnce();
        eq("reset: no error", "", r.error);
        eq("reset: every note is sent again", true, wiped.notes.containsKey(a.id) && wiped.notes.containsKey(b.id));
        eq("reset: two sent", 2, r.pushed);
        eq("reset: the note written after the wipe is received", "from the PC after the wipe", e.store.notes.get(id(99)).text);
        eq("reset: the cursor follows the wiped relay", String.valueOf(wiped.seq), e.store.meta.get("relay_cursor"));
        eq("reset: the profile is sent again", 1L, wiped.profileVersion);
        r = new SyncEngine(e.store, wiped, e.cfg).syncOnce();
        eq("reset: quiet afterwards", "0/0/", r.pushed + "/" + r.pulled + "/" + r.error);

        // a relay that does not say its number (an old relay.py): nothing changes
        Env quiet = new Env();
        Note q = quiet.store.add("kept", 1);
        quiet.sync();
        FakeRelay unknown = new FakeRelay();
        unknown.noSeq = true;
        r = new SyncEngine(quiet.store, unknown, quiet.cfg).syncOnce();
        eq("reset: no number, no reset", "0/", r.pushed + "/" + r.error);
        eq("reset: no number, the cursor stays", "1", quiet.store.meta.get("relay_cursor"));
        eq("reset: no number, the note is not sent again", false, q.dirty);

        // the relay cannot be reached: the run stops as before, nothing is reset
        Env off = new Env();
        Note o = off.store.add("offline", 1);
        off.sync();
        off.relay.always.put("health", new RelayApi.RelayError(0, "Cannot reach the relay (is Tailscale running?): ConnectException"));
        r = off.sync();
        eq("reset: offline is reported", true, r.error.contains("Cannot reach the relay"));
        eq("reset: offline keeps the cursor", "1", off.store.meta.get("relay_cursor"));
        eq("reset: offline keeps the note clean", false, o.dirty);
    }

    /** F6 part 2: the sync state belongs to one relay; another address means everything is sent again. */
    private static void relayChange() {
        Env e = new Env();
        Note a = e.store.add("first", 100);
        Note b = e.store.add("second", 200);
        Note gone = e.store.add("third", 300);
        e.sync();
        gone.deleted = true;
        gone.updatedAt = 400;
        gone.dirty = true;
        e.sync();
        eq("relay change: set up, all clean", false, a.dirty || b.dirty || gone.dirty);
        eq("relay change: the origin is remembered", "http://relay.test:8787", e.store.meta.get("relay_origin"));

        // the same relay spelled differently: nothing is reset
        e.cfg.url = "HTTP://Relay.TEST:8787/";
        SyncResult r = e.sync();
        eq("relay change: another spelling is not another relay", "0/", r.pushed + "/" + r.error);
        eq("relay change: no note sent again", 1, e.relay.puts(a.id));
        eq("relay change: the cursor is kept", String.valueOf(e.relay.seq), e.store.meta.get("relay_cursor"));
        eq("relay change: a path counts (another place on the same host)", false, SyncEngine.originOf("http://h/a").equals(SyncEngine.originOf("http://h/b")));

        // a new, empty relay (with a note another device put there first)
        FakeRelay fresh = new FakeRelay();
        fresh.store(id(99), "from the PC", 150, false);
        e.cfg.url = "http://other.test:8787/";
        SyncResult r2 = new SyncEngine(e.store, fresh, e.cfg).syncOnce();
        eq("relay change: no error", "", r2.error);
        eq("relay change: the live notes are pushed", true, fresh.notes.containsKey(a.id) && fresh.notes.containsKey(b.id));
        eq("relay change: the delete marker is pushed", Boolean.TRUE, fresh.notes.get(gone.id).get("deleted"));
        eq("relay change: three sent", 3, r2.pushed);
        eq("relay change: the note the new relay had is received", "from the PC", e.store.notes.get(id(99)).text);
        eq("relay change: all clean again", false, a.dirty || b.dirty || gone.dirty);
        eq("relay change: the cursor follows the new relay", String.valueOf(fresh.seq), e.store.meta.get("relay_cursor"));
        eq("relay change: the profile is sent to the new relay", 1L, fresh.profileVersion);
        eq("relay change: the new origin is remembered", "http://other.test:8787", e.store.meta.get("relay_origin"));
        r2 = new SyncEngine(e.store, fresh, e.cfg).syncOnce();
        eq("relay change: quiet afterwards", "0/0", r2.pushed + "/" + r2.pulled);

        // a first sync after an update has no origin saved: it is recorded, not treated as a change
        Env old = new Env();
        Note n = old.store.add("kept", 1);
        old.store.markSynced(n.id, 1, 5);
        old.store.setMeta("relay_cursor", "5");
        old.relay.seq = 5;   // the relay has had those five writes (a relay below the cursor was wiped: see relayReset)
        old.sync();
        eq("relay change: no origin saved yet is not a change", "5", old.store.meta.get("relay_cursor"));
        eq("relay change: note not sent again", 0, old.relay.puts(n.id));
        eq("relay change: origin recorded", "http://relay.test:8787", old.store.meta.get("relay_origin"));

        // the address is read once per run: a config that changes it between reads must not split the run
        final int[] reads = {0};
        FakeCfg flipping = new FakeCfg() {
            @Override public String relayUrl() { reads[0]++; return reads[0] == 1 ? "http://a.test:8787" : "http://b.test:8787"; }
        };
        String pinnedUrl = flipping.relayUrl();   // what the worker reads, once
        Env pin = new Env();
        pin.store.add("n", 1);
        SyncResult pr = new SyncEngine(pin.store, pin.relay, new PinnedUrlConfig(flipping, pinnedUrl)).syncOnce();
        eq("pinned url: ok", "", pr.error);
        eq("pinned url: the wrapped config is not asked again", 1, reads[0]);
        eq("pinned url: the run's origin is the first address", "http://a.test:8787", pin.store.meta.get("relay_origin"));
        eq("pinned url: other settings pass through", false, new PinnedUrlConfig(flipping, "x").syncKeys());
    }

    private static Object parse(String s) {
        return PlainJson.parse(s);
    }

    private static void neverThrows() {
        // anything unexpected becomes a message, never an exception
        Env e = new Env();
        e.store.add("x", 1);
        e.relay.beforeNotePut = new Runnable() {
            public void run() { throw new NullPointerException("secret detail that must not be shown"); }
        };
        SyncResult r = e.sync();
        eq("unexpected exception: a short message without the detail", "Sync failed: NullPointerException", r.error);
        eq("unexpected exception: nothing lost", true, e.store.notes.get(id(1)).dirty);

        // an answer that is not what a relay says
        e = new Env();
        e.store.add("x", 1);
        RelayApi odd = new FakeRelayReturning(map("applied", true));
        SyncResult r2 = new SyncEngine(e.store, odd, e.cfg).syncOnce();
        eq("answer without a note: says it is not a relay", RelayApi.RelayError.NOT_A_RELAY, r2.error);
        eq("answer without a note: the note stays dirty", true, e.store.notes.get(id(1)).dirty);
        e = new Env();
        e.store.add("x", 1);
        r2 = new SyncEngine(e.store, new FakeRelayReturning(map("note", "text", "applied", true)), e.cfg).syncOnce();
        eq("a note that is text: not a relay", RelayApi.RelayError.NOT_A_RELAY, r2.error);
        e = new Env();
        e.store.add("x", 1);
        r2 = new SyncEngine(e.store, new FakeRelayReturning(map("note", map("seq", 1L))), e.cfg).syncOnce();   // no "applied"
        eq("an answer without applied: not a relay", RelayApi.RelayError.NOT_A_RELAY, r2.error);

        // the notes store failing (a database error on the phone)
        e = new Env();
        e.relay.store(id(3), "from the PC", 10, false);
        SyncStore broken = new MemStore() {
            @Override public boolean applyRemote(Note n) { throw new IllegalStateException("disk full"); }
        };
        r = new SyncEngine(broken, e.relay, e.cfg).syncOnce();
        eq("store failure: a short message", "Sync failed: IllegalStateException", r.error);

        // a config that fails
        e = new Env();
        SyncConfig brokenCfg = new FakeCfg() {
            @Override public Map<String, Object> readProfile() { throw new IllegalStateException("prefs"); }
        };
        r = new SyncEngine(e.store, e.relay, brokenCfg).syncOnce();
        eq("settings failure: a short message", "Sync failed: IllegalStateException", r.error);
    }

    /** A relay whose note answers are fixed (what a server that is not a Vox relay might say). */
    static final class FakeRelayReturning implements RelayApi {
        private final Map<String, Object> answer;

        FakeRelayReturning(Map<String, Object> answer) {
            this.answer = answer;
        }

        @Override public Map<String, Object> putNote(Map<String, Object> wire) { return answer; }
        @Override public Changes changes(long since, int limit) { return new Changes(new ArrayList<Map<String, Object>>(), since, false); }
        @Override public Profile getProfile() { return new Profile(0, new LinkedHashMap<String, Object>()); }
        @Override public Profile putProfile(long ifMatch, Map<String, Object> data) { return new Profile(ifMatch + 1, data); }
    }

    /** Things that could keep a run going forever must not. */
    private static void loopGuards() {
        // a store that never clears the flag: the run still ends, and the note is sent once in it
        Env e = new Env();
        e.store.neverClears = true;
        e.store.add("stuck", 100);
        SyncResult r = e.sync();
        eq("flag never clears: the run ends with the note sent once", "1/1/", r.pushed + "/" + e.relay.puts(id(1)) + "/" + r.error);

        // a note edited while it is being sent stays dirty and its new version goes in the same run
        e = new Env();
        final Note n = e.store.add("v1", 100);
        final Env env = e;
        e.relay.beforeNotePut = new Runnable() {
            public void run() {
                if (env.relay.puts(n.id) == 1) {   // during the first send only
                    n.text = "v2";
                    n.updatedAt = 200;
                    n.dirty = true;
                }
            }
        };
        r = e.sync();
        eq("edited while sending: both versions were sent", "2/", r.pushed + "/" + r.error);
        eq("edited while sending: the relay has the newer one", "v2", e.relay.notes.get(n.id).get("text"));
        eq("edited while sending: and it is clean now", false, e.store.notes.get(n.id).dirty);
        eq("edited while sending: the second send was not counted as a new note", 1, e.relay.notes.size());
    }

    private static void wireFormat() {
        Note n = new Note();
        n.id = id(1);
        n.source = "voice note";
        n.title = "Title";
        n.text = "Body é";
        n.raw = "raw";
        n.createdAt = 1790035199.25;
        n.updatedAt = 1790035200.5;
        n.secs = 3.5;
        n.device = "Pixel 7";
        n.tags = new ArrayList<>(Arrays.asList("a", "b"));
        n.deleted = false;
        n.dirty = true;
        n.seq = 9;
        Map<String, Object> w = SyncEngine.toWire(n);
        eq("toWire: the fields of sync.wire, in its order", Arrays.asList("id", "source", "title", "text", "raw", "created_at", "updated_at", "secs", "device", "tags", "deleted"),
                new ArrayList<String>(w.keySet()));
        eq("toWire: values", map("id", id(1), "source", "voice note", "title", "Title", "text", "Body é", "raw", "raw", "created_at", 1790035199.25,
                "updated_at", 1790035200.5, "secs", 3.5, "device", "Pixel 7", "tags", list("a", "b"), "deleted", false), w);
        eq("toWire: through JSON and back", w, PlainJson.parse(PlainJson.stringify(w)));

        Note back = SyncEngine.fromWire(map("id", id(1), "source", "voice note", "title", "Title", "text", "Body é", "raw", "raw", "created_at", 1790035199.25,
                "updated_at", 1790035200.5, "secs", 3.5, "device", "Pixel 7", "tags", list("a", "b"), "deleted", false, "seq", 9L));
        eq("fromWire: the id", id(1), back.id);
        eq("fromWire: source, title, text, raw, device", Arrays.asList("voice note", "Title", "Body é", "raw", "Pixel 7"),
                Arrays.asList(back.source, back.title, back.text, back.raw, back.device));
        eq("fromWire: times and length", Arrays.asList(1790035199.25, 1790035200.5, 3.5), Arrays.asList(back.createdAt, back.updatedAt, back.secs));
        eq("fromWire: tags, deleted and the relay's number", "[a, b]/false/9", back.tags + "/" + back.deleted + "/" + back.seq);
        eq("fromWire: a received note is not dirty", false, back.dirty);

        Note loose = SyncEngine.fromWire(map("id", id(2), "created_at", 5L, "updated_at", 6L, "secs", 0L, "deleted", true, "seq", 3L, "tags", list("x", 7L, null, "y")));
        eq("fromWire: whole numbers are times too", "5.0/6.0/0.0", loose.createdAt + "/" + loose.updatedAt + "/" + loose.secs);
        eq("fromWire: missing text fields are empty, the source is a voice note", "voice note/|/", loose.source + "/" + loose.title + "|" + loose.text + "/" + loose.device);
        eq("fromWire: only text tags are kept", Arrays.asList("x", "y"), loose.tags);
        eq("fromWire: deleted", true, loose.deleted);
        eq("fromWire: a blank source is a voice note", "voice note", SyncEngine.fromWire(map("id", id(3), "source", "")).source);
        eq("fromWire: no id is no note", null, SyncEngine.fromWire(map("title", "x")));
        eq("fromWire: an id that is not text is no note", null, SyncEngine.fromWire(map("id", 5L)));
        eq("fromWire: an empty id is no note", null, SyncEngine.fromWire(map("id", "")));
        eq("fromWire: null", null, SyncEngine.fromWire(null));
        Note wrong = SyncEngine.fromWire(map("id", id(4), "title", 5L, "updated_at", "soon", "deleted", "yes", "tags", "a", "seq", "9"));
        eq("fromWire: wrong types become empty values", "//0.0/false/[]/0", wrong.title + "/" + wrong.text + "/" + wrong.updatedAt + "/" + wrong.deleted + "/" + wrong.tags + "/" + wrong.seq);
    }
}
