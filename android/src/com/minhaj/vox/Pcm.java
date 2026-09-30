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
}
