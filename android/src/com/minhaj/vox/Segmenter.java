package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Cuts a recording that is still going on into pieces at pauses, so each piece can be sent to speech-to-text while the
 * user keeps talking. {@link #feed} takes audio as it arrives and returns the pieces that are complete; {@link #rest}
 * returns what is left. The pieces and the rest together are exactly the audio that was fed, whatever the size of the
 * blocks it arrived in.
 *
 * <p>The Java twin of Segmenter in windows/vox_core.py: same frame size, same quiet level, same cut rules, and the
 * "segcuts" rows of spec/golden.txt prove it. Audio is 16 kHz, 16-bit little-endian mono. Pure Java (no android.*), so it
 * is unit-tested on a plain JDK. Not thread-safe: one thread feeds it.
 */
final class Segmenter {
    static final int SAMPLE_RATE = 16000;
    /** 30 ms at 16 kHz, in samples. */
    static final int FRAME = 480;
    /** A frame whose loudest sample is below this counts as a pause. */
    static final int QUIET_PEAK = 900;

    private final long minBytes, maxBytes;
    private final int pauseFrames;
    private byte[] buf = new byte[64 * 1024];
    private int len;
    private int scanned, quietRun, lastQuietEnd;

    /** The defaults of the Windows app: a piece is at least 12 s, at most 28 s, and ends in a pause of 0.6 s. */
    Segmenter() {
        this(12.0, 28.0, 0.6);
    }

    Segmenter(double minSeconds, double maxSeconds, double pauseSeconds) {
        this.minBytes = (long) (minSeconds * SAMPLE_RATE * 2);
        this.maxBytes = (long) (maxSeconds * SAMPLE_RATE * 2);
        // Python's round() takes a tie to the even number, Math.rint does the same
        this.pauseFrames = (int) Math.max(1, Math.rint(pauseSeconds / 0.03));
    }

    /** Adds audio; returns the pieces that are complete now, in order (usually none). */
    List<byte[]> feed(byte[] pcm, int off, int n) {
        if (n > 0) {
            if (len + n > buf.length) buf = Arrays.copyOf(buf, Math.max(buf.length * 2, len + n));
            System.arraycopy(pcm, off, buf, len, n);
            len += n;
        }
        List<byte[]> out = new ArrayList<>();
        final int size = FRAME * 2;
        while (len - scanned >= size) {
            int peak = 0;
            for (int i = scanned; i < scanned + size; i += 2) {
                int v = (short) ((buf[i] & 0xff) | (buf[i + 1] << 8));
                int a = v < 0 ? -v : v;
                if (a > peak) peak = a;
            }
            scanned += size;
            if (peak < QUIET_PEAK) {
                quietRun++;
                lastQuietEnd = scanned;
            } else {
                quietRun = 0;
            }
            int cut = 0;
            if (scanned >= minBytes && quietRun >= pauseFrames) {
                cut = scanned;                       // long enough and a pause: cut here
            } else if (scanned >= maxBytes) {        // no pause for a long time: cut at the last quiet moment if there was one
                cut = lastQuietEnd >= maxBytes / 2 ? lastQuietEnd : scanned;
            }
            if (cut > 0) {
                out.add(Arrays.copyOfRange(buf, 0, cut));
                System.arraycopy(buf, cut, buf, 0, len - cut);
                len -= cut;
                scanned = quietRun = lastQuietEnd = 0;   // the remainder is scanned again from its start
            }
        }
        return out;
    }

    List<byte[]> feed(byte[] pcm) {
        return feed(pcm, 0, pcm.length);
    }

    /** What has not been cut yet. The segmenter starts over afterwards. */
    byte[] rest() {
        byte[] data = Arrays.copyOf(buf, len);
        len = 0;
        scanned = quietRun = lastQuietEnd = 0;
        return data;
    }
}
