package com.minhaj.vox;

import java.io.ByteArrayOutputStream;
import java.util.List;

/**
 * Plain-Java checks for Segmenter beyond the "segcuts" golden rows (those run in ParityTest): no audio is lost, a
 * segmenter can be used again after rest(), and odd block sizes do not change the pieces. Exits non-zero on failure.
 */
public final class SegmenterTest {
    private static int checks;

    private static void check(String name, boolean ok) {
        checks++;
        if (!ok) {
            System.err.println("FAIL " + name);
            System.exit(1);
        }
    }

    private static byte[] tone(double seconds) { return fill(seconds, 8000); }

    private static byte[] silence(double seconds) { return fill(seconds, 0); }

    private static byte[] fill(double seconds, int v) {
        int n = (int) (seconds * Segmenter.SAMPLE_RATE);
        byte[] b = new byte[n * 2];
        for (int i = 0; i < n; i++) { b[2 * i] = (byte) (v & 0xff); b[2 * i + 1] = (byte) ((v >> 8) & 0xff); }
        return b;
    }

    private static byte[] cat(byte[]... parts) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        for (byte[] p : parts) o.write(p, 0, p.length);
        return o.toByteArray();
    }

    private static byte[] all(List<byte[]> pieces, byte[] rest) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        for (byte[] p : pieces) o.write(p, 0, p.length);
        o.write(rest, 0, rest.length);
        return o.toByteArray();
    }

    private static final int FRAME_BYTES = Segmenter.FRAME * 2;

    /** n frames of constant loud audio; dips holds {frame, peak} pairs for the frames that have another peak instead. */
    private static byte[] loudFrames(int n, int... dips) {
        byte[] b = new byte[n * FRAME_BYTES];
        for (int f = 0; f < n; f++) {
            int v = 8000;
            for (int d = 0; d < dips.length; d += 2) if (dips[d] == f) v = dips[d + 1];
            for (int i = 0; i < Segmenter.FRAME; i++) {
                b[f * FRAME_BYTES + 2 * i] = (byte) (v & 0xff);
                b[f * FRAME_BYTES + 2 * i + 1] = (byte) ((v >> 8) & 0xff);
            }
        }
        return b;
    }

    private static int firstPiece(Segmenter s, byte[] audio) {
        List<byte[]> pieces = s.feed(audio);
        check("pieces plus rest are the audio", java.util.Arrays.equals(audio, all(pieces, s.rest())));
        return pieces.isEmpty() ? -1 : pieces.get(0).length;
    }

    public static void main(String[] args) {
        byte[] audio = cat(tone(14), silence(1), tone(14), silence(1), tone(3));
        Segmenter whole = new Segmenter();
        List<byte[]> expected = whole.feed(audio);
        byte[] expectedRest = whole.rest();
        check("a long recording is cut", expected.size() == 2);
        check("pieces plus rest are the audio", java.util.Arrays.equals(audio, all(expected, expectedRest)));
        for (int block : new int[] {2, 3, 1000, 1601, 7777, 96000}) {
            Segmenter s = new Segmenter();
            java.util.ArrayList<byte[]> got = new java.util.ArrayList<>();
            for (int i = 0; i < audio.length; i += block) got.addAll(s.feed(audio, i, Math.min(block, audio.length - i)));
            check("same pieces for block " + block, got.size() == expected.size());
            for (int k = 0; k < got.size(); k++) check("piece " + k + " for block " + block, java.util.Arrays.equals(got.get(k), expected.get(k)));
            check("same rest for block " + block, java.util.Arrays.equals(s.rest(), expectedRest));
        }
        // used again after rest(): starts from nothing
        Segmenter s = new Segmenter();
        s.feed(tone(5));
        check("rest returns what was fed", s.rest().length == 5 * Segmenter.SAMPLE_RATE * 2);
        check("rest again is empty", s.rest().length == 0);
        check("short audio is not cut after reuse", s.feed(tone(5)).isEmpty());
        // an empty feed is harmless
        check("empty feed", new Segmenter().feed(new byte[0]).isEmpty());
        // the pause must come after the minimum: speech then a pause before 12 s is not a cut
        Segmenter early = new Segmenter();
        check("a pause before the minimum is ignored", early.feed(cat(tone(6), silence(1), tone(4))).isEmpty());
        // constructor rules
        Segmenter tiny = new Segmenter(0.0, 0.0, 0.0);   // pause of 0 s still needs one quiet frame
        check("one quiet frame is enough when the pause is 0", tiny.feed(cat(tone(0.03), silence(0.03))).size() >= 1);
        // forced cuts: no pause by the maximum, so the cut is at the quietest frame of the last 2 s (limit = frame 934)
        check("quietest frame", firstPiece(new Segmenter(), loudFrames(2000, 910, 200, 920, 60, 925, 500)) == 921 * FRAME_BYTES);
        check("latest of equal frames", firstPiece(new Segmenter(), loudFrames(1200, 905, 0, 915, 0, 930, 0)) == 931 * FRAME_BYTES);
        check("no dip falls back to the limit", firstPiece(new Segmenter(), loudFrames(1200)) == 934 * FRAME_BYTES);
        check("a dip before the window is ignored", firstPiece(new Segmenter(), loudFrames(1200, 867, 0)) == 934 * FRAME_BYTES);
        check("a dip at the start of the window", firstPiece(new Segmenter(), loudFrames(1200, 868, 0)) == 869 * FRAME_BYTES);
        check("a soft dip is enough", firstPiece(new Segmenter(), loudFrames(1200, 900, 3000)) == 901 * FRAME_BYTES);
        check("never shorter than the minimum", firstPiece(new Segmenter(5, 6, 0.6), loudFrames(400, 140, 0)) == 200 * FRAME_BYTES);
        check("a dip after the minimum", firstPiece(new Segmenter(5, 6, 0.6), loudFrames(400, 170, 0)) == 171 * FRAME_BYTES);
        byte[] forced = loudFrames(2200, 915, 0, 1500, 100);
        Segmenter forcedWhole = new Segmenter();
        List<byte[]> forcedExpected = forcedWhole.feed(forced);
        for (int block : new int[] {2, 1000, 1601, 7777, 96000}) {
            Segmenter fs = new Segmenter();
            java.util.ArrayList<byte[]> got = new java.util.ArrayList<>();
            for (int i = 0; i < forced.length; i += block) got.addAll(fs.feed(forced, i, Math.min(block, forced.length - i)));
            check("forced cuts for block " + block, got.size() == forcedExpected.size());
            for (int k = 0; k < got.size(); k++) check("forced piece " + k + " for block " + block, java.util.Arrays.equals(got.get(k), forcedExpected.get(k)));
        }
        System.out.println("OK: " + checks + " checks passed");
    }
}
