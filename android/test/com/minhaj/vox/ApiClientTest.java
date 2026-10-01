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

        // silence hallucinations
        eq("silence: thank you", true, ApiClient.isSilenceHallucination("Thank you."));
        eq("silence: thanks for watching", true, ApiClient.isSilenceHallucination("Thanks for watching!"));
        eq("silence: real sentence", false, ApiClient.isSilenceHallucination("Thank you for the update on Friday"));

        // cleanup gate (the golden file covers the main cases; these are the edges)
        eq("cleanMinWords clamps low", 1, ApiClient.cleanMinWords("-4"));
        eq("cleanMinWords clamps high", 20, ApiClient.cleanMinWords("99999999999999999999"));
        eq("cleanMinWords trims", 7, ApiClient.cleanMinWords(" 7 "));
        eq("cleanMinWords null", 3, ApiClient.cleanMinWords(null));
        eq("cleanMinWords empty", 3, ApiClient.cleanMinWords(""));
        eq("cleanMinWords decimal", 3, ApiClient.cleanMinWords("2.5"));
        eq("needsCleanup odd spacing", true, ApiClient.needsCleanup("  one \t two\nthree  ", "casual", true, "3"));
        eq("needsCleanup odd spacing short", false, ApiClient.needsCleanup("  one \t two\n", "casual", true, "3"));
        eq("needsCleanup null text", false, ApiClient.needsCleanup(null, "casual", true, "1"));

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

        System.out.println("OK: " + checks + " checks passed");
    }
}
