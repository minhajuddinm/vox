package com.minhaj.vox;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * A server that cannot read m4a (a whisper.cpp server without ffmpeg, a relay upstream that only decodes WAV) answers 4xx to
 * the m4a upload: ApiClient sends the same audio once more as WAV and remembers, per server, to skip m4a from then on.
 * A stub server on loopback stands in for it. Exits non-zero on failure.
 */
public final class M4aFallbackTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    /** Answers `status` to an upload named audio.m4a and 200 to a WAV (`status` to both when `rejectAll`). */
    private static HttpServer stub(final int status, final boolean rejectAll, final List<String> filenames) throws IOException {
        HttpServer s = HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        s.createContext("/", new HttpHandler() {
            @Override public void handle(HttpExchange ex) throws IOException {
                ByteArrayOutputStream bo = new ByteArrayOutputStream();
                try (InputStream in = ex.getRequestBody()) {
                    byte[] buf = new byte[8192];
                    int n;
                    while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
                }
                String body = new String(bo.toByteArray(), StandardCharsets.ISO_8859_1);
                boolean m4a = body.contains("filename=\"audio.m4a\"");
                filenames.add(m4a ? "audio.m4a" : "audio.wav");
                boolean reject = rejectAll || m4a;
                byte[] reply = (reject ? "{\"error\":{\"message\":\"cannot decode\"}}" : "{\"text\": \"ok\"}").getBytes(StandardCharsets.UTF_8);
                ex.getResponseHeaders().add("Content-Type", "application/json");
                ex.sendResponseHeaders(reject ? status : 200, reply.length);
                try (OutputStream o = ex.getResponseBody()) { o.write(reply); }
            }
        });
        s.start();
        return s;
    }

    private static String base(HttpServer s) {
        return "http://127.0.0.1:" + s.getAddress().getPort() + "/v1";
    }

    private static File temp(String suffix) throws IOException {
        File f = File.createTempFile("vox-m4a-test", suffix);
        Files.write(f.toPath(), new byte[2000]);
        return f;
    }

    /** An m4a upload whose WAV twin is a WAV file. */
    private static ApiClient.Upload m4a(File m4a, final File wav) {
        return new ApiClient.Upload(m4a, UploadFormat.fileName(UploadFormat.M4A), UploadFormat.mime(UploadFormat.M4A), 6.0, false,
                new ApiClient.WavTwin() {
                    @Override public ApiClient.Upload make() {
                        return ApiClient.Upload.wav(wav);
                    }
                });
    }

    private static void expectStatus(String name, int status, ApiClient c, ApiClient.Upload up) throws IOException {
        try {
            c.transcribeRaw(up, "m", "", "");
            eq(name + ": an error was expected", true, false);
        } catch (ApiClient.ApiException e) {
            eq(name, status, e.code);
        }
    }

    public static void main(String[] args) throws Exception {
        List<String> picky415 = Collections.synchronizedList(new ArrayList<String>());
        List<String> picky422 = Collections.synchronizedList(new ArrayList<String>());
        List<String> refuses = Collections.synchronizedList(new ArrayList<String>());
        List<String> broken = Collections.synchronizedList(new ArrayList<String>());
        List<String> wavOnly = Collections.synchronizedList(new ArrayList<String>());
        HttpServer s415 = stub(415, false, picky415);   // only reads WAV
        HttpServer s422 = stub(422, false, picky422);
        HttpServer sAll = stub(400, true, refuses);     // refuses everything
        HttpServer s500 = stub(500, false, broken);     // a server error is not a format problem
        HttpServer sWav = stub(400, true, wavOnly);
        File m4aFile = temp(".m4a"), wavFile = temp(".wav");
        try {
            ApiClient c = new ApiClient("k", base(s415));
            eq("m4a is allowed at first", true, c.m4aAllowed());
            String a = c.transcribeRaw(m4a(m4aFile, wavFile), "m", "", "");
            eq("the WAV retry gets the answer", "{\"text\": \"ok\"}", a);
            eq("m4a first, then WAV", "[audio.m4a, audio.wav]", picky415.toString());
            eq("this server is remembered as not reading m4a", false, c.m4aAllowed());
            eq("a new client for the same address knows too", false, new ApiClient("k", base(s415)).m4aAllowed());
            eq("another server is not affected", true, new ApiClient("k", base(sAll)).m4aAllowed());

            ApiClient c4 = new ApiClient("k", base(s422));
            eq("422 is retried as WAV too", "{\"text\": \"ok\"}", c4.transcribeRaw(m4a(m4aFile, wavFile), "m", "", ""));

            // both formats refused: the server's error comes up and the format is not blamed
            ApiClient cb = new ApiClient("k", base(sAll));
            expectStatus("the error is the server's", 400, cb, m4a(m4aFile, wavFile));
            eq("one try each", "[audio.m4a, audio.wav]", refuses.toString());
            eq("m4a is not remembered as the cause", true, cb.m4aAllowed());

            // a server error is not retried as WAV
            expectStatus("500 passes through", 500, new ApiClient("k", base(s500)), m4a(m4aFile, wavFile));
            eq("one try only", "[audio.m4a]", broken.toString());

            // a WAV upload has no twin: its 4xx is final
            expectStatus("a WAV refusal is final", 400, new ApiClient("k", base(sWav)), ApiClient.Upload.wav(wavFile));
            eq("sent once", "[audio.wav]", wavOnly.toString());

            eq("the format rule", true, UploadFormat.formatRejected(400) && UploadFormat.formatRejected(415) && UploadFormat.formatRejected(422));
            eq("not a format rule", false, UploadFormat.formatRejected(401) || UploadFormat.formatRejected(429) || UploadFormat.formatRejected(500));
        } finally {
            s415.stop(0); s422.stop(0); sAll.stop(0); s500.stop(0); sWav.stop(0);
            m4aFile.delete(); wavFile.delete();
        }
        System.out.println("OK: " + checks + " checks passed");
    }
}
