package com.minhaj.vox;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URI;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Which server, key and model each role uses, and how to read a provider's model list.
 * Roles: "stt" (speech to text) and "llm" (cleanup). Twin of windows/providers.py; the classification rules are
 * shared through spec/golden.txt. The pure helpers avoid org.json so they run in the off-device tests.
 */
final class Providers {
    private Providers() { }

    static final String STT = "stt";
    static final String LLM = "llm";

    /** id, name, address, page to get a key, suggested voice model, suggested cleanup model. A phone cannot use localhost, so servers of your own are "custom". */
    static final String[][] PRESETS = {
        {"groq", "Groq (free tier)", ApiClient.DEFAULT_BASE, "https://console.groq.com/keys", "whisper-large-v3-turbo", "openai/gpt-oss-20b"},
        {"openai", "OpenAI", "https://api.openai.com/v1", "https://platform.openai.com/api-keys", "whisper-1", "gpt-4o-mini"},
        {"openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "https://openrouter.ai/keys", "", ""},
        {"together", "Together AI", "https://api.together.xyz/v1", "https://api.together.ai/settings/api-keys", "", ""},
        {"mistral", "Mistral", "https://api.mistral.ai/v1", "https://console.mistral.ai/api-keys", "voxtral-mini-latest", "mistral-small-latest"},
        {"custom", "Your own server (Ollama, Whisper server, ...)", "", "", "", ""},
    };

    private static final Pattern HIDE = Pattern.compile(
            "orpheus|(?<![a-z])tts|guard|embed|rerank|moderation|dall-e|imagen|image|diffusion|flux|playai", Pattern.CASE_INSENSITIVE);
    private static final Pattern SPEECH = Pattern.compile(
            "whisper|transcribe|voxtral|parakeet|moonshine|canary|speech-to-text|(?<![a-z])stt(?![a-z])", Pattern.CASE_INSENSITIVE);
    private static final Pattern THINK = Pattern.compile("^\\s*<think>.*?</think>\\s*", Pattern.DOTALL);

    private static final Set<String> rejected = Collections.synchronizedSet(new HashSet<String>());

    // ------------------------------------------------------------- settings per role

    /**
     * {address, key, model} for a role. A role with its own address uses only its own key: the main key never
     * goes to a different server.
     */
    static String[] roleSettings(String mainBase, String mainKey, String ownBase, String ownKey, String model, String defaultModel) {
        String main = Endpoint.normalize(mainBase);
        if (main.isEmpty()) main = ApiClient.DEFAULT_BASE;
        String own = Endpoint.normalize(ownBase);
        String ok = ownKey == null ? "" : ownKey.trim();
        String mk = mainKey == null ? "" : mainKey.trim();
        String base, key;
        if (!own.isEmpty() && !own.equals(main)) {
            base = own;
            key = ok;
        } else {
            base = main;
            key = ok.isEmpty() ? mk : ok;
        }
        String m = model == null ? "" : model.trim();
        return new String[]{base, key, m.isEmpty() ? defaultModel : m};
    }

    /** True when the server is outside the private network, so it needs a key. */
    static boolean keyRequired(String base) {
        String host = null;
        try { host = new URI(Endpoint.normalize(base)).getHost(); } catch (Exception ignored) { }
        return !Endpoint.isPrivateHost(host);
    }

    // ------------------------------------------------------------- classification

    /** "stt", "llm" or "hidden" for a model id (the rules the golden file checks). */
    static String classify(String id) {
        String s = id == null ? "" : id;
        if (HIDE.matcher(s).find()) return "hidden";
        if (SPEECH.matcher(s).find()) return STT;
        return LLM;
    }

    /** Uses an explicit field when the provider gives one (task, type, output_modalities, active), otherwise the id. */
    static String classify(JSONObject e) {
        if (e.has("active") && !e.optBoolean("active", true)) return "hidden";
        String id = e.optString("id", e.optString("name", e.optString("model", "")));
        String task = e.optString("task", "");
        if (task.isEmpty()) task = e.optString("type", "");
        task = task.toLowerCase(Locale.ROOT);
        if (task.contains("speech-recognition") || task.equals("transcribe") || task.equals("transcription")
                || task.equals("stt") || task.equals("audio-transcription")) return STT;
        JSONObject arch = e.optJSONObject("architecture");
        JSONArray mods = arch == null ? null : arch.optJSONArray("output_modalities");
        if (mods != null && mods.length() > 0) {
            boolean text = false;
            for (int i = 0; i < mods.length(); i++) {
                String m = mods.optString(i, "");
                if (m.equals("transcription")) return STT;
                if (m.equals("text")) text = true;
            }
            if (!text) return "hidden";
        }
        return classify(id);
    }

    /** {id, kind} pairs from an OpenAI-style, plain-list or Ollama /api/tags answer; hidden models are dropped. */
    static List<String[]> parseModels(String body) {
        List<String[]> out = new ArrayList<>();
        JSONArray items;
        try {
            String t = body == null ? "" : body.trim();
            if (t.startsWith("[")) {
                items = new JSONArray(t);
            } else {
                JSONObject o = new JSONObject(t);
                items = o.optJSONArray("data");
                if (items == null) items = o.optJSONArray("models");
            }
        } catch (Exception e) {
            return out;
        }
        if (items == null) return out;
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < items.length(); i++) {
            Object it = items.opt(i);
            String id;
            String kind;
            if (it instanceof JSONObject) {
                JSONObject o = (JSONObject) it;
                id = o.optString("id", o.optString("name", o.optString("model", ""))).trim();
                kind = classify(o);
            } else if (it instanceof String) {
                id = ((String) it).trim();
                kind = classify(id);
            } else {
                continue;
            }
            if (id.isEmpty() || "hidden".equals(kind) || !seen.add(id)) continue;
            out.add(new String[]{id, kind});
        }
        Collections.sort(out, new Comparator<String[]>() {
            @Override public int compare(String[] a, String[] b) {
                return a[0].toLowerCase(Locale.ROOT).compareTo(b[0].toLowerCase(Locale.ROOT));
            }
        });
        return out;
    }

