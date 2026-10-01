package com.minhaj.vox;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * A multipart/form-data body whose exact length is known before anything is sent: text fields and one file.
 * The length matters because the relay (and some other servers) refuse an upload with no Content-Length (chunked
 * uploads get 411), and HttpURLConnection.setFixedLengthStreamingMode needs the number up front.
 * Pure Java: no android.* and no org.json, so it is unit-tested on a plain JDK.
 */
final class Multipart {
    private final String boundary;
    private final List<byte[]> head = new ArrayList<>();   // the text fields, then the file's part header
    private long headLength;
    private long fileLength = -1;
    private byte[] tail;

    Multipart(String boundary) {
        this.boundary = boundary;
    }

    String contentType() {
        return "multipart/form-data; boundary=" + boundary;
    }

    Multipart field(String name, String value) {
        add(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        add(value.getBytes(StandardCharsets.UTF_8));
        add("\r\n".getBytes(StandardCharsets.UTF_8));
        return this;
    }

    /** The file part, last: `length` is the number of bytes that {@link #writeTo} will copy from the stream. */
    Multipart file(String name, String filename, String contentType, long length) {
        if (fileLength >= 0) throw new IllegalStateException("one file only");
        add(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"; filename=\"" + filename
                + "\"\r\nContent-Type: " + contentType + "\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        fileLength = length;
        tail = ("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8);
        return this;
    }

    private void add(byte[] b) {
        head.add(b);
        headLength += b.length;
    }

    /** Exactly the number of bytes {@link #writeTo} writes. */
    long length() {
        if (fileLength < 0) throw new IllegalStateException("no file added");
        return headLength + fileLength + tail.length;
    }

    /** Writes the whole body, copying exactly the declared number of bytes from `file` (IOException if it has fewer). */
    void writeTo(OutputStream out, InputStream file) throws IOException {
        length();
        for (byte[] b : head) out.write(b);
        byte[] buf = new byte[16384];
        long left = fileLength;
        while (left > 0) {
            int n = file.read(buf, 0, (int) Math.min(buf.length, left));
            if (n < 0) throw new IOException("the audio file is shorter than it was when its size was read");
            out.write(buf, 0, n);
            left -= n;
        }
        out.write(tail);
    }
}
