package com.minhaj.vox;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.content.res.Configuration;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;

/** The app screen: a local HTML UI (assets/index.html) with a small Java bridge for settings and setup. */
public class MainActivity extends Activity {
    private WebView web;
    private Prefs prefs;
    private final Handler main = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = new Prefs(this);
        boolean dark = isDark();
        Window w = getWindow();
        int bg = dark ? 0xFF161618 : 0xFFF7F7F5;
        w.setStatusBarColor(bg);
        w.setNavigationBarColor(dark ? 0xFF1E1E21 : 0xFFFFFFFF);
        if (!dark) {
            int flags = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            if (Build.VERSION.SDK_INT >= 27) flags |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            w.getDecorView().setSystemUiVisibility(flags);
        }

        web = new WebView(this);
        web.setBackgroundColor(bg);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);   // the UI is loaded from assets, which does not need file access
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient());
        web.addJavascriptInterface(new Bridge(), "Vox");
        web.loadUrl("file:///android_asset/index.html");
        setContentView(web);

        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 2);
        }
    }

    private boolean isDark() {
        return (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                == Configuration.UI_MODE_NIGHT_YES;
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshJs();
        main.postDelayed(this::refreshJs, 700);
    }

    @Override
    public void onBackPressed() {
        web.evaluateJavascript("window.voxBack && window.voxBack()", v -> {
            if (!"true".equals(v)) MainActivity.super.onBackPressed();
        });
    }

    private void refreshJs() {
        if (web != null) web.evaluateJavascript("window.voxRefresh && window.voxRefresh()", null);
    }

    private void js(String code) {
        main.post(() -> web.evaluateJavascript(code, null));
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        if (code == 1 && res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED
                && DictationService.instance == null) {
            startForegroundService(new Intent(this, DictationService.class));
        }
        main.postDelayed(this::refreshJs, 400);
    }

    /** Methods callable from the HTML UI as window.Vox.name(...). All data crosses as JSON strings. */
    public class Bridge {
        @JavascriptInterface
        public String state() {
            try {
                JSONObject o = new JSONObject();
                JSONObject cfg = new JSONObject();
                cfg.put("api_key", prefs.apiKey());
                cfg.put("base_url", prefs.baseUrl());
                cfg.put("language", prefs.language());
                cfg.put("cleanup", prefs.cleanupEnabled());
                cfg.put("keep_history", prefs.keepHistory());
                cfg.put("only_typing", prefs.onlyWhenTyping());
                cfg.put("default_style", prefs.defaultStyle());
                cfg.put("stt_model", prefs.sttModel());
                cfg.put("llm_model", prefs.llmModel());
                cfg.put("provider", prefs.provider());
                cfg.put("user_context", prefs.userContext());
                for (String f : new String[]{"stt_base_url", "stt_api_key", "llm_base_url", "llm_api_key"}) cfg.put(f, prefs.raw(f));
                cfg.put("dictionary", lines(prefs.dictionaryRaw()));
                cfg.put("people", lines(prefs.peopleRaw()));
                cfg.put("app_styles", appStyles());
                o.put("config", cfg);
                o.put("presets", Providers.presetsJson());
                o.put("mic", checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED);
                o.put("a11y", VoxAccessibilityService.instance != null);
                o.put("service", DictationService.instance != null);
                o.put("history", prefs.history());
                o.put("dark", isDark());
                o.put("version", getPackageManager().getPackageInfo(getPackageName(), 0).versionName);
                return o.toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        @JavascriptInterface
        public void save(String json) {
            try {
                JSONObject c = new JSONObject(json);
                android.content.SharedPreferences.Editor e = prefs.edit();
                if (c.has("api_key")) e.putString("api_key", c.getString("api_key").trim());
                if (c.has("base_url") && Endpoint.error(c.getString("base_url")) == null) {
                    e.putString("base_url", Endpoint.normalize(c.getString("base_url")));
                }
                if (c.has("language")) e.putString("language", c.getString("language"));
                if (c.has("cleanup")) e.putBoolean("cleanup", c.getBoolean("cleanup"));
                if (c.has("keep_history")) e.putBoolean("keep_history", c.getBoolean("keep_history"));
                if (c.has("only_typing")) e.putBoolean("only_typing", c.getBoolean("only_typing"));
                if (c.has("default_style")) e.putString("default_style", c.getString("default_style"));
                if (c.has("stt_model")) e.putString("stt_model", c.getString("stt_model"));
                if (c.has("llm_model")) e.putString("llm_model", c.getString("llm_model"));
                if (c.has("provider")) e.putString("provider", c.getString("provider"));
                if (c.has("user_context")) e.putString("user_context", c.getString("user_context"));
                for (String f : new String[]{"stt_base_url", "llm_base_url"}) {
                    if (c.has(f) && Endpoint.error(c.getString(f)) == null) e.putString(f, Endpoint.normalize(c.getString(f)));
                }
                if (c.has("stt_api_key")) e.putString("stt_api_key", c.getString("stt_api_key").trim());
                if (c.has("llm_api_key")) e.putString("llm_api_key", c.getString("llm_api_key").trim());
                if (c.has("dictionary")) e.putString("dictionary", join(c.getJSONArray("dictionary")));
                if (c.has("people")) e.putString("people", join(c.getJSONArray("people")));
                if (c.has("app_styles")) {
                    JSONObject m = c.getJSONObject("app_styles");
                    StringBuilder sb = new StringBuilder();
                    Iterator<String> it = m.keys();
                    while (it.hasNext()) { String k = it.next(); sb.append(k).append(" = ").append(m.getString(k)).append('\n'); }
                    e.putString("app_styles", sb.toString());
                }
                e.apply();
                main.post(() -> { VoxAccessibilityService a = VoxAccessibilityService.instance; if (a != null) a.refreshVisibility(); });
            } catch (Exception ignored) { }
        }

        @JavascriptInterface
        public void requestMic() {
            main.post(() -> requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 1));
        }

        @JavascriptInterface
        public void openAccessibility() {
            main.post(() -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        }

        @JavascriptInterface
        public void openAppInfo() {
            main.post(() -> startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:" + getPackageName()))));
        }

        @JavascriptInterface
        public void openBattery() {
            main.post(() -> {
                try { startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)); }
                catch (Exception e) { openAppInfo(); }
            });
        }

        @JavascriptInterface
        public void setService(boolean on) {
            main.post(() -> {
                if (on) {
                    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                        requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 1);
                        return;
                    }
                    startForegroundService(new Intent(MainActivity.this, DictationService.class));
                } else {
                    stopService(new Intent(MainActivity.this, DictationService.class));
                }
                main.postDelayed(MainActivity.this::refreshJs, 500);
            });
        }

        @JavascriptInterface
        public void testKey(String key, String baseUrl, String callback) {
            new Thread(() -> {
                String res;
                try { res = new GroqClient(key.trim(), baseUrl).checkKey() ? "ok" : "bad"; }
                catch (Exception e) { res = "offline"; }
                js(callback + "('" + res + "')");
            }).start();
        }

        private String[] formRole(String role, String form) throws Exception {
            JSONObject f = new JSONObject(form);
            return Providers.roleSettings(f.optString("base_url", prefs.baseUrl()), f.optString("api_key", prefs.apiKey()),
                    f.optString(role + "_base_url", ""), f.optString(role + "_api_key", ""), f.optString(role + "_model", ""),
                    Providers.STT.equals(role) ? Prefs.DEFAULT_STT_MODEL : Prefs.DEFAULT_LLM_MODEL);
        }

        private void put(JSONObject o, String k, Object v) {
            try { o.put(k, v); } catch (Exception ignored) { }
        }

        /** Models a role's server offers. `form` holds the settings as typed; answers callback("{models, error}"). */
        @JavascriptInterface
        public void listModels(String role, String form, String callback) {
            new Thread(() -> {
                JSONArray arr = new JSONArray();
                String err = "";
                try {
                    String[] s = formRole(role, form);
                    for (String[] m : new GroqClient(s[1], s[0]).listModels(role)) arr.put(m[0]);
                    if (arr.length() == 0) err = "The server listed no models for this. Type the model name instead.";
                } catch (GroqClient.ApiException e) {
                    err = Providers.explain(e.code, role, "");
                } catch (Exception e) {
                    String m = e.getMessage();
                    err = m != null && m.startsWith("Plain http") ? m : "Could not reach the server.";
                }
                JSONObject res = new JSONObject();
                put(res, "models", arr);
                put(res, "error", err);
                js(callback + "(" + JSONObject.quote(res.toString()) + ")");
            }).start();
        }

        /** One real call to a role's server; answers callback("{ok, message}"). */
        @JavascriptInterface
        public void testRole(String role, String form, String callback) {
            new Thread(() -> {
                boolean ok = false;
                String msg;
                long t0 = System.currentTimeMillis();
                try {
                    String[] s = formRole(role, form);
                    String problem = Endpoint.error(s[0]);
                    if (problem != null) msg = problem;
                    else if (s[1].isEmpty() && Providers.keyRequired(s[0])) msg = "Add an API key for this server first.";
                    else {
                        new GroqClient(s[1], s[0]).test(role, s[2]);
                        ok = true;
                        msg = "Works (" + (System.currentTimeMillis() - t0) + " ms) with " + s[2] + ".";
                    }
                } catch (GroqClient.ApiException e) {
                    msg = Providers.explain(e.code, role, "");
                } catch (Exception e) {
                    msg = "Could not reach the server.";
                }
                JSONObject res = new JSONObject();
                put(res, "ok", ok);
                put(res, "message", msg);
                js(callback + "(" + JSONObject.quote(res.toString()) + ")");
            }).start();
        }

        /** Word swaps found between a dictation and the user's fixed version, as JSON [[wrong, right], ...]. */
        @JavascriptInterface
        public String suggestCorrections(String original, String edited) {
            JSONArray arr = new JSONArray();
            for (String[] p : Corrections.suggest(original, edited, 3)) {
                arr.put(new JSONArray().put(p[0]).put(p[1]));
            }
            return arr.toString();
        }

        /** Empty when the server address is acceptable, otherwise the reason it is not. */
        @JavascriptInterface
        public String endpointProblem(String baseUrl) {
            String p = Endpoint.error(baseUrl);
            return p == null ? "" : p;
        }

        @JavascriptInterface
        public void copy(String text) {
            main.post(() -> {
                ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                cm.setPrimaryClip(ClipData.newPlainText("Vox", text));
            });
        }

        @JavascriptInterface
        public void deleteHistory(double t) { prefs.deleteHistory(t); }

        @JavascriptInterface
        public void clearHistory() { prefs.clearHistory(); }

        @JavascriptInterface
        public void openUrl(String url) {
            if (url.startsWith("https://")) main.post(() -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))));
        }

        @JavascriptInterface
        public void toast(String msg) {
            main.post(() -> Toast.makeText(MainActivity.this, msg, Toast.LENGTH_SHORT).show());
        }

        /** Launchable apps, for picking per-app styles. */
        @JavascriptInterface
        public String apps() {
            JSONArray out = new JSONArray();
            try {
                PackageManager pm = getPackageManager();
                Intent i = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
                List<ResolveInfo> list = pm.queryIntentActivities(i, 0);
                List<String[]> rows = new ArrayList<>();
                for (ResolveInfo r : list) {
                    String pkg = r.activityInfo.packageName;
                    if (pkg.equals(getPackageName())) continue;
                    rows.add(new String[]{String.valueOf(r.loadLabel(pm)), pkg});
                }
                Collections.sort(rows, (a, c) -> a[0].compareToIgnoreCase(c[0]));
                String last = "";
                for (String[] r : rows) {
                    if (r[1].equals(last)) continue;
                    last = r[1];
                    out.put(new JSONObject().put("label", r[0]).put("pkg", r[1]));
                }
            } catch (Exception ignored) { }
            return out.toString();
        }

        @JavascriptInterface
        public String appLabel(String pkg) {
            try {
                PackageManager pm = getPackageManager();
                ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
                return pm.getApplicationLabel(ai).toString();
            } catch (Exception e) {
                return pkg;
            }
        }
    }

    // ---------------------------------------------------------------- helpers

    private static JSONArray lines(String raw) {
        JSONArray a = new JSONArray();
        for (String l : raw.split("\n")) {
            String t = l.trim();
            if (!t.isEmpty() && !t.startsWith("#")) a.put(t);
        }
        return a;
    }

    private static String join(JSONArray a) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < a.length(); i++) sb.append(a.getString(i)).append('\n');
        return sb.toString();
    }

    private JSONObject appStyles() throws Exception {
        JSONObject m = new JSONObject();
        for (String line : prefs.appStylesRaw().split("\n")) {
            String l = line.trim();
            if (l.isEmpty() || l.startsWith("#") || !l.contains("=")) continue;
            m.put(l.substring(0, l.indexOf('=')).trim(), l.substring(l.indexOf('=') + 1).trim());
        }
        return m;
    }
}
