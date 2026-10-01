package com.minhaj.vox;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

/**
 * What voice notes show outside the app window: the "Record note" notification, the "Note saved" notification, and
 * the intent that starts a note (used by that notification, the quick settings tile and the note bubble).
 *
 * Every way of starting a note goes through TrampolineActivity with EXTRA_START and EXTRA_DEST=note, because Android
 * only lets the microphone service start while the app is visible (see TrampolineActivity). Stopping does not need
 * the activity: see DictationService.ACTION_STOP_RECORDING.
 */
final class NoteEntry {
    private static final String CH_ENTRY = "vox_note_entry";
    private static final String CH_SAVED = "vox_note_saved";
    /** 1 is the dictation service's own notification. */
    static final int ENTRY_ID = 2;
    static final int SAVED_ID = 3;
    private static final int REQ_RECORD = 20;
    private static final int REQ_OPEN = 21;

    private NoteEntry() { }

    /** The intent that opens the trampoline in note mode, which makes the service start recording a voice note. */
    static Intent startIntent(Context c) {
        return new Intent(c, TrampolineActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_ANIMATION)
                .putExtra(DictationService.EXTRA_START, true)
                .putExtra(DictationService.EXTRA_DEST, DictationService.DEST_NOTE);
    }

    /** {@link #startIntent} as a PendingIntent (immutable), for the notification action and the tile. */
    static PendingIntent startPendingIntent(Context c) {
        return PendingIntent.getActivity(c, REQ_RECORD, startIntent(c),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    /** Posts or removes the "Record note" notification to match the setting {@code note_notification}. */
    static void applySettings(Context c) {
        if (new Prefs(c).noteNotification()) ensureNotification(c);
        else cancelNotification(c);
    }

    /**
     * Posts the ongoing "Record note" notification (quiet channel "Voice notes", one action). Posting it again
     * replaces it, so it is safe to call whenever it might be missing (app start, the accessibility service
     * connecting after a reboot).
     */
    static void ensureNotification(Context c) {
        Context app = c.getApplicationContext();
        NotificationManager nm = app.getSystemService(NotificationManager.class);
        if (nm == null) return;
        nm.createNotificationChannel(new NotificationChannel(CH_ENTRY, "Voice notes", NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(app, REQ_OPEN,
                new Intent(app, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = new Notification.Builder(app, CH_ENTRY)
                .setSmallIcon(R.drawable.ic_note)
                .setContentTitle("Vox voice notes")
                .setContentText("Tap Record note, then speak")
                .setContentIntent(open)
                .addAction(new Notification.Action.Builder(null, "Record note", startPendingIntent(app)).build())
                .setOngoing(true)
                .setShowWhen(false)
                .build();
        nm.notify(ENTRY_ID, n);
    }

    static void cancelNotification(Context c) {
        NotificationManager nm = c.getApplicationContext().getSystemService(NotificationManager.class);
        if (nm != null) nm.cancel(ENTRY_ID);
    }

    /**
     * Tells the user a note was saved: a heads-up notification "Note saved: title" (it replaces the previous one and
     * goes away by itself), or a toast when notifications are switched off for the app. Any thread.
     */
    static void postSaved(Context c, final String title) {
        final Context app = c.getApplicationContext();
        NotificationManager nm = app.getSystemService(NotificationManager.class);
        if (nm == null || !nm.areNotificationsEnabled()) {
            new Handler(Looper.getMainLooper()).post(
                    () -> Toast.makeText(app, "Note saved: " + title, Toast.LENGTH_LONG).show());
            return;
        }
        nm.createNotificationChannel(new NotificationChannel(CH_SAVED, "Voice note saved", NotificationManager.IMPORTANCE_HIGH));
        PendingIntent open = PendingIntent.getActivity(app, REQ_OPEN,
                new Intent(app, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = new Notification.Builder(app, CH_SAVED)
                .setSmallIcon(R.drawable.ic_note)
                .setContentTitle("Note saved: " + title)
                .setContentIntent(open)
                .setAutoCancel(true)
                .setTimeoutAfter(20000)
                .build();
        nm.notify(SAVED_ID, n);
    }
}
