package com.minhaj.vox;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * The relay proves it holds the token before the phone sends it (SEC-2; windows/sync.py prove_relay is the twin).
 * GET /proof?nonce=N carries no token; the relay answers HMAC-SHA256(token, "vox-relay-proof:" + N). A program squatting
 * on the relay's port while the relay is down cannot answer, so it never gets the token. A relay from before /proof
 * answers 401: it is still used (with a warning) until its address has once proved itself; from then on a missing proof
 * is refused ({@link #pins} remembers those addresses; Prefs keeps them in SharedPreferences). A good answer is kept for
 * {@link #TTL_MS}. Pure Java (no android.*), tested off-device.
 */
final class RelayProof {
    private RelayProof() { }

    /**
     * Short: a program that takes the port the moment the relay stops causes no failed connection, so only this bounds how
     * long the token could still go there. A failed connection or a 502, 503 or 504 forgets the proof at once (forget).
     * Twin of PROOF_TTL in windows/sync.py.
     */
    static final long TTL_MS = 10_000;

    /** tailscale serve's answers while the relay behind it is stopped: the next request proves again. */
    static boolean gatewayDown(int status) {
        return status == 502 || status == 503 || status == 504;
    }
    static final String PROVEN = "proven";
    static final String OLD = "old relay";
    static final String NOT_PROVEN = "The relay did not prove it holds this token, so the token was not sent. Either the token is wrong, or "
            + "another program is answering at the relay's address.";
    static final String NO_LONGER = "This relay proved it holds the token before and now does not, so the token was not sent: another program may "
            + "be answering at its address. If you went back to an older relay, update it.";
    static final String OLD_RELAY = "This relay is too old to prove it holds the token before Vox sends it: update it.";

    /** The relay addresses (SyncEngine.originOf) that have proved themselves once. */
    interface Pins {
        boolean has(String origin);

        void add(String origin);
    }

    /** In memory until Prefs puts its SharedPreferences-backed store here. */
    static volatile Pins pins = new Pins() {
        private final Set<String> set = new HashSet<>();

        @Override
        public synchronized boolean has(String origin) { return set.contains(origin); }

        @Override
        public synchronized void add(String origin) { set.add(origin); }
    };

    private static final Map<String, Object[]> CACHE = new HashMap<>();   // origin + "\n" + token -> {time ms, result}
    private static final SecureRandom RANDOM = new SecureRandom();

    static String proofOf(String token, String nonce) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(token.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] out = mac.doFinal(("vox-relay-proof:" + nonce).getBytes(StandardCharsets.US_ASCII));
            StringBuilder sb = new StringBuilder();
            for (byte b : out) sb.append(String.format("%02x", b & 0xff));
            return sb.toString();
        } catch (Exception e) {
            throw new IllegalStateException(e);   // HmacSHA256 is always there
        }
    }

    /** The next request to the relay at {@code base} asks for a new proof (a connection to it failed). */
    static void forget(String base) {
        String origin = SyncEngine.originOf(base);
        synchronized (CACHE) {
            for (Iterator<String> it = CACHE.keySet().iterator(); it.hasNext(); ) {
                if (it.next().startsWith(origin + "\n")) it.remove();
            }
        }
    }

    /** {@link #PROVEN}, or {@link #OLD} (an old relay never proved at this address: use it, with OLD_RELAY as the warning). */
    static String check(String base, String token) throws RelayApi.RelayError {
        String origin = SyncEngine.originOf(base);
        String t = token == null ? "" : token.trim();
        String key = origin + "\n" + t;
        synchronized (CACHE) {
            Object[] hit = CACHE.get(key);
            if (hit != null && System.currentTimeMillis() - (Long) hit[0] < TTL_MS) return (String) hit[1];
        }
        byte[] raw = new byte[16];
        RANDOM.nextBytes(raw);
        StringBuilder nonce = new StringBuilder();
        for (byte b : raw) nonce.append(String.format("%02x", b & 0xff));
        int status;
        String body = "";
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(Endpoint.normalize(base) + "/proof?nonce=" + nonce).openConnection();
            c.setConnectTimeout(RelayClient.TIMEOUT_MS);
            c.setReadTimeout(RelayClient.TIMEOUT_MS);
            c.setInstanceFollowRedirects(false);
            c.setUseCaches(false);
            c.setRequestProperty("Accept", "application/json");
            status = c.getResponseCode();
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            if (in != null) {
                try {
                    body = readSome(in);
                } finally {
                    in.close();
                }
            }
        } catch (IOException e) {
            throw new RelayApi.RelayError(0, "Cannot reach the relay (is Tailscale running?): " + e.getClass().getSimpleName());
        }
        String result;
        if (status == 200) {
            Object proof = null;
            try {
                Object json = PlainJson.parse(body);
                if (json instanceof Map) proof = ((Map<?, ?>) json).get("proof");
            } catch (IllegalArgumentException notJson) {
                proof = null;
            }
            byte[] want = proofOf(t, nonce.toString()).getBytes(StandardCharsets.US_ASCII);
            if (!(proof instanceof String) || !MessageDigest.isEqual(((String) proof).getBytes(StandardCharsets.UTF_8), want)) {
                throw new RelayApi.RelayError(401, NOT_PROVEN);
            }
            if (!pins.has(origin)) pins.add(origin);
            result = PROVEN;
        } else if (status == 401 || status == 404) {   // a relay from before /proof checks the token first: 401
            if (pins.has(origin)) throw new RelayApi.RelayError(401, NO_LONGER);
            result = OLD;
        } else {
            throw new RelayApi.RelayError(status, "The relay answered HTTP " + status + ".");
        }
        synchronized (CACHE) {
            CACHE.put(key, new Object[]{System.currentTimeMillis(), result});
        }
        return result;
    }

    /** Clears the cache (tests). */
    static void reset() {
        synchronized (CACHE) {
            CACHE.clear();
        }
    }

    private static String readSome(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) != -1) {
            out.write(buf, 0, n);
            if (out.size() > 65536) break;    // a proof is about a hundred bytes
        }
        return new String(out.toByteArray(), StandardCharsets.UTF_8);
    }
}
