package com.minhaj.vox;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
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
import java.util.List;
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
    /** A vox-up-* temp upload file older than this belongs to a send that was killed (see PendingQueue.sweepUploads). */
    private static final long UPLOAD_MAX_AGE_MS = 10 * 60 * 1000L;

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
    /**
     * Sends the recording in pieces while it goes on (see StreamingStt); null when none. Set in startRecording, kept until
     * the job ends so cancel() can stop it, and handed to the worker (a local copy) by stopRecording.
     */
    private volatile StreamingStt stream;
    /** The clients of the send in progress, so cancel() can cut its HTTP request instead of leaving the worker stuck in it. */
    private volatile ApiClient[] liveClients;
    /** Longest wait for the pieces still being sent after the user stops, before the whole recording is sent instead. */
    private static final long STREAM_WAIT_MS = 120000;
    /** The saved microphone choice that the "not connected" notice was already shown for (see MicChoice.shouldWarn). */
    private static volatile String micWarnedFor;
    /** AudioRecord.getMinBufferSize never changes on a device: asked once, off the main thread. */
    private static volatile int minBuf;
    private static long lastWarm;
    /** Where the time of the recording in progress goes (the Speed card); null when none. Set in startRecording, read by stopRecording. */
    private volatile Timing timing;
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
        // Listing the cache folder is not needed to start recording: do it on the worker, then show what was found.
        worker.execute(() -> {
            restorePending();
            main.post(this::refreshNotification);
        });
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
            // A Retry that reached a service which is not running as the foreground service (a stale button) sends
            // nothing: a send here would never stop the service.
            if (NotificationActions.retryIsStale(instance == this)) {
                stopSelf(startId);
                return START_NOT_STICKY;
            }
            retryLast();
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_CLEAR.equals(intent.getAction())) {
            clearUnsent();
            // A Clear that reached a service which is not running as the foreground service (a stale button) has nothing
            // to keep alive: with nothing recording or queued it must not leave a background service behind.
            if (instance == null && state == IDLE && pending.size() == 0) stopSelf(startId);
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
    /** True while a recording that failed to go through is kept for Retry (Home shows it as the last dictation outcome). */
    boolean hasUnsent() { return pending.size() > 0; }

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
                        : hasPending ? NotificationActions.retryHint(pending.size(), pending.stuck()) : "Tap the bubble in any text field to dictate")
                .setContentIntent(openPi)
                .setOngoing(true);
        // At most three buttons (Android shows no more): NotificationActions decides which.
        for (String a : NotificationActions.choose(noting, pending.size())) {
            if (NotificationActions.STOP.equals(a)) {
                Intent stopRec = new Intent(this, DictationService.class).setAction(ACTION_STOP_RECORDING);
                PendingIntent stopRecPi = PendingIntent.getService(this, 3, stopRec, PendingIntent.FLAG_IMMUTABLE);
                b.addAction(new Notification.Action.Builder(null, a, stopRecPi).build());
            } else if (NotificationActions.RETRY.equals(a)) {
                Intent retry = new Intent(this, DictationService.class).setAction(ACTION_RETRY);
                PendingIntent retryPi = PendingIntent.getService(this, 2, retry, PendingIntent.FLAG_IMMUTABLE);
                b.addAction(new Notification.Action.Builder(null, a, retryPi).build());
            } else if (NotificationActions.CLEAR.equals(a)) {
                Intent clear = new Intent(this, DictationService.class).setAction(ACTION_CLEAR);
                PendingIntent clearPi = PendingIntent.getService(this, 4, clear, PendingIntent.FLAG_IMMUTABLE);
                b.addAction(new Notification.Action.Builder(null, a, clearPi).build());
            } else {
                b.addAction(new Notification.Action.Builder(null, a, stopPi).build());
            }
        }
        return b.build();
    }

    private void refreshNotification() {
        if (instance != this) return;   // not the foreground service (turned off, or a stale button): never repost the ongoing notification
        try {
            getSystemService(NotificationManager.class).notify(1, buildNotification());
        } catch (Exception ignored) { }
    }

    @Override
    public void onDestroy() {
        recording = false;
        jobId++;
        instance = null;
        dropStream();
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
        // Set before the early error returns below: onError picks the bubble from targetDest (noteJob()), so an
        // error for this request must not flash the previous job's bubble.
        targetDest = DEST_NOTE.equals(dest) ? DEST_NOTE : DEST_DICTATION;
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
        warm(this);   // open the server connections while the user speaks (a no-op when the bubble touch just did it)
        // Nothing slow happens on the main thread from here to the microphone: the AudioRecord is made and started on the
        // recording thread (a refusal comes back through failRecording), and the pieces go out from their own thread.
        final StreamingStt streamer = newStream(p);
        stream = streamer;
        final String micKey = p.micDevice();   // the microphone chosen in Settings ("" = the phone's default)
        final ByteArrayOutputStream data = new ByteArrayOutputStream();
        pcm = data;
        final Timing tm = new Timing(new Timing.Clock() {
            @Override
            public long nowMs() { return SystemClock.elapsedRealtime(); }
        });
        tm.mark("key_down", tapAtMs > 0 ? tapAtMs : SystemClock.elapsedRealtime());   // the tap when it is known, else now
        timing = tm;
        final int job = ++jobId;
        pending.beginRecording();   // nothing queued belongs to this recording yet: a cancel now must not touch an older unsent one
        recording = true;
        setState(RECORDING);
        recThread = new Thread(() -> {
            byte[] buf = new byte[1280]; // 40 ms: about 25 meter updates a second
            long maxBytes = (long) SAMPLE_RATE * 2 * MAX_SECONDS;
            boolean firstFrame = true;
            AudioRecord rec = null;
            try {
                if (minBuf == 0) minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                rec = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, SAMPLE_RATE,
                        AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(minBuf, SAMPLE_RATE));
                if (rec.getState() != AudioRecord.STATE_INITIALIZED) {
                    failRecording(job, "Microphone unavailable (another app may be using it)");
                    return;
                }
                preferMic(rec, micKey);
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
                        tm.mark("rec_start");   // the first audio really arrived
                        if (tapAtMs > 0) Log.d("vox", "tap->recording ms=" + (SystemClock.elapsedRealtime() - tapAtMs));
                    }
                    data.write(buf, 0, n);
                    if (streamer != null) streamer.feed(buf, 0, n);   // a copy and a queue put: the pieces are cut and sent elsewhere
                    postLevel(rms(buf, n));
                    if (data.size() >= maxBytes) {
                        main.post(this::stopRecording);
                        break;
                    }
                }
            } catch (SecurityException e) {
                failRecording(job, "Microphone permission missing. Open Vox and allow it.");
            } catch (Exception e) {
                failRecording(job, "Recording failed: " + e.getMessage());
            } finally {
                if (rec != null) {
                    try { rec.stop(); } catch (Exception ignored) { }
                    rec.release();
                }
            }
        }, "vox-rec");
        recThread.start();
    }

    /** The recorder broke: go back to idle so the bubble is not stuck, and tell the user. */
    private synchronized void failRecording(int job, String message) {
        if (job != jobId || state != RECORDING) return;
        recording = false;
        dropStream();
        setState(IDLE);
        postError(message);
    }

    /** Stops the pieces being sent for the recording in progress (a cancel, a failed recorder, the service going away). */
    private void dropStream() {
        StreamingStt s = stream;
        stream = null;
        if (s != null) s.cancel();
    }

    /** The pieces-while-recording sender for the recording that is starting: the server and settings it will use are fixed now. */
    private StreamingStt newStream(final Prefs p) {
        final File dir = getCacheDir();
        // The settings are read on the sending thread, with the piece: nothing is parsed on the main thread.
        StreamingStt s = new StreamingStt((piece, context) -> {
            String[] stt = p.role(Providers.STT);
            List<String> terms = p.dictionaryTerms();
            ApiClient api = new ApiClient(stt[1], stt[0]);
            ApiClient.Upload up = AudioUpload.fromPcm(dir, piece, api.m4aAllowed());
            try {
                return api.transcribe(up, p.sttModel(), p.language(), terms, context);
            } finally {
                up.release();
            }
        }, new Segmenter());
        s.start();
        return s;
    }

    /**
     * Opens the connections to the speech and cleanup servers in the background (the TLS handshake included), so the upload
     * does not wait for them. Called when the bubble is touched and again when recording starts; a call within
     * {@link Latency#WARM_GAP_MS} of the last one does nothing. Needs no service instance, so it also runs before the
     * service is up.
     */
    static void warm(android.content.Context ctx) {
        final long now = SystemClock.elapsedRealtime();
        synchronized (DictationService.class) {
            if (!Latency.shouldWarm(lastWarm, now)) return;
            lastWarm = now;
        }
        final android.content.Context app = ctx.getApplicationContext();
        new Thread(() -> {
            Prefs p = new Prefs(app);
            if (p.keyMissing()) return;
            final String[] stt = p.role(Providers.STT), llm = p.role(Providers.LLM);
            if (!llm[0].equals(stt[0])) {
                new Thread(() -> new ApiClient(llm[1], llm[0]).warm(), "vox-warm-llm").start();
            }
            new ApiClient(stt[1], stt[0]).warm();
        }, "vox-warm").start();
    }

    /** Stops recording and sends the audio for transcription. */
    public synchronized void stopRecording() {
        if (state != RECORDING) return;
        recording = false;
        final Timing tm = timing;
        timing = null;
        if (tm != null) tm.mark("key_up");
        setState(PROCESSING);
        final int job = jobId;
        final Thread t = recThread;
        final ByteArrayOutputStream data = pcm;
        final String pkg = targetPkg;
        final String label = targetLabel;
        final String dest = targetDest;   // this job's own copy, like pkg and label: a later recording cannot change it
        final StreamingStt streamer = stream;
        worker.execute(() -> {
            try { if (t != null) t.join(2000); } catch (InterruptedException ignored) { }
            // A recording thread that is still running would go on feeding the pieces after the last one is cut: then the
            // whole recording is sent instead.
            final boolean streamUsable = t == null || !t.isAlive();
            if (!streamUsable && streamer != null) streamer.cancel();
            if (!isCurrent(job)) { if (streamer != null) streamer.cancel(); return; }
            byte[] audio = data.toByteArray();
            if (audio.length < SAMPLE_RATE * 2 * 0.4) { // under 0.4 s
                if (streamer != null) streamer.cancel();
                finish(job);
                return;
            }
            if (Pcm.isSilent(audio)) {
                if (streamer != null) streamer.cancel();
                postError("Vox did not hear anything");
                finish(job);
                return;
            }
            PendingQueue.Entry entry = newEntry(pkg, label, dest);
            try {
                writeWav(fileOf(entry), audio);
            } catch (IOException e) {
                fileOf(entry).delete();   // a half-written file
                if (streamer != null) streamer.cancel();
                postError("Could not save the recording: " + e.getMessage());
                finish(job);
                return;
            }
            synchronized (DictationService.this) {   // with cancel(): either it saw this entry, or this sees the cancel
                if (!isCurrent(job)) { fileOf(entry).delete(); if (streamer != null) streamer.cancel(); return; }   // cancelled while the file was being written: this recording is the one being cancelled
                pending.beginFresh(entry.id);
                enqueue(entry);
            }
            send(job, entry, tm, streamUsable ? streamer : null);
        });
    }

    /**
     * Sends the oldest recording that failed to go through (one per tap; with several kept, the notification says how
     * many). A Retry that fails moves its recording to the back, so the next tap tries the next one; after
     * {@link PendingQueue#MAX_RETRIES} failed retries a recording is parked (the notification says how many are stuck,
     * Clear removes them). Started from the notification's Retry button. It goes to where that
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
        if (kept == null) {
            int stuck = pending.stuck();
            if (stuck > 0) main.post(() -> Toast.makeText(this, stuck + (stuck == 1 ? " recording is" : " recordings are")
                    + " stuck after " + PendingQueue.MAX_RETRIES + " tries. Tap Clear to remove.", Toast.LENGTH_LONG).show());
            refreshNotification();
            return;
        }
        final PendingQueue.Entry entry = kept;
        final int job = ++jobId;
        pending.beginRetry(entry.id);
        targetPkg = entry.pkg;       // the state shown on screen (isNoteJob) follows the job being sent
        targetLabel = entry.label;
        targetDest = entry.dest;
        setState(PROCESSING);
        worker.execute(() -> send(job, entry, null, null));   // a retry has no key or recording marks (it is not timed) and no pieces
    }

    /**
     * Discards the recording in progress (long-press on the bubble). Only that recording's own file goes: a fresh
     * recording that is being sent is removed, but an unsent recording kept from before is never touched (a cancel
     * during a Retry only stops that send).
     */
    public synchronized void cancel() {
        jobId++;
        final ApiClient[] live = liveClients;
        liveClients = null;
        if (live != null) new Thread(() -> { for (ApiClient a : live) a.abort(); }, "vox-abort").start();   // off the main thread: disconnect closes a socket
        recording = false;
        timing = null;
        dropStream();
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

    /** A send of this entry failed: when it was a Retry, the entry goes behind the others (and is parked after the third failure). */
    private void retryFailed(PendingQueue.Entry entry) {
        if (pending.onSendFailed(entry.id)) refreshNotification();
    }

    /** The input devices Android reports now, as the pure MicChoice sees them (no permission is needed to list them). */
    static List<MicChoice.Candidate> micCandidates(android.content.Context c) {
        List<MicChoice.Candidate> out = new ArrayList<>();
        AudioManager am = (AudioManager) c.getSystemService(AUDIO_SERVICE);
        if (am == null) return out;
        for (AudioDeviceInfo d : am.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
            out.add(new MicChoice.Candidate(d.getType(), String.valueOf(d.getProductName()), d));
        }
        return out;
    }

    /**
     * Points the recorder at the microphone chosen in Settings when it is connected; otherwise the phone's default stays, and
     * a saved choice that is not connected says so once (a toast), then not again until another choice is saved.
     */
    private void preferMic(AudioRecord rec, String micKey) {
        micKey = MicChoice.usable(micKey);   // a saved Bluetooth choice is the default now, with no notice
        if (micKey.isEmpty()) { micWarnedFor = null; return; }
        MicChoice.Candidate c = MicChoice.pick(micKey, micCandidates(this));
        if (c != null) {
            micWarnedFor = null;
            try { rec.setPreferredDevice((AudioDeviceInfo) c.ref); } catch (RuntimeException e) { Log.w("vox", "preferred microphone refused"); }
        } else if (MicChoice.shouldWarn(micKey, micWarnedFor)) {
            micWarnedFor = micKey;
            main.post(() -> Toast.makeText(this, MicChoice.NOT_CONNECTED, Toast.LENGTH_LONG).show());
        }
    }

    /** Goes back to idle, but only for the job that is still current. */
    private synchronized void finish(int job) {
        if (job == jobId) { stream = null; pending.endJob(); setState(IDLE); }
    }

    /** Where the unsent recordings live: the app's own folder, which Android does not empty when it trims the cache or the user clears it (and which is left out of backups). */
    private File pendingDir() {
        File d = new File(getNoBackupFilesDir(), "pending");
        d.mkdirs();
        return d;
    }

    private File fileOf(PendingQueue.Entry e) { return new File(pendingDir(), PendingQueue.fileName(e)); }

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
        PendingQueue.migrate(getCacheDir(), pendingDir());   // earlier versions kept them in the cache folder
        PendingQueue.sweepUploads(getCacheDir(), System.currentTimeMillis(), UPLOAD_MAX_AGE_MS);   // temp upload files a kill left behind
        File[] files = pendingDir().listFiles();
        if (files == null) return;
        for (File f : files) {
            String name = f.getName();
            PendingQueue.Entry e = name.equals("vox_pending.wav") ? migrateOldSlot(f) : PendingQueue.parseFileName(name);
            if (e != null) {
                lastEntryId = Math.max(lastEntryId, e.id);
                for (PendingQueue.Entry d : pending.add(e)) fileOf(d).delete();
            }
        }
        for (PendingQueue.Entry old : pending.purgeOlder(System.currentTimeMillis())) fileOf(old).delete();
    }

    /**
     * The old single-slot file of earlier versions ({@code vox_pending.wav}): the user was told it was kept, so it moves
     * into the queue as one entry. Its app and destination are unknown, so it is a restored dictation (pkg "": the text
     * is copied to the clipboard, never typed). Its id is its modified time, made unique. Returns null (the file is
     * left where it is, to be tried again at the next start) when it cannot be renamed.
     */
    private PendingQueue.Entry migrateOldSlot(File old) {
        long id = Math.max(old.lastModified() > 0 ? old.lastModified() : System.currentTimeMillis(), lastEntryId + 1);
        PendingQueue.Entry e = new PendingQueue.Entry(id, "", "", DEST_DICTATION);
        return old.renameTo(fileOf(e)) ? e : null;
    }

    /**
     * Uploads the saved recording, with a few retries. The audio is only deleted after a success.
     * {@code dest} is the destination this recording was made for (a copy taken when it stopped): DEST_DICTATION types
     * the text through the accessibility listener, DEST_NOTE stores it as a voice note and types nothing.
     */
    private void send(int job, PendingQueue.Entry entry, Timing tm, StreamingStt streamer) {
        final String pkg = entry.pkg, label = entry.label, dest = entry.dest;
        final boolean note = DEST_NOTE.equals(dest);
        Prefs p = new Prefs(this);
        File wav = fileOf(entry);
        try {
            String[] stt = p.role(Providers.STT), llm = p.role(Providers.LLM);
            ApiClient g = new ApiClient(stt[1], stt[0]);
            ApiClient gl = new ApiClient(llm[1], llm[0]);
            final ApiClient[] mine = new ApiClient[]{g, gl};
            liveClients = mine;
            String raw = null;
            if (tm != null) tm.mark("stt_start");
            if (streamer != null) {
                // The pieces were sent while the user spoke: only the last one is still to come. null means nothing was cut
                // or something failed; an empty text is checked again on the whole recording.
                String streamed = streamer.finish(STREAM_WAIT_MS);
                if (streamed != null && !streamed.isEmpty()) raw = streamed;
            }
            if (raw == null && isCurrent(job)) {
                // The clip goes up as m4a from 4 s (a quarter of the size), as the WAV itself when it is short or the
                // encoder fails, or the server refused an m4a before (then ApiClient also resends a refused m4a as WAV).
                // One encoding serves every attempt.
                ApiClient.Upload up = AudioUpload.fromWavFile(getCacheDir(), wav, g.m4aAllowed());
                try {
                    for (int attempt = 1; attempt <= SEND_ATTEMPTS && raw == null; attempt++) {
                        if (!isCurrent(job)) return;
                        try {
                            raw = g.transcribe(up, p.sttModel(), p.language(), p.dictionaryTerms(), "");
                        } catch (IOException e) {
                            // A connection that could not be opened was already tried twice (ApiClient): do not wait for a third.
                            if (!ApiClient.isRetryable(e, p.usesRelay()) || Latency.isConnectFailure(e) || attempt == SEND_ATTEMPTS) throw e;
                            try { Thread.sleep(800L * attempt); } catch (InterruptedException ie) { return; }
                        }
                    }
                } finally {
                    up.release();
                }
            }
            if (tm != null) tm.mark("stt_done");
            if (!isCurrent(job)) return;
            double seconds = Math.max(0, wav.length() - 44) / (SAMPLE_RATE * 2.0);
            if (raw.isEmpty() || ApiClient.isSilenceHallucination(raw)) {
                discard(entry.id);
                if (note) postError("Vox did not hear any words, so no note was saved");
                return;
            }
            String style = p.styleFor(pkg);   // a note has no pkg (see startRecording): the default style, as on Windows
            String out = raw;
            boolean cleaned = false, cleanupFailed = false, rejected = false;
            boolean doClean = ApiClient.needsCleanup(raw, style, p.cleanupEnabled(), p.cleanupMinWords());
            if (doClean) {
                if (tm != null) tm.mark("llm_start");
                try {
                    String strength = p.cleanupStrength();   // the prompt and the guard use the same value
                    String c = gl.cleanup(raw, style, p.llmModel(), p.dictionaryTerms(), label, p.userContext(), strength, p.myCleanupRules());
                    if (ApiClient.looksValid(raw, c, strength)) { out = c; cleaned = true; }
                    else { cleanupFailed = rejected = true; Log.w("vox", "fidelity guard: the cleanup answer lost the spoken words, used the raw words"); }
                } catch (IOException e) {
                    // Cleanup failure should never lose the dictation. Fall back to the raw transcript.
                    cleanupFailed = true;
                } finally {
                    if (tm != null) tm.mark("llm_done");
                }
            }
            if (!cleaned) out = rejected ? ApiClient.fallbackText(out) : ApiClient.applySpokenCommands(out);
            if (cleanupFailed) {
                postError(note ? "Cleanup did not work, so Vox saved your words as spoken"
                        : "Cleanup did not work, so Vox typed your words as spoken");
            }
            out = Terms.fuzzy(ApiClient.applyReplacements(out, p.replacements()), p.dictionaryTerms());   // as Windows: replacements, then the dictionary's spellings
            if (!isCurrent(job)) return;
            if (note) {
                saveNote(job, entry, raw, out, seconds, p);   // a note is not typed and is not added to the dictation history
                return;
            }
            final String result = out, rawText = raw;
            final boolean fellBack = rejected;
            final double secs = seconds;
            // The text goes to the screen first; the recording file is removed after (a notification update that
            // would otherwise sit between the text and the screen).
            main.post(() -> {
                int route = InsertGuard.route(isCurrent(job), listener != null);
                if (route == InsertGuard.ROUTE_TYPE) {
                    listener.onResult(result, pkg);
                } else if (route == InsertGuard.ROUTE_CLIPBOARD) {   // nothing can type it (accessibility is off): the text is not lost
                    ((ClipboardManager) getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("Vox", result));
                    Toast.makeText(this, InsertGuard.noListenerMessage(), Toast.LENGTH_LONG).show();
                }
                // The history entry is written after the text went in, so its timing includes the insertion; the
                // write is off the main thread (the history is a JSON list in the preferences).
                final Timing.Entry te;
                if (tm != null) {
                    tm.mark("inserted");
                    te = tm.entry(p.sttModel(), p.llmModel(), p.usesRelay() ? "relay" : p.provider(), p.usesRelay());
                } else {
                    te = null;
                }
                new Thread(() -> p.addHistory(label, rawText, result, secs, te, fellBack), "vox-history").start();
            });
            discard(entry.id);
        } catch (ApiClient.ApiException e) {
            if (!isCurrent(job)) return;
            retryFailed(entry);
            if ((e.code == 401 || e.code == 403) && p.usesRelay()) postError("The relay or the AI server behind it refused the request (" + Providers.RELAY_HINT + "). Then tap Retry in the notification.");
            else if (e.code == 401) postError("The server rejected the API key. Fix it, then tap Retry in the notification.");
            else if (e.code == 429) postError("Rate limit reached. Tap Retry in the notification.");
            else postError(e.getMessage() + ". Your recording is kept: tap Retry in the notification.");
        } catch (IOException e) {
            if (!isCurrent(job)) return;
            retryFailed(entry);
            postError("Network error:" + e.getMessage() + ". Your recording is kept: tap Retry in the notification.");
        } finally {
            if (isCurrent(job)) liveClients = null;
            if (streamer != null) streamer.cancel();   // ends its thread on every way out (a no-op once it has finished)
            PendingQueue.sweepUploads(getCacheDir(), System.currentTimeMillis(), UPLOAD_MAX_AGE_MS);   // temp upload files an earlier kill left
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
            retryFailed(entry);
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
