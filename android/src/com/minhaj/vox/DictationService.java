package com.minhaj.vox;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Foreground service of type "microphone". It must be started from a visible activity
 * (Android blocks background mic access), then it keeps running so the bubble can record any time.
 */
public class DictationService extends Service {
    public static final int SAMPLE_RATE = 16000;
    private static final int MAX_SECONDS = 360;
    /**
     * The id changed from "vox_service": that channel was created with IMPORTANCE_MIN, so its notification was collapsed
     * and the Stop button was hard to find, and Android cannot raise the importance of an existing channel. The new
     * channel is IMPORTANCE_LOW (still silent); onCreate deletes the old one.
     */
    private static final String CH = "vox_service_low";
    private static final String CH_OLD = "vox_service";
    /** Turns the whole service off (the notification's "Turn off" button). */
    public static final String ACTION_STOP = "com.minhaj.vox.STOP";
    /**
     * Finishes the recording in progress and sends it; the service keeps running. The "Stop" button of the
     * notification while a voice note is recorded and the quick settings tile send it.
     */
    public static final String ACTION_STOP_RECORDING = "com.minhaj.vox.STOP_RECORDING";
    private static final String ACTION_RETRY = "com.minhaj.vox.RETRY";
    /** Throws away every unsent recording (the notification's "Clear" button). */
    private static final String ACTION_CLEAR = "com.minhaj.vox.CLEAR_UNSENT";
    private static final int SEND_ATTEMPTS = 3;

    /**
     * Extras of the start intent. TrampolineActivity passes its own extras on, so one set of keys serves both:
     * EXTRA_START (boolean) = start recording as soon as the service is in the foreground; EXTRA_PKG and
     * EXTRA_LABEL = the app being typed into; EXTRA_DEST = DEST_DICTATION (default) or DEST_NOTE;
     * EXTRA_TAP_AT (long, SystemClock.elapsedRealtime) = when the user tapped, only for the tap->recording log.
     */
    public static final String EXTRA_START = "com.minhaj.vox.EXTRA_START";
    public static final String EXTRA_PKG = "com.minhaj.vox.EXTRA_PKG";
    public static final String EXTRA_LABEL = "com.minhaj.vox.EXTRA_LABEL";
    public static final String EXTRA_DEST = "com.minhaj.vox.EXTRA_DEST";
    public static final String EXTRA_TAP_AT = "com.minhaj.vox.EXTRA_TAP_AT";
    public static final String DEST_DICTATION = "dictation", DEST_NOTE = "note";

    public static final int IDLE = 0, RECORDING = 1, PROCESSING = 2;

    public interface Listener {
        void onState(int s);
        void onLevel(float level);          // 0..1 while recording
        void onResult(String text, String targetPkg);
        void onError(String message);
    }

    /**
     * Told when a voice note has been saved. Separate from {@link Listener}, which belongs to the accessibility
     * service and types text: a note is never typed, so it does not go through onResult. Called on the main thread.
     */
    public interface NoteListener {
        void onNoteSaved(String id, String title);
    }

    public static volatile DictationService instance;
    private static Listener listener;
    private static volatile NoteListener noteListener;

    public static void setListener(Listener l) { listener = l; }

    public static void setNoteListener(NoteListener l) { noteListener = l; }

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private volatile int state = IDLE;
    private volatile boolean recording;
    /** Bumped on every new recording, retry and cancel. A job that is no longer current must not touch state or insert text. */
    private volatile int jobId;
    private Thread recThread;
    private ByteArrayOutputStream pcm;
    private String targetPkg;
    private String targetLabel;
    /** Where the job in progress sends its result (DEST_DICTATION or DEST_NOTE). Each job takes a copy (see Job). */
    private volatile String targetDest = DEST_DICTATION;
    /**
     * The unsent recordings kept on disk for Retry, each with its own file (vox_pending_<id>_<dest>.wav) and the
     * pkg/label/dest it was made with. An entry is added when its file is written and leaves only when it is sent,
     * discarded by the user (Clear, or a long-press cancel of that very recording) or dropped by the cap or age rule.
     */
    private final PendingQueue pending = new PendingQueue();
    private long lastEntryId;

