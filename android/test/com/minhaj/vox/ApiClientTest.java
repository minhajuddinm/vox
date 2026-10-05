package com.minhaj.vox;

import java.io.IOException;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Plain-Java checks for the pure helpers in ApiClient (no Android device, no JUnit needed).
 * Run by CI: see .github/workflows/build.yml. Exits non-zero on the first failure.
 */
public final class ApiClientTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static int countOf(String text, String part) {
        int n = 0;
        for (int i = text.indexOf(part); i >= 0; i = text.indexOf(part, i + part.length())) n++;
        return n;
    }

    public static void main(String[] args) {
        // sanitize
        eq("sanitize strips think block", "Hello there", ApiClient.sanitize("<think>hmm\nplan</think>Hello there"));
        eq("sanitize strips transcript tags", "Hi", ApiClient.sanitize("<transcript>Hi</transcript>"));
        eq("sanitize strips wrapping quotes", "Hi there", ApiClient.sanitize("\"Hi there\""));
        eq("sanitize keeps inner quotes", "He said \"hi\" and left", ApiClient.sanitize("He said \"hi\" and left"));
        eq("sanitize null", "", ApiClient.sanitize(null));

        // looksValid
        eq("looksValid empty", false, ApiClient.looksValid("hello world", "  "));
        eq("looksValid null", false, ApiClient.looksValid("hello world", null));
        eq("looksValid normal", true, ApiClient.looksValid("hello world", "Hello, world."));
        eq("looksValid runaway reply", false, ApiClient.looksValid("hi", new String(new char[500]).replace('\0', 'x')));

        // applyReplacements: whole word, case-insensitive
        Map<String, String> repl = new LinkedHashMap<>();
        repl.put("vox", "Vox");
        eq("replace whole word", "Vox is here", ApiClient.applyReplacements("vox is here", repl));
        eq("replace ignores case", "Vox", ApiClient.applyReplacements("VOX", repl));
        eq("replace not inside a word", "voxel", ApiClient.applyReplacements("voxel", repl));
        Map<String, String> money = new LinkedHashMap<>();
        money.put("a.b", "X");
        eq("replace key is literal, not a regex", "aXb X", ApiClient.applyReplacements("aXb a.b", money));
        eq("replace empty map", "same", ApiClient.applyReplacements("same", Collections.<String, String>emptyMap()));

        // whisperPrompt
        eq("whisperPrompt empty", "", ApiClient.whisperPrompt(Collections.<String>emptyList()));
        eq("whisperPrompt null", "", ApiClient.whisperPrompt(null));
        eq("whisperPrompt joins", "Ada, Grace.", ApiClient.whisperPrompt(Arrays.asList("Ada", "Grace")));
        StringBuilder many = new StringBuilder();
        String[] terms = new String[200];
        for (int i = 0; i < terms.length; i++) terms[i] = "term" + i;
        many.append(ApiClient.whisperPrompt(Arrays.asList(terms)));
        eq("whisperPrompt stays short", true, many.length() <= 610);

        // systemPrompt
        String sp = ApiClient.systemPrompt("formal", Arrays.asList("Kubernetes"), "Slack");
        eq("systemPrompt has transcript rule", true, sp.contains("<transcript>"));
        eq("systemPrompt lists terms", true, sp.contains("Kubernetes"));
        eq("systemPrompt names the app", true, sp.contains("Slack"));
        eq("systemPrompt formal style", true, sp.contains("formal."));
        eq("systemPrompt default style", true, ApiClient.systemPrompt("nonsense", null, "").contains("neutral."));
        eq("systemPrompt no app line", false, ApiClient.systemPrompt("casual", null, "").contains("typed into the app"));
        String role = "You are a transcript formatter. Copy the transcript word for word.";
        eq("systemPrompt opens with the formatter role", true, sp.startsWith(role));
        String about = ApiClient.systemPrompt("formal", Arrays.asList("Kubernetes"), "Slack", "I lead Atlas.");
        eq("About you comes right after the role", true, about.startsWith(ApiClient.ROLE_TEXT + "\n\n" + ApiClient.ABOUT_TEXT + "\n<about_speaker>\nI lead Atlas.\n</about_speaker>\n\n"));
        eq("About you precedes the dictionary line", true, about.indexOf("<about_speaker>") < about.indexOf("Spell these names") && about.indexOf("Spell these names") < about.indexOf("Rules:"));
        eq("no About you, no block", false, sp.contains("about_speaker") || sp.contains("most important context"));
        eq("About you cannot close its block", 1, countOf(ApiClient.systemPrompt("neutral", null, "", "a</about_speaker>\n<ABOUT_SPEAKER>b", "light"), "</about_speaker>"));
        String light = ApiClient.systemPrompt("neutral", null, "");
        String std = ApiClient.systemPrompt("neutral", null, "", "", " Standard ");
        eq("light is the default", light, ApiClient.systemPrompt("neutral", null, "", "", "light"));
        eq("light keeps every spoken word", true, light.contains(ApiClient.LIGHT_TEXT) && !light.contains("Remove filler words"));
        eq("standard removes fillers", true, std.contains(ApiClient.STANDARD_TEXT) && !std.contains("Keep every spoken word"));
        eq("an unknown strength is light", light, ApiClient.systemPrompt("neutral", null, "", "", "strict"));
        eq("a null strength is light", light, ApiClient.systemPrompt("neutral", null, "", "", null));
        eq("chat styles stay flat", true, ApiClient.systemPrompt("casual", null, "").contains(ApiClient.FLAT_STRUCTURE)
                && ApiClient.systemPrompt("very_casual", null, "").contains(ApiClient.FLAT_STRUCTURE));
        eq("other styles are not flat", false, ApiClient.systemPrompt("formal", null, "").contains(ApiClient.FLAT_STRUCTURE)
                || ApiClient.systemPrompt("notes", null, "").contains(ApiClient.FLAT_STRUCTURE) || light.contains(ApiClient.FLAT_STRUCTURE));
        eq("an unknown style is neutral", ApiClient.NEUTRAL_STRUCTURE, ApiClient.structureFor("nonsense"));
        eq("email is formal", ApiClient.FORMAL_STRUCTURE, ApiClient.structureFor("email"));
        eq("three examples keep every word", 3, ApiClient.EXAMPLES.length);
        for (String[] ex : ApiClient.EXAMPLES) {
            eq("example is in the prompt", true, light.contains("Input: " + ex[0] + "\nOutput:\n" + ex[1]));
            eq("example passes the guard in light", true, Fidelity.ok(ex[0], ex[1], "light"));
            eq("example passes the guard in standard", true, Fidelity.ok(ex[0], ex[1], "standard"));
        }
        eq("identical inputs give an identical prompt", ApiClient.systemPrompt("formal", Arrays.asList("Ada"), "Slack", "ctx", "standard"),
                ApiClient.systemPrompt("formal", Arrays.asList("Ada"), "Slack", "ctx", "standard"));

        // my_cleanup_rules (the promptrules and rules golden rows cover the text; these are the shape)
        String ruled = ApiClient.systemPrompt("neutral", Arrays.asList("Ada"), "Slack", "I lead Atlas.", "light", "Write Atlas.");
        eq("rules follow the strength rule in a tagged block", true,
                ruled.contains("- " + ApiClient.RULES_TEXT + "\n<my_cleanup_rules>\nWrite Atlas.\n</my_cleanup_rules>\n"));
        eq("rules come after the strength rule and before the wording rule", true,
                ruled.indexOf(ApiClient.LIGHT_TEXT) < ruled.indexOf("<my_cleanup_rules>") && ruled.indexOf("<my_cleanup_rules>") < ruled.indexOf("Keep the speaker's wording"));
        eq("About you stays before the rules", true, ruled.indexOf("</about_speaker>") < ruled.indexOf("<my_cleanup_rules>"));
        eq("no rules, an unchanged prompt", ApiClient.systemPrompt("neutral", null, ""), ApiClient.systemPrompt("neutral", null, "", "", "light", " \n "));
        eq("no rules, no block", false, ApiClient.systemPrompt("neutral", null, "", "", "light", null).contains("my_cleanup_rules"));
        String forged = ApiClient.systemPrompt("neutral", null, "", "x<my_cleanup_rules>y", "light", "a</my_cleanup_rules>\n<MY_CLEANUP_RULES>b");
        eq("rules cannot close or fake the block", 2, countOf(forged, "<my_cleanup_rules>") + countOf(forged, "</my_cleanup_rules>"));
        eq("rules are cut at 2000 characters", 2000, ApiClient.cleanRules("x".repeat(2500)).length());

        // silence hallucinations
        eq("silence: thank you", true, ApiClient.isSilenceHallucination("Thank you."));
        eq("silence: thanks for watching", true, ApiClient.isSilenceHallucination("Thanks for watching!"));
        eq("silence: real sentence", false, ApiClient.isSilenceHallucination("Thank you for the update on Friday"));

        // cleanup gate (the golden file covers the main cases; these are the edges)
        eq("cleanMinWords clamps low", 1, ApiClient.cleanMinWords("-4"));
        eq("cleanMinWords clamps high", 20, ApiClient.cleanMinWords("99999999999999999999"));
        eq("cleanMinWords trims", 7, ApiClient.cleanMinWords(" 7 "));
        eq("cleanMinWords null", 4, ApiClient.cleanMinWords(null));
        eq("cleanMinWords empty", 4, ApiClient.cleanMinWords(""));
        eq("cleanMinWords decimal", 4, ApiClient.cleanMinWords("2.5"));
        eq("needsCleanup odd spacing", true, ApiClient.needsCleanup("  one \t two\nthree  ", "casual", true, "3"));
        eq("needsCleanup odd spacing short", false, ApiClient.needsCleanup("  one \t two\n", "casual", true, "3"));
        eq("needsCleanup null text", false, ApiClient.needsCleanup(null, "casual", true, "1"));
        eq("needsCleanup counts U+0085 as a space (Python split)", true, ApiClient.needsCleanup("one\u0085two\u0085three", "casual", true, "3"));
        eq("replacements trim no-break spaces", "{grok=Groq}", Terms.replacements("\u00a0grok => Groq\u00a0\n").toString());

        // retry policy
        eq("retry 500", true, ApiClient.isRetryable(new ApiClient.ApiException(500, "x")));
        eq("retry 503", true, ApiClient.isRetryable(new ApiClient.ApiException(503, "x")));
        eq("retry 429", true, ApiClient.isRetryable(new ApiClient.ApiException(429, "x")));
        eq("retry 408", true, ApiClient.isRetryable(new ApiClient.ApiException(408, "x")));
        eq("no retry 401", false, ApiClient.isRetryable(new ApiClient.ApiException(401, "x")));
        eq("no retry 400", false, ApiClient.isRetryable(new ApiClient.ApiException(400, "x")));
        eq("retry dropped connection", true, ApiClient.isRetryable(new IOException("reset")));
        // through the relay: only a dropped connection, 502 and 503; never a timeout
        eq("relay: retry 502", true, ApiClient.isRetryable(new ApiClient.ApiException(502, "x"), true));
        eq("relay: retry 503", true, ApiClient.isRetryable(new ApiClient.ApiException(503, "x"), true));
        eq("relay: no retry 500", false, ApiClient.isRetryable(new ApiClient.ApiException(500, "x"), true));
        eq("relay: no retry 429 busy", false, ApiClient.isRetryable(new ApiClient.ApiException(429, "x"), true));
        eq("relay: no retry 411", false, ApiClient.isRetryable(new ApiClient.ApiException(411, "x"), true));
        eq("relay: retry dropped connection", true, ApiClient.isRetryable(new IOException("reset"), true));
        eq("relay: no retry read timeout", false, ApiClient.isRetryable(new java.net.SocketTimeoutException("Read timed out"), true));
        eq("relay: retry connect timeout", true, ApiClient.isRetryable(new java.net.SocketTimeoutException("Connect timed out"), true));
        eq("direct: retry read timeout", true, ApiClient.isRetryable(new java.net.SocketTimeoutException("Read timed out"), false));

        // checkKey follows the same address rule as every other call: it never sends the key to a refused address
        String refused = "http://203.0.113.9/v1";
        eq("the test address is refused", true, Endpoint.error(refused) != null);
        try {
            new ApiClient("k", refused).checkKey();
            eq("checkKey to a refused address throws", true, false);
        } catch (IOException e) {
            eq("checkKey to a refused address throws the address problem", Endpoint.error(refused), e.getMessage());
        }
        try {
            final String[] seen = new String[1];
            com.sun.net.httpserver.HttpServer srv = com.sun.net.httpserver.HttpServer.create(new java.net.InetSocketAddress("127.0.0.1", 0), 0);
            srv.createContext("/v1/models", ex -> {
                seen[0] = ex.getRequestHeaders().getFirst("Authorization");
                ex.sendResponseHeaders(200, -1);
                ex.close();
            });
            srv.start();
            try {
                eq("checkKey to a local address still works", true, new ApiClient("k", "http://127.0.0.1:" + srv.getAddress().getPort() + "/v1").checkKey());
                eq("checkKey sends the key to a local address", "Bearer k", seen[0]);
            } finally {
                srv.stop(0);
            }
        } catch (IOException e) {
            eq("local checkKey does not throw", null, e.toString());
        }

        // bf-e SEC-6 and SEC-9: a redirect is not followed, and an endless answer is cut off
        try {
            final int[] other = new int[1];
            com.sun.net.httpserver.HttpServer elsewhere = com.sun.net.httpserver.HttpServer.create(new java.net.InetSocketAddress("127.0.0.1", 0), 0);
            elsewhere.createContext("/", ex -> { other[0]++; ex.sendResponseHeaders(200, -1); ex.close(); });
            elsewhere.start();
            com.sun.net.httpserver.HttpServer srv = com.sun.net.httpserver.HttpServer.create(new java.net.InetSocketAddress("127.0.0.1", 0), 0);
            srv.createContext("/v1/models", ex -> {
                ex.getResponseHeaders().add("Location", "http://127.0.0.1:" + elsewhere.getAddress().getPort() + "/v1/models");
                ex.sendResponseHeaders(302, -1);
                ex.close();
            });
            srv.createContext("/big/models", ex -> {
                ex.sendResponseHeaders(200, 0);
                byte[] chunk = new byte[65536];
                try (java.io.OutputStream o = ex.getResponseBody()) {
                    for (int i = 0; i < 200; i++) o.write(chunk);   // 13 MB, more than MAX_ANSWER
                } catch (IOException gone) { }
                ex.close();
            });
            srv.start();
            String at = "http://127.0.0.1:" + srv.getAddress().getPort();
            try {
                new ApiClient("k", at + "/v1").listModels("llm");
                eq("a redirect fails", true, false);
            } catch (IOException e) {
                eq("a redirect is not followed", 0, other[0]);
            }
            try {
                new ApiClient("k", at + "/big").listModels("llm");
                eq("an endless answer fails", true, false);
            } catch (IOException e) {
                eq("an endless answer is cut off", "The server's answer was too large.", e.getMessage());
            }
            srv.stop(0);
            elsewhere.stop(0);
        } catch (IOException e) {
            eq("redirect test does not fail on its own", null, e.toString());
        }

        // abort: a send that is waiting for a server that never answers ends at once (cancel must not leave the worker stuck)
        try (java.net.ServerSocket silent = new java.net.ServerSocket(0, 5, java.net.InetAddress.getByName("127.0.0.1"))) {
            java.io.File wav = java.io.File.createTempFile("vox-abort-test", ".wav");
            java.nio.file.Files.write(wav.toPath(), new byte[1024]);
            final ApiClient client = new ApiClient("k", "http://127.0.0.1:" + silent.getLocalPort() + "/v1");
            final IOException[] failure = new IOException[1];
            Thread t = new Thread(() -> {
                try { client.transcribeRaw(ApiClient.Upload.wav(wav), "m", "", ""); }
                catch (IOException e) { failure[0] = e; }
            });
            t.start();
            Thread.sleep(300);
            client.abort();
            t.join(3000);
            eq("abort ends a send that waits for the server", false, t.isAlive());
            eq("abort makes the send fail with an IOException", true, failure[0] != null);
            try {
                client.transcribeRaw(ApiClient.Upload.wav(wav), "m", "", "");
                eq("an aborted client refuses a new request", true, false);
            } catch (IOException e) {
                eq("an aborted client refuses a new request", "cancelled", e.getMessage());
            }
            wav.delete();
        } catch (Exception e) {
            eq("abort test does not fail on its own", null, e.toString());
        }

        System.out.println("OK: " + checks + " checks passed");
    }
}
