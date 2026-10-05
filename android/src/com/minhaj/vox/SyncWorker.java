package com.minhaj.vox;

import android.content.Context;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Message;
import android.util.Log;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Runs the relay sync in the background of the app's process: a thread called {@code vox-sync} that runs one
 * SyncEngine pass when asked ({@link #kick}: the app comes to the front, the dictation service starts, settings are
 * saved, and later a note is saved) and again every 90 seconds for as long as the process lives. There is no
 * background scheduler on purpose: a phone that never opens the app syncs the next time it is opened.
 *
 * Requests are coalesced: asking while a run is on the way changes nothing, and asking while a run is going makes
 * exactly one more run start after it, so a note saved during a run is not left behind. The runs are one at a time on
 * the one thread, which is also what keeps two engines from sending the same note. Nothing here throws to the caller.
 * Only counts and "ok or failed" go to the log, never note text, addresses or the token.
 *
 * It needs a device (Handler, Prefs, SQLite), so it is only compiled by the local check; the sync rules are in
 * SyncEngine and RelayClient, which the off-device tests cover.
 */
final class SyncWorker {
    /** The pause between background runs (windows/sync.py INTERVAL). */
    static final long INTERVAL_MS = 90000L;

    /** What a waiting caller gets when its run is over. */
    interface Done {
        void done(SyncResult result);
    }

    private static final int MSG_NOW = 1;    // a run is wanted as soon as possible
    private static final int MSG_TICK = 2;   // the 90 second timer

    /** The words when sync is off or has no address or token (windows/sync.py settings). */
    static final String OFF = "Sync is off or not set up.";

    private static SyncWorker instance;

    // The state the page shows (the fields of the Windows /sync/status), per process.
    private static volatile boolean running;
    private static volatile double lastRun;
    private static volatile double lastOk;
    private static volatile String error = "";
    private static volatile int pushed;
    private static volatile int pulled;
    private static volatile Runnable profileListener;

    private final Context app;
    private final Handler handler;
    private final List<Done> waiting = new ArrayList<>();   // callers for the next run; guarded by this

    private SyncWorker(Context c) {
        app = c.getApplicationContext();
        HandlerThread thread = new HandlerThread("vox-sync");
        thread.start();
        handler = new Handler(thread.getLooper()) {
            @Override
            public void handleMessage(Message m) {
                try {
                    runOnce();
                } catch (RuntimeException e) {
                    Log.w("vox", "sync run failed: " + e.getClass().getSimpleName());   // a dead thread would take the app down
                }
            }
        };
    }

    private static synchronized SyncWorker get(Context c) {
        if (instance == null) instance = new SyncWorker(c);
        return instance;
    }

    // ------------------------------------------------------------------ asking for a run

    private static final java.util.concurrent.atomic.AtomicBoolean LISTENING = new java.util.concurrent.atomic.AtomicBoolean();

    /**
     * Makes a saved note trigger a sync: registers, once per process, a listener on {@link NoteEvents} that calls
     * {@link #kick}. Idempotent; holds only the application context, so no Activity or Service is leaked.
     */
    static void start(Context c) {
        final Context appCtx = c.getApplicationContext();
        NoteEvents.addSavedListenerOnce(LISTENING, () -> kick(appCtx));
    }

    /** Syncs soon, in the background, when sync is on. Cheap and safe to call from anywhere on any thread. */
    static void kick(Context c) {
        start(c);   // every entry point (app resume, service start, settings) also makes sure notes trigger a sync
        try {
            if (enabled(new Prefs(c))) get(c).request(null);
        } catch (RuntimeException e) {
            Log.w("vox", "sync kick failed: " + e.getClass().getSimpleName());
        }
    }

    /**
     * Syncs soon and tells {@code done} how it went, on the sync thread, or at once on this thread when sync is off or
     * not set up (the result is then {@link SyncResult#failed} with {@link #OFF}).
     */
    static void syncNow(Context c, Done done) {
        try {
            if (enabled(new Prefs(c))) {
                get(c).request(done);
                return;
            }
        } catch (RuntimeException e) {
            Log.w("vox", "sync request failed: " + e.getClass().getSimpleName());
            done.done(SyncResult.failed("Sync failed: " + e.getClass().getSimpleName()));
            return;
        }
        done.done(SyncResult.failed(OFF));
    }

    private void request(Done done) {
        if (done != null) {
            synchronized (this) {
                waiting.add(done);
            }
        }
        // While a run is going its message is gone from the queue, so this queues exactly one more; while one is still
        // waiting in the queue it is enough.
        if (!handler.hasMessages(MSG_NOW)) handler.sendEmptyMessage(MSG_NOW);
    }

    // ------------------------------------------------------------------ a run

    private void runOnce() {
        List<Done> mine;
        synchronized (this) {
            mine = new ArrayList<>(waiting);   // the callers that asked before this run started
            waiting.clear();
        }
        handler.removeMessages(MSG_TICK);
        SyncResult res;
        Prefs prefs = new Prefs(app);
        if (!enabled(prefs)) {
            res = SyncResult.failed(OFF);
        } else {
            running = true;
            try {
                res = sync(prefs);
            } catch (RuntimeException e) {
                res = SyncResult.failed("Sync failed: " + e.getClass().getSimpleName());   // the store or the settings could not be opened
            } finally {
                running = false;
            }
            double now = System.currentTimeMillis() / 1000.0;
            lastRun = now;
            error = res.error;
            pushed = res.pushed;
            pulled = res.pulled;
            if (res.ok()) lastOk = now;
            Log.d("vox", "sync: " + res.pushed + " sent, " + res.pulled + " received, " + (res.ok() ? "ok" : "failed"));
            handler.sendEmptyMessageDelayed(MSG_TICK, INTERVAL_MS);
            Runnable l = profileListener;
            if (l != null && ("received".equals(res.profile) || "both".equals(res.profile))) {
                try {
                    l.run();
                } catch (RuntimeException ignored) {
                    // the page is gone; nothing to refresh
                }
            }
        }
        for (Done d : mine) {
            try {
                d.done(res);
            } catch (RuntimeException e) {
                Log.w("vox", "sync callback failed: " + e.getClass().getSimpleName());
            }
        }
    }

    private SyncResult sync(Prefs prefs) {
        String url = prefs.relayUrl(), token = prefs.relayToken();
        String problem = RelayClient.problem(url, token);
        if (!problem.isEmpty()) return SyncResult.failed(problem);
        RelayClient client = new RelayClient(url, token, prefs.deviceName());
        // The address is read once for the whole run: the engine ties its state to the same address the client uses,
        // even when the user saves another one while the run is going (the next run then follows it).
        return new SyncEngine(NotesStore.get(app), client, new PinnedUrlConfig(new PrefsConfig(prefs), url)).syncOnce();
    }

    /** The profile settings of the phone, read from and written to Prefs through ProfileMap. */
    private static final class PrefsConfig implements SyncConfig {
        private final Prefs prefs;

        PrefsConfig(Prefs prefs) {
            this.prefs = prefs;
        }

        @Override
        public boolean syncKeys() {
            return prefs.relaySyncKeys();
        }

        @Override
        public String relayUrl() {
            return prefs.relayUrl();
        }

        @Override
        public Map<String, Object> readProfile() {
            return ProfileMap.toProfile(prefs.profileStored());
        }

        @Override
        public void writeProfile(Map<String, Object> received, Map<String, Object> seen) {
            prefs.applyReceived(received, seen);
        }
    }

    // ------------------------------------------------------------------ state for the page

    /** Sync is switched on and has an address and a token (windows/sync.py settings is not None). */
    static boolean enabled(Prefs p) {
        return p.relaySync() && !p.relayUrl().isEmpty() && !p.relayToken().isEmpty();
    }

    static boolean running() { return running; }

    /** Unix seconds of the last finished run, 0 when there was none in this process. */
    static double lastRun() { return lastRun; }

    /** Unix seconds of the last run that went through without an error, 0 when there was none. */
    static double lastOk() { return lastOk; }

    /** The words about the last run, "" when it went through. */
    static String error() { return error; }

    static int pushed() { return pushed; }

    static int pulled() { return pulled; }

    /**
     * Called (on the sync thread) after a run that changed the settings of this phone, so an open page can show them.
     * One listener; null removes it.
     */
    static void setProfileListener(Runnable listener) {
        profileListener = listener;
    }
}
