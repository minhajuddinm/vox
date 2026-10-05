package com.minhaj.vox;

import java.io.IOException;
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

    /** True when audio of this many bytes is too big for one upload (see {@link #inPieces}). */
    static boolean needsPieces(long pcmBytes) {
        return pcmBytes > MAX_UPLOAD_BYTES;
    }

    /**
     * The text of a recording too big for one upload: cut at pauses ({@link Segmenter}) and sent piece by piece on the
     * caller's thread, each with the end of the text before it as context (vox_core._transcribe_in_pieces). A silent piece
     * is not sent; a silence phrase before any text is dropped. A failed piece fails the whole call.
     */
    static String inPieces(byte[] pcm, Transcriber t) throws IOException {
        Segmenter seg = new Segmenter();
        List<byte[]> pieces = new ArrayList<>(seg.feed(pcm));
        pieces.add(seg.rest());
        List<String> texts = new ArrayList<>();
        for (byte[] piece : pieces) {
            if (piece.length == 0 || Pcm.isSilent(piece)) continue;
            String text = t.transcribe(piece, tail(texts));
            if (text != null && !text.isEmpty() && (!texts.isEmpty() || !ApiClient.isSilenceHallucination(text))) texts.add(text);
        }
        return joined(texts).trim();
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
