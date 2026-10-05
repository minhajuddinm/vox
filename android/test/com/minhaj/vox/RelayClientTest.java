package com.minhaj.vox;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * RelayClient against a real HTTP server on this computer (the JDK's own): headers, paths, bodies, the way every status
 * and every error body shape becomes a message, network failures, no redirects. No org.json, no device.
 * Run by CI, exits non-zero on failure.
 */
public final class RelayClientTest {
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

    /** One request the server saw. */
    private static final class Seen {
        String method, path, body;
        Map<String, String> headers = new LinkedHashMap<>();   // lower-cased names
    }

    private static final List<Seen> seen = Collections.synchronizedList(new ArrayList<Seen>());
    private static volatile int status = 200;
    private static volatile String reply = "{}";
    private static volatile String location;   // when set, the server redirects there

    private static final String TOKEN = "s3cr3t-T0KEN";

    private interface Call {
        void run() throws RelayApi.RelayError;
    }

    private static RelayApi.RelayError fails(String name, Call c) {
        checks++;
        try {
            c.run();
        } catch (RelayApi.RelayError e) {
            return e;
        }
        System.err.println("FAIL " + name + ": no error was thrown");
        System.exit(1);
        return null;
    }

    private static void answer(int code, String body) {
        status = code;
        reply = body;
        location = null;
        seen.clear();
    }

    private static String show(RelayClient.Check c) {
        return c.ok + "/" + c.message;
    }

    private static Seen last() {
        return seen.get(seen.size() - 1);
    }

    private static String readAll(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        return new String(out.toByteArray(), StandardCharsets.UTF_8);
    }

    private static HttpServer start(final boolean record) throws IOException {
        HttpServer s = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        s.createContext("/", new HttpHandler() {
            @Override
            public void handle(HttpExchange x) throws IOException {
                Seen r = new Seen();
                r.method = x.getRequestMethod();
                r.path = x.getRequestURI().getRawPath() + (x.getRequestURI().getRawQuery() == null ? "" : "?" + x.getRequestURI().getRawQuery());
                r.body = readAll(x.getRequestBody());
                for (String k : x.getRequestHeaders().keySet()) r.headers.put(k.toLowerCase(java.util.Locale.ROOT), x.getRequestHeaders().getFirst(k));
                if (record) seen.add(r);
                byte[] b = reply.getBytes(StandardCharsets.UTF_8);
                if (location != null) x.getResponseHeaders().add("Location", location);
                x.getResponseHeaders().add("Content-Type", "application/json");
                x.sendResponseHeaders(status, b.length == 0 ? -1 : b.length);
                if (b.length > 0) {
                    OutputStream o = x.getResponseBody();
                    o.write(b);
                    o.close();
                }
                x.close();
            }
        });
        s.start();
        return s;
    }

    private static String url(HttpServer s) {
        return "http://127.0.0.1:" + s.getAddress().getPort();
    }

    public static void main(String[] args) throws Exception {
        HttpServer server = start(true);
        try {
            run(server);
        } finally {
            server.stop(0);
        }
        System.out.println("OK: " + checks + " checks passed");
    }

    private static void run(HttpServer server) throws Exception {
        problems();
        final String base = url(server);
        final RelayClient c = new RelayClient(base, TOKEN, "Pixel 7");

        // ---- putNote: method, path, headers, body, answer
        final Map<String, Object> wire = map("id", "0123456789abcdef0123456789abcdef", "source", "voice note", "title", "T", "text", "café 😀",
                "raw", "", "created_at", 1790035199.25, "updated_at", 1790035200.5, "secs", 3.5, "device", "Pixel 7", "tags", list("a"), "deleted", false);
        answer(200, "{\"note\": {\"id\": \"0123456789abcdef0123456789abcdef\", \"seq\": 7, \"updated_at\": 1790035200.5, \"deleted\": false, \"tags\": [\"x\"]}, \"applied\": true}");
        Map<String, Object> out = c.putNote(wire);
        eq("putNote: answer", map("note", map("id", "0123456789abcdef0123456789abcdef", "seq", 7L, "updated_at", 1790035200.5, "deleted", false, "tags", list("x")), "applied", true), out);
        Seen s = last();
        eq("putNote: method and path", "PUT /notes/0123456789abcdef0123456789abcdef", s.method + " " + s.path);
        eq("putNote: bearer token", "Bearer " + TOKEN, s.headers.get("authorization"));
        eq("putNote: device name", "Pixel 7", s.headers.get("x-vox-device"));
        eq("putNote: JSON content type", true, s.headers.get("content-type").startsWith("application/json"));
        eq("putNote: the body is the note, non-ASCII intact", wire, PlainJson.parse(s.body));
        eq("putNote: a plain Content-Length body, not chunked", false, s.headers.containsKey("transfer-encoding"));
        eq("putNote: no If-Match", false, s.headers.containsKey("if-match"));

        // ---- changes
        answer(200, "{\"notes\": [{\"id\": \"a\", \"seq\": 6}, {\"id\": \"b\", \"seq\": 7}], \"next\": 7, \"more\": true}");
        RelayApi.Changes ch = c.changes(5, 200);
        s = last();
        eq("changes: request", "GET /changes?since=5&limit=200", s.method + " " + s.path);
        eq("changes: nothing sent in a GET", "", s.body);
        eq("changes: no content type on a GET", false, s.headers.containsKey("content-type"));
        eq("changes: cursor and flag", "7/true", ch.next + "/" + ch.more);
        eq("changes: notes", list(map("id", "a", "seq", 6L), map("id", "b", "seq", 7L)), new ArrayList<Object>(ch.notes));
        answer(200, "{\"notes\": [], \"next\": 5, \"more\": false}");
        ch = c.changes(5, 200);
        eq("changes: an empty page", "0/5/false", ch.notes.size() + "/" + ch.next + "/" + ch.more);
        for (String junk : new String[]{"{\"notes\": \"x\", \"next\": 1, \"more\": false}", "{\"next\": 1, \"more\": false}", "{\"notes\": [], \"more\": false}",
                "{\"notes\": [], \"next\": \"1\", \"more\": false}", "{\"notes\": [1], \"next\": 1, \"more\": false}", "[]", "\"x\"", "null"}) {
            answer(200, junk);
            eq("changes: " + junk + " is not a relay's answer", RelayApi.RelayError.NOT_A_RELAY, fails("changes " + junk, () -> c.changes(0, 200)).getMessage());
        }
        answer(200, "{\"notes\": [], \"next\": 2}");
        eq("changes: a missing 'more' means no more", false, c.changes(0, 200).more);

        // ---- relaySeq: the relay's newest sequence number from /health (a wiped relay is found by it, SyncEngine)
        answer(200, "{\"ok\": true, \"notes\": 3, \"seq\": 42, \"version\": \"2.0.0\"}");
        eq("relaySeq: the number", 42L, c.relaySeq());
        eq("relaySeq: request", "GET /health", last().method + " " + last().path);
        answer(200, "{\"ok\": true, \"notes\": 3}");
        eq("relaySeq: not said is -1", -1L, c.relaySeq());
        answer(200, "{\"ok\": true, \"seq\": \"7\"}");
        eq("relaySeq: not a number is -1", -1L, c.relaySeq());
        answer(200, "[]");
        eq("relaySeq: not an object is -1", -1L, c.relaySeq());
        answer(500, "{\"error\": \"boom\"}");
        eq("relaySeq: a failure is a RelayError", 500, fails("relaySeq 500", () -> c.relaySeq()).status);

        // ---- profile
        answer(200, "{\"version\": 3, \"data\": {\"user_context\": \"hi\", \"dictionary\": [\"a\"]}}");
        RelayApi.Profile p = c.getProfile();
        eq("getProfile: request", "GET /profile", last().method + " " + last().path);
        eq("getProfile: version and data", "3/" + map("user_context", "hi", "dictionary", list("a")), p.version + "/" + p.data);
        answer(200, "{\"version\": 0, \"data\": {}}");
        eq("getProfile: no profile yet", "0/{}", c.getProfile().version + "/" + c.getProfile().data);
        for (String junk : new String[]{"{\"version\": \"3\", \"data\": {}}", "{\"version\": 3, \"data\": []}", "{\"data\": {}}", "{\"version\": 3}", "[]"}) {
            answer(200, junk);
            eq("getProfile: " + junk + " is not a relay's answer", RelayApi.RelayError.NOT_A_RELAY, fails("profile " + junk, () -> c.getProfile()).getMessage());
        }
        answer(200, "{\"version\": 4, \"data\": {\"user_context\": \"new\"}}");
        p = c.putProfile(3, map("user_context", "new"));
        s = last();
        eq("putProfile: request", "PUT /profile", s.method + " " + s.path);
        eq("putProfile: If-Match is the version read", "3", s.headers.get("if-match"));
        eq("putProfile: body", map("user_context", "new"), PlainJson.parse(s.body));
        eq("putProfile: answer", 4L, p.version);
        answer(200, "{\"version\": 1, \"data\": {}}");
        c.putProfile(0, map());
        eq("putProfile: the first save says If-Match 0", "0", last().headers.get("if-match"));
        answer(412, "{\"version\": 5, \"data\": {\"user_context\": \"other\"}}");
        RelayApi.RelayError stale = fails("412", () -> c.putProfile(3, map()));
        eq("putProfile: a stale write is status 412", 412, stale.status);

        // ---- errors: every status and every body shape
        Object[][] cases = {
                {401, "{\"error\": \"missing or wrong token\"}", "The relay refused the token.", false},
                {403, "{\"error\": \"this relay belongs to another tailnet user\"}", "The relay belongs to another Tailscale user.", false},
                {400, "{\"error\": \"bad note id\"}", "The relay answered HTTP 400 (bad note id).", true},
                {400, "{\"error\": \"created_at must be a number\"}", "The relay answered HTTP 400 (created_at must be a number).", true},
                {404, "{\"error\": \"unknown request\"}", "The relay answered HTTP 404 (unknown request).", true},
                {411, "{\"error\": \"length required\"}", "The relay answered HTTP 411 (length required).", true},
                {413, "{\"error\": \"request too large\"}", "The relay answered HTTP 413 (request too large).", true},
                {429, "{\"error\": \"slow down\"}", "The relay answered HTTP 429 (slow down).", false},
                {500, "{\"error\": \"the relay hit an error\"}", "The relay answered HTTP 500 (the relay hit an error).", false},
                {502, "{\"error\": {\"message\": \"upstream exploded\", \"type\": \"x\"}}", "The relay answered HTTP 502 (upstream exploded).", false},
                {503, "{\"error\": \"busy\"}", "The relay answered HTTP 503 (busy).", false},
                {500, "<html><body>Internal Server Error</body></html>", "The relay answered HTTP 500.", false},
                {404, "", "The relay answered HTTP 404.", true},
                {400, "{\"error\": \"\"}", "The relay answered HTTP 400.", true},
                {400, "{\"error\": 5}", "The relay answered HTTP 400.", true},
                {400, "{\"error\": {\"message\": 5}}", "The relay answered HTTP 400.", true},
                {400, "{\"error\": \"ends with a period.\"}", "The relay answered HTTP 400 (ends with a period).", true},
                {400, "{\"error\": \"two\\nlines\\tand   spaces\"}", "The relay answered HTTP 400 (two lines and spaces).", true},
        };
        for (Object[] k : cases) {
            answer((Integer) k[0], (String) k[1]);
            RelayApi.RelayError err = fails("error " + k[0] + " " + k[1], () -> c.putNote(wire));
            eq("error " + k[0] + " " + k[1] + ": message", k[2], err.getMessage());
            eq("error " + k[0] + " " + k[1] + ": status", k[0], err.status);
            eq("error " + k[0] + " " + k[1] + ": permanent", k[3], err.permanent());
            eq("error " + k[0] + " " + k[1] + ": the message field is the message", k[2], err.message);
            eq("error " + k[0] + " " + k[1] + ": the token is not in it", false, err.getMessage().contains(TOKEN));
        }
        StringBuilder longText = new StringBuilder();
        for (int i = 0; i < 500; i++) longText.append('x');
        answer(400, "{\"error\": \"" + longText + "\"}");
        String cut = fails("long detail", () -> c.putNote(wire)).getMessage();
        eq("error detail is cut", true, cut.length() < 260 && cut.startsWith("The relay answered HTTP 400 (xxx") && cut.endsWith("...)."));
        // 401 and 403 use the same words for every body, the status decides
        answer(401, "<html>nope</html>");
        eq("401 with any body", "The relay refused the token.", fails("401 html", () -> c.getProfile()).getMessage());
        answer(200, "<html><body>Not a relay</body></html>");
        RelayApi.RelayError html = fails("html", () -> c.getProfile());
        eq("200 that is not JSON", RelayApi.RelayError.NOT_A_RELAY, html.getMessage());
        eq("200 that is not JSON: no status, not permanent", "0/false", html.status + "/" + html.permanent());
        answer(200, "");
        eq("200 with no body", RelayApi.RelayError.NOT_A_RELAY, fails("empty", () -> c.getProfile()).getMessage());
        eq("the wording for a server that is not a relay is the one of sync.py", "That address did not answer like a Vox relay.", RelayApi.RelayError.NOT_A_RELAY);

        // errorDetail on its own
        eq("detail: string error", "bad", RelayClient.errorDetail("{\"error\":\"bad\"}"));
        eq("detail: OpenAI error", "worse", RelayClient.errorDetail("{\"error\":{\"message\":\"worse\"}}"));
        eq("detail: none", "", RelayClient.errorDetail("{}"));
        eq("detail: not JSON", "", RelayClient.errorDetail("nope"));
        eq("detail: null", "", RelayClient.errorDetail(null));
        eq("detail: an array", "", RelayClient.errorDetail("[\"error\"]"));
        eq("detail: control characters become spaces", "a b", RelayClient.errorDetail("{\"error\":\"a\\u0001b\"}"));

        // ---- no redirects: the token must never follow a Location to another server
        final int[] hits = {0};
        HttpServer other = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        other.createContext("/", new HttpHandler() {
            @Override public void handle(HttpExchange x) throws IOException {
                hits[0]++;
                x.sendResponseHeaders(200, -1);
                x.close();
            }
        });
        other.start();
        try {
            answer(302, "");
            location = url(other) + "/profile";
            RelayApi.RelayError moved = fails("redirect", () -> c.getProfile());
            eq("redirect: reported, not followed", "The relay answered HTTP 302.", moved.getMessage());
            eq("redirect: the other server was never asked", 0, hits[0]);
            eq("redirect: not a permanent error", false, moved.permanent());
            location = null;
        } finally {
            other.stop(0);
        }

        // ---- address handling
        answer(200, "{\"notes\": [], \"next\": 0, \"more\": false}");
        new RelayClient(base + "/vox/", TOKEN, "d").changes(0, 10);
        eq("a path in the address is a prefix, a trailing slash is dropped", "/vox/changes?since=0&limit=10", last().path);
        new RelayClient("  " + base + "//  ", TOKEN, "d").changes(0, 10);
        eq("blanks and slashes around the address are dropped", "/changes?since=0&limit=10", last().path);
        answer(200, "{\"note\": {}, \"applied\": false}");
        new RelayClient(base, TOKEN, "d").putNote(map("id", "a b/../c?x#y"));
        eq("an odd note id cannot change the path", "/notes/a+b%2F..%2Fc%3Fx%23y", last().path);

        // ---- header values
        answer(200, "{\"version\": 0, \"data\": {}}");
        new RelayClient(base, TOKEN, "Yuvraj's phone é\n中").getProfile();
        eq("device name: only printable ASCII goes into a header", "Yuvraj's phone ???", last().headers.get("x-vox-device"));
        new RelayClient(base, " " + TOKEN + " ", "").getProfile();
        eq("token: blanks around it are dropped", "Bearer " + TOKEN, last().headers.get("authorization"));

        // ---- the network
        HttpServer gone = start(false);
        String deadUrl = url(gone);
        gone.stop(0);
        RelayClient dead = new RelayClient(deadUrl, TOKEN, "Pixel 7");
        RelayApi.RelayError down = fails("connection refused", () -> dead.getProfile());
        eq("network failure: Tailscale is mentioned, the exception class named", "Cannot reach the relay (is Tailscale running?): ConnectException", down.getMessage());
        eq("network failure: no status, not permanent", "0/false", down.status + "/" + down.permanent());
        eq("network failure: neither the token nor the address is in the message", false, down.getMessage().contains(TOKEN) || down.getMessage().contains(deadUrl));
        eq("network failure on a put", "Cannot reach the relay (is Tailscale running?): ConnectException", fails("put down", () -> dead.putNote(wire)).getMessage());

        // ---- check(): the Test connection button
        answer(200, "{\"ok\": true, \"version\": \"0.2\", \"notes\": 3, \"seq\": 9}");
        RelayClient.Check ok = RelayClient.check(base, TOKEN, "Pixel 7");
        eq("check: connected", "true/Connected. The relay holds 3 notes.", ok.ok + "/" + ok.message);
        eq("check: asks /health with the token and the device", "GET /health/Bearer " + TOKEN + "/Pixel 7", last().method + " " + last().path + "/" + last().headers.get("authorization") + "/" + last().headers.get("x-vox-device"));
        answer(200, "{\"ok\": true, \"version\": \"0.2\"}");
        eq("check: a relay without a count", "Connected. The relay holds 0 notes.", RelayClient.check(base, TOKEN, "d").message);
        answer(200, "{\"ok\": true, \"notes\": 1.0}");
        eq("check: a count written as a fraction", "Connected. The relay holds 1 notes.", RelayClient.check(base, TOKEN, "d").message);
        for (String junk : new String[]{"{\"ok\": false}", "{}", "[1]", "<html>hi</html>", "null", "{\"ok\": \"yes\"}"}) {
            answer(200, junk);
            RelayClient.Check bad = RelayClient.check(base, TOKEN, "d");
            eq("check: " + junk + " is not a relay", "false/That address did not answer like a Vox relay.", bad.ok + "/" + bad.message);
        }
        answer(401, "{\"error\": \"missing or wrong token\"}");
        RelayClient.Check refused = RelayClient.check(base, "wrong", "d");
        eq("check: wrong token", "false/The relay refused the token.", refused.ok + "/" + refused.message);
        answer(500, "{}");
        eq("check: a relay that is broken", "false/The relay answered HTTP 500.", show(RelayClient.check(base, TOKEN, "d")));
        RelayClient.Check offline = RelayClient.check(deadUrl, TOKEN, "d");
        eq("check: offline", "false/Cannot reach the relay (is Tailscale running?): ConnectException", offline.ok + "/" + offline.message);
        seen.clear();
        eq("check: a bad address is explained without a request", "false/" + Endpoint.error("http://relay.example.com"),
                show(RelayClient.check("http://relay.example.com", TOKEN, "d")));
        eq("check: no address", "false/Enter the relay address.", show(RelayClient.check("", TOKEN, "d")));
        eq("check: no token", "false/Enter the relay token.", show(RelayClient.check(base, "", "d")));
        eq("check: a token with a space", "false/" + RelayClient.problem(base, "a b"), show(RelayClient.check(base, "a b", "d")));
        eq("check: none of those reached the server", 0, seen.size());

        // ---- check(): what the test learned (the decision itself is pinned by the relaycheck rows in spec/golden.txt)
        answer(200, "{\"ok\": true, \"version\": \"0.2\", \"notes\": 3}");
        RelayClient.Check full = RelayClient.check(base, TOKEN, "Pixel 7");
        eq("check fields: a good test", "true/true/true/Pixel 7/0.2/3", full.ok + "/" + full.reachable + "/" + full.tokenOk + "/" + full.deviceName + "/" + full.relayVersion + "/" + full.notes);
        eq("check fields: the device name is the one the header carries", "Caf?", RelayClient.check(base, TOKEN, "Café").deviceName);
        answer(200, "{\"ok\": true, \"version\": \"<b>0.2</b>\"}");
        eq("check fields: a version that could carry markup is dropped", "true/", RelayClient.check(base, TOKEN, "d").ok + "/" + RelayClient.check(base, TOKEN, "d").relayVersion);
        answer(401, "{\"error\": \"missing or wrong token\"}");
        RelayClient.Check noToken = RelayClient.check(base, "wrong", "d");
        eq("check fields: a refused token is reachable, not ok", "false/true/false", noToken.ok + "/" + noToken.reachable + "/" + noToken.tokenOk);
        answer(403, "{\"error\": \"this relay belongs to another tailnet user\"}");
        RelayClient.Check otherUser = RelayClient.check(base, TOKEN, "d");
        eq("check fields: another tailnet user: reachable and the token was right", "false/true/true/The relay belongs to another Tailscale user.", otherUser.ok + "/" + otherUser.reachable + "/" + otherUser.tokenOk + "/" + otherUser.message);
        RelayClient.Check off = RelayClient.check(deadUrl, TOKEN, "Pixel 7");
        eq("check fields: offline is not reachable and still names the device", "false/false/false/Pixel 7/0", off.ok + "/" + off.reachable + "/" + off.tokenOk + "/" + off.deviceName + "/" + off.notes);
        eq("check fields: the token is in none of the texts", false, (full.message + full.deviceName + full.relayVersion + noToken.message + off.message).contains(TOKEN));

        // ---- devices(): the Devices card (GET /devices, rows from DevicesView)
        answer(200, "{\"devices\": [{\"name\": \"Pixel 7\", \"first_seen\": 1.5, \"last_seen\": 999990.5, \"requests\": 4, \"login\": \"me@example.com\"},"
                + " {\"name\": \"Laptop\", \"first_seen\": 1.5, \"last_seen\": 990000, \"requests\": 9, \"login\": \"\"}]}");
        RelayClient.DeviceList dl = RelayClient.listDevices(base, TOKEN, "Pixel 7", 1000000.0);
        eq("devices: ok and no error", "true/", dl.ok + "/" + dl.error);
        eq("devices: asks GET /devices with the token and the device name", "GET /devices/Bearer " + TOKEN + "/Pixel 7",
                last().method + " " + last().path + "/" + last().headers.get("authorization") + "/" + last().headers.get("x-vox-device"));
        eq("devices: two rows, this phone marked", "active;true;just now;Pixel 7|recent;false;3 h ago;Laptop|", rowsText(dl.rows));
        eq("devices: the raw list for callers that want the fields", 2, c.devices().size());
        answer(200, "{\"devices\": []}");
        dl = RelayClient.listDevices(base, TOKEN, "Pixel 7", 1000000.0);
        eq("devices: an empty list is ok", "true/0", dl.ok + "/" + dl.rows.size());
        for (String junk : new String[]{"{}", "[]", "{\"devices\": \"x\"}", "{\"devices\": [1]}", "{\"devices\": null}", "<html>hi</html>", "null"}) {
            answer(200, junk);
            dl = RelayClient.listDevices(base, TOKEN, "d", 1000000.0);
            eq("devices: " + junk + " is not a relay", "false/That address did not answer like a Vox relay./0", dl.ok + "/" + dl.error + "/" + dl.rows.size());
        }
        answer(401, "{\"error\": \"missing or wrong token\"}");
        dl = RelayClient.listDevices(base, "wrong", "d", 1000000.0);
        eq("devices: wrong token", "false/The relay refused the token./0", dl.ok + "/" + dl.error + "/" + dl.rows.size());
        answer(404, "{\"error\": \"not found\"}");
        dl = RelayClient.listDevices(base, TOKEN, "d", 1000000.0);
        eq("devices: a relay too old for the list", "false/This relay is too old to list devices. Update relay.py on it./0", dl.ok + "/" + dl.error + "/" + dl.rows.size());
        answer(500, "{}");
        eq("devices: a relay that is broken", "false/The relay answered HTTP 500.", show(RelayClient.listDevices(base, TOKEN, "d", 1000000.0)));
        dl = RelayClient.listDevices(deadUrl, TOKEN, "d", 1000000.0);
        eq("devices: offline", "false/Cannot reach the relay (is Tailscale running?): ConnectException/0", dl.ok + "/" + dl.error + "/" + dl.rows.size());
        seen.clear();
        eq("devices: a bad address is explained without a request", "false/" + Endpoint.error("http://relay.example.com"),
                show(RelayClient.listDevices("http://relay.example.com", TOKEN, "d", 1000000.0)));
        eq("devices: no token", "false/Enter the relay token.", show(RelayClient.listDevices(base, "", "d", 1000000.0)));
        eq("devices: none of those reached the server", 0, seen.size());
    }

    private static String rowsText(List<DevicesView.Row> rows) {
        StringBuilder sb = new StringBuilder();
        for (DevicesView.Row r : rows) sb.append(r.state).append(';').append(r.thisDevice).append(';').append(r.ago).append(';').append(r.name).append('|');
        return sb.toString();
    }

    private static String show(RelayClient.DeviceList d) {
        return d.ok + "/" + d.error;
    }

    private static void problems() {
        eq("problem: a good https address and token", "", RelayClient.problem("https://your-pi.your-tailnet.ts.net", "abc_DEF-123"));
        eq("problem: a trailing slash is fine", "", RelayClient.problem("https://your-pi.your-tailnet.ts.net/", "abc"));
        eq("problem: surrounding blanks are fine", "", RelayClient.problem("  https://your-pi.your-tailnet.ts.net//  ", "abc"));
        eq("problem: a path is fine (a relay published under a path)", "", RelayClient.problem("https://host.ts.net/vox", "abc"));
        eq("problem: plain http on Tailscale", "", RelayClient.problem("http://100.101.102.103:8765", "abc"));
        eq("problem: plain http on a public host", Endpoint.error("http://relay.example.com"), RelayClient.problem("http://relay.example.com", "abc"));
        eq("problem: not an address", Endpoint.error("ftp://x"), RelayClient.problem("ftp://x", "abc"));
        eq("problem: blank address", "Enter the relay address.", RelayClient.problem("", "abc"));
        eq("problem: blank address with only a slash", "Enter the relay address.", RelayClient.problem(" / ", "abc"));
        eq("problem: null address", "Enter the relay address.", RelayClient.problem(null, "abc"));
        eq("problem: blank token", "Enter the relay token.", RelayClient.problem("https://h.ts.net", "  "));
        eq("problem: null token", "Enter the relay token.", RelayClient.problem("https://h.ts.net", null));
        eq("problem: address wins over token", Endpoint.error("http://relay.example.com"), RelayClient.problem("http://relay.example.com", ""));
        eq("problem: a question mark in the address", "The relay address must not have a ? or # in it.", RelayClient.problem("https://h.ts.net/x?y=1", "abc"));
        eq("problem: a fragment in the address", "The relay address must not have a ? or # in it.", RelayClient.problem("https://h.ts.net/#top", "abc"));
        eq("problem: a space inside the token", "The relay token has a space or another character that cannot be sent.", RelayClient.problem("https://h.ts.net", "ab cd"));
        eq("problem: a line break inside the token", "The relay token has a space or another character that cannot be sent.", RelayClient.problem("https://h.ts.net", "ab\ncd"));
        eq("problem: a non-ASCII character in the token", "The relay token has a space or another character that cannot be sent.", RelayClient.problem("https://h.ts.net", "abé"));
        eq("problem: spaces around the token are trimmed before, so fine", "", RelayClient.problem("https://h.ts.net", " abc "));
    }
}
