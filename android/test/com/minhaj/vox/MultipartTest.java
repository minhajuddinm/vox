package com.minhaj.vox;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/** Multipart: the length computed up front equals the bytes written, and the body is well formed. */
public final class MultipartTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static byte[] bytes(int n) {
        byte[] b = new byte[n];
        for (int i = 0; i < n; i++) b[i] = (byte) (i * 131 + 17);
        return b;
    }

    private static byte[] write(Multipart m, byte[] file) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        m.writeTo(out, new ByteArrayInputStream(file));
        return out.toByteArray();
    }

    public static void main(String[] args) throws Exception {
        String b = "----vox123";
        // the length equals what is written, for several combinations
        int[] sizes = {0, 1, 44, 16384, 16385, 300000};
        String[] prompts = {"", "Ada, Grace.", "Café, 東京, 🎙."};
        String[] names = {"audio.wav", "音声 café.wav"};
        for (int size : sizes) {
            for (String prompt : prompts) {
                for (String filename : names) {
                    Multipart m = new Multipart(b).field("model", "whisper-large-v3-turbo").field("temperature", "0");
                    if (!prompt.isEmpty()) m.field("prompt", prompt);
                    m.file("file", filename, "audio/wav", size);
                    byte[] out = write(m, bytes(size));
                    eq("length matches written (size " + size + ", prompt '" + prompt + "', file " + filename + ")", (long) out.length, m.length());
                }
            }
        }
        // an empty field value, and no fields at all
        Multipart empty = new Multipart(b).field("prompt", "").file("file", "a.wav", "audio/wav", 3);
        eq("empty value length", (long) write(empty, bytes(3)).length, empty.length());
        Multipart bare = new Multipart(b).file("file", "a.wav", "audio/wav", 0);
        eq("file only, empty", (long) write(bare, new byte[0]).length, bare.length());

        // exact framing for a small body
        Multipart m = new Multipart(b).field("model", "m").file("file", "a.wav", "audio/wav", 2);
        String got = new String(write(m, new byte[]{'A', 'B'}), StandardCharsets.UTF_8);
        String want = "--" + b + "\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nm\r\n"
                + "--" + b + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\nAB\r\n"
                + "--" + b + "--\r\n";
        eq("framing", want, got);
        eq("content type", "multipart/form-data; boundary=" + b, m.contentType());

        // the file bytes are copied exactly, even when they look like a boundary
        byte[] tricky = ("\r\n--" + b + "--\r\n").getBytes(StandardCharsets.UTF_8);
        Multipart t = new Multipart(b).file("file", "a.wav", "audio/wav", tricky.length);
        byte[] out = write(t, tricky);
        eq("tricky length", (long) out.length, t.length());

        // a file that turned out shorter than declared is an error, not a silently short body
        Multipart s = new Multipart(b).file("file", "a.wav", "audio/wav", 10);
        boolean threw = false;
        try { write(s, bytes(4)); } catch (IOException e) { threw = true; }
        eq("short file throws", true, threw);

        // a longer file than declared: only the declared bytes go out (the length stays true)
        Multipart l = new Multipart(b).file("file", "a.wav", "audio/wav", 4);
        eq("long file is cut to the declared size", (long) write(l, bytes(9)).length, l.length());

        System.out.println("OK: " + checks + " checks passed");
    }
}
