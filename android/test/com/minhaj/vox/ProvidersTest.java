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
        String groq = ApiClient.DEFAULT_BASE;
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

        // the relay as the AI server
        String relay = "https://your-pi.tail1234.ts.net";
        check("proxy on, stt role", same(
                Providers.roleSettings("", "MAIN-KEY", "", "OWN-KEY", "", "whisper-large-v3-turbo", true, relay, "RELAY-TOKEN", Providers.STT),
                relay + "/proxy/stt", "RELAY-TOKEN", "whisper-large-v3-turbo"));
        check("proxy on, llm role ignores own address and keys", same(
                Providers.roleSettings("https://api.openai.com/v1", "MAIN-KEY", "http://laptop:11434/v1", "OWN-KEY", "gpt-4o-mini", "m", true, relay, "RELAY-TOKEN", Providers.LLM),
                relay + "/proxy/llm", "RELAY-TOKEN", "gpt-4o-mini"));
        check("proxy off changes nothing", same(
                Providers.roleSettings(groq, "main", "", "", "", "m", false, relay, "RELAY-TOKEN", Providers.LLM), groq, "main", "m"));
        check("proxy on without a token falls back", same(
                Providers.roleSettings(groq, "main", "", "", "", "m", true, relay, " ", Providers.LLM), groq, "main", "m"));
        check("proxy on without an address falls back", same(
                Providers.roleSettings(groq, "main", "", "", "", "m", true, "  ", "tok", Providers.STT), groq, "main", "m"));
        check("trailing slashes and spaces are stripped", same(
                Providers.roleSettings(groq, "main", "", "", "", "m", true, "  " + relay + "///  ", "  tok\t", Providers.LLM), relay + "/proxy/llm", "tok", "m"));
        check("uses relay", Providers.usesRelay(true, relay, "t") && !Providers.usesRelay(false, relay, "t")
                && !Providers.usesRelay(true, "", "t") && !Providers.usesRelay(true, relay, " ") && !Providers.usesRelay(true, null, null));
        check("problem when on without a relay", "Turn on the relay first".equals(Providers.proxyProblem(true, "", "t"))
                && "Turn on the relay first".equals(Providers.proxyProblem(true, relay, "")));
        check("no problem when off or working", Providers.proxyProblem(false, "", "").isEmpty() && Providers.proxyProblem(true, relay, "t").isEmpty());
        check("proxy url", (relay + "/proxy/stt").equals(Providers.proxyUrl(relay + "/", "stt")) && Providers.proxyUrl(" ", "stt").isEmpty());
        check("relay token never goes to the provider address", !String.join("|",
                Providers.roleSettings(groq, "main", "", "", "", "m", false, relay, "RELAY-TOKEN", Providers.LLM)).contains("RELAY-TOKEN"));
        check("401 through the relay says where to look", Providers.explain(401, "llm", "", true).contains("check the relay token and the AI server key set on the relay page")
                && Providers.explain(403, "stt", "", true).contains("relay"));
        check("401 not through the relay is unchanged", Providers.explain(401, "llm", "", false).equals(Providers.explain(401, "llm", "")));
        check("429 through the relay unchanged", Providers.explain(429, "llm", "", true).contains("Rate limit"));

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
