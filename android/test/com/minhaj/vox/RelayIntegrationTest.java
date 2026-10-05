package com.minhaj.vox;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.TimeUnit;

/**
 * The phone's sync client against a real relay: relay/relay.py is started as a Python process on a free port with a
 * new data folder, and two or three phones (SyncEngine over RelayClient, each with an in-memory notes store) sync
 * through it. Everything the other tests check against fakes is checked here against the real thing: what the relay
 * stores and numbers, the delete markers, a stale write that is refused (applied=false), the profile version and its
 * 412, the error bodies, the 401, and the paging of /changes. Nothing is mocked.
 *
 * Not part of the normal run: android/run-tests.sh runs it only with --integration (it needs Python 3.9 or newer and
 * the repository root as the working directory). Python is found as VOX_PYTHON, python3 or python; the relay is
 * started with --data-dir in a temp folder (never the real %APPDATA%) and stopped at the end. The token is read from
 * the relay.json that relay makes and is never printed. Exits non-zero on failure.
 */
public final class RelayIntegrationTest {
    private static int checks;

    /** A failed check. A plain exception (not System.exit) so the relay is always stopped on the way out. */
    private static final class Failure extends RuntimeException {
        private static final long serialVersionUID = 1L;

        Failure(String message) {
            super(message);
        }
    }

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (!Objects.equals(expected, actual)) {
            throw new Failure(name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
        }
    }

    private static String id(int n) {
        return String.format("%032x", n);
    }

    private static List<Object> list(Object... v) {
        return new ArrayList<Object>(Arrays.asList(v));
    }

    /** What one sync run did, on one line: pushed/pulled/error/profile. */
    private static String outcome(SyncResult r) {
        return r.pushed + "/" + r.pulled + "/" + r.error + "/" + r.profile;
    }

    // ------------------------------------------------------------------ the real relay

    /** relay/relay.py running in a child process. */
    static final class RealRelay {
        final String url;
        final String token;
        private final Process process;
        private final File root;

        private RealRelay(String url, String token, Process process, File root) {
            this.url = url;
            this.token = token;
            this.process = process;
            this.root = root;
        }