    static JSONArray presetsJson() {
        JSONArray arr = new JSONArray();
        try {
            for (String[] p : PRESETS) {
                arr.put(new JSONObject().put("id", p[0]).put("name", p[1]).put("base_url", p[2])
                        .put("key_url", p[3]).put("stt_model", p[4]).put("llm_model", p[5]));
            }
        } catch (Exception ignored) { }
        return arr;
    }

    // ------------------------------------------------------------- messages and reasoning fields

    /** A plain reason for an HTTP status. */
    static String explain(int status, String role, String body) {
        if (status == 401 || status == 403) return "The server refused the key. Check that it is right and belongs to this server.";
        if (status == 404) {
            return STT.equals(role)
                    ? "This server cannot do speech-to-text (no /audio/transcriptions). Use a different server for voice."
                    : "The server has no such endpoint or model. Check the address and the model name.";
        }
        if (status == 429) return "Rate limit reached. Wait a moment and try again.";
        String t = "The server answered HTTP " + status + ".";
        return body == null || body.isEmpty() ? t : t + " " + body;
    }

    /** Whether to send reasoning_effort: only for gpt-oss models, unless switched off or refused by this server before. */
    static boolean sendReasoning(String mode, String base, String model) {
        return !"off".equals(mode) && model != null && model.contains("gpt-oss") && !rejected.contains(base + "|" + model);
    }

    static void rememberRejected(String base, String model) {
        rejected.add(base + "|" + model);
    }

    /** Removes a leading think block some models put before the answer. */
    static String stripThink(String text) {
        return text == null ? "" : THINK.matcher(text).replaceFirst("");
    }
}
