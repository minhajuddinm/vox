package com.minhaj.vox;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Foreground service of type "microphone". It must be started from a visible activity
 * (Android blocks background mic access), then it keeps running so the bubble can record any time.
 */
public class DictationService extends Service {
    public static final int SAMPLE_RATE = 16000;
    private static final int MAX_SECONDS = 360;
    private static final String CH = "vox_service";
    private static final String ACTION_STOP = "com.minhaj.vox.STOP";
    private static final String ACTION_RETRY = "com.minhaj.vox.RETRY";
    private static final int SEND_ATTEMPTS = 3;

    public static final int IDLE = 0, RECORDING = 1, PROCESSING = 2;

    public interface Listener {
        void onState(int s);
        void onLevel(float level);          // 0..1 while recording
        void onResult(String text, String targetPkg);
        void onError(String message);
    }

    public static volatile DictationService instance;
    private static Listener listener;

    public static void setListener(Listener l) { listener = l; }

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
    private volatile boolean hasPending;

    @Override public IBinder onBind(Intent i) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel ch = new NotificationChannel(CH, "Vox dictation", NotificationManager.IMPORTANCE_MIN);
        ch.setShowBadge(false);
        nm.createNotificationChannel(ch);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_RETRY.equals(intent.getAction())) {
            retryLast();
            return START_NOT_STICKY;
        }
        Notification n = buildNotification();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        } else {
            startForeground(1, n);
        }
        instance = this;
        VoxAccessibilityService a = VoxAccessibilityService.instance;
        if (a != null) a.onDictationServiceReady();
        return START_NOT_STICKY;
    }

    private Notification buildNotification() {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent openPi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);
        Intent stop = new Intent(this, DictationService.class).setAction(ACTION_STOP);
        PendingIntent stopPi = PendingIntent.getService(this, 1, stop, PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = new Notification.Builder(this, CH)
                .setSmallIcon(R.drawable.ic_stat_mic)
                .setContentTitle(hasPending ? "Last dictation not sent" : "Vox is ready")
                .setContentText(hasPending ? "Tap Retry to send it again" : "Tap the bubble in any text field to dictate")
                .setContentIntent(openPi)
                .setOngoing(true);
        if (hasPending) {
            Intent retry = new Intent(this, DictationService.class).setAction(ACTION_RETRY);
            PendingIntent retryPi = PendingIntent.getService(this, 2, retry, PendingIntent.FLAG_IMMUTABLE);
            b.addAction(new Notification.Action.Builder(null, "Retry", retryPi).build());
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
        pendingFile().delete();
        hasPending = false;
        setState(IDLE);
        super.onDestroy();
    }

    public int getState() { return state; }

    // ------------------------------------------------------------ recording

    public synchronized void startRecording(String pkg, String label) {
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
        targetPkg = pkg;
        targetLabel = label;
        final String[] warmStt = p.role(Providers.STT), warmLlm = p.role(Providers.LLM);
        new Thread(() -> {   // open the server connections while the user speaks
            new GroqClient(warmStt[1], warmStt[0]).warm();
            if (!warmLlm[0].equals(warmStt[0])) new GroqClient(warmLlm[1], warmLlm[0]).warm();
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
        recording = true;
        setState(RECORDING);
        recThread = new Thread(() -> {
            byte[] buf = new byte[1280]; // 40 ms: about 25 meter updates a second
            long maxBytes = (long) SAMPLE_RATE * 2 * MAX_SECONDS;
            try {
                rec.startRecording();
                while (recording) {
                    int n = rec.read(buf, 0, buf.length);
                    if (n < 0) {
                        failRecording(job, "Recording failed (audio error " + n + ")");
                        break;
                    }
                    if (n == 0) continue;
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
            try {
                writeWav(pendingFile(), audio);
            } catch (IOException e) {
                postError("Could not save the recording: " + e.getMessage());
                finish(job);
                return;
            }
            setPending(true);
            send(job, pkg, label);
        });
    }

    /** Sends the last recording that failed to go through. Started from the notification's Retry button. */
    public synchronized void retryLast() {
        if (state != IDLE || !hasPending || !pendingFile().exists()) return;
        final int job = ++jobId;
        final String pkg = targetPkg;
        final String label = targetLabel;
        setState(PROCESSING);
        worker.execute(() -> send(job, pkg, label));
    }

    /** Discards the current recording. */
    public synchronized void cancel() {
        jobId++;
        recording = false;
        pendingFile().delete();
        setPending(false);
        setState(IDLE);
    }

    private boolean isCurrent(int job) { return job == jobId; }

    /** Goes back to idle, but only for the job that is still current. */
    private synchronized void finish(int job) {
        if (job == jobId) setState(IDLE);
    }

    private File pendingFile() { return new File(getCacheDir(), "vox_pending.wav"); }

    private void setPending(boolean on) {
        if (hasPending == on) return;
        hasPending = on;
        refreshNotification();
    }

    /** Uploads the saved recording, with a few retries. The audio is only deleted after a success. */
    private void send(int job, String pkg, String label) {
        Prefs p = new Prefs(this);
        File wav = pendingFile();
        try {
            String[] stt = p.role(Providers.STT), llm = p.role(Providers.LLM);
            GroqClient g = new GroqClient(stt[1], stt[0]);
            GroqClient gl = new GroqClient(llm[1], llm[0]);
            String raw = null;
            for (int attempt = 1; attempt <= SEND_ATTEMPTS && raw == null; attempt++) {
                if (!isCurrent(job)) return;
                try {
                    raw = g.transcribe(wav, p.sttModel(), p.language(), p.dictionaryTerms());
                } catch (IOException e) {
                    if (!GroqClient.isRetryable(e) || attempt == SEND_ATTEMPTS) throw e;
                    try { Thread.sleep(800L * attempt); } catch (InterruptedException ie) { return; }
                }
            }
            if (!isCurrent(job)) return;
            double seconds = Math.max(0, wav.length() - 44) / (SAMPLE_RATE * 2.0);
            if (raw.isEmpty() || GroqClient.isSilenceHallucination(raw)) {
                wav.delete();
                setPending(false);
                return;
            }
            String style = p.styleFor(pkg);
            String out = raw;
            boolean cleaned = false, cleanupFailed = false;
            boolean doClean = p.cleanupEnabled() && !"raw".equals(style) && raw.split("\\s+").length >= 3;
            if (doClean) {
                try {
                    String c = gl.cleanup(raw, style, p.llmModel(), p.dictionaryTerms(), label, p.userContext());
                    if (GroqClient.looksValid(raw, c)) { out = c; cleaned = true; }
                    else cleanupFailed = true;
                } catch (IOException e) {
                    // Cleanup failure should never lose the dictation. Fall back to the raw transcript.
                    cleanupFailed = true;
                }
            }
            if (!cleaned) out = GroqClient.applySpokenCommands(out);
            if (cleanupFailed) postError("Cleanup did not work, so Vox typed your words as spoken");
            out = GroqClient.applyReplacements(out, p.replacements());
            if (!isCurrent(job)) return;
            p.addHistory(label, raw, out, seconds);
            wav.delete();
            setPending(false);
            final String result = out;
            main.post(() -> { if (isCurrent(job) && listener != null) listener.onResult(result, pkg); });
        } catch (GroqClient.ApiException e) {
            if (!isCurrent(job)) return;
            if (e.code == 401) postError("The server rejected the API key. Fix it, then tap Retry in the notification.");
            else if (e.code == 429) postError("Rate limit reached. Tap Retry in the notification.");
            else postError(e.getMessage() + ". Your recording is saved: tap Retry in the notification.");
        } catch (IOException e) {
            if (!isCurrent(job)) return;
            postError("Network error: " + e.getMessage() + ". Your recording is saved: tap Retry in the notification.");
        } finally {
            finish(job);
        }
    }

    // ------------------------------------------------------------- helpers

    private void setState(int s) {
        state = s;
        main.post(() -> { if (listener != null) listener.onState(s); });
    }

    private void postLevel(float l) { main.post(() -> { if (listener != null) listener.onLevel(l); }); }

    private void postError(String m) { main.post(() -> { if (listener != null) listener.onError(m); }); }

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
