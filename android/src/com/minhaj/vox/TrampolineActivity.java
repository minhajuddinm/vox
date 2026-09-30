package com.minhaj.vox;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.WeakHashMap;

/**
 * Invisible activity that starts the mic service while the app is visible, then closes. Android only lets a
 * microphone foreground service start from a visible app, so this stays on screen until DictationService has
 * called startForeground (and started recording, if asked to) and then calls {@link #finishNow()}.
 * Its extras are passed on to the service untouched (see DictationService.EXTRA_*).
 * Every entry point (the bubbles, the "Record note" notification, the quick settings tile) starts it this way.
 */
public class TrampolineActivity extends Activity {
    /** Closes the activity anyway if the service never answers (it was refused or crashed), so the screen is not covered. */
    static final int SAFETY_FINISH_MS = 1500;

    /**
     * Every trampoline that is open. A fast double tap on a cold start opens a second one before the first is
     * closed, so a single "latest" reference would leave the first up until the safety timeout. Weak keys: an
     * instance the system destroyed without onDestroy cannot be leaked by this set.
     */
    private static final Set<TrampolineActivity> live =
            Collections.synchronizedSet(Collections.newSetFromMap(new WeakHashMap<TrampolineActivity, Boolean>()));

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable safetyFinish = this::close;

    /** Called by DictationService once it is in the foreground. Safe from any thread, and a no-op when no trampoline is open. */
    static void finishNow() {
        List<TrampolineActivity> open;
        synchronized (live) {
            open = new ArrayList<>(live);   // copy: close() runs later, and onDestroy changes the set
        }
        for (final TrampolineActivity a : open) a.runOnUiThread(a::close);
    }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        live.add(this);
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            // Nothing is started, so nothing would call finishNow(): send the user to the app and close right away.
            Toast.makeText(this, "Open Vox and allow the microphone", Toast.LENGTH_LONG).show();
            startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            close();
            return;
        }
        Intent service = new Intent(this, DictationService.class);
        Bundle extras = getIntent() == null ? null : getIntent().getExtras();
        if (extras != null) service.putExtras(extras);
        startForegroundService(service);
        handler.postDelayed(safetyFinish, SAFETY_FINISH_MS);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(safetyFinish);
        live.remove(this);
        super.onDestroy();
    }

    private void close() {
        if (isFinishing() || isDestroyed()) return;
        finish();
        overridePendingTransition(0, 0);
    }
}
