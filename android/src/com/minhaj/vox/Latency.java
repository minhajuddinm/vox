package com.minhaj.vox;

import java.io.IOException;
import java.net.ConnectException;
import java.net.NoRouteToHostException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import java.util.Locale;

/**
 * The small rules that decide how long a request may take and when it is repeated, so that the phone gives up on a
 * dead connection quickly instead of waiting a minute (see documentation/specs/p9b-speed.md). Pure Java (no android.*),
 * unit-tested by LatencyTest.
 */
final class Latency {
    private Latency() { }

    /** Time to open a connection (TCP and TLS). A server that does not answer in 5 s is not going to. */
    static final int CONNECT_MS = 5000;

    /** Do not warm the connection again when it was warmed less than this long ago (a touch, then the recording start). */
    static final long WARM_GAP_MS = 3000;

    /** Extra tokens for the hidden reasoning of gpt-oss models: they count against max_tokens even when not returned. */
    static final int REASONING_HEADROOM = 768;

    /**
     * How long to wait for the answer to a speech-to-text upload: 20 s plus 3 s for every second of audio, at least 30 s and
     * at most 3 minutes (before: a flat 60 s, which was too short for a six minute note and far too long for a short clip).
     */
    static int sttReadMs(double audioSeconds) {
        if (!(audioSeconds > 0)) return 30000;
        return (int) Math.max(30000, Math.min(180000, 20000 + audioSeconds * 3000));
    }

    /**
     * How long to wait for a cleanup answer: 20 s plus 60 ms for every word, at most 60 s. A cleanup that runs out of time
     * falls back to the words as spoken, so a long wait only delays the text.
     */
    static int llmReadMs(int words) {
        return (int) Math.min(60000L, 20000L + Math.max(0, words) * 60L);
    }

    /**
     * True when the request never reached the server (connection refused, no route, unknown host, or the connection could not
     * be opened in time), so sending the same request again at once cannot make it happen twice. A wait for the answer that
     * ran out is not a connect failure: the server may be working on the first request (the relay always is).
     */
    static boolean isConnectFailure(IOException e) {
        if (e == null || e instanceof ApiClient.ApiException) return false;
        if (e instanceof ConnectException || e instanceof NoRouteToHostException || e instanceof UnknownHostException) return true;
        if (e instanceof SocketTimeoutException) {
            String m = e.getMessage();
            return m != null && m.toLowerCase(Locale.ROOT).contains("connect");   // "connect timed out", "failed to connect to ..."
        }
        return false;
    }

    /**
     * The max_tokens of a cleanup request: twice the estimated tokens of the text plus 64 (a ceiling against a runaway answer,
     * far above the answer of a faithful cleanup), plus {@link #REASONING_HEADROOM} when the model reasons first. The estimate
     * is the larger of two words per word and half the characters for Latin text, and one token per character otherwise
     * (Hindi and other scripts use many tokens per word).
     */
    static int maxTokens(String raw, boolean reasoning) {
        int chars = raw == null ? 0 : raw.length();
        boolean ascii = true;
        for (int i = 0; i < chars && ascii; i++) if (raw.charAt(i) > 127) ascii = false;
        int est = ascii ? Math.max(words(raw) * 2, (chars + 1) / 2) : chars;
        return 2 * est + 64 + (reasoning ? REASONING_HEADROOM : 0);
    }

    /** The number of words (runs of characters that are not white space). */
    static int words(String text) {
        int words = 0;
        boolean inWord = false;
        for (int i = 0; text != null && i < text.length(); i++) {
            char c = text.charAt(i);
            boolean space = Character.isWhitespace(c) || Character.isSpaceChar(c);
            if (!space && !inWord) words++;
            inWord = !space;
        }
        return words;
    }

    /** Whether to open the server connections now: never warmed, or the last warm was long enough ago. */
    static boolean shouldWarm(long lastWarmMs, long nowMs) {
        return lastWarmMs <= 0 || nowMs < lastWarmMs || nowMs - lastWarmMs >= WARM_GAP_MS;
    }
}
