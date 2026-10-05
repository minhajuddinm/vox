package com.minhaj.vox;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Plain-Java checks for StreamingStt: a recording sent in pieces while it is still going on. The Android twin of
 * tests/test_streaming.py. A fake transcriber stands in for the server. Exits non-zero on failure.
 */
public final class StreamingSttTest {
    private static int checks;

    private static void check(String name, boolean ok) {
        checks++;
        if (!ok) {
            System.err.println("FAIL " + name);
            System.exit(1);
        }
    }

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static byte[] fill(double seconds, int v) {
        int n = (int) (seconds * Segmenter.SAMPLE_RATE);
        byte[] b = new byte[n * 2];
        for (int i = 0; i < n; i++) { b[2 * i] = (byte) (v & 0xff); b[2 * i + 1] = (byte) ((v >> 8) & 0xff); }
        return b;
    }

    private static byte[] tone(double s) { return fill(s, 8000); }

    private static byte[] silence(double s) { return fill(s, 0); }

    private static byte[] cat(byte[]... parts) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        for (byte[] p : parts) o.write(p, 0, p.length);
        return o.toByteArray();
    }

    /** Answers "piece1", "piece2", ...; remembers the context of each call; can fail on the nth call or wait on a latch. */
    private static final class Fake implements StreamingStt.Transcriber {
        final List<String> contexts = Collections.synchronizedList(new ArrayList<String>());
        final List<Integer> sizes = Collections.synchronizedList(new ArrayList<Integer>());
        final int failOn;
        final String[] answers;
        final CountDownLatch gate;   // when set, every call waits for it
        final CountDownLatch called = new CountDownLatch(1);
        final CountDownLatch aborted = new CountDownLatch(1);   // counted down when the sender cuts the request in flight

        @Override
        public void abort() {
            aborted.countDown();
        }

        Fake(int failOn, CountDownLatch gate, String... answers) {
            this.failOn = failOn; this.gate = gate; this.answers = answers;
        }

        @Override
        public String transcribe(byte[] pcm, String context) throws IOException {
            contexts.add(context);
            sizes.add(pcm.length);
            int n = contexts.size();
            called.countDown();
            if (gate != null) {
                try { gate.await(30, TimeUnit.SECONDS); } catch (InterruptedException e) { throw new IOException("interrupted"); }
            }
            if (n == failOn) throw new IOException("down");
            return answers.length >= n ? answers[n - 1] : "piece" + n;
        }
    }

    private static StreamingStt run(StreamingStt.Transcriber t, byte[] audio, int block) {
        StreamingStt s = new StreamingStt(t, new Segmenter());
        s.start();
        for (int i = 0; i < audio.length; i += block) s.feed(audio, i, Math.min(block, audio.length - i));
        return s;
    }

    public static void main(String[] args) throws Exception {
        byte[] three = cat(tone(13), silence(1), tone(13), silence(1), tone(4));

        // pieces are sent in order, each with the end of the text before it as context
        Fake f = new Fake(0, null);
        StreamingStt s = run(f, three, 3200);
        eq("text of the whole recording", "piece1 piece2 piece3", s.finish(10000));
        eq("three calls", 3, f.contexts.size());
        eq("first context", "", f.contexts.get(0));
        eq("second context", "piece1", f.contexts.get(1));
        eq("third context", "piece1 piece2", f.contexts.get(2));
        eq("no error", "", s.error());

        // the context is only the end of the earlier text
        StringBuilder longText = new StringBuilder();
        for (int i = 0; i < 40; i++) longText.append("word").append(i).append(' ');
        Fake lf = new Fake(0, null, longText.toString().trim(), "next");
        StreamingStt sl = run(lf, cat(tone(13), silence(1), tone(13), silence(1), tone(1)), 3200);
        sl.finish(10000);
        check("context is cut to its last 150 characters", lf.contexts.get(1).length() <= StreamingStt.CONTEXT_CHARS
                && longText.toString().trim().endsWith(lf.contexts.get(1)));

        // a short recording is left to the normal path
        Fake sf = new Fake(0, null);
        StreamingStt ss = run(sf, tone(5), 3200);
        check("a short recording gives null", ss.finish(10000) == null);
        eq("and sends nothing", 0, sf.contexts.size());

        // a failure hands back to the normal path
        Fake bad = new Fake(2, null);
        StreamingStt sb = run(bad, three, 3200);
        check("a failure gives null", sb.finish(10000) == null);
        check("the failure is kept", sb.error().contains("down"));

        // a silent piece is not sent, a hallucinated one is dropped
        Fake hf = new Fake(0, null, "Thank you.", "real words");
        StreamingStt sh = run(hf, cat(silence(13), silence(1), tone(13), silence(1), tone(2)), 3200);
        eq("silent piece skipped, hallucination dropped", "real words", sh.finish(10000));
        eq("two sends", 2, hf.contexts.size());

        // a closing "Thank you." after real words is real text; with nothing real before it, it is dropped
        Fake ty = new Fake(0, null, "real words", "Thank you.");
        StreamingStt sty = run(ty, cat(tone(13), silence(1), tone(3)), 3200);
        eq("closing thank you kept", "real words Thank you.", sty.finish(10000));
        Fake ty2 = new Fake(0, null, "Thank you.", "Bye");
        StreamingStt sty2 = run(ty2, cat(tone(13), silence(1), tone(3)), 3200);
        eq("nothing real before: both dropped", "", sty2.finish(10000));

        // a last piece shorter than 0.3 s is not sent
        Fake tf = new Fake(0, null);
        StreamingStt st = run(tf, cat(tone(13), silence(0.7), tone(0.2)), 3200);
        eq("tiny tail not sent", "piece1", st.finish(10000));
        eq("one call", 1, tf.contexts.size());

        // the overlap: the first piece is already being sent while the user is still talking (before finish)
        CountDownLatch gate = new CountDownLatch(1);
        Fake of = new Fake(0, gate);
        StreamingStt so = run(of, cat(tone(13), silence(1), tone(3)), 3200);
        check("the first piece goes out before the recording ends", of.called.await(10, TimeUnit.SECONDS));
        eq("only that piece so far", 1, of.contexts.size());
        gate.countDown();
        eq("and the rest follows at the end", "piece1 piece2", so.finish(10000));

        // what is still to send after the user stops is the tail (about 3.4 s), not the whole recording of 17 s
        check("only the tail is left to send", of.sizes.get(1) < 4 * Segmenter.SAMPLE_RATE * 2 && of.sizes.get(0) > 13 * Segmenter.SAMPLE_RATE * 2);

        // finish gives up when the server is too slow, and says so
        CountDownLatch never = new CountDownLatch(1);
        Fake slow = new Fake(0, never);
        StreamingStt sw = run(slow, cat(tone(13), silence(1), tone(3)), 3200);
        check("a slow server gives null", sw.finish(300) == null);
        check("and says why", sw.error().contains("timed out"));
        never.countDown();

        // cancel: nothing more is sent and the worker ends
        Fake cf = new Fake(0, null);
        StreamingStt sc = new StreamingStt(cf, new Segmenter());
        sc.start();
        sc.cancel();
        sc.feed(three, 0, three.length);
        Thread.sleep(200);
        eq("nothing is sent after a cancel", 0, cf.contexts.size());
        check("a cancelled job has no text", sc.finish(1000) == null);

        // cancel while pieces are out: finish hands back nothing, even though a piece was sent
        CountDownLatch hold = new CountDownLatch(1);
        Fake mid = new Fake(0, hold);
        StreamingStt sm = run(mid, cat(tone(13), silence(1), tone(3)), 3200);
        check("a piece is out", mid.called.await(10, TimeUnit.SECONDS));
        sm.cancel();
        hold.countDown();
        check("a job cancelled with a piece out gives null", sm.finish(2000) == null);

        // cancel with a piece in flight to a server that never answers (a silent socket): the request is cut, and finish()
        // returns at once instead of holding the caller's single worker thread for the whole read timeout
        CountDownLatch silent = new CountDownLatch(1);   // never counted down: abort() does not wake this fake, like a dead socket
        Fake dead = new Fake(0, silent);
        StreamingStt sd = run(dead, cat(tone(13), silence(1), tone(3)), 3200);
        check("a piece is out to the silent server", dead.called.await(10, TimeUnit.SECONDS));
        final String[] result = {"not returned"};
        final long[] tookMs = {-1};
        Thread waiter = new Thread(new Runnable() {
            @Override
            public void run() {
                long t0 = System.nanoTime();
                String r = sd.finish(60000);
                tookMs[0] = (System.nanoTime() - t0) / 1000000L;
                result[0] = r == null ? "null" : r;
            }
        });
        waiter.start();
        Thread.sleep(200);                                   // finish() is now waiting on the piece
        sd.cancel();
        waiter.join(5000);
        check("finish returns after a cancel while a piece is out", !waiter.isAlive());
        eq("and hands back nothing", "null", result[0]);
        check("promptly, not after the read timeout", tookMs[0] >= 0 && tookMs[0] < 3000);
        check("the request in flight was cut", dead.aborted.await(5, TimeUnit.SECONDS));
        silent.countDown();   // let the stuck fake end

        // a cancel that comes before any piece is out does not need to cut anything, and a second cancel is harmless
        Fake idle = new Fake(0, null);
        StreamingStt si = new StreamingStt(idle, new Segmenter());
        si.start();
        si.cancel();
        si.cancel();
        check("a cancelled idle stream gives null", si.finish(1000) == null);

        // inPieces: a recording too big for one upload is cut at pauses and sent piece by piece (vox_core._transcribe_in_pieces)
        Fake pf = new Fake(0, null);
        eq("in pieces: the texts joined", "piece1 piece2 piece3", StreamingStt.inPieces(three, pf));
        eq("in pieces: the context is the text before", "[, piece1, piece1 piece2]", pf.contexts.toString());
        eq("in pieces: every byte is sent once", three.length, pf.sizes.get(0) + pf.sizes.get(1) + pf.sizes.get(2));
        Fake quiet = new Fake(0, null);
        eq("in pieces: a silent piece is not sent", "piece1 piece2", StreamingStt.inPieces(cat(tone(13), silence(1), silence(13), silence(1), tone(4)), quiet));
        eq("in pieces: two calls", 2, quiet.contexts.size());
        Fake thanks = new Fake(0, null, "Thank you.", "real words");
        eq("in pieces: a silence phrase before any text is dropped", "real words", StreamingStt.inPieces(cat(tone(13), silence(1), tone(4)), thanks));
        Fake broken = new Fake(2, null);
        try {
            StreamingStt.inPieces(three, broken);
            check("in pieces: a failed piece fails the whole send", false);
        } catch (IOException expected) {
            eq("in pieces: stops at the failure", 2, broken.contexts.size());
        }
        check("in pieces: only a recording over the upload limit is cut", StreamingStt.needsPieces(StreamingStt.MAX_UPLOAD_BYTES + 1)
                && !StreamingStt.needsPieces(StreamingStt.MAX_UPLOAD_BYTES));
        eq("the upload limit is the PC app's (vox_core.MAX_UPLOAD_BYTES)", 20000000L, StreamingStt.MAX_UPLOAD_BYTES);

        System.out.println("OK: " + checks + " checks passed");
    }
}
