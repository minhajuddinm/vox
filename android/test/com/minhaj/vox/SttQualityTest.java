package com.minhaj.vox;

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
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * Speech-to-text basics (cleanup-quality stream 5), the Android side: the silent start and end are not sent, Whisper
 * models are asked for verbose_json (a 400 gets plain json), made-up segments and a prompt read back are dropped. The
 * rules themselves are pinned by the golden rows edgetrim, sttseg, sttkept and echo (ParityTest). Exits non-zero on failure.
 */
public final class SttQualityTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static byte[] level(double seconds, int v) {
        int n = (int) (seconds * 16000);
        byte[] b = new byte[n * 2];
        for (int i = 0; i < n; i++) {
            int s = i % 2 == 0 ? v : -v;
            b[2 * i] = (byte) (s & 0xff);
            b[2 * i + 1] = (byte) ((s >> 8) & 0xff);
        }
        return b;
    }

    private static byte[] cat(byte[]... parts) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        for (byte[] p : parts) o.write(p, 0, p.length);
        return o.toByteArray();
    }

    private static boolean startsWithSilence(byte[] pcm, double seconds) {
        int n = (int) (seconds * 32000);
        if (pcm.length < n) return false;
        for (int i = 0; i < n; i++) if (pcm[i] != 0) return false;
        return true;
    }

    private static boolean endsWithSilence(byte[] pcm, double seconds) {
        int n = (int) (seconds * 32000);
        if (pcm.length < n) return false;
        for (int i = pcm.length - n; i < pcm.length; i++) if (pcm[i] != 0) return false;
        return true;
    }

    /** A stub speech server: records each request's response_format, answers `verbose` to verbose_json (or 400 when null). */
    private static HttpServer stub(final String verbose, final String plain, final List<String> formats) throws IOException {
        HttpServer s = HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        s.createContext("/", ex -> {
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            try (InputStream in = ex.getRequestBody()) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
            }
            String body = new String(bo.toByteArray(), StandardCharsets.ISO_8859_1);
            boolean v = body.contains("name=\"response_format\"\r\n\r\nverbose_json\r\n");
            formats.add(v ? "verbose_json" : "json");
            String reply = v ? (verbose == null ? "{\"error\":{\"message\":\"bad response_format\"}}" : verbose) : plain;
            byte[] r = reply.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(v && verbose == null ? 400 : 200, r.length);
            try (OutputStream o = ex.getResponseBody()) { o.write(r); }
        });
        s.start();
        return s;
    }

    private static String base(HttpServer s) {
        return "http://127.0.0.1:" + s.getAddress().getPort() + "/v1";
    }

    private static final String MADE_UP = "{\"text\":\" Send it today. Thank you.\",\"segments\":["
            + "{\"start\":0,\"end\":2,\"text\":\" Send it today.\",\"avg_logprob\":-0.2,\"no_speech_prob\":0.01,\"compression_ratio\":1.1},"
            + "{\"start\":2,\"end\":4,\"text\":\" Thank you.\",\"avg_logprob\":-1.3,\"no_speech_prob\":0.8,\"compression_ratio\":0.9}]}";

    public static void main(String[] args) throws Exception {
        // ---- the trim
        byte[] audio = cat(level(2, 0), level(1, 8000), level(0.8, 0), level(1, 8000), level(3, 0));
        byte[] out = Pcm.trimEdges(audio, true, true);
        int[] r = Pcm.trimRange(audio, true, true);
        eq("about 200 ms of quiet stays before the speech", (2 * 32000 / 960 - 7) * 960, r[0]);   // from the frame the speech starts in
        eq("and after it", (int) (4.8 * 32000) + 7 * 960, r[1]);
        eq("the trimmed audio is that range", Arrays.toString(Arrays.copyOfRange(audio, r[0], r[1])), Arrays.toString(out));
        byte[] silent = level(3, 0), click = cat(level(1, 0), level(0.03, 8000), level(1, 0));
        eq("silence is never trimmed to nothing", true, Pcm.trimEdges(silent, true, true) == silent);
        eq("a click is not speech", true, Pcm.trimEdges(click, true, true) == click);
        eq("empty stays empty", 0, Pcm.trimEdges(new byte[0], true, true).length);
        eq("lead only keeps the end", true, endsWithSilence(Pcm.trimEdges(audio, true, false), 3));
        eq("tail only keeps the start", true, startsWithSilence(Pcm.trimEdges(audio, false, true), 2));
        eq("-32768 counts", 32768, Pcm.framePeaks(new byte[]{0, (byte) 0x80})[0]);

        // ---- streamed pieces: the first loses its start, the last its end, the middle is sent as cut
        final List<byte[]> got = Collections.synchronizedList(new ArrayList<byte[]>());
        StreamingStt st = new StreamingStt((pcm, context) -> { got.add(pcm); return "p" + got.size(); }, new Segmenter(6.0, 20.0, 0.6));
        st.start();
        byte[] rec = cat(level(2, 0), level(7, 8000), level(1, 0), level(7, 8000), level(1, 0), level(3, 8000), level(2, 0));
        for (int i = 0; i < rec.length; i += 3200) st.feed(rec, i, Math.min(3200, rec.length - i));
        eq("three pieces", "p1 p2 p3", st.finish(10000));
        eq("the first piece lost its silent start", false, startsWithSilence(got.get(0), 0.5));
        eq("but kept the pause at its cut", true, endsWithSilence(got.get(0), 0.6));
        eq("a middle piece keeps its pause", true, endsWithSilence(got.get(1), 0.6));
        eq("the last piece lost its silent end", false, endsWithSilence(got.get(2), 0.5));
        eq("the last piece keeps its start", true, startsWithSilence(got.get(2), 0.3));

        // ---- the answer
        eq("made-up segments are dropped", "Send it today.", ApiClient.transcriptOf(MADE_UP, ""));
        eq("plain json is read as before", "hi", ApiClient.transcriptOf("{\"text\":\" hi \"}", ""));
        eq("no text is nothing", "", ApiClient.transcriptOf("{}", ""));
        eq("segments without scores drop nothing", "a b",
                ApiClient.transcriptOf("{\"text\":\"a b\",\"segments\":[{\"start\":0,\"end\":1,\"text\":\"a\",\"avg_logprob\":null}]}", ""));
        eq("unreadable scores drop nothing", "a b",
                ApiClient.transcriptOf("{\"text\":\"a b\",\"segments\":[{\"start\":0,\"end\":1,\"text\":\"a\",\"no_speech_prob\":\"high\",\"avg_logprob\":-3}]}", ""));
        eq("the prompt read back is nothing", "", ApiClient.transcriptOf("{\"text\":\"Kubernetes, Tailscale, Groq.\"}", "Kubernetes, Tailscale, Groq."));
        eq("one real word is never an echo", "Groq", ApiClient.transcriptOf("{\"text\":\"Groq\"}", "Kubernetes, Tailscale, Groq."));
        for (String bad : new String[]{"{\"text\":null}", "{\"text\":5}", "[1]"}) {
            try {
                ApiClient.transcriptOf(bad, "");
                eq("an unreadable answer fails (kept for Retry): " + bad, true, false);
            } catch (IOException e) {
                checks++;
            }
        }
        eq("whisper models want segments", true, ApiClient.wantsSegments("whisper-large-v3-turbo") && ApiClient.wantsSegments("Whisper-1"));
        eq("other models do not", false, ApiClient.wantsSegments("gpt-4o-transcribe") || ApiClient.wantsSegments(null));

        // ---- the request
        File wav = File.createTempFile("vox-stt-test", ".wav");
        Files.write(wav.toPath(), new byte[2000]);
        List<String> f1 = Collections.synchronizedList(new ArrayList<String>());
        List<String> f2 = Collections.synchronizedList(new ArrayList<String>());
        List<String> f3 = Collections.synchronizedList(new ArrayList<String>());
        HttpServer verbose = stub(MADE_UP, "{\"text\":\"plain\"}", f1);
        HttpServer refuses = stub(null, "{\"text\":\"Ada, Grace, Kubernetes.\"}", f2);
        HttpServer other = stub(MADE_UP, "{\"text\":\"plain\"}", f3);
        try {
            List<String> none = Collections.emptyList();
            eq("whisper: verbose_json, made-up text dropped", "Send it today.",
                    new ApiClient("k", base(verbose)).transcribe(ApiClient.Upload.wav(wav), "whisper-large-v3-turbo", "", none, ""));
            eq("asked once, verbose", "[verbose_json]", f1.toString());
            eq("a 400 to verbose_json gets plain json, and an echo is still dropped", "",
                    new ApiClient("k", base(refuses)).transcribe(ApiClient.Upload.wav(wav), "whisper-large-v3", "",
                            Arrays.asList("Ada", "Grace", "Kubernetes"), ""));
            eq("verbose, then json", "[verbose_json, json]", f2.toString());
            eq("another model gets json", "plain",
                    new ApiClient("k", base(other)).transcribe(ApiClient.Upload.wav(wav), "gpt-4o-transcribe", "", none, ""));
            eq("json only", "[json]", f3.toString());
        } finally {
            verbose.stop(0); refuses.stop(0); other.stop(0);
            wav.delete();
        }
        System.out.println("OK: " + checks + " checks passed");
    }
}
