package com.minhaj.vox;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * The phone's speech upload through the REAL relay's proxy: the relay (relay/relay.py) is started as a child process,
 * a tiny stub AI server (JDK HttpServer, on loopback) is set as its speech-to-text server, and ApiClient.transcribe's
 * request goes to {relay}/proxy/stt exactly as it does with "Use my relay as the AI server" on. The relay refuses an
 * upload that has no Content-Length (411), so this is the check that Android dictation works through it at all.
 * Run only with --integration (see android/run-tests.sh). Exits non-zero on failure.
 */
public final class ProxyUploadIntegrationTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new IllegalStateException(name + ": expected <" + expected + "> but got <" + actual + ">");
        }
    }

    /** What the stub saw of one request. */
    private static final class Seen {
        String path, contentType, contentLength, transferEncoding, authorization;
        byte[] body;
    }

    public static void main(String[] args) throws Exception {
        final CopyOnWriteArrayList<Seen> seen = new CopyOnWriteArrayList<>();
        HttpServer stub = HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        stub.createContext("/", new HttpHandler() {
            @Override public void handle(HttpExchange ex) throws IOException {
                Seen s = new Seen();
                s.path = ex.getRequestURI().getPath();
                s.contentType = ex.getRequestHeaders().getFirst("Content-Type");
                s.contentLength = ex.getRequestHeaders().getFirst("Content-Length");
                s.transferEncoding = ex.getRequestHeaders().getFirst("Transfer-Encoding");
                s.authorization = ex.getRequestHeaders().getFirst("Authorization");
                ByteArrayOutputStream bo = new ByteArrayOutputStream();
                try (InputStream in = ex.getRequestBody()) {
                    byte[] buf = new byte[8192];
                    int n;
                    while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
                }
                s.body = bo.toByteArray();
                seen.add(s);
                byte[] reply = "{\"text\": \"from the stub\"}".getBytes(StandardCharsets.UTF_8);
                ex.getResponseHeaders().add("Content-Type", "application/json");
                ex.sendResponseHeaders(200, reply.length);
                try (OutputStream o = ex.getResponseBody()) { o.write(reply); }
            }
        });
        stub.start();
        RelayIntegrationTest.RealRelay relay = null;
        File wav = File.createTempFile("vox-it", ".wav");
        boolean ok = false;
        try {
            byte[] audio = new byte[300000];
            for (int i = 0; i < audio.length; i++) audio[i] = (byte) (i * 31 + 7);   // includes CR LF and boundary-like bytes by chance
            Files.write(wav.toPath(), audio);
            relay = RelayIntegrationTest.RealRelay.start("relay/relay.py");
            String stubUrl = "http://127.0.0.1:" + stub.getAddress().getPort() + "/v1";
            setUpstream(relay, "{\"role\":\"stt\",\"base_url\":\"" + stubUrl + "\",\"api_key\":\"sk-stub-UPSTREAM-1234\"}");

            ApiClient phone = new ApiClient(relay.token, relay.url + "/proxy/stt");
            String answer = phone.transcribeRaw(wav, "whisper-large-v3-turbo", "en", Arrays.asList("Café", "東京"));
            eq("the answer comes back through the relay", "{\"text\": \"from the stub\"}", answer);
            eq("the stub got one request", 1, seen.size());
            Seen s = seen.get(0);
            eq("the fixed upstream path", "/v1/audio/transcriptions", s.path);
            eq("the relay's token did not travel on", "Bearer sk-stub-UPSTREAM-1234", s.authorization);
            eq("the upload arrived with a length", String.valueOf(s.body.length), s.contentLength);
            eq("and not chunked", null, s.transferEncoding);
            String text = new String(s.body, StandardCharsets.ISO_8859_1);
            eq("it is multipart", true, s.contentType.startsWith("multipart/form-data; boundary="));
            eq("the model field", true, text.contains("name=\"model\"\r\n\r\nwhisper-large-v3-turbo\r\n"));
            eq("the unicode prompt, as UTF-8", true, text.contains(new String("Café, 東京.".getBytes(StandardCharsets.UTF_8), StandardCharsets.ISO_8859_1)));
            eq("the audio file, byte for byte", true, indexOf(s.body, audio) > 0);
            ok = true;
        } catch (Throwable t) {
            System.err.println("FAIL " + t);
            t.printStackTrace();
        } finally {
            stub.stop(0);
            wav.delete();
            if (relay != null) relay.stop();
        }
        if (!ok) System.exit(1);
        System.out.println("OK: " + checks + " checks passed (ApiClient upload through the real relay proxy)");
    }

    private static void setUpstream(RelayIntegrationTest.RealRelay relay, String json) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(relay.url + "/admin/upstream").openConnection();
        c.setRequestMethod("PUT");
        c.setDoOutput(true);
        c.setRequestProperty("Authorization", "Bearer " + relay.token);
        c.setRequestProperty("Content-Type", "application/json");
        try (OutputStream o = c.getOutputStream()) { o.write(json.getBytes(StandardCharsets.UTF_8)); }
        int code = c.getResponseCode();
        c.disconnect();
        eq("the relay accepts the stub as its speech server", 200, code);
    }

    private static int indexOf(byte[] hay, byte[] needle) {
        outer:
        for (int i = 0; i + needle.length <= hay.length; i++) {
            for (int j = 0; j < needle.length; j++) if (hay[i + j] != needle[j]) continue outer;
            return i;
        }
        return -1;
    }
}
