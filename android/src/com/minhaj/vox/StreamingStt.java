package com.minhaj.vox;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Transcribes a long recording piece by piece while it is still being recorded: the Android twin of windows/streaming.py.
 *
 * <p>The recording thread hands audio to {@link #feed} (a queue put and a copy, nothing slow). A worker thread cuts the audio
 * at pauses ({@link Segmenter}), sends each finished piece to speech-to-text with the end of the text before it as context,
 * and keeps the texts in order. When the user stops, only the last piece is left to send, so a long dictation is ready
 * sooner. If anything goes wrong, or the recording was too short to be cut, {@link #finish} returns null and the caller
 * sends the whole recording as before: streaming is only a shortcut.
 *
 * <p>Pure Java (the server call is a {@link Transcriber} the caller supplies), so it is unit-tested on a plain JDK.
 */
final class StreamingStt {
    /** A last piece shorter than this is not sent. */
    static final double MIN_TAIL_SECONDS = 0.3;
    /** How much of the text before goes into the request for the next piece. */
    static final int CONTEXT_CHARS = 150;
    /** A recording bigger than this is sent in pieces (the speech servers refuse about 25 MB): vox_core.MAX_UPLOAD_BYTES. */
    static final long MAX_UPLOAD_BYTES = 20000000L;

    /** A piece sent in a row with others waits out a rate limit (429) this many times (vox_core.RATE_LIMIT_TRIES). */
    static final int RATE_LIMIT_TRIES = 3;
    /** How long then when the server does not say (Retry-After), and never longer than the max (vox_core.RATE_LIMIT_WAIT). */
    static final long RATE_LIMIT_WAIT_MS = 20_000, RATE_LIMIT_MAX_WAIT_MS = 60_000;
    private static final int READ_CHUNK = 1 << 20;

    /** True when audio (or an upload) of this many bytes is too big for one upload (see {@link #inPieces}). */
    static boolean needsPieces(long bytes) {
        return bytes > MAX_UPLOAD_BYTES;
    }

    /** Waits; a test passes one that does not, the app one that a cancel ends early (it then throws InterruptedException). */
    interface Sleeper {
        void sleep(long ms) throws InterruptedException;
    }

    static String inPieces(byte[] pcm, Transcriber t) throws IOException {
        return inPieces(new ByteArrayInputStream(pcm), t, Thread::sleep);
    }

    static String inPieces(byte[] pcm, Transcriber t, Sleeper sleeper) throws IOException {
        return inPieces(new ByteArrayInputStream(pcm), t, sleeper);
    }

    /**
     * The text of a recording too big for one upload: read from {@code pcm} a megabyte at a time, cut at pauses
     * ({@link Segmenter}) and each piece sent as soon as it is cut, on the caller's thread, with the end of the text before
     * it as context (vox_core._transcribe_in_pieces). Only one piece is in memory at a time, never the whole recording. A
     * silent piece is not sent; a silence phrase before any text is dropped; a rate limit is waited out ({@link #waiting}).
     * A piece that still fails fails the whole call.
     */
    static String inPieces(InputStream pcm, Transcriber t, Sleeper sleeper) throws IOException {
        Segmenter seg = new Segmenter();
        List<String> texts = new ArrayList<>();
        byte[] buf = new byte[READ_CHUNK];
        int n;
        while ((n = fill(pcm, buf)) > 0) {
            for (byte[] piece : seg.feed(buf, 0, n)) sendPiece(piece, t, texts, sleeper);
        }
        sendPiece(seg.rest(), t, texts, sleeper);
        return joined(texts).trim();
    }

    /** Reads until buf is full or the stream ends (a whole number of samples except at the very end); the bytes read. */
    private static int fill(InputStream in, byte[] buf) throws IOException {
        int got = 0, n;
        while (got < buf.length && (n = in.read(buf, got, buf.length - got)) > 0) got += n;
        return got;
    }

    private static void sendPiece(byte[] piece, Transcriber t, List<String> texts, Sleeper sleeper) throws IOException {
        if (piece.length == 0 || Pcm.isSilent(piece)) return;
        String text = waiting(t, piece, tail(texts), sleeper);
        if (text != null && !text.isEmpty() && (!texts.isEmpty() || !ApiClient.isSilenceHallucination(text))) texts.add(text);
    }

    /**
     * t.transcribe, but a rate limit (429: pieces sent back to back hit a per-minute limit, such as Groq's free tier) is
     * waited out and the same piece sent again, up to RATE_LIMIT_TRIES times: the server's Retry-After, else
     * RATE_LIMIT_WAIT_MS, at most RATE_LIMIT_MAX_WAIT_MS. Twin of vox_core._transcribe_waiting.
     */
    static String waiting(Transcriber t, byte[] piece, String context, Sleeper sleeper) throws IOException {
        for (int attempt = 0; ; attempt++) {
            try {
                return t.transcribe(piece, context);
            } catch (ApiClient.ApiException e) {
                if (e.code != 429 || attempt == RATE_LIMIT_TRIES) throw e;
                long wait = Math.min(RATE_LIMIT_MAX_WAIT_MS, e.retryAfterMs >= 0 ? e.retryAfterMs : RATE_LIMIT_WAIT_MS);
                try {
                    sleeper.sleep(wait);
                } catch (InterruptedException ie) {
                    throw new IOException("cancelled");
                }
            }
        }
    }

    /** Sends one piece of 16-bit mono 16 kHz audio to speech-to-text; returns its text ("" when there is none). */
    interface Transcriber {
        String transcribe(byte[] pcm, String context) throws IOException;

        /**
         * Cuts the request in flight so that {@link #transcribe} fails at once, and refuses the next one. Called once, from
         * another thread than the one inside transcribe (a cancel, or a finish that ran out of time). Default: nothing to cut.
         */
        default void abort() { }
    }

    private static final byte[] END = new byte[0];   // compared by identity: "the recording is over"

    private final Transcriber transcriber;
    private final Segmenter seg;
    private final LinkedBlockingQueue<byte[]> queue = new LinkedBlockingQueue<>();
    private final CountDownLatch done = new CountDownLatch(1);
    private final List<String> texts = new ArrayList<>();
    private final AtomicBoolean cut = new AtomicBoolean();   // the in-flight request is cut once
    private volatile boolean cancelled;
    private volatile int pieces;
    private volatile String error = "";
    private Thread thread;

    StreamingStt(Transcriber transcriber, Segmenter seg) {
        this.transcriber = transcriber;
        this.seg = seg;
    }

    void start() {
        thread = new Thread(this::run, "vox-stream");
        thread.setDaemon(true);
        thread.start();
    }

    /** Called from the recording thread: copies the bytes and queues them. */
    void feed(byte[] pcm, int off, int n) {
        if (cancelled || n <= 0) return;
        byte[] copy = new byte[n];
        System.arraycopy(pcm, off, copy, 0, n);
        queue.add(copy);
    }

    /**
     * Stops the worker; whatever it has is thrown away. The piece in flight is cut (so the worker ends), and a {@link #finish}
     * that is waiting for it returns at once: the caller's own worker thread must not stay blocked on a request nobody wants.
     */
    void cancel() {
        cancelled = true;
        queue.add(END);
        done.countDown();
        cutInFlight();
    }

    /** Aborts the request in flight, once, on a thread of its own (cutting a connection must not happen on the caller's thread). */
    private void cutInFlight() {
        if (!cut.compareAndSet(false, true)) return;
        Thread t = new Thread(transcriber::abort, "vox-stream-abort");
        t.setDaemon(true);
        t.start();
    }

    int pieces() { return pieces; }

    /** The reason streaming gave up ("" when it did not). */
    String error() { return error; }

    /**
     * The text of the whole recording, or null when the caller should transcribe the whole audio itself (nothing was cut,
     * something failed, or it took too long). Call it once the recording thread has stopped feeding.
     */
    String finish(long timeoutMs) {
        queue.add(END);
        boolean finished;
        try {
            finished = done.await(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            finished = false;
        }
        if (!finished) {
            cancelled = true;
            error = "timed out";
            cutInFlight();   // the caller sends the whole recording instead: the stuck piece must not keep its connection
        }
        if (cancelled || !error.isEmpty() || pieces == 0) return null;   // a cancelled job has no text to hand over
        StringBuilder b = new StringBuilder();
        for (String t : texts) {
            if (t.isEmpty()) continue;
            if (b.length() > 0) b.append(' ');
            b.append(t);
        }
        return b.toString().trim();
    }

    private void run() {
        try {
            while (true) {
                byte[] data = queue.take();
                if (data == END || cancelled) break;
                for (byte[] piece : seg.feed(data, 0, data.length)) send(piece);
            }
            if (!cancelled) {
                byte[] rest = seg.rest();
                if (pieces > 0 && rest.length >= Segmenter.SAMPLE_RATE * 2 * MIN_TAIL_SECONDS) send(rest);
            }
        } catch (Exception e) {   // includes IOException from the server: the caller falls back to the whole recording
            String m = e.getMessage();
            error = m == null || m.isEmpty() ? e.getClass().getSimpleName() : m;
        } finally {
            done.countDown();
        }
    }

    private void send(byte[] pcm) throws IOException {
        if (cancelled) throw new IOException("cancelled");   // a cancel came while earlier pieces were being sent: no new request
        pieces++;
        if (Pcm.isSilent(pcm)) return;   // a piece of pure silence has nothing to say
        String text = transcriber.transcribe(pcm, context());
        // a silence hallucination ("Thank you.") is only possible before any real text; after speech it is the speaker's
        if (text != null && !text.isEmpty() && (!texts.isEmpty() || !ApiClient.isSilenceHallucination(text))) texts.add(text);
    }

    private String context() {
        return tail(texts);
    }

    /** The texts joined with spaces. */
    private static String joined(List<String> texts) {
        StringBuilder b = new StringBuilder();
        for (String t : texts) {
            if (b.length() > 0) b.append(' ');
            b.append(t);
        }
        return b.toString();
    }

    /** The end of the texts so far, as the context of the next piece. */
    private static String tail(List<String> texts) {
        String b = joined(texts);
        return b.length() <= CONTEXT_CHARS ? b : b.substring(b.length() - CONTEXT_CHARS);
    }
}
