package com.minhaj.vox;

import java.io.IOException;
import java.net.ConnectException;
import java.net.NoRouteToHostException;
import java.net.SocketException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;

/**
 * Plain-Java checks for the latency rules in Latency (timeouts, the fast retry on a connect failure, the cleanup
 * token bound, when to warm the connection) and the upload format rule in UploadFormat. Exits non-zero on failure.
 */
public final class LatencyTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static String repeat(String s, int n) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < n; i++) b.append(s);
        return b.toString();
    }

    public static void main(String[] args) {
        // connect timeout: 5 s
        eq("connect timeout", 5000, Latency.CONNECT_MS);

        // speech-to-text read timeout grows with the audio, between 30 s and 3 min
        eq("stt timeout of a short clip", 32000, Latency.sttReadMs(4.0));
        eq("stt timeout is never under 30 s", 30000, Latency.sttReadMs(0.5));
        eq("stt timeout of one minute", 180000, Latency.sttReadMs(60));
        eq("stt timeout of six minutes is capped", 180000, Latency.sttReadMs(360));
        eq("stt timeout of nothing", 30000, Latency.sttReadMs(0));
        eq("stt timeout of a bad length", 30000, Latency.sttReadMs(-5));

        // cleanup read timeout grows with the words, between 20 s and 60 s
        eq("cleanup timeout of a short text", 20000 + 10 * 60, Latency.llmReadMs(10));
        eq("cleanup timeout of nothing", 20000, Latency.llmReadMs(0));
        eq("cleanup timeout of a long text is capped", 60000, Latency.llmReadMs(1000));
        eq("cleanup timeout of a negative count", 20000, Latency.llmReadMs(-3));

        // what counts as a connect failure (never reached the server, so the same request may be sent at once)
        eq("connection refused", true, Latency.isConnectFailure(new ConnectException("Connection refused")));
        eq("no route", true, Latency.isConnectFailure(new NoRouteToHostException("no route")));
        eq("dns", true, Latency.isConnectFailure(new UnknownHostException("api.groq.com")));
        eq("java connect timeout", true, Latency.isConnectFailure(new SocketTimeoutException("connect timed out")));
        eq("android connect timeout", true, Latency.isConnectFailure(
                new SocketTimeoutException("failed to connect to api.groq.com/1.2.3.4 (port 443) from /10.0.0.2 (port 5) after 5000ms")));
        eq("read timeout is not", false, Latency.isConnectFailure(new SocketTimeoutException("Read timed out")));
        eq("timeout with no message is not", false, Latency.isConnectFailure(new SocketTimeoutException()));
        eq("reset is not", false, Latency.isConnectFailure(new SocketException("Connection reset")));
        eq("plain io error is not", false, Latency.isConnectFailure(new IOException("boom")));
        eq("a server answer is not", false, Latency.isConnectFailure(new ApiClient.ApiException(503, "down")));
        eq("null is not", false, Latency.isConnectFailure(null));

        // the cleanup token bound: 2 x the estimated input tokens + 64, with room for the hidden reasoning of gpt-oss
        eq("bound of nothing", 64, Latency.maxTokens("", false));
        eq("bound of null", 64, Latency.maxTokens(null, false));
        eq("bound of one word", 2 * 3 + 64, Latency.maxTokens("hello", false));
        eq("bound with reasoning", 2 * 3 + 64 + Latency.REASONING_HEADROOM, Latency.maxTokens("hello", true));
        String thousand = repeat("lorem ipsum ", 500);   // 1000 words, 6000 characters
        eq("bound of 1000 words", 2 * 3000 + 64, Latency.maxTokens(thousand, false));
        eq("bound of non-Latin text counts characters", 2 * 100 + 64, Latency.maxTokens(repeat("न", 100), false));
        eq("a few long words count by characters", 2 * 19 + 64, Latency.maxTokens("extraordinarily incomprehensibilities", false));
        check(Latency.maxTokens(repeat("word ", 50), false) < Latency.maxTokens(repeat("word ", 500), false), "bound grows with the text");
        // the bound is a ceiling several times the real answer: 130 tokens are about 100 English words
        check(Latency.maxTokens(repeat("word ", 100), false) >= 4 * 130, "bound leaves plenty of room for a long answer");

        // when to warm the connection: not again within 3 s of the last time
        eq("first warm", true, Latency.shouldWarm(0, 1000));
        eq("again at once", false, Latency.shouldWarm(1000, 1500));
        eq("just under the gap", false, Latency.shouldWarm(1000, 1000 + Latency.WARM_GAP_MS - 1));
        eq("at the gap", true, Latency.shouldWarm(1000, 1000 + Latency.WARM_GAP_MS));
        eq("clock went back", true, Latency.shouldWarm(5000, 100));

        // upload format: compress from 4 s, keep WAV for short clips
        eq("a short clip stays WAV", UploadFormat.WAV, UploadFormat.choose(2.0, 64000));
        eq("just under 4 s stays WAV", UploadFormat.WAV, UploadFormat.choose(3.99, 127680));
        eq("4 s is compressed", UploadFormat.M4A, UploadFormat.choose(4.0, 128000));
        eq("a long clip is compressed", UploadFormat.M4A, UploadFormat.choose(120.0, 3840000));
        eq("a clip whose size is wrong stays WAV", UploadFormat.WAV, UploadFormat.choose(10.0, 100));
        eq("a clip whose length is wrong stays WAV", UploadFormat.WAV, UploadFormat.choose(0, 500000));
        eq("wav mime", "audio/wav", UploadFormat.mime(UploadFormat.WAV));
        eq("m4a mime", "audio/mp4", UploadFormat.mime(UploadFormat.M4A));
        eq("wav name", "audio.wav", UploadFormat.fileName(UploadFormat.WAV));
        eq("m4a name", "audio.m4a", UploadFormat.fileName(UploadFormat.M4A));
        eq("an unknown format is WAV", "audio/wav", UploadFormat.mime("flac"));
        eq("encoded is used when smaller", true, UploadFormat.useEncoded(1000000, 250000));
        eq("encoded is dropped when empty", false, UploadFormat.useEncoded(1000000, 0));
        eq("encoded is dropped when not smaller", false, UploadFormat.useEncoded(1000, 1000));
        eq("encoded is dropped when bigger", false, UploadFormat.useEncoded(1000, 2000));

        System.out.println("OK: " + checks + " checks passed");
    }

    private static void check(boolean ok, String name) {
        eq(name, true, ok);
    }
}
