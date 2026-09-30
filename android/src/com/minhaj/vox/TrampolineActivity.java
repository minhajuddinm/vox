package com.minhaj.vox;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

import java.lang.ref.WeakReference;

/**
 * Invisible activity that starts the mic service while the app is visible, then closes. Android only lets a
 * microphone foreground service start from a visible app, so this stays on screen until DictationService has
 * called startForeground (and started recording, if asked to) and then calls {@link #finishNow()}.
 * Its extras are passed on to the service untouched (see DictationService.EXTRA_*).
 */
public class TrampolineActivity extends Activity {
    /** Closes the activity anyway if the service never answers (it was refused or crashed), so the screen is not covered. */
    static final int SAFETY_FINISH_MS = 1500;

    private static volatile WeakReference<TrampolineActivity> live;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable safetyFinish = this::close;

    /** Called by DictationService once it is in the foreground. Safe from any thread, and a no-op when no trampoline is open. */
    static void finishNow() {
        WeakReference<TrampolineActivity> ref = live;
        final TrampolineActivity a = ref == null ? null : ref.get();
        if (a != null) a.runOnUiThread(a::close);
    }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        live = new WeakReference<>(this);
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
        WeakReference<TrampolineActivity> ref = live;
        if (ref != null && ref.get() == this) live = null;
        super.onDestroy();
    }

    private void close() {
        if (isFinishing() || isDestroyed()) return;
        finish();
        overridePendingTransition(0, 0);
    }
}
