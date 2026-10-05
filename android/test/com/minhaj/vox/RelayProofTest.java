package com.minhaj.vox;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * bf-e SEC-2: the phone sends the relay token only to a relay that proved it holds it (RelayProof; windows/sync.py
 * prove_relay and tests/test_relay_proof.py are the twins). A squatter on the relay's port gets nothing from the sync
 * client nor from dictation through the relay; an old relay without /proof is used with a warning until its address
 * has proved itself once. Run by CI, exits non-zero on failure.
 */
public final class RelayProofTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    private static final String TOKEN = "REAL-TOKEN-A";
    // wrong (a proof that does not match), old (401 like a relay from before), good, gateway (good proofs, but every other
    // request answered 502 like tailscale serve while the relay behind it is stopped)
    private static volatile String mode = "wrong";
    private static final List<String> auth = Collections.synchronizedList(new ArrayList<String>());
    private static final List<String> proofs = Collections.synchronizedList(new ArrayList<String>());

    private static void reply(HttpExchange x, int status, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        x.getResponseHeaders().add("Content-Type", "application/json");
        x.sendResponseHeaders(status, b.length);
        x.getResponseBody().write(b);
        x.close();
    }

    public static void main(String[] args) throws Exception {
        HttpServer s = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        s.createContext("/", x -> {
            String a = x.getRequestHeaders().getFirst("Authorization");
            if (a != null) auth.add(a);
            if (x.getRequestURI().getRawPath().equals("/proof")) {
                proofs.add(x.getRequestURI().getRawQuery());
                String q = x.getRequestURI().getRawQuery();
                if (mode.equals("old")) reply(x, 401, "{\"error\": \"missing or wrong token\"}");
                else if (mode.equals("silent")) reply(x, 404, "{\"error\": \"unknown request\"}");
                else reply(x, 200, "{\"proof\": \"" + (mode.equals("good") || mode.equals("gateway") ? RelayProof.proofOf(TOKEN, q.substring(q.indexOf('=') + 1)) : "00") + "\"}");
                return;
            }
            if (mode.equals("gateway")) {
                reply(x, 502, "{\"error\": \"upstream down\"}");
                return;
            }
            reply(x, 200, "{\"ok\": true, \"notes\": 0, \"version\": 0, \"data\": {}, \"devices\": [], \"next\": 0, \"more\": false, \"text\": \"hi\"}");
        });
        s.start();
        String base = "http://127.0.0.1:" + s.getAddress().getPort();
        try {
            // relay.token_proof("key", "nonce"), computed with Python's hmac module
            eq("proof: the relay's HMAC-SHA256", "c34a0e0dede933298c8dfc891fa6cadf10f9514d82938c78ee47b196be317f65", RelayProof.proofOf("key", "nonce"));

            // a squatter that answers /proof wrongly gets no token, from any client
            mode = "wrong";
            RelayClient.Check c = RelayClient.check(base, TOKEN, "Pixel");
            eq("wrong proof: refused", "false/" + RelayProof.NOT_PROVEN, c.ok + "/" + c.message);
            try {
                new ApiClient(TOKEN, base + "/proxy/llm").listModels("llm");
                eq("dictation through the squatted relay throws", true, false);
            } catch (IOException e) {
                eq("dictation through the squatted relay: the reason", RelayProof.NOT_PROVEN, e.getMessage());
            }
            new ApiClient(TOKEN, base + "/proxy/stt").warm();
            eq("the squatter saw no token", 0, auth.size());

            // an old relay (401 on /proof) is used, with a warning, while its address never proved itself
            mode = "old";
            RelayProof.reset();
            c = RelayClient.check(base, TOKEN, "Pixel");
            eq("old relay: used with a warning", "true/Connected. The relay holds 0 notes. " + RelayProof.OLD_RELAY, c.ok + "/" + c.message);
            eq("old relay: the token went (the fallback)", true, auth.size() > 0);

            // once the address proved itself, a missing proof is refused
            mode = "good";
            RelayProof.reset();
            proofs.clear();
            c = RelayClient.check(base, TOKEN, "Pixel");
            eq("good proof: connected", "true/Connected. The relay holds 0 notes.", c.ok + "/" + c.message);
            eq("good proof: asked once for the check and kept", 1, proofs.size());
            new RelayClient(base, TOKEN, "Pixel").getProfile();
            eq("a kept proof is not asked again", 1, proofs.size());
            mode = "old";
            RelayProof.reset();
            auth.clear();
            c = RelayClient.check(base, TOKEN, "Pixel");
            eq("pinned address without a proof: refused", "false/" + RelayProof.NO_LONGER, c.ok + "/" + c.message);
            eq("pinned address without a proof: no token", 0, auth.size());

            // a connection failure makes the next request ask again
            mode = "good";
            RelayProof.reset();
            proofs.clear();
            new RelayClient(base, TOKEN, "Pixel").getProfile();
            RelayProof.forget(base);
            new RelayClient(base, TOKEN, "Pixel").getProfile();
            eq("forget: asked again", 2, proofs.size());

            // final review RC-M3: an old relay answers 401 (it checks the token first); a 404 is a wrong address or path
            mode = "silent";
            RelayProof.reset();
            auth.clear();
            c = RelayClient.check(base, TOKEN, "Pixel");
            eq("a 404 on /proof is not taken for an old relay", false, c.ok);
            eq("a 404 on /proof: no token", 0, auth.size());
            mode = "good";

            // final review RC-I1: the proof is kept for seconds, and a 502, 503 or 504 forgets it
            eq("the proof is trusted for 10 s at most", true, RelayProof.TTL_MS <= 10_000);
            RelayProof.reset();
            proofs.clear();
            new RelayClient(base, TOKEN, "Pixel").getProfile();
            mode = "gateway";
            try {
                new RelayClient(base, TOKEN, "Pixel").getProfile();
                eq("a 502 from the relay throws", true, false);
            } catch (RelayApi.RelayError e) {
                eq("a 502 from the relay: the status", 502, e.status);
            }
            mode = "good";
            new RelayClient(base, TOKEN, "Pixel").getProfile();
            eq("after a 502 the sync proves again", 2, proofs.size());
            RelayProof.reset();
            proofs.clear();
            java.io.File wav = java.io.File.createTempFile("vox-proof", ".wav");
            try {
                java.nio.file.Files.write(wav.toPath(), new byte[44 + 3200]);
                mode = "gateway";
                try {
                    new ApiClient(TOKEN, base + "/proxy/stt").transcribeRaw(wav, "m", "", new ArrayList<String>());
                    eq("a 502 through the relay throws", true, false);
                } catch (ApiClient.ApiException e) {
                    eq("a 502 through the relay: the status", 502, e.code);
                }
                mode = "good";
                new ApiClient(TOKEN, base + "/proxy/stt").transcribeRaw(wav, "m", "", new ArrayList<String>());
                eq("after a 502 a dictation through the relay proves again", 2, proofs.size());
            } finally {
                wav.delete();
            }
        } finally {
            s.stop(0);
        }
        System.out.println("OK: " + checks + " checks passed");
    }
}
