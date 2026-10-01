package com.minhaj.vox;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import android.os.SystemClock;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;

/**
 * Makes the file that is uploaded for speech to text: a clip of 4 s or more as AAC in an m4a file (a quarter of the WAV, see
 * {@link UploadFormat}), a shorter one as the WAV it already is. Any failure of the encoder (no codec, a refusal, too slow, a
 * file that is not smaller) hands back the WAV: the upload always works, the encoder is only a shortcut. Uses
 * android.media, so it is checked by compile-check.sh and on a phone, not by the unit tests.
 */
final class AudioUpload {
    private AudioUpload() { }

    /** 64 kbit/s AAC-LC mono at 16 kHz: transparent for speech, and a quarter of the 256 kbit/s of the WAV. */
    private static final int BIT_RATE = 64000;
    /** Gives up on the encoder after this long, so a stuck codec cannot hold up a dictation. */
    private static final long ENCODE_LIMIT_MS = 15000;

    /**
     * The upload for a WAV file written by DictationService.writeWav. The result is the WAV itself or a temporary m4a in dir;
     * call {@link ApiClient.Upload#release} when it has been sent.
     */
    static ApiClient.Upload fromWavFile(File dir, File wav) {
        ApiClient.Upload plain = ApiClient.Upload.wav(wav);
        if (!UploadFormat.M4A.equals(UploadFormat.choose(plain.seconds, wav.length()))) return plain;
        byte[] pcm;
        try {
            pcm = readPcm(wav);
        } catch (IOException | OutOfMemoryError e) {
            return plain;
        }
        ApiClient.Upload up = encoded(dir, pcm, plain.seconds, wav.length());
        return up != null ? up : plain;
    }

    /** The upload for a piece of audio in memory (a piece of a long recording): a temporary m4a or WAV file in dir. */
    static ApiClient.Upload fromPcm(File dir, byte[] pcm) throws IOException {
        double seconds = pcm.length / 32000.0;
        long wavBytes = pcm.length + 44L;
        if (UploadFormat.M4A.equals(UploadFormat.choose(seconds, wavBytes))) {
            ApiClient.Upload up = encoded(dir, pcm, seconds, wavBytes);
            if (up != null) return up;
        }
        File f = File.createTempFile("vox-up-", ".wav", dir);
        try {
            DictationService.writeWav(f, pcm);
        } catch (IOException e) {
            f.delete();
            throw e;
        }
        return new ApiClient.Upload(f, UploadFormat.fileName(UploadFormat.WAV), UploadFormat.mime(UploadFormat.WAV), seconds, true);
    }

    /** The m4a of this audio when it could be made and is smaller than the WAV; null otherwise. */
    private static ApiClient.Upload encoded(File dir, byte[] pcm, double seconds, long wavBytes) {
        File f = null;
        try {
            f = File.createTempFile("vox-up-", ".m4a", dir);
            if (encodeAac(pcm, f) && UploadFormat.useEncoded(wavBytes, f.length())) {
                return new ApiClient.Upload(f, UploadFormat.fileName(UploadFormat.M4A), UploadFormat.mime(UploadFormat.M4A), seconds, true);
            }
        } catch (Exception | OutOfMemoryError e) {
            // fall through: the WAV is sent
        }
        if (f != null) f.delete();
        return null;
    }

    private static byte[] readPcm(File wav) throws IOException {
        long n = wav.length() - 44;
        if (n <= 0 || n > Integer.MAX_VALUE - 16) throw new IOException("not a recording");
        byte[] pcm = new byte[(int) n];
        try (InputStream in = new FileInputStream(wav)) {
            long skipped = 0;
            while (skipped < 44) {
                long s = in.skip(44 - skipped);
                if (s <= 0) throw new IOException("short file");
                skipped += s;
            }
            int got = 0;
            while (got < pcm.length) {
                int r = in.read(pcm, got, pcm.length - got);
                if (r < 0) throw new IOException("short file");
                got += r;
            }
        }
        return pcm;
    }

    /** AAC-LC in an MPEG-4 container, 16 kHz mono, through MediaCodec and MediaMuxer. False on any failure. */
    static boolean encodeAac(byte[] pcm, File out) {
        final int rate = DictationService.SAMPLE_RATE;
        final int len = pcm.length & ~1;
        if (len == 0) return false;
        MediaCodec codec = null;
        MediaMuxer muxer = null;
        boolean muxerStarted = false;
        try {
            MediaFormat fmt = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, rate, 1);
            fmt.setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC);
            fmt.setInteger(MediaFormat.KEY_BIT_RATE, BIT_RATE);
            fmt.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 16384);
            codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC);
            codec.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            codec.start();
            muxer = new MediaMuxer(out.getAbsolutePath(), MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            final long deadline = SystemClock.elapsedRealtime() + ENCODE_LIMIT_MS;
            int track = -1, pos = 0;
            boolean inputDone = false, outputDone = false;
            while (!outputDone) {
                if (SystemClock.elapsedRealtime() > deadline) return false;
                if (!inputDone) {
                    int i = codec.dequeueInputBuffer(10000);
                    if (i >= 0) {
                        ByteBuffer in = codec.getInputBuffer(i);
                        in.clear();
                        int n = Math.min(in.remaining(), len - pos) & ~1;
                        if (n > 0) in.put(pcm, pos, n);
                        long pts = (long) (pos / 2) * 1000000L / rate;
                        pos += n;
                        inputDone = pos >= len;
                        codec.queueInputBuffer(i, 0, n, pts, inputDone ? MediaCodec.BUFFER_FLAG_END_OF_STREAM : 0);
                    }
                }
                int o = codec.dequeueOutputBuffer(info, 10000);
                if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    track = muxer.addTrack(codec.getOutputFormat());
                    muxer.start();
                    muxerStarted = true;
                } else if (o >= 0) {
                    ByteBuffer ob = codec.getOutputBuffer(o);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) info.size = 0;   // the muxer takes this from the format
                    if (info.size > 0 && muxerStarted && ob != null) {
                        ob.position(info.offset);
                        ob.limit(info.offset + info.size);
                        muxer.writeSampleData(track, ob, info);
                    }
                    outputDone = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                    codec.releaseOutputBuffer(o, false);
                }
            }
            return muxerStarted;
        } catch (Exception e) {
            return false;
        } finally {
            if (codec != null) {
                try { codec.stop(); } catch (Exception ignored) { }
                try { codec.release(); } catch (Exception ignored) { }
            }
            if (muxer != null) {
                if (muxerStarted) {
                    try { muxer.stop(); } catch (Exception ignored) { }
                }
                try { muxer.release(); } catch (Exception ignored) { }
            }
        }
    }
}
