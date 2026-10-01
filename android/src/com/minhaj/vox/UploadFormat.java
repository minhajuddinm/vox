package com.minhaj.vox;

/**
 * Which container the audio goes to the server in. The recording is 16 kHz 16-bit mono PCM; as a WAV file that is 32 KB
 * per second (256 kbit/s), which takes a while to send on a phone connection. From {@link #MIN_COMPRESS_SECONDS} on it is
 * encoded as AAC in an m4a file (64 kbit/s, a quarter of the size); shorter clips stay WAV, because the encoder costs
 * more time than the smaller upload saves. If the encoder fails the WAV is sent. Groq, OpenAI-compatible servers and the
 * relay all accept both. Pure Java; the encoding itself is AudioUpload (android.media).
 */
final class UploadFormat {
    private UploadFormat() { }

    static final String WAV = "wav", M4A = "m4a";

    /** Clips shorter than this stay WAV. */
    static final double MIN_COMPRESS_SECONDS = 4.0;
    /** And so do clips smaller than this (a wrong length must not turn a tiny clip into an encoder run). */
    static final long MIN_COMPRESS_BYTES = 100000;

    /** The format for a clip of this length and size (the size of its WAV file or PCM, in bytes). */
    static String choose(double seconds, long bytes) {
        return seconds >= MIN_COMPRESS_SECONDS && bytes >= MIN_COMPRESS_BYTES ? M4A : WAV;
    }

    static String mime(String format) {
        return M4A.equals(format) ? "audio/mp4" : "audio/wav";
    }

    static String fileName(String format) {
        return M4A.equals(format) ? "audio.m4a" : "audio.wav";
    }

    /** True when the encoded file is worth sending: it exists and is smaller than the WAV it replaces. */
    static boolean useEncoded(long wavBytes, long encodedBytes) {
        return encodedBytes > 0 && encodedBytes < wavBytes;
    }
}