    @Override public IBinder onBind(Intent i) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        try { nm.deleteNotificationChannel(CH_OLD); } catch (RuntimeException ignored) { }
        NotificationChannel ch = new NotificationChannel(CH, "Vox dictation", NotificationManager.IMPORTANCE_LOW);
        ch.setShowBadge(false);
        nm.createNotificationChannel(ch);
        restorePending();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_STOP_RECORDING.equals(intent.getAction())) {
            stopRecording();
            // A stop that reached a service which is not running as the foreground service (a stale button) has
            // nothing to stop and must not leave a background service behind.
            if (instance == null) stopSelf(startId);   // with the id: a start queued behind this one must not be killed
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_RETRY.equals(intent.getAction())) {
            retryLast();
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_CLEAR.equals(intent.getAction())) {
            clearUnsent();
            return START_NOT_STICKY;
        }
        Notification n = buildNotification();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        } else {
            startForeground(1, n);
        }
        instance = this;
        requestTileUpdate();   // an active tile is only bound on request: let it read the new state
        try {
            // Started through TrampolineActivity (a bubble tap, the "Record note" notification, the tile): record
            // right away, with no fixed delay. What lets the microphone work is the while-in-use grant this
            // microphone foreground service received at startForeground above, which needs the app to be visible
            // at that moment; the trampoline is still on screen then. This code does not depend on the order of
            // the AudioRecord capture starting (on the vox-rec thread) and the trampoline closing.
            if (intent != null && intent.getBooleanExtra(EXTRA_START, false)) {
                handleStart(intent.getStringExtra(EXTRA_PKG), intent.getStringExtra(EXTRA_LABEL),
                        intent.getStringExtra(EXTRA_DEST), intent.getLongExtra(EXTRA_TAP_AT, 0L));
            }
        } finally {
            TrampolineActivity.finishNow();   // the service is in the foreground: the trampoline is no longer needed
        }
        SyncWorker.kick(this);   // the notes may have waited for the network: sync now that the app is running (no-op when sync is off)
        return START_NOT_STICKY;
    }

    /**
     * A start request that came through the trampoline. When the service is already busy startRecording would return
     * silently: a note recording is stopped by a second "record note" (the same button), anything else says so.
     */
    private void handleStart(String pkg, String label, String dest, long tapAtMs) {
        switch (NoteLogic.startAction(state, isNoteJob(), DEST_NOTE.equals(dest))) {
            case NoteLogic.START: startRecording(pkg, label, dest, tapAtMs); break;
            case NoteLogic.STOP: stopRecording(); break;
            default: Toast.makeText(this, "Vox is busy", Toast.LENGTH_SHORT).show();
        }
    }

    private Notification buildNotification() {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent openPi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);
        Intent stop = new Intent(this, DictationService.class).setAction(ACTION_STOP);
        PendingIntent stopPi = PendingIntent.getService(this, 1, stop, PendingIntent.FLAG_IMMUTABLE);
        boolean noting = isNoteRecording();
        boolean hasPending = pending.size() > 0;
        PendingQueue.Entry kept = pending.next();
        String unsent = pending.summary() != null ? pending.summary()
                : kept != null && DEST_NOTE.equals(kept.dest) ? "Last voice note not sent" : "Last dictation not sent";
        Notification.Builder b = new Notification.Builder(this, CH)
                .setSmallIcon(R.drawable.ic_stat_mic)
                .setContentTitle(noting ? "Recording a voice note" : hasPending ? unsent : "Vox is ready")
                .setContentText(noting ? "Tap Stop when you are done"
                        : hasPending ? "Tap Retry to send it again" : "Tap the bubble in any text field to dictate")
                .setContentIntent(openPi)
                .setOngoing(true);
        if (noting) {
            Intent stopRec = new Intent(this, DictationService.class).setAction(ACTION_STOP_RECORDING);
            PendingIntent stopRecPi = PendingIntent.getService(this, 3, stopRec, PendingIntent.FLAG_IMMUTABLE);
            b.addAction(new Notification.Action.Builder(null, "Stop", stopRecPi).build());
        }
        if (hasPending) {
            Intent clear = new Intent(this, DictationService.class).setAction(ACTION_CLEAR);
            PendingIntent clearPi = PendingIntent.getService(this, 4, clear, PendingIntent.FLAG_IMMUTABLE);
            Intent retry = new Intent(this, DictationService.class).setAction(ACTION_RETRY);
            PendingIntent retryPi = PendingIntent.getService(this, 2, retry, PendingIntent.FLAG_IMMUTABLE);
            b.addAction(new Notification.Action.Builder(null, "Retry", retryPi).build());
            b.addAction(new Notification.Action.Builder(null, "Clear", clearPi).build());
        }
        b.addAction(new Notification.Action.Builder(null, "Turn off", stopPi).build());
        return b.build();
    }

    private void refreshNotification() {
        try {
            getSystemService(NotificationManager.class).notify(1, buildNotification());
        } catch (Exception ignored) { }
    }

    @Override
    public void onDestroy() {
        recording = false;
        jobId++;
        instance = null;
        worker.shutdownNow();
        // The unsent recordings stay on disk: restorePending() finds them at the next start (Clear discards them).
        // Only a recording still in progress (never queued) is lost here.
        setState(IDLE);
        requestTileUpdate();
        super.onDestroy();
    }

    public int getState() { return state; }

    /** True when the job in progress (recording or sending) is a voice note. Only meaningful while the state is not IDLE. */
    public boolean isNoteJob() { return DEST_NOTE.equals(targetDest); }

    /** True while a voice note is being recorded (not while it is being sent). */
    public boolean isNoteRecording() { return state == RECORDING && isNoteJob(); }

    static String currentDest() { DictationService s = instance; return s == null || s.state == IDLE ? null : s.targetDest; }   // "note", "dictation" or null (idle)

    // ------------------------------------------------------------ recording

    public void startRecording(String pkg, String label) {
        startRecording(pkg, label, DEST_DICTATION);
    }

    public void startRecording(String pkg, String label, String dest) {
        startRecording(pkg, label, dest, 0L);
    }

    /** @param tapAtMs SystemClock.elapsedRealtime() of the user's tap, or 0 when unknown (only used for the log) */
    public synchronized void startRecording(String pkg, String label, String dest, final long tapAtMs) {
        if (state != IDLE) return;
        Prefs p = new Prefs(this);
        String problem = Endpoint.error(p.role(Providers.STT)[0]);
        if (problem == null) problem = Endpoint.error(p.role(Providers.LLM)[0]);
        if (problem != null) {
            postError(problem);
            return;
        }
        if (p.keyMissing()) {
            postError("Add your API key in the Vox app first");
            return;
        }
        boolean note = DEST_NOTE.equals(dest);
        // A note belongs to no app: like Windows note mode (engine.py _process) it uses the default style and sends
        // no app name to the cleanup model.
        targetPkg = note ? null : pkg;
        targetLabel = note ? "" : label;
        targetDest = note ? DEST_NOTE : DEST_DICTATION;
        final String[] warmStt = p.role(Providers.STT), warmLlm = p.role(Providers.LLM);
        new Thread(() -> {   // open the server connections while the user speaks
            new ApiClient(warmStt[1], warmStt[0]).warm();
            if (!warmLlm[0].equals(warmStt[0])) new ApiClient(warmLlm[1], warmLlm[0]).warm();
        }, "vox-warm").start();
        int minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        final AudioRecord rec;
        try {
            rec = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(minBuf, SAMPLE_RATE));
        } catch (SecurityException e) {
            postError("Microphone permission missing. Open Vox and allow it.");
            return;
        }
        if (rec.getState() != AudioRecord.STATE_INITIALIZED) {
            rec.release();
            postError("Microphone unavailable (another app may be using it)");
            return;
        }
        final ByteArrayOutputStream data = new ByteArrayOutputStream();
        pcm = data;
        final int job = ++jobId;
        pending.beginRecording();   // nothing queued belongs to this recording yet: a cancel now must not touch an older unsent one
        recording = true;
        setState(RECORDING);
        recThread = new Thread(() -> {
            byte[] buf = new byte[1280]; // 40 ms: about 25 meter updates a second
            long maxBytes = (long) SAMPLE_RATE * 2 * MAX_SECONDS;
            boolean firstFrame = true;
            try {
                rec.startRecording();
                while (recording) {
                    int n = rec.read(buf, 0, buf.length);
                    if (n < 0) {
                        failRecording(job, "Recording failed (audio error " + n + ")");
                        break;
                    }
                    if (n == 0) continue;
                    if (firstFrame) {
                        firstFrame = false;
                        if (tapAtMs > 0) Log.d("vox", "tap->recording ms=" + (SystemClock.elapsedRealtime() - tapAtMs));
                    }
                    data.write(buf, 0, n);
                    postLevel(rms(buf, n));
                    if (data.size() >= maxBytes) {
                        main.post(this::stopRecording);
                        break;
                    }
                }
            } catch (Exception e) {
                failRecording(job, "Recording failed: " + e.getMessage());
            } finally {
                try { rec.stop(); } catch (Exception ignored) { }
                rec.release();
            }
        }, "vox-rec");
        recThread.start();
    }

    /** The recorder broke: go back to idle so the bubble is not stuck, and tell the user. */
    private synchronized void failRecording(int job, String message) {
        if (job != jobId || state != RECORDING) return;
        recording = false;
        setState(IDLE);
        postError(message);
    }

    /** Stops recording and sends the audio for transcription. */
    public synchronized void stopRecording() {
        if (state != RECORDING) return;
        recording = false;
        setState(PROCESSING);
        final int job = jobId;
        final Thread t = recThread;
        final ByteArrayOutputStream data = pcm;
        final String pkg = targetPkg;
        final String label = targetLabel;
        final String dest = targetDest;   // this job's own copy, like pkg and label: a later recording cannot change it
        worker.execute(() -> {
            try { if (t != null) t.join(2000); } catch (InterruptedException ignored) { }
            if (!isCurrent(job)) return;
            byte[] audio = data.toByteArray();
            if (audio.length < SAMPLE_RATE * 2 * 0.4) { // under 0.4 s
                finish(job);
                return;
            }
            if (Pcm.isSilent(audio)) {
                postError("Vox did not hear anything");
                finish(job);
                return;
            }
            PendingQueue.Entry entry = newEntry(pkg, label, dest);
            try {
                writeWav(fileOf(entry), audio);
            } catch (IOException e) {
                fileOf(entry).delete();   // a half-written file
                postError("Could not save the recording: " + e.getMessage());
                finish(job);
                return;
            }
            synchronized (DictationService.this) {   // with cancel(): either it saw this entry, or this sees the cancel
                if (!isCurrent(job)) { fileOf(entry).delete(); return; }   // cancelled while the file was being written: this recording is the one being cancelled
                pending.beginFresh(entry.id);
                enqueue(entry);
            }
            send(job, entry);
        });
    }

    /**
     * Sends the oldest recording that failed to go through (one per tap; with several kept, the notification says how
     * many and each tap sends the next one). Started from the notification's Retry button. It goes to where that
     * recording was made for (the entry kept with the file), not to wherever the latest recording pointed: a short or
     * silent recording started in between must not send an old voice note into a text field.
     */
    public synchronized void retryLast() {
        if (state != IDLE) return;
        PendingQueue.Entry kept = pending.next();
        while (kept != null && !fileOf(kept).exists()) {   // the file is gone (cleared cache): forget the entry
            pending.remove(kept.id);
            kept = pending.next();
        }
        if (kept == null) { refreshNotification(); return; }
        final PendingQueue.Entry entry = kept;
        final int job = ++jobId;
        pending.beginRetry(entry.id);
        targetPkg = entry.pkg;       // the state shown on screen (isNoteJob) follows the job being sent
        targetLabel = entry.label;
        targetDest = entry.dest;
        setState(PROCESSING);
        worker.execute(() -> send(job, entry));
    }

    /**
     * Discards the recording in progress (long-press on the bubble). Only that recording's own file goes: a fresh
     * recording that is being sent is removed, but an unsent recording kept from before is never touched (a cancel
     * during a Retry only stops that send).
     */
    public synchronized void cancel() {
        jobId++;
        recording = false;
        long id = pending.onCancel();   // the rule lives in PendingQueue: only a fresh, queued recording is discarded
        if (id != 0) discard(id);
        setState(IDLE);
    }

    /** The notification's "Clear" button: the user explicitly throws away every unsent recording. Not while a send is running. */
    private synchronized void clearUnsent() {
        if (state == PROCESSING) return;
        for (PendingQueue.Entry e : pending.clear()) fileOf(e).delete();
        refreshNotification();
    }

    private boolean isCurrent(int job) { return job == jobId; }

    /** Goes back to idle, but only for the job that is still current. */
    private synchronized void finish(int job) {
        if (job == jobId) { pending.endJob(); setState(IDLE); }
    }

    private File fileOf(PendingQueue.Entry e) { return new File(getCacheDir(), PendingQueue.fileName(e)); }

    /** A new entry whose id is the time now, made unique so two recordings never share a file. */
    private synchronized PendingQueue.Entry newEntry(String pkg, String label, String dest) {
        long id = Math.max(System.currentTimeMillis(), lastEntryId + 1);
        lastEntryId = id;
        return new PendingQueue.Entry(id, pkg, label, dest);
    }

    /** Keeps an entry for Retry. The cap drops the oldest one (file too) and says so. */
    private void enqueue(PendingQueue.Entry e) {
        boolean dropped = false;
        for (PendingQueue.Entry d : pending.add(e)) { fileOf(d).delete(); dropped = true; }
        if (dropped) main.post(() -> Toast.makeText(this, "Oldest unsent recording dropped", Toast.LENGTH_LONG).show());
        refreshNotification();
    }

    /** Removes one entry and its file: it was sent, or the user discarded it. */
    private void discard(long id) {
        PendingQueue.Entry e = pending.get(id);
        if (e != null) fileOf(e).delete();
        pending.remove(id);
        refreshNotification();
    }

    /** At service start: delete unsent recordings older than 7 days and keep the rest (files written before a restart are retried). */
    private void restorePending() {
        File[] files = getCacheDir().listFiles();
        if (files == null) return;
        for (File f : files) {
            String name = f.getName();
            if (name.equals("vox_pending.wav")) { f.delete(); continue; }   // the old single-slot file of earlier versions: its app and destination are unknown
            PendingQueue.Entry e = PendingQueue.parseFileName(name);
            if (e != null) {
                lastEntryId = Math.max(lastEntryId, e.id);
                for (PendingQueue.Entry d : pending.add(e)) fileOf(d).delete();
            }
        }
        for (PendingQueue.Entry old : pending.purgeOlder(System.currentTimeMillis())) fileOf(old).delete();
    }

    /**
     * Uploads the saved recording, with a few retries. The audio is only deleted after a success.
     * {@code dest} is the destination this recording was made for (a copy taken when it stopped): DEST_DICTATION types
     * the text through the accessibility listener, DEST_NOTE stores it as a voice note and types nothing.
     */
    private void send(int job, PendingQueue.Entry entry) {
        final String pkg = entry.pkg, label = entry.label, dest = entry.dest;
        final boolean note = DEST_NOTE.equals(dest);
        Prefs p = new Prefs(this);
        File wav = fileOf(entry);
        try {
            String[] stt = p.role(Providers.STT), llm = p.role(Providers.LLM);
            ApiClient g = new ApiClient(stt[1], stt[0]);
            ApiClient gl = new ApiClient(llm[1], llm[0]);
            String raw = null;
            for (int attempt = 1; attempt <= SEND_ATTEMPTS && raw == null; attempt++) {
                if (!isCurrent(job)) return;
                try {
                    raw = g.transcribe(wav, p.sttModel(), p.language(), p.dictionaryTerms());
                } catch (IOException e) {
                    if (!ApiClient.isRetryable(e) || attempt == SEND_ATTEMPTS) throw e;
                    try { Thread.sleep(800L * attempt); } catch (InterruptedException ie) { return; }
                }
            }
            if (!isCurrent(job)) return;
            double seconds = Math.max(0, wav.length() - 44) / (SAMPLE_RATE * 2.0);
            if (raw.isEmpty() || ApiClient.isSilenceHallucination(raw)) {
                discard(entry.id);
                if (note) postError("Vox did not hear any words, so no note was saved");
                return;
            }
            String style = p.styleFor(pkg);   // a note has no pkg (see startRecording): the default style, as on Windows
            String out = raw;
            boolean cleaned = false, cleanupFailed = false;
            boolean doClean = ApiClient.needsCleanup(raw, style, p.cleanupEnabled(), p.cleanupMinWords());
            if (doClean) {
                try {
                    String c = gl.cleanup(raw, style, p.llmModel(), p.dictionaryTerms(), label, p.userContext());
                    if (ApiClient.looksValid(raw, c)) { out = c; cleaned = true; }
                    else cleanupFailed = true;
                } catch (IOException e) {
                    // Cleanup failure should never lose the dictation. Fall back to the raw transcript.
                    cleanupFailed = true;
                }
            }
            if (!cleaned) out = ApiClient.applySpokenCommands(out);
            if (cleanupFailed) {
                postError(note ? "Cleanup did not work, so Vox saved your words as spoken"
                        : "Cleanup did not work, so Vox typed your words as spoken");
            }
            out = ApiClient.applyReplacements(out, p.replacements());
            if (!isCurrent(job)) return;
            if (note) {
                saveNote(job, entry, raw, out, seconds, p);   // a note is not typed and is not added to the dictation history
                return;
            }
            p.addHistory(label, raw, out, seconds);
            discard(entry.id);
            final String result = out;
            main.post(() -> { if (isCurrent(job) && listener != null) listener.onResult(result, pkg); });
        } catch (ApiClient.ApiException e) {
            if (!isCurrent(job)) return;
            if (e.code == 401) postError("The server rejected the API key. Fix it, then tap Retry in the notification.");
            else if (e.code == 429) postError("Rate limit reached. Tap Retry in the notification.");
            else postError(e.getMessage() + ". Your recording is kept: tap Retry in the notification.");
        } catch (IOException e) {
            if (!isCurrent(job)) return;
            postError("Network error: " + e.getMessage() + ". Your recording is kept: tap Retry in the notification.");
        } finally {
            finish(job);
        }
    }

    /**
     * The note branch of {@link #send}, the same as the note branch of windows/engine.py _process: the text after the
     * cleanup step is stored with the transcript before cleanup, the length and this phone's name; then it says so
     * ("Note saved: title"), tells the listeners, and never types anything. The recording is deleted only once the
     * note is stored, so a full disk or a damaged database keeps it for Retry.
     */
    private void saveNote(int job, PendingQueue.Entry entry, String raw, String text, double seconds, Prefs p) {
        if (NoteLogic.strip(text).isEmpty()) {   // nothing left to keep (engine.py saves only when there is text)
            discard(entry.id);
            postError("Vox did not hear any words, so no note was saved");
            return;
        }
        if (!isCurrent(job)) return;   // a cancel that came after the last check in send(): nothing is stored
        String id;
        try {
            id = NotesStore.get(this).add(text, raw, seconds, Note.SOURCE_NOTE, p.deviceName(), new ArrayList<String>(), "");
        } catch (RuntimeException e) {   // SQLiteException: disk full, database damaged
            postError("Could not save the note: " + e.getMessage() + ". Your recording is kept: tap Retry in the notification.");
            return;
        }
        // From here the note is saved: reading the title back is cosmetic and must never report a failure (Retry
        // would store the note a second time).
        String title = NoteLogic.autoTitle(NoteLogic.strip(text));
        try {
            Note saved = NotesStore.get(this).get(id);
            if (saved != null && !saved.title.isEmpty()) title = saved.title;
        } catch (RuntimeException ignored) { }
        discard(entry.id);
        NoteEvents.fireSaved();   // the relay sync listens here
        final String noteId = id, noteTitle = title;
        main.post(() -> {
            NoteEntry.postSaved(this, noteTitle);
            NoteListener nl = noteListener;
            if (nl != null) nl.onNoteSaved(noteId, noteTitle);
        });
    }

    // ------------------------------------------------------------- helpers

    private void setState(int s) {
        int was = state;
        state = s;
        // The notification has a Stop button only while a note is being recorded: show or hide it. Not after the
        // service has been destroyed (instance is cleared first), or the notification would come back.
        if (instance == this && isNoteJob() && (s == RECORDING || was == RECORDING)) refreshNotification();
        if (isNoteJob() && (s == RECORDING || was == RECORDING)) requestTileUpdate();
        main.post(() -> { if (listener != null) listener.onState(s); });
    }

    /** Asks the system to call NoteTileService.onStartListening again (API 24+). Works because the tile declares ACTIVE_TILE in the manifest. */
    private void requestTileUpdate() {
        try {
            android.service.quicksettings.TileService.requestListeningState(this, new ComponentName(this, NoteTileService.class));
        } catch (RuntimeException ignored) { }   // the tile is not added, or the system refused
    }

    private void postLevel(float l) { main.post(() -> { if (listener != null) listener.onLevel(l); }); }

    private void postError(String m) {
        main.post(() -> {
            if (listener != null) listener.onError(m);
            // No accessibility service (a note started from the tile or the notification): nobody else would say why nothing happened.
            else Toast.makeText(this, m, Toast.LENGTH_LONG).show();
        });
    }

    private static float rms(byte[] b, int n) {
        long sum = 0;
        int samples = n / 2;
        for (int i = 0; i + 1 < n; i += 2) {
            short v = (short) ((b[i] & 0xff) | (b[i + 1] << 8));
            sum += (long) v * v;
        }
        double r = Math.sqrt(sum / (double) Math.max(1, samples)) / 32768.0;
        return (float) Pcm.levelFromRms(r);
    }

    static void writeWav(File f, byte[] pcm) throws IOException {
        int byteRate = SAMPLE_RATE * 2;
        try (FileOutputStream o = new FileOutputStream(f)) {
            o.write(new byte[]{'R', 'I', 'F', 'F'});
            le32(o, 36 + pcm.length);
            o.write(new byte[]{'W', 'A', 'V', 'E', 'f', 'm', 't', ' '});
            le32(o, 16);
            le16(o, 1);            // PCM
            le16(o, 1);            // mono
            le32(o, SAMPLE_RATE);
            le32(o, byteRate);
            le16(o, 2);            // block align
            le16(o, 16);           // bits per sample
            o.write(new byte[]{'d', 'a', 't', 'a'});
            le32(o, pcm.length);
            o.write(pcm);
        }
    }

    private static void le32(FileOutputStream o, int v) throws IOException {
        o.write(v & 0xff); o.write((v >> 8) & 0xff); o.write((v >> 16) & 0xff); o.write((v >> 24) & 0xff);
    }

    private static void le16(FileOutputStream o, int v) throws IOException {
        o.write(v & 0xff); o.write((v >> 8) & 0xff);
    }
}
