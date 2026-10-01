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
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;
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
import java.util.TimeZone;

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
        NoteEntry.applySettings(this);   // brings the "Record note" notification back (Android 14 lets users swipe it away)
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
        // settings another device changed arrive while the page is open: show them (a stale page could save over them)
        SyncWorker.setProfileListener(() -> main.post(this::refreshJs));
        SyncWorker.kick(this);
    }

    @Override
    protected void onPause() {
        SyncWorker.setProfileListener(null);
        super.onPause();
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
                cfg.put("cleanup_min_words", ApiClient.cleanMinWords(prefs.cleanupMinWords()));
                cfg.put("keep_history", prefs.keepHistory());
                cfg.put("only_typing", prefs.onlyWhenTyping());
                cfg.put("note_bubble", prefs.noteBubble());
                cfg.put("note_notification", prefs.noteNotification());
                cfg.put("default_style", prefs.defaultStyle());
                cfg.put("stt_model", prefs.sttModel());
                cfg.put("llm_model", prefs.llmModel());
                cfg.put("provider", prefs.provider());
                cfg.put("user_context", prefs.userContext());
                for (String f : new String[]{"stt_base_url", "stt_api_key", "llm_base_url", "llm_api_key"}) cfg.put(f, prefs.raw(f));
                cfg.put("relay_sync", prefs.relaySync());
                cfg.put("relay_url", prefs.relayUrl());
                cfg.put("relay_token", prefs.relayToken());
                cfg.put("relay_sync_keys", prefs.relaySyncKeys());
                cfg.put("relay_proxy", prefs.relayProxy());
                cfg.put("device_name", prefs.raw("device_name"));
                cfg.put("dictionary", lines(prefs.dictionaryRaw()));
                cfg.put("people", lines(prefs.peopleRaw()));
                cfg.put("app_styles", appStyles());
                o.put("config", cfg);
                o.put("presets", Providers.presetsJson());
                o.put("device_default", NoteLogic.deviceName("", Build.MODEL));   // the name used while Settings has none typed
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
                if (c.has("cleanup_min_words")) e.putString("cleanup_min_words", String.valueOf(ApiClient.cleanMinWords(c.getString("cleanup_min_words"))));
                if (c.has("keep_history")) e.putBoolean("keep_history", c.getBoolean("keep_history"));
                if (c.has("only_typing")) e.putBoolean("only_typing", c.getBoolean("only_typing"));
                if (c.has("note_bubble")) e.putBoolean("note_bubble", c.getBoolean("note_bubble"));
                if (c.has("note_notification")) e.putBoolean("note_notification", c.getBoolean("note_notification"));
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
                if (c.has("relay_sync")) e.putBoolean("relay_sync", c.getBoolean("relay_sync"));
                if (c.has("relay_sync_keys")) e.putBoolean("relay_sync_keys", c.getBoolean("relay_sync_keys"));
                if (c.has("relay_proxy")) e.putBoolean("relay_proxy", c.getBoolean("relay_proxy"));
                if (c.has("relay_url") && Endpoint.error(text(c, "relay_url")) == null) e.putString("relay_url", Endpoint.normalize(text(c, "relay_url")));
                if (c.has("relay_token")) e.putString("relay_token", text(c, "relay_token").trim());
                if (c.has("device_name")) e.putString("device_name", text(c, "device_name").trim());
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
                SyncWorker.kick(MainActivity.this);   // the profile settings may have changed, or sync was just switched on
                main.post(() -> {
                    VoxAccessibilityService a = VoxAccessibilityService.instance;
                    if (a != null) a.refreshVisibility();   // also shows or hides the note bubble
                    NoteEntry.applySettings(MainActivity.this);   // and posts or removes the "Record note" notification
                });
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
                try { res = new ApiClient(key.trim(), baseUrl).checkKey() ? "ok" : "bad"; }
                catch (Exception e) { res = "offline"; }
                js(callback + "('" + res + "')");
            }).start();
        }

        private String[] formRole(String role, String form) throws Exception {
            JSONObject f = new JSONObject(form);
            return Providers.roleSettings(f.optString("base_url", prefs.baseUrl()), f.optString("api_key", prefs.apiKey()),
                    f.optString(role + "_base_url", ""), f.optString(role + "_api_key", ""), f.optString(role + "_model", ""),
                    Providers.STT.equals(role) ? Prefs.DEFAULT_STT_MODEL : Prefs.DEFAULT_LLM_MODEL,
                    prefs.relayProxy(), prefs.relayUrl(), prefs.relayToken(), role);   // the switch and the relay are saved as they change: not part of the form
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
                    for (String[] m : new ApiClient(s[1], s[0]).listModels(role)) arr.put(m[0]);
                    if (arr.length() == 0) err = "The server listed no models for this. Type the model name instead.";
                } catch (ApiClient.ApiException e) {
                    err = Providers.explain(e.code, role, "", prefs.usesRelay());
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
                        new ApiClient(s[1], s[0]).test(role, s[2]);
                        ok = true;
                        msg = "Works (" + (System.currentTimeMillis() - t0) + " ms) with " + s[2] + ".";
                    }
                } catch (ApiClient.ApiException e) {
                    msg = Providers.explain(e.code, role, "", prefs.usesRelay());
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

        // ------------------------------------------------------------ voice notes
        // JavaScript may pass null for any string: every argument goes through nz() before it reaches the store.

        /**
         * Voice notes, newest first, as a JSON array of {id, title, text, created_at, updated_at, secs, device, tags}.
         * `period` is all, today, week or month; a blank `tag` filters nothing. Answers {"error": ...} when the notes
         * cannot be read.
         */
        @JavascriptInterface
        public String notesList(String query, String period, String tag) {
            try {
                String t = nz(tag).trim();
                Double since = NoteLogic.periodStart(nz(period), System.currentTimeMillis() / 1000.0, TimeZone.getDefault());
                JSONArray out = new JSONArray();
                for (Note n : NotesStore.get(MainActivity.this).search(nz(query), Note.SOURCE_NOTE, since, null,
                        t.isEmpty() ? null : t, NOTES_LIMIT)) {
                    out.put(noteJson(n));
                }
                return out.toString();
            } catch (Exception e) {
                Log.w("vox", "notes list failed: " + e.getClass().getSimpleName());
                return errorJson("The notes could not be read.");
            }
        }

        /**
         * Changes a note's title, text and tags (`tagsJson` is a JSON array of strings; blank keeps the current tags).
         * Answers the note as it is now, or {"error": ...}.
         */
        @JavascriptInterface
        public String noteEdit(String id, String title, String text, String tagsJson) {
            try {
                List<String> tags = null;
                String tj = nz(tagsJson).trim();
                if (!tj.isEmpty()) {
                    JSONArray a = new JSONArray(tj);
                    tags = new ArrayList<>();
                    for (int i = 0; i < a.length(); i++) tags.add(a.isNull(i) ? "" : a.optString(i));
                }
                Note n = NotesStore.get(MainActivity.this).update(nz(id), nz(title), nz(text), tags);
                return n == null ? errorJson("That note no longer exists.") : noteJson(n).toString();
            } catch (Exception e) {
                Log.w("vox", "note edit failed: " + e.getClass().getSimpleName());
                return errorJson("The note could not be saved.");
            }
        }

        /** Deletes a note (its marker stays so a sync can tell the other devices). Answers {"ok": true} or {"error": ...}. */
        @JavascriptInterface
        public String noteDelete(String id) {
            try {
                return NotesStore.get(MainActivity.this).delete(nz(id)) ? "{\"ok\":true}" : errorJson("That note no longer exists.");
            } catch (Exception e) {
                Log.w("vox", "note delete failed: " + e.getClass().getSimpleName());
                return errorJson("The note could not be deleted.");
            }
        }

        /**
         * Starts a voice note, or finishes the one being recorded. Answers {"ok": true, "action": "start" or "stop"},
         * or {"error": ...} when it cannot start. Like a bubble tap it starts the dictation service if needed (this
         * activity is on screen, which is what lets the microphone open); the recording itself starts a moment later,
         * so the page polls noteStatus.
         */
        @JavascriptInterface
        public String noteToggle() {
            final DictationService svc = DictationService.instance;
            int st = svc == null ? DictationService.IDLE : svc.getState();
            boolean dictating = DictationService.DEST_DICTATION.equals(DictationService.currentDest());   // a bubble dictation, not a note
            if (st == DictationService.RECORDING && !dictating) {
                main.post(svc::stopRecording);
                return "{\"ok\":true,\"action\":\"stop\"}";
            }
            if (st == DictationService.RECORDING) return errorJson("Vox is taking a dictation right now. Finish it first.");
            if (st == DictationService.PROCESSING) return errorJson("Vox is still writing down the last recording. Try again in a moment.");
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                return errorJson("Allow the microphone first (Home, Set up).");
            }
            String problem = Endpoint.error(prefs.role(Providers.STT)[0]);
            if (problem == null) problem = Endpoint.error(prefs.role(Providers.LLM)[0]);
            if (problem != null) return errorJson(problem);
            if (prefs.keyMissing()) return errorJson("Add your API key in Settings first.");
            main.post(() -> {
                try {
                    if (svc != null) {
                        svc.startRecording(null, NOTE_LABEL, DictationService.DEST_NOTE);
                    } else {
                        startForegroundService(new Intent(MainActivity.this, DictationService.class)
                                .putExtra(DictationService.EXTRA_START, true)
                                .putExtra(DictationService.EXTRA_LABEL, NOTE_LABEL)
                                .putExtra(DictationService.EXTRA_DEST, DictationService.DEST_NOTE)
                                .putExtra(DictationService.EXTRA_TAP_AT, SystemClock.elapsedRealtime()));
                    }
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "Could not start the voice note", Toast.LENGTH_SHORT).show();
                }
            });
            return "{\"ok\":true,\"action\":\"start\"}";
        }

        /**
         * {"recording": bool, "busy": bool}: a voice note is being recorded, or the service is busy (writing a recording
         * down, or taking a bubble dictation, which a note must never stop).
         */
        @JavascriptInterface
        public String noteStatus() {
            DictationService svc = DictationService.instance;
            int st = svc == null ? DictationService.IDLE : svc.getState();
            boolean dictating = DictationService.DEST_DICTATION.equals(DictationService.currentDest());
            JSONObject o = new JSONObject();
            put(o, "recording", st == DictationService.RECORDING && !dictating);
            put(o, "busy", st == DictationService.PROCESSING || (st == DictationService.RECORDING && dictating));
            return o.toString();
        }

        // ------------------------------------------------------------ relay sync (SyncWorker runs it, see there)

        /**
         * {"enabled", "running", "last_run", "last_ok", "error", "pushed", "pulled"}, the same fields as the Windows app's
         * /sync/status. {@code enabled} is the switch on with an address and a token; the times are Unix seconds (0 when
         * there was no run yet in this process) and {@code error} is "" after a run that went through.
         */
        @JavascriptInterface
        public String syncStatus() {
            JSONObject o = new JSONObject();
            put(o, "enabled", SyncWorker.enabled(prefs));
            put(o, "running", SyncWorker.running());
            put(o, "last_run", SyncWorker.lastRun());
            put(o, "last_ok", SyncWorker.lastOk());
            put(o, "error", SyncWorker.error());
            put(o, "pushed", SyncWorker.pushed());
            put(o, "pulled", SyncWorker.pulled());
            return o.toString();
        }

        /** Runs a sync and answers callback("{ok, message}") when it is over; the page then reads syncStatus. */
        @JavascriptInterface
        public void syncNow(String callback) {
            SyncWorker.syncNow(MainActivity.this, r -> answerSync(callback, r.ok(), r.ok() ? "Synced." : r.error));
        }

        /** Tries the saved relay address and token and answers callback("{ok, message}"). */
        @JavascriptInterface
        public void syncTest(String callback) {
            final String url = prefs.relayUrl(), token = prefs.relayToken(), device = prefs.deviceName();
            new Thread(() -> {
                RelayClient.Check c = RelayClient.check(url, token, device);
                answerSync(callback, c.ok, c.message);
            }, "vox-sync-test").start();
        }

        private void answerSync(String callback, boolean ok, String message) {
            JSONObject res = new JSONObject();
            put(res, "ok", ok);
            put(res, "message", message);
            js(callback + "(" + JSONObject.quote(res.toString()) + ")");
        }
    }

    // ---------------------------------------------------------------- helpers

    /** The most notes the Voice notes page lists at once (the Windows page lists the same number). */
    private static final int NOTES_LIMIT = 200;

    /** The "app" a voice note is recorded for, in the cleanup request: the note is not typed into another app. */
    private static final String NOTE_LABEL = "Vox";

    /** A string that may be null (JavaScript null) as "". */
    private static String nz(String s) {
        return s == null ? "" : s;
    }

    /** A string field of a settings object; a missing or JSON null value counts as "" (never the text "null"). */
    private static String text(JSONObject o, String key) throws Exception {
        return o.isNull(key) ? "" : o.getString(key);
    }

    private static String errorJson(String message) {
        try {
            return new JSONObject().put("error", message).toString();
        } catch (Exception e) {
            return "{}";
        }
    }

    private static JSONObject noteJson(Note n) throws Exception {
        return new JSONObject()
                .put("id", n.id)
                .put("title", n.title)
                .put("text", n.text)
                .put("created_at", n.createdAt)
                .put("updated_at", n.updatedAt)
                .put("secs", n.secs)
                .put("device", n.device)
                .put("tags", new JSONArray(n.tags));
    }

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
