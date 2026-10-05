package com.minhaj.vox;

/** Helpers for raw 16-bit little-endian mono audio. Pure Java so it can be tested off-device. */
final class Pcm {
    /** Loudest sample (about -34 dBFS) below which a recording is treated as silence. Same value as the Windows app. */
    static final int SILENCE_PEAK = 655;

    private Pcm() { }

    /** Meter level 0..1 for a normalised rms (0..1). Same curve as Windows (vox_core.level_from_rms). */
    static double levelFromRms(double rms) {
        return 1.0 - Math.pow(10.0, -30.0 * Math.max(0.0, rms - 0.004));
    }

    /** True when the recording never gets louder than {@link #SILENCE_PEAK}: nothing was said. */
    static boolean isSilent(byte[] pcm) {
        return isSilent(pcm, SILENCE_PEAK);
    }

    static boolean isSilent(byte[] pcm, int threshold) {
        if (pcm == null) return true;
        int peak = 0;
        for (int i = 0; i + 1 < pcm.length; i += 2) {
            int v = (short) ((pcm[i] & 0xff) | (pcm[i + 1] << 8));
            int a = v < 0 ? -v : v;
            if (a > peak) peak = a;
        }
        return peak < threshold;
    }

    // Edge-silence trim before upload: the twin of vox_core.trim_edges (golden rows "edgetrim"). Whisper invents text in long
    // silence, most often at the start or end of a clip; the pauses inside are kept.
    /** 300 ms (10 frames of 30 ms) of the quiet before the first and after the last speech is kept. */
    static final int TRIM_PAD_FRAMES = 10;
    /** Speech = this many frames in a row (90 ms) at {@link #SILENCE_PEAK} or louder: a lone click is not speech. */
    static final int TRIM_RUN_FRAMES = 3;
    /** From there the edge moves out over softer frames (a quiet first or last word) ... */
    static final int TRIM_SOFT_PEAK = SILENCE_PEAK / 2;
    /** ... with at most this many quieter frames (300 ms) between them. */
    static final int TRIM_GAP_FRAMES = 10;

    /** The loudest sample of every 30 ms frame ({@link Segmenter#FRAME} samples); a short last frame counts too. */
    static int[] framePeaks(byte[] pcm) {
        int n = pcm == null ? 0 : pcm.length / 2;
        int[] peaks = new int[(n + Segmenter.FRAME - 1) / Segmenter.FRAME];
        for (int i = 0; i < n; i++) {
            int v = (short) ((pcm[2 * i] & 0xff) | (pcm[2 * i + 1] << 8));
            int a = v < 0 ? -v : v;
            int f = i / Segmenter.FRAME;
            if (a > peaks[f]) peaks[f] = a;
        }
        return peaks;
    }

    /**
     * {first, end}: the frames to send. Speech is the first and the last run of TRIM_RUN_FRAMES frames at SILENCE_PEAK or
     * louder; each edge then moves outward over frames at TRIM_SOFT_PEAK or louder with at most TRIM_GAP_FRAMES quieter
     * frames between (a soft "so" before a pause), and TRIM_PAD_FRAMES of the quiet next to it stay. Only the edges asked
     * for are cut. With no such run nothing is cut: a recording is never trimmed to nothing, and the silence gate decides
     * about it as before.
     */
    static int[] edgeTrim(int[] peaks, boolean lead, boolean tail) {
        int n = peaks.length, first = -1, last = -1, run = 0;
        for (int i = 0; i < n; i++) {
            run = peaks[i] >= SILENCE_PEAK ? run + 1 : 0;
            if (run >= TRIM_RUN_FRAMES) {
                if (first < 0) first = i - TRIM_RUN_FRAMES + 1;
                last = i;
            }
        }
        if (first < 0) return new int[]{0, n};
        for (int k = first - 1, gap = 0; k >= 0 && gap <= TRIM_GAP_FRAMES; k--) {
            if (peaks[k] >= TRIM_SOFT_PEAK) { first = k; gap = 0; } else gap++;
        }
        for (int k = last + 1, gap = 0; k < n && gap <= TRIM_GAP_FRAMES; k++) {
            if (peaks[k] >= TRIM_SOFT_PEAK) { last = k; gap = 0; } else gap++;
        }
        return new int[]{lead ? Math.max(0, first - TRIM_PAD_FRAMES) : 0, tail ? Math.min(n, last + 1 + TRIM_PAD_FRAMES) : n};
    }

    /** {start, end}: the bytes of the recording that are sent (see {@link #edgeTrim}). */
    static int[] trimRange(byte[] pcm, boolean lead, boolean tail) {
        int len = pcm == null ? 0 : pcm.length;
        int[] peaks = framePeaks(pcm);
        int[] f = edgeTrim(peaks, lead, tail);
        int size = Segmenter.FRAME * 2;
        return new int[]{f[0] * size, f[1] >= peaks.length ? len : f[1] * size};
    }

    /** The recording without its silent start and end (the same array when nothing is cut). */
    static byte[] trimEdges(byte[] pcm, boolean lead, boolean tail) {
        int[] r = trimRange(pcm, lead, tail);
        if (pcm == null || (r[0] == 0 && r[1] == pcm.length)) return pcm;
        return java.util.Arrays.copyOfRange(pcm, r[0], r[1]);
    }
}
