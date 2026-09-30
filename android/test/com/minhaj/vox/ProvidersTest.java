package com.minhaj.vox;

/** Plain-Java checks for Providers (per-role settings, key rule, reasoning fields, messages). Run by CI, exits non-zero on failure. */
public final class ProvidersTest {
    private static void check(String name, boolean ok) {
        if (!ok) {
            System.err.println("FAIL " + name);
            System.exit(1);
        }
    }

    private static boolean same(String[] a, String... b) {
        return java.util.Arrays.equals(a, b);
    }

    public static void main(String[] args) {
        String groq = GroqClient.DEFAULT_BASE;
        check("defaults", same(Providers.roleSettings("", "", "", "", "", "m"), groq, "", "m"));
        check("inherits address, key and model", same(
                Providers.roleSettings("https://api.openai.com/v1/", " k ", "", "", "whisper-1", "m"),
                "https://api.openai.com/v1", "k", "whisper-1"));
        check("own address never gets the main key", same(
                Providers.roleSettings(groq, "main", "http://laptop:11434/v1", "", "", "m"),
                "http://laptop:11434/v1", "", "m"));
        check("own key is used", same(
                Providers.roleSettings(groq, "main", "http://laptop:11434/v1", "own", "x", "m"),
                "http://laptop:11434/v1", "own", "x"));
        check("same address override still inherits the key", same(
                Providers.roleSettings(groq, "main", groq + "/", "", "", "m"), groq, "main", "m"));

        check("key needed for hosted servers", Providers.keyRequired(groq) && Providers.keyRequired("https://api.openai.com/v1"));
        check("no key needed for the private network", !Providers.keyRequired("http://laptop:8000/v1")
                && !Providers.keyRequired("http://100.64.0.5:8000/v1") && !Providers.keyRequired("http://192.168.1.4/v1"));

        check("classify speech", "stt".equals(Providers.classify("whisper-large-v3-turbo")));
        check("classify chat", "llm".equals(Providers.classify("openai/gpt-oss-20b")));
        check("classify hidden", "hidden".equals(Providers.classify("playai-tts")));

        check("reasoning for gpt-oss", Providers.sendReasoning("auto", "b1", "openai/gpt-oss-20b"));
        check("no reasoning for other models", !Providers.sendReasoning("auto", "b1", "llama3.2:3b"));
        check("reasoning can be switched off", !Providers.sendReasoning("off", "b1", "openai/gpt-oss-20b"));
        Providers.rememberRejected("b1", "openai/gpt-oss-20b");
        check("rejected pair is remembered", !Providers.sendReasoning("auto", "b1", "openai/gpt-oss-20b"));
        check("other address unaffected", Providers.sendReasoning("auto", "b2", "openai/gpt-oss-20b"));

        check("think block removed", "Hi.".equals(Providers.stripThink("<think>hmm\nlong</think>\nHi.")));
        check("inner think kept", "Hi <think>x</think> there".equals(Providers.stripThink("Hi <think>x</think> there")));

        check("401 message", Providers.explain(401, "llm", "").contains("refused the key"));
        check("404 stt message", Providers.explain(404, "stt", "").contains("cannot do speech-to-text"));
        check("429 message", Providers.explain(429, "llm", "").contains("Rate limit"));
        check("other status", Providers.explain(500, "llm", "").contains("HTTP 500"));
        System.out.println("OK: providers checks passed");
    }
}