        /** Starts the relay and waits until it answers /health. Throws a Failure that says why when it does not. */
        static RealRelay start(String relayPy) throws Exception {
            if (!new File(relayPy).isFile()) {
                throw new Failure("no relay script at " + relayPy + " (run from the repository root)");
            }
            String python = python();
            int port;
            try (ServerSocket free = new ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))) {
                port = free.getLocalPort();
            }
            File root = Files.createTempDirectory("vox-relay-it").toFile();
            File data = new File(root, "data");
            ProcessBuilder pb = new ProcessBuilder(python, relayPy, "--data-dir", data.getPath(), "--port", String.valueOf(port));
            pb.redirectErrorStream(true);
            Process process;
            try {
                process = pb.start();
            } catch (IOException | RuntimeException e) {
                delete(root);   // nothing is running yet that would clean up the temp folder
                throw e;
            }
            StringBuilder output = drain(process);
            String url = "http://127.0.0.1:" + port;
            long deadline = System.currentTimeMillis() + 20000;
            while (true) {
                String token = tokenIn(data);
                if (process.isAlive() && token != null && RelayClient.check(url, token, "it-probe").ok) {
                    return new RealRelay(url, token, process, root);
                }
                boolean stopped = !process.isAlive();
                if (stopped || System.currentTimeMillis() > deadline) {
                    process.destroyForcibly();
                    String why = stopped ? "the relay stopped while starting (exit " + process.exitValue() + ")" : "the relay did not answer within 20 s";
                    delete(root);
                    synchronized (output) {
                        throw new Failure(why + ":\n" + output);
                    }
                }
                Thread.sleep(100);
            }
        }

        /** Stops the relay and removes its folder. */
        void stop() throws InterruptedException {
            process.destroy();
            if (!process.waitFor(5, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                process.waitFor(5, TimeUnit.SECONDS);
            }
            if (RelayClient.check(url, token, "it-probe").ok) {
                System.err.println("WARNING: the relay still answers on " + url + " after it was stopped");
            }
            delete(root);
        }

        private static void delete(File f) {
            File[] kids = f.listFiles();
            if (kids != null) for (File k : kids) delete(k);
            f.delete();   // best effort: a leftover temp folder is not a failure
        }

        /** The admin token in relay.json (bf-e SEC-4: changing the AI server needs it). */
        String adminToken() {
            return valueIn(new File(root, "data"), "admin_token");
        }

        /** The token in relay.json, or null while the relay has not made the file yet. */
        private static String tokenIn(File data) {
            return valueIn(data, "token");
        }

        private static String valueIn(File data, String key) {
            try {
                String json = new String(Files.readAllBytes(new File(data, "relay.json").toPath()), StandardCharsets.UTF_8);
                Object cfg = PlainJson.parse(json);
                Object token = cfg instanceof Map ? ((Map<?, ?>) cfg).get(key) : null;
                return token instanceof String && !((String) token).isEmpty() ? (String) token : null;
            } catch (IOException | IllegalArgumentException notThereYet) {
                return null;
            }
        }

        /** Keeps the end of what the relay prints (for a failure message) and keeps its pipe from filling up. */
        private static StringBuilder drain(final Process p) {
            final StringBuilder out = new StringBuilder();
            Thread t = new Thread(new Runnable() {
                @Override public void run() {
                    byte[] buf = new byte[1024];
                    try (InputStream in = p.getInputStream()) {
                        int n;
                        while ((n = in.read(buf)) != -1) {
                            synchronized (out) {
                                out.append(new String(buf, 0, n, StandardCharsets.UTF_8));
                                if (out.length() > 4000) out.delete(0, out.length() - 2000);
                            }
                        }
                    } catch (IOException closed) {
                        // the relay is gone
                    }
                }
            }, "relay-output");
            t.setDaemon(true);
            t.start();
            return out;
        }

        /**
         * The Python 3.9+ to run the relay with: VOX_PYTHON, python3 or python, the first that answers. Its own
         * sys.executable is what gets started, so a launcher or shim in front of it cannot outlive the test.
         */
        private static String python() throws Exception {
            List<String> candidates = new ArrayList<>();
            String given = System.getenv("VOX_PYTHON");
            if (given != null && !given.trim().isEmpty()) candidates.add(given.trim());
            candidates.add("python3");
            candidates.add("python");
            for (String c : candidates) {
                try {
                    Process p = new ProcessBuilder(c, "-c", "import sys; print(sys.executable); sys.exit(sys.version_info < (3, 9))")
                            .redirectErrorStream(true).start();
                    String out = read(p.getInputStream()).trim();
                    if (p.waitFor() == 0 && !out.isEmpty()) return out;
                } catch (IOException notInstalled) {
                    // try the next name
                }
            }
            throw new Failure("no Python 3.9 or newer found (put python3 on the PATH or set VOX_PYTHON)");
        }

        private static String read(InputStream in) throws IOException {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    // ------------------------------------------------------------------ phones

    /** A call hook that may throw (a RelayError from a second client). */
    interface Hook {
        void run() throws Exception;
    }

    /** Wraps a real client and, before the first {@code PUT /profile}, lets another device write: a real 412. */
    static final class RacingApi implements RelayApi {
        private final RelayApi real;
        Hook beforeFirstProfilePut;
        final List<String> profilePuts = new ArrayList<>();

        RacingApi(RelayApi real) {
            this.real = real;
        }

        @Override public Map<String, Object> putNote(Map<String, Object> wire) throws RelayError { return real.putNote(wire); }

        @Override public Changes changes(long since, int limit) throws RelayError { return real.changes(since, limit); }

        @Override public Profile getProfile() throws RelayError { return real.getProfile(); }

        @Override
        public Profile putProfile(long ifMatch, Map<String, Object> data) throws RelayError {
            if (beforeFirstProfilePut != null) {
                Hook h = beforeFirstProfilePut;
                beforeFirstProfilePut = null;
                try {
                    h.run();
                } catch (Exception e) {
                    throw new Failure("the other device could not write: " + e);
                }
            }
            try {
                Profile p = real.putProfile(ifMatch, data);
                profilePuts.add("ok");
                return p;
            } catch (RelayError e) {
                profilePuts.add(String.valueOf(e.status));
                throw e;
            }
        }
    }

    /** Wraps a real client and counts the pages of {@code /changes} that were asked for. */
    static final class CountingApi implements RelayApi {
        private final RelayApi real;
        int changePages;

        CountingApi(RelayApi real) {
            this.real = real;
        }

        @Override public Map<String, Object> putNote(Map<String, Object> wire) throws RelayError { return real.putNote(wire); }

        @Override public Changes changes(long since, int limit) throws RelayError {
            changePages++;
            return real.changes(since, limit);
        }

        @Override public Profile getProfile() throws RelayError { return real.getProfile(); }

        @Override public Profile putProfile(long ifMatch, Map<String, Object> data) throws RelayError { return real.putProfile(ifMatch, data); }
    }

    /** A phone: the notes and settings in memory (the same fakes SyncEngineTest uses), a real client to the relay. */
    static final class Phone {
        final SyncEngineTest.MemStore store = new SyncEngineTest.MemStore();
        final SyncEngineTest.FakeCfg cfg = new SyncEngineTest.FakeCfg();
        final RelayClient client;
        RelayApi api;

        Phone(String device, String url, String token) {
            client = new RelayClient(url, token, device);
            api = client;
        }

        SyncResult sync() {
            return new SyncEngine(store, api, cfg).syncOnce();
        }

        Note note(String id) {
            return store.notes.get(id);
        }
    }

    // ------------------------------------------------------------------ checks

    private static void sameNote(String name, Note expected, Note actual) {
        eq(name + ": exists", true, actual != null);
        eq(name + ": id", expected.id, actual.id);
        eq(name + ": source", expected.source, actual.source);
        eq(name + ": title", expected.title, actual.title);
        eq(name + ": text", expected.text, actual.text);
        eq(name + ": raw", expected.raw, actual.raw);
        eq(name + ": created_at", expected.createdAt, actual.createdAt);
        eq(name + ": updated_at", expected.updatedAt, actual.updatedAt);
        eq(name + ": secs", expected.secs, actual.secs);
        eq(name + ": device", expected.device, actual.device);
        eq(name + ": tags", expected.tags, actual.tags);
        eq(name + ": deleted", expected.deleted, actual.deleted);
        eq(name + ": not dirty", false, actual.dirty);
    }

    /** The relay's own copy of a note (or its delete marker), or null. */
    private static Map<String, Object> onRelay(Phone via, String id) throws RelayApi.RelayError {
        for (Map<String, Object> n : via.client.changes(0, 500).notes) if (id.equals(n.get("id"))) return n;
        return null;
    }

    private static Map<String, Object> sharedSettings(Phone p) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (String k : ProfileMerge.SHARED_FIELDS) out.put(k, p.cfg.profile.get(k));
        return out;
    }

    // ------------------------------------------------------------------ the story

    public static void main(String[] args) throws Exception {
        RealRelay relay = null;
        boolean ok = false;
        try {
            relay = RealRelay.start(args.length > 0 ? args[0] : "relay/relay.py");
            Phone a = new Phone("phone-a", relay.url, relay.token);
            Phone b = new Phone("phone-b", relay.url, relay.token);
            testConnection(relay);
            double t = noteTravels(a, b);
            devicesList(relay);
            t = editTravels(a, b, t);
            t = olderEditLoses(a, b, t);
            t = deleteTravels(a, b, t);
            t = refusedNoteDoesNotBlock(a, b, t);
            profileConflict(a, b);
            profileRetriesOn412(a, b);
            keysStayHome(a);
            t = pagingToNewPhone(relay, a, t);
            wrongToken(relay, a, t);
            fastClockStillConverges(a, b);
            ok = true;
        } catch (Failure f) {
            System.err.println("FAIL " + f.getMessage());
        } catch (Throwable t) {   // anything else (a crash in the client, an unexpected exception) is a failure too
            System.err.println("FAIL unexpected " + t);
            t.printStackTrace();
        } finally {
            if (relay != null) {
                try {
                    relay.stop();
                } catch (Throwable stopFailed) {   // must not hide the failure above, and must not pass silently either
                    System.err.println("FAIL stopping the relay: " + stopFailed);
                    ok = false;
                }
            }
        }
        if (!ok) System.exit(1);
        System.out.println("OK: " + checks + " checks passed against a real relay");
    }

    private static void testConnection(RealRelay relay) {
        RelayClient.Check good = RelayClient.check(relay.url, relay.token, "phone-a");
        eq("check: connects with the token", true, good.ok);
        eq("check: says what the relay holds", "Connected. The relay holds 0 notes.", good.message);
        RelayClient.Check bad = RelayClient.check(relay.url, "not-the-token", "phone-a");
        eq("check: a wrong token is refused", false, bad.ok);
        eq("check: in plain words (bf-e SEC-2: the relay first proves it holds the token, so a wrong one is never sent)", RelayProof.NOT_PROVEN, bad.message);
        eq("check: the relay's version is reported (the relay sends a short string such as 0.2)", true, good.relayVersion.matches("[0-9]+([.][0-9]+)*"));
        eq("check: reachable and the token took", "true/true/phone-a", good.reachable + "/" + good.tokenOk + "/" + good.deviceName);
        eq("check: a wrong token is reachable but not accepted", "true/false/", bad.reachable + "/" + bad.tokenOk + "/" + bad.relayVersion);
    }

    /** The two phones have used the relay by now: the Devices card lists both, marks the asking one, and a wrong token gets no list. */
    private static void devicesList(RealRelay relay) {
        RelayClient.DeviceList l = RelayClient.listDevices(relay.url, relay.token, "phone-b", System.currentTimeMillis() / 1000.0);
        eq("devices: the real relay answers", true, l.ok);
        java.util.Set<String> names = new java.util.TreeSet<>();
        String mine = "";
        for (DevicesView.Row r : l.rows) {
            names.add(r.name);
            if (r.thisDevice) mine += r.name;
            eq("devices: " + r.name + " was just seen", "active", r.state);
        }
        eq("devices: both phones are listed (the harness's own start-up probe is a device too)", "[it-probe, phone-a, phone-b]", names.toString());
        eq("devices: the asking phone is marked, only it", "phone-b", mine);
        RelayClient.DeviceList bad = RelayClient.listDevices(relay.url, "not-the-token", "phone-b", System.currentTimeMillis() / 1000.0);
        eq("devices: a wrong token is refused in plain words (and never sent)", "false/" + RelayProof.NOT_PROVEN + "/0", bad.ok + "/" + bad.error + "/" + bad.rows.size());
    }

    /** A adds a note and syncs; B syncs and has the same note, field for field. Returns the note's time. */
    private static double noteTravels(Phone a, Phone b) throws Exception {
        Note n = a.store.add(id(1), "placeholder", 0);
        n.title = "Trip to 東京";
        n.text = "Café ☕ 東京 🎙 \"quoted\" back\\slash\nsecond line\ttab";   // quotes, a backslash, a line break, a tab, an emoji (a surrogate pair)
        n.raw = "cafe tokyo raw";
        n.createdAt = 1700000000.123456;
        n.updatedAt = 1700000000.654321;
        n.secs = 12.75;
        n.tags = new ArrayList<>(Arrays.asList("travel", "日本"));
        eq("a sends its note, the profile too", "1/0//sent", outcome(a.sync()));
        eq("a: the note is clean", false, a.note(id(1)).dirty);
        eq("a: the relay's number is stored", 1L, a.note(id(1)).seq);
        eq("relay: one note", 1, a.client.changes(0, 500).notes.size());
        eq("b gets the note", "0/1//", outcome(b.sync()));
        sameNote("b has a's note", n, b.note(id(1)));
        eq("b: the relay's number is stored", 1L, b.note(id(1)).seq);
        eq("b: the cursor is saved", "1", b.store.getMeta("relay_cursor", ""));
        eq("a again: quiet", "0/0//", outcome(a.sync()));
        return n.updatedAt;
    }

    /** B edits (a newer updated_at), A pulls the edit. */
    private static double editTravels(Phone a, Phone b, double t) throws Exception {
        Note nb = b.note(id(1));
        nb.title = "Trip to Kyoto";
        nb.text = "edited on b";
        nb.updatedAt = t + 10;
        nb.dirty = true;
        eq("b sends the edit", "1/0//", outcome(b.sync()));
        eq("a gets the edit", "0/1//", outcome(a.sync()));
        sameNote("a has b's edit", nb, a.note(id(1)));
        eq("relay: the edit", "edited on b", onRelay(a, id(1)).get("text"));
        return t + 10;
    }

    /** Both edit while apart; the newer one is on the relay first, so the older one is refused (applied=false) and replaced. */
    private static double olderEditLoses(Phone a, Phone b, double t) throws Exception {
        Note na = a.note(id(1));
        na.text = "a's edit, made earlier";
        na.updatedAt = t + 20;
        na.dirty = true;
        Note nb = b.note(id(1));
        nb.text = "b's edit, made later";
        nb.updatedAt = t + 30;
        nb.dirty = true;
        eq("b sends the later edit", "1/0//", outcome(b.sync()));
        eq("a's earlier edit is refused and replaced", "0/1//", outcome(a.sync()));
        eq("a: now has b's text", "b's edit, made later", a.note(id(1)).text);
        eq("a: is clean", false, a.note(id(1)).dirty);
        eq("relay: kept the later edit", "b's edit, made later", onRelay(a, id(1)).get("text"));
        eq("b: quiet", "0/0//", outcome(b.sync()));
        return t + 30;
    }

    /** A deletes the note; B pulls the delete marker, and the text is gone on the relay too. */
    private static double deleteTravels(Phone a, Phone b, double t) throws Exception {
        Note na = a.note(id(1));
        na.deleted = true;
        na.title = "";
        na.text = "";
        na.raw = "";
        na.tags = new ArrayList<>();
        na.updatedAt = t + 10;
        na.dirty = true;
        eq("a sends the delete", "1/0//", outcome(a.sync()));
        Map<String, Object> marker = onRelay(a, id(1));
        eq("relay: keeps a delete marker", true, marker.get("deleted"));
        eq("relay: the marker has no text", "", marker.get("text"));
        eq("relay: the marker has no tags", new ArrayList<Object>(), marker.get("tags"));
        eq("b gets the delete marker", "0/1//", outcome(b.sync()));
        sameNote("b has the marker", na, b.note(id(1)));
        eq("b: the text is gone", "", b.note(id(1)).text);
        return t + 10;
    }

    /** A note the relay refuses for good (a bad id: 400) does not stop the note after it, and the relay's own words are shown. */
    private static double refusedNoteDoesNotBlock(Phone a, Phone b, double t) throws Exception {
        a.store.add(id(2), "after the bad one", t + 10);
        a.store.add("not-a-valid-id", "refused for good", t + 11);
        SyncResult r = a.sync();
        eq("a: the good note went, the bad one is reported",
                "1/0/1 note could not be sent: The relay answered HTTP 400 (bad note id)./", outcome(r));
        eq("a: the good note is clean", false, a.note(id(2)).dirty);
        eq("a: the bad note is still waiting", true, a.note("not-a-valid-id").dirty);
        eq("b gets the good note", "0/1//", outcome(b.sync()));
        eq("b: has its text", "after the bad one", b.note(id(2)).text);
        a.store.notes.remove("not-a-valid-id");   // it would be refused on every later run
        return t + 11;
    }

    /** One more note than a page of /changes holds (and more than one push batch): a phone that is new gets them all, over two pages. */
    private static double pagingToNewPhone(RealRelay relay, Phone a, double t) throws Exception {
        final int bulk = SyncEngine.PULL_LIMIT + 1;
        for (int i = 0; i < bulk; i++) a.store.add(id(1000 + i), "bulk " + i, t + 1 + i);
        eq("a sends " + bulk + " notes", bulk + "/0//", outcome(a.sync()));
        RelayApi.Changes all = a.client.changes(0, 2 * bulk);
        eq("relay: the marker, the good note and the bulk notes", bulk + 2, all.notes.size());
        eq("a: the cursor is the relay's last number", String.valueOf(all.next), a.store.getMeta("relay_cursor", ""));
        Phone c = new Phone("phone-c", relay.url, relay.token);
        CountingApi counting = new CountingApi(c.client);
        c.api = counting;
        eq("a new phone gets the good note and the bulk notes (no delete marker for a note it never had) and the profile",
                "0/" + (bulk + 1) + "//received", outcome(c.sync()));
        eq("c: asked for exactly two pages of /changes", 2, counting.changePages);
        eq("c: the profile is the phones' profile", sharedSettings(a), sharedSettings(c));
        eq("c: has the good note and the bulk notes", bulk + 1, c.store.notes.size());
        eq("c: the cursor is the relay's last number", String.valueOf(all.next), c.store.getMeta("relay_cursor", ""));
        eq("c: has the last bulk note", "bulk " + (bulk - 1), c.note(id(1000 + bulk - 1)).text);
        return t + bulk;
    }

    /** Both phones change the same field and each a field of its own; the relay's value wins the clash, the rest merges. */
    private static void profileConflict(Phone a, Phone b) throws Exception {
        eq("profile: a starts from the relay's first version", "1", a.store.getMeta("profile_version", ""));
        eq("profile: so does b", "1", b.store.getMeta("profile_version", ""));
        a.cfg.profile.put("user_context", "A: I am a nurse");
        a.cfg.profile.put("dictionary", list("Zeppelin"));
        b.cfg.profile.put("user_context", "B: I am a pilot");
        b.cfg.profile.put("default_style", "casual");
        eq("profile: a sends its changes", "0/0//sent", outcome(a.sync()));
        eq("profile: b sends its own and receives a's", "0/0//both", outcome(b.sync()));
        eq("profile: the clash goes to the relay's value", "A: I am a nurse", b.cfg.profile.get("user_context"));
        eq("profile: b took a's dictionary", list("Zeppelin"), b.cfg.profile.get("dictionary"));
        eq("profile: b kept its own style", "casual", b.cfg.profile.get("default_style"));
        eq("profile: a gets b's style", "0/0//received", outcome(a.sync()));
        eq("profile: the phones agree", sharedSettings(a), sharedSettings(b));
        RelayApi.Profile onRelay = a.client.getProfile();
        eq("profile: the relay has three versions", 3L, onRelay.version);
        eq("profile: the relay's user_context", "A: I am a nurse", onRelay.data.get("user_context"));
        eq("profile: the relay's style", "casual", onRelay.data.get("default_style"));
        eq("profile: the relay's dictionary", list("Zeppelin"), onRelay.data.get("dictionary"));
    }

    /** Another device writes the profile between A's read and A's write: the relay answers 412, A reads again and merges. */
    private static void profileRetriesOn412(final Phone a, final Phone b) throws Exception {
        RacingApi racing = new RacingApi(a.client);
        racing.beforeFirstProfilePut = new Hook() {
            @Override public void run() throws Exception {
                RelayApi.Profile p = b.client.getProfile();
                Map<String, Object> doc = new LinkedHashMap<>(p.data);
                doc.put("cleanup", false);
                b.client.putProfile(p.version, doc);
            }
        };
        a.api = racing;
        a.cfg.profile.put("language", "en");
        eq("412: a retries and both changes land", "0/0//both", outcome(a.sync()));
        eq("412: the first write got the relay's 412, the second went through", Arrays.asList("412", "ok"), racing.profilePuts);
        eq("412: a took the other device's change", false, a.cfg.profile.get("cleanup"));
        eq("412: a kept its own change", "en", a.cfg.profile.get("language"));
        RelayApi.Profile onRelay = a.client.getProfile();
        eq("412: the relay has both changes", "en/false", onRelay.data.get("language") + "/" + onRelay.data.get("cleanup"));
        eq("412: the relay's version", 5L, onRelay.version);
        a.api = a.client;
        eq("412: b receives both", "0/0//received", outcome(b.sync()));
        eq("412: the phones agree", sharedSettings(a), sharedSettings(b));
    }

    /** Provider settings and API keys stay on the phone while the switch is off. */
    private static void keysStayHome(Phone a) throws Exception {
        Map<String, Object> data = a.client.getProfile().data;
        eq("keys: the phone does have an API key", "gsk_phone", a.cfg.profile.get("api_key"));
        for (String k : ProfileMerge.KEY_FIELDS) eq("keys: " + k + " is not on the relay", false, data.containsKey(k));
    }

    /** A phone with the wrong token: the relay answers 401, the phone says so and loses nothing. */
    private static void wrongToken(RealRelay relay, Phone a, double t) throws Exception {
        Phone x = new Phone("phone-x", relay.url, "not-the-token");
        x.store.add(id(3), "never sent", t + 1);
        eq("401: a plain message (bf-e SEC-2: the token is not even sent)", "0/0/" + RelayProof.NOT_PROVEN + "/", outcome(x.sync()));
        eq("401: the note is still waiting", true, x.note(id(3)).dirty);
        eq("401: the cursor did not move", "0", x.store.getMeta("relay_cursor", "0"));
        eq("401: nothing reached the relay", null, onRelay(a, id(3)));
    }

    /**
     * Final fixes (relay-docs 1): phone A's clock is an hour fast. B edits A's note, then deletes it: A takes both. The
     * relay keeps A's own time for the note (a smaller one would make A ignore every later change for good) and raises
     * B's winning writes above it.
     */
    private static void fastClockStillConverges(Phone a, Phone b) throws Exception {
        double now = System.currentTimeMillis() / 1000.0;
        a.store.add(id(4000), "written on the fast phone", now + 3600);
        eq("fast: a sends its note", "1/0//", outcome(a.sync()).substring(0, 5));
        b.sync();
        eq("fast: b has it", "written on the fast phone", b.note(id(4000)).text);
        Note nb = b.note(id(4000));
        nb.text = "edited on b";
        nb.updatedAt = now + 30;
        nb.dirty = true;
        eq("fast: b's later edit is stored", 1, b.sync().pushed);
        a.sync();
        eq("fast: a takes b's edit", "edited on b", a.note(id(4000)).text);
        nb = b.note(id(4000));
        nb.deleted = true;
        nb.title = "";
        nb.text = "";
        nb.raw = "";
        nb.tags = new ArrayList<>();
        nb.updatedAt = now + 40;
        nb.dirty = true;
        eq("fast: b's delete is stored", 1, b.sync().pushed);
        a.sync();
        eq("fast: a takes the delete", true, a.note(id(4000)).deleted);
        eq("fast: a and b agree on the time", b.note(id(4000)).updatedAt, a.note(id(4000)).updatedAt);
    }
}
