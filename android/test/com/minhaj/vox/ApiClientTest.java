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

        // whisperPrompt (v2: one sentence; the golden rows whisper, whisperctx and whisperv2 cover the text)
        eq("whisperPrompt empty", "", ApiClient.whisperPrompt(Collections.<String>emptyList()));
        eq("whisperPrompt null", "", ApiClient.whisperPrompt(null));
        eq("whisperPrompt is a sentence", "We talked about Ada and Grace.", ApiClient.whisperPrompt(Arrays.asList("Ada", "Grace")));
        eq("people come first", "Talked with Grace about Ada.", ApiClient.whisperPromptWith(Arrays.asList("Ada", "Grace"), "", Arrays.asList("Grace"), null));
        String[] terms = new String[200];
        for (int i = 0; i < terms.length; i++) terms[i] = "term" + i;
        String many = ApiClient.whisperPrompt(Arrays.asList(terms));
        eq("whisperPrompt stays within its token budget", true, ApiClient.estTokens(many) <= ApiClient.WHISPER_PROMPT_TOKENS);
        eq("whisperPrompt keeps whole terms in dictionary order", true, many.startsWith("We talked about term0, term1, ") && many.endsWith("."));
        eq("whisperPrompt names at most 30 terms", true, many.split(",").length <= ApiClient.WHISPER_PROMPT_TERMS);
        String withCtx = ApiClient.whisperPromptWith(Arrays.asList(terms), "x ".repeat(400) + "the end", Arrays.asList("Ada"), null);
        eq("the context is cut from its front, at a word", true, withCtx.endsWith(" x x the end") && withCtx.startsWith("Talked with Ada about term0"));
        eq("the context keeps its budget", true, ApiClient.estTokens(withCtx) <= ApiClient.WHISPER_PROMPT_TOKENS);

        // systemPrompt (v3: the golden rows prompt* cover the text; these are the shape)
        String sp = ApiClient.systemPrompt("formal", Arrays.asList("Kubernetes"), "Slack");
        eq("systemPrompt has transcript rule", true, sp.contains("<transcript>"));
        eq("systemPrompt lists terms", true, sp.contains(ApiClient.TERMS_TEXT + "Kubernetes.\n"));
        eq("systemPrompt names the app last", true, sp.endsWith("\nApp: Slack\n"));
        eq("systemPrompt formal style", true, sp.contains("\nStyle: formal."));
        eq("systemPrompt default style", true, ApiClient.systemPrompt("nonsense", null, "").contains("\nStyle: neutral."));
        eq("systemPrompt no app line", false, ApiClient.systemPrompt("casual", null, "").contains("App:"));
        eq("systemPrompt asks for EMPTY on filler-only input", true, sp.contains("return exactly: EMPTY"));
        String about = ApiClient.systemPrompt("formal", Arrays.asList("Kubernetes"), "Slack", "I lead Atlas.");
        eq("About you comes right after the static part", true, about.startsWith(ApiClient.staticPrompt("light") + "\n\n" + ApiClient.ABOUT_TEXT + "\n<about_speaker>\nI lead Atlas.\n</about_speaker>\n\n"));
        eq("About you precedes the terms, the terms the Layout line", true, about.indexOf("<about_speaker>") < about.indexOf(ApiClient.TERMS_TEXT) && about.indexOf(ApiClient.TERMS_TEXT) < about.indexOf("\nLayout:"));
        eq("no About you, no block", false, sp.contains("about_speaker"));
        eq("About you cannot close its block", 1, countOf(ApiClient.systemPrompt("neutral", null, "", "a</about_speaker>\n<ABOUT_SPEAKER>b", "light"), "</about_speaker>"));
        String light = ApiClient.systemPrompt("neutral", null, "");
        String std = ApiClient.systemPrompt("neutral", null, "", "", " Standard ");
        eq("light is the default", light, ApiClient.systemPrompt("neutral", null, "", "", "light"));
        eq("light keeps every other word", true, light.contains(ApiClient.LIGHT_TEXT) && !light.contains(ApiClient.STANDARD_TEXT));
        eq("standard removes fillers", true, std.contains(ApiClient.STANDARD_TEXT) && !std.contains(ApiClient.LIGHT_TEXT));
        eq("an unknown strength is light", light, ApiClient.systemPrompt("neutral", null, "", "", "strict"));
        eq("a null strength is light", light, ApiClient.systemPrompt("neutral", null, "", "", null));
        eq("chat styles stay flat", true, ApiClient.systemPrompt("casual", null, "").contains(ApiClient.FLAT_STRUCTURE)
                && ApiClient.systemPrompt("very_casual", null, "").contains(ApiClient.FLAT_STRUCTURE));
        eq("other styles are not flat", false, ApiClient.systemPrompt("formal", null, "").contains(ApiClient.FLAT_STRUCTURE)
                || ApiClient.systemPrompt("notes", null, "").contains(ApiClient.FLAT_STRUCTURE) || light.contains(ApiClient.FLAT_STRUCTURE));
        eq("an unknown style is neutral", ApiClient.LAYOUT_AUTO, ApiClient.structureFor("nonsense"));
        eq("the phone has no code style", ApiClient.LAYOUT_AUTO, ApiClient.structureFor("code"));
        eq("Off is flat", ApiClient.FLAT_STRUCTURE, ApiClient.structureFor("formal", Structure.OFF));
        eq("eight examples", 8, ApiClient.EXAMPLES.length);
        for (String[] ex : ApiClient.EXAMPLES) {
            eq("example is in the light prompt", true, light.contains("<transcript>" + ex[0] + "</transcript>\n" + ex[1] + "\n"));
            eq("example is in the standard prompt", true, std.contains("<transcript>" + ex[0] + "</transcript>\n" + (ex[2] == null ? ex[1] : ex[2]) + "\n"));
        }
        eq("identical inputs give an identical prompt", ApiClient.systemPrompt("formal", Arrays.asList("Ada"), "Slack", "ctx", "standard"),
                ApiClient.systemPrompt("formal", Arrays.asList("Ada"), "Slack", "ctx", "standard"));
        String[] twentyFive = new String[25];
        for (int i = 0; i < twentyFive.length; i++) twentyFive[i] = "T" + i;
        eq("at most 20 terms are named", true, ApiClient.systemPrompt("neutral", Arrays.asList(twentyFive), "").contains("T18, T19.\n"));
        for (String strength : new String[] {"light", "standard"}) {   // the static prefix: the same for every style, app, About you and term
            String fixed = ApiClient.staticPrompt(strength);
            for (String style : new String[] {"neutral", "formal", "casual", "very_casual", "notes", "email", "raw", ""}) {
                eq("the prompt starts with the static part", true,
                        ApiClient.systemPrompt(style, Arrays.asList("Ada"), "Slack", "ctx", strength, "Rule.", Structure.LISTS).startsWith(fixed + "\n\n"));
            }
            int tokens = ApiClient.estTokens(fixed);
            System.out.println("static prompt prefix (" + strength + "): " + fixed.length() + " chars, about " + tokens + " tokens");
            eq("the static prefix stays between 700 and 900 estimated tokens", true, tokens >= 700 && tokens <= 900);
        }

        // cleanupAnswer: EMPTY (asked for filler-only input) is ""
        eq("EMPTY is empty", "", ApiClient.cleanupAnswer("EMPTY"));
        eq("EMPTY. is empty", "", ApiClient.cleanupAnswer(" EMPTY. "));
        eq("a sentence with EMPTY stays", "The box is EMPTY.", ApiClient.cleanupAnswer("The box is EMPTY."));
        eq("a cleanup answer is sanitized", "Hi", ApiClient.cleanupAnswer("<think>x</think>\"Hi\""));

        // Terms.select: fast enough for a big dictionary (a cache per dictionary; the golden rows pickterms cover the rules)
        java.util.Random rnd = new java.util.Random(1);
        java.util.List<String> dict = new java.util.ArrayList<>();
        for (int i = 0; i < 500; i++) {
            StringBuilder w = new StringBuilder();
            for (int k = 4 + rnd.nextInt(9); k > 0; k--) w.append((char) ('a' + rnd.nextInt(26)));
            dict.add(Character.toUpperCase(w.charAt(0)) + w.substring(1));
        }
        String[] vocab = {"the", "meeting", "about", "deploy", "server", "kubernetes", "tomorrow", "friday", "budget", "yuvraj", "rocks", "alpha", "plan"};
        StringBuilder talk = new StringBuilder();
        for (int i = 0; i < 300; i++) talk.append(vocab[rnd.nextInt(vocab.length)]).append(' ');
        long t0 = System.nanoTime();
        for (int i = 0; i < 3; i++) Terms.select(talk.toString(), dict, null);
        long ms = (System.nanoTime() - t0) / 3_000_000;
        System.out.println("Terms.select, 500 terms x 300 words: " + ms + " ms per call");
        eq("Terms.select takes well under a second for 500 terms and 300 words", true, ms < 1000);
        eq("Terms.select caps at 20", true, Terms.select(talk.toString(), dict, null).size() <= ApiClient.PROMPT_TERMS_MAX);

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
