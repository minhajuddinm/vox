package com.minhaj.vox;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** All user settings, stored in SharedPreferences. */
public final class Prefs {
    public static final String DEFAULT_STT_MODEL = "whisper-large-v3-turbo";
    public static final String DEFAULT_LLM_MODEL = "openai/gpt-oss-20b";

    public static final String DEFAULT_APP_STYLES =
            "# package = style   (styles: formal, casual, very_casual, neutral, raw)\n"
            + "com.whatsapp = casual\n"
            + "com.google.android.apps.messaging = casual\n"
            + "com.instagram.android = very_casual\n"
            + "com.discord = very_casual\n"
            + "com.google.android.gm = formal\n"
            + "com.microsoft.office.outlook = formal\n"
            + "com.Slack = neutral\n"
            + "com.linkedin.android = formal\n";

    public static final String DEFAULT_DICTIONARY =
            "# One term per line. Use  wrong => right  to force a replacement.\n";

    private final SharedPreferences sp;

    public Prefs(Context c) {
        sp = c.getApplicationContext().getSharedPreferences("vox", Context.MODE_PRIVATE);
    }

    public String apiKey() { return sp.getString("api_key", "").trim(); }
    /** Server address; Groq unless the user set their own. */
    public String baseUrl() { return nonEmpty(Endpoint.normalize(sp.getString("base_url", "")), ApiClient.DEFAULT_BASE); }
    /** {address, key, model} for a role ("stt" or "llm"); a role with its own address never gets the main key. */
    public String[] role(String role) {
        return Providers.roleSettings(baseUrl(), apiKey(), sp.getString(role + "_base_url", ""), sp.getString(role + "_api_key", ""),
                sp.getString(role + "_model", ""), Providers.STT.equals(role) ? DEFAULT_STT_MODEL : DEFAULT_LLM_MODEL);
    }
    /** True when a role talks to a server outside the private network without a key (a server of your own needs none). */
    public boolean keyMissing() {
        for (String r : new String[]{Providers.STT, Providers.LLM}) {
            String[] s = role(r);
            if (s[1].isEmpty() && Providers.keyRequired(s[0])) return true;
        }
        return false;
    }
    public String sttModel() { return role(Providers.STT)[2]; }
    public String llmModel() { return role(Providers.LLM)[2]; }
    /** Preset chosen in Settings (a convenience only: the address decides behaviour). */
    public String provider() { return nonEmpty(sp.getString("provider", ""), "groq"); }
    /** A stored setting as typed (blank when unset), for the provider form. */
    public String raw(String key) { return sp.getString(key, ""); }
    /** Free text about the user (work, projects, style) added to every cleanup request. */
    public String userContext() { return sp.getString("user_context", ""); }
    public String language() { return sp.getString("language", "").trim(); }
    public String dictionaryRaw() { return sp.getString("dictionary", DEFAULT_DICTIONARY); }
    public String peopleRaw() { return sp.getString("people", ""); }
    public String appStylesRaw() { return sp.getString("app_styles", DEFAULT_APP_STYLES); }
    public String defaultStyle() { return nonEmpty(sp.getString("default_style", ""), "neutral"); }
    /** When false, nothing dictated is saved on the phone. */
    public boolean keepHistory() { return sp.getBoolean("keep_history", true); }
    public boolean cleanupEnabled() { return sp.getBoolean("cleanup", true); }
    public boolean onlyWhenTyping() { return sp.getBoolean("only_typing", true); }
    public int bubbleX() { return sp.getInt("bubble_x", -1); }
    public int bubbleY() { return sp.getInt("bubble_y", -1); }

    public SharedPreferences.Editor edit() { return sp.edit(); }

    public void saveBubblePos(int x, int y) {
        sp.edit().putInt("bubble_x", x).putInt("bubble_y", y).apply();
    }

    /** Plain dictionary terms (lines without "=>"). */
    public List<String> dictionaryTerms() {
        return Terms.terms(peopleRaw(), dictionaryRaw());
    }

    /** Forced replacements from lines of the form "wrong => right". */
    public Map<String, String> replacements() {
        return Terms.replacements(dictionaryRaw());
    }

    /** Style for a package name, falling back to the default style. */
    public String styleFor(String pkg) {
        if (pkg != null) {
            for (String line : appStylesRaw().split("\n")) {
                String l = line.trim();
                if (l.isEmpty() || l.startsWith("#") || !l.contains("=")) continue;
                String p = l.substring(0, l.indexOf('=')).trim();
                String s = l.substring(l.indexOf('=') + 1).trim().toLowerCase(Locale.ROOT);
                if (p.equalsIgnoreCase(pkg)) return s;
            }
        }
        return defaultStyle();
    }

    // ---- history ----

    public void addHistory(String app, String raw, String clean, double secs) {
        if (!keepHistory()) return;
        try {
            JSONArray arr = new JSONArray(sp.getString("history", "[]"));
            JSONObject o = new JSONObject();
            o.put("t", System.currentTimeMillis() / 1000.0);
            o.put("app", app == null ? "" : app);
            o.put("raw", raw);
            o.put("text", clean);
            o.put("words", clean.trim().isEmpty() ? 0 : clean.trim().split("\\s+").length);
            o.put("secs", Math.round(secs * 10) / 10.0);
            JSONArray next = new JSONArray();
            next.put(o);
            for (int i = 0; i < arr.length() && i < 499; i++) next.put(arr.get(i));
            sp.edit().putString("history", next.toString()).apply();
        } catch (Exception ignored) { }
    }

    public void deleteHistory(double t) {
        try {
            JSONArray arr = history(), next = new JSONArray();
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.getJSONObject(i);
                if (Math.abs(o.optDouble("t") - t) > 0.0005) next.put(o);
            }
            sp.edit().putString("history", next.toString()).apply();
        } catch (Exception ignored) { }
    }

    public JSONArray history() {
        try { return new JSONArray(sp.getString("history", "[]")); }
        catch (Exception e) { return new JSONArray(); }
    }

    public void clearHistory() { sp.edit().putString("history", "[]").apply(); }

    private static String nonEmpty(String v, String def) {
        return v == null || v.trim().isEmpty() ? def : v.trim();
    }
}
