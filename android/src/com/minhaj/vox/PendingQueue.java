package com.minhaj.vox;

import java.util.ArrayList;
import java.util.List;

/**
 * The unsent recordings of DictationService, as pure logic (no android.*, no files): the service wires it to the
 * files in the cache folder. Each failed recording is one Entry with its own id and its own file
 * ({@link #fileName}); a new recording never replaces an unsent one. Kept oldest first. At most {@link #MAX_KEPT}
 * are kept (adding one more drops the oldest; a fresh recording is only counted once its send failed, see
 * {@link #addFresh}) and {@link #purgeOlder} drops the ones older than {@link #MAX_AGE_MS}.
 * The id of an entry is the time it was made (milliseconds since 1970, made unique by the caller), so the age needs
 * no other field and the order survives a restart of the service. All methods are synchronized: the worker thread
 * and the main thread both use it.
 */
public final class PendingQueue {
    public static final int MAX_KEPT = 5;
    public static final long MAX_AGE_MS = 7L * 24 * 60 * 60 * 1000;

    /** A failed Retry moves its entry behind the others; after this many failed retries it is parked (Retry skips it). */
    public static final int MAX_RETRIES = 3;

    /**
     * One kept recording: where it goes (pkg, label, dest) and when it was made (id). Only the retry-failure counter
     * changes, and only through the synchronized methods of {@link PendingQueue}.
     */
    public static final class Entry {
        public final long id;
        public final String pkg, label, dest;
        int failures;   // failed Retry sends so far (the first send of a fresh recording is not counted)
        public Entry(long id, String pkg, String label, String dest) {
            this.id = id; this.pkg = pkg; this.label = label; this.dest = dest;
        }
    }

    private final List<Entry> items = new ArrayList<>();

    /** Adds an entry as the newest. Returns the entries pushed out because more than MAX_KEPT would be kept (oldest first). */
    public synchronized List<Entry> add(Entry e) {
        addFresh(e);
        return trim();
    }

    /**
     * Adds a fresh recording that is about to be sent, without the cap: a send that works must not cost the oldest kept
     * recording. When its send fails, {@link #trim} applies the cap (the caller does that after {@link #onSendFailed}).
     */
    public synchronized void addFresh(Entry e) {
        int at = items.size();
        while (at > 0 && items.get(at - 1).id > e.id) at--;   // keeps oldest-first order when a restored entry arrives late
        items.add(at, e);
    }

    /** Applies the cap: removes and returns the entries over MAX_KEPT, oldest first. */
    public synchronized List<Entry> trim() {
        List<Entry> dropped = new ArrayList<>();
        while (items.size() > MAX_KEPT) {   // the oldest by age (lowest id), which after a rotation is not always the first in line
            int oldest = 0;
            for (int i = 1; i < items.size(); i++) if (items.get(i).id < items.get(oldest).id) oldest = i;
            dropped.add(items.remove(oldest));
        }
        return dropped;
    }

    /**
     * The entry Retry sends next: the first in line that is not parked, or null when nothing is kept or every entry is
     * parked. Does not remove it: only a success does. A failed retry moves its entry to the back ({@link #onSendFailed}).
     */
    public synchronized Entry next() {
        for (Entry e : items) if (e.failures < MAX_RETRIES) return e;
        return null;
    }

    /** Failed retries of this entry so far (0 when unknown). */
    public synchronized int failures(long id) {
        Entry e = get(id);
        return e == null ? 0 : e.failures;
    }

    /** How many kept entries are parked (failed {@link #MAX_RETRIES} retries). */
    public synchronized int stuck() {
        int n = 0;
        for (Entry e : items) if (e.failures >= MAX_RETRIES) n++;
        return n;
    }

    /**
     * The send of this entry failed. When it was a Retry of this entry (see {@link #beginRetry}) the failure is counted
     * and the entry goes to the back, so the next Retry tries the next one, and true is returned. A failed first send of
     * a fresh recording is not a retry: nothing changes and false is returned. Call it before {@link #endJob}.
     */
    public synchronized boolean onSendFailed(long id) {
        if (inFlight != id || inFlightFresh) return false;
        for (int i = 0; i < items.size(); i++) {
            if (items.get(i).id == id) {
                Entry e = items.remove(i);
                e.failures++;
                items.add(e);
                return true;
            }
        }
        return false;
    }

    /** The entry with this id, or null. */
    public synchronized Entry get(long id) {
        for (Entry e : items) if (e.id == id) return e;
        return null;
    }

    /** Removes the entry with this id (sent, or discarded by the user). Returns whether there was one. */
    public synchronized boolean remove(long id) {
        for (int i = 0; i < items.size(); i++) {
            if (items.get(i).id == id) { items.remove(i); return true; }
        }
        return false;
    }

    // ---- the job in flight, and what a cancel may remove

    private long inFlight;          // id of the queued entry the job is sending, or 0 (recording, or no job)
    private boolean inFlightFresh;  // true: a fresh recording (cancel discards it); false: a Retry of an older entry

    /** A recording has started (or any job ended): nothing queued belongs to it, so a cancel removes nothing. */
    public synchronized void beginRecording() { inFlight = 0; inFlightFresh = false; }

    /** The job in flight is a fresh recording that is now queued as this entry. */
    public synchronized void beginFresh(long id) { inFlight = id; inFlightFresh = true; }

    /** The job in flight is a Retry of this older entry: a cancel only stops the send. */
    public synchronized void beginRetry(long id) { inFlight = id; inFlightFresh = false; }

    /** The job is over (sent, failed, or went idle): a later cancel removes nothing. */
    public synchronized void endJob() { inFlight = 0; inFlightFresh = false; }

    /**
     * The user cancelled. Returns the id of the one entry to discard (a fresh recording already queued), or 0 when
     * nothing must be removed (still recording, a Retry, or no job). Ends the job. Does not remove it from the
     * queue: the caller discards the id (remove + delete file).
     */
    public synchronized long onCancel() {
        long id = inFlightFresh ? inFlight : 0;
        inFlight = 0; inFlightFresh = false;
        return id;
    }

    public synchronized int size() { return items.size(); }

    /** Removes and returns every entry (the "Clear" button). */
    public synchronized List<Entry> clear() {
        List<Entry> all = new ArrayList<>(items);
        items.clear();
        return all;
    }

    /** Removes and returns the entries made more than MAX_AGE_MS before nowMs. */
    public synchronized List<Entry> purgeOlder(long nowMs) {
        List<Entry> old = new ArrayList<>();
        for (int i = items.size() - 1; i >= 0; i--) {
            if (nowMs - items.get(i).id > MAX_AGE_MS) old.add(0, items.remove(i));
        }
        return old;
    }

    /**
     * The notification line when more than one recording is kept ("2 recordings kept"), else null. When some are
     * parked it says how many ("2 recordings kept, 1 stuck"), also for a single kept recording.
     */
    public synchronized String summary() {
        int stuck = stuck();
        if (stuck > 0) return items.size() + (items.size() == 1 ? " recording kept, " : " recordings kept, ") + stuck + " stuck";
        return items.size() > 1 ? items.size() + " recordings kept" : null;
    }

    // ---- file names: vox_pending_<id>_<dest>.wav (the pkg and label are not kept across a restart)

    private static final String PREFIX = "vox_pending_", SUFFIX = ".wav";

    public static String fileName(Entry e) { return PREFIX + e.id + "_" + e.dest + SUFFIX; }

    /**
     * Reads a file name written by {@link #fileName}. Returns null for any other name. A recording found after a
     * restart has lost its app: pkg is "" (never null) and the label is empty. VoxAccessibilityService never types a
     * dictation whose pkg is empty (InsertGuard.NO_TARGET, whatever package the focused field reports): the text is
     * copied to the clipboard instead.
     */
    public static Entry parseFileName(String name) {
        if (name == null || !name.startsWith(PREFIX) || !name.endsWith(SUFFIX)) return null;
        String mid = name.substring(PREFIX.length(), name.length() - SUFFIX.length());
        int us = mid.indexOf('_');
        if (us <= 0) return null;
        String dest = mid.substring(us + 1);
        if (!dest.equals("note") && !dest.equals("dictation")) return null;
        long id;
        try { id = Long.parseLong(mid.substring(0, us)); } catch (NumberFormatException ex) { return null; }
        if (id <= 0) return null;
        return new Entry(id, "", "", dest);
    }

    /**
     * Moves every unsent recording (a file written by {@link #fileName}, or the old single slot {@code vox_pending.wav})
     * from {@code from} to {@code to}: earlier versions kept them in the cache folder, which Android may empty.
     * Other files stay. Returns how many were moved; a file that cannot be renamed stays where it is.
     */
    public static int migrate(java.io.File from, java.io.File to) {
        java.io.File[] files = from.listFiles();
        if (files == null) return 0;
        to.mkdirs();
        int moved = 0;
        for (java.io.File f : files) {
            String name = f.getName();
            if ((name.equals("vox_pending.wav") || parseFileName(name) != null) && f.renameTo(new java.io.File(to, name))) moved++;
        }
        return moved;
    }

    /**
     * Deletes the temporary upload files ({@code vox-up-*}, the user's voice as WAV or m4a, see AudioUpload) in {@code cacheDir}
     * that are older than {@code maxAgeMs}: a kill in the middle of an upload leaves them behind. Other files stay.
     * Returns how many were deleted.
     */
    public static int sweepUploads(java.io.File cacheDir, long nowMs, long maxAgeMs) {
        java.io.File[] files = cacheDir.listFiles();
        if (files == null) return 0;
        int n = 0;
        for (java.io.File f : files) {
            if (f.getName().startsWith("vox-up-") && f.lastModified() < nowMs - maxAgeMs && f.delete()) n++;
        }
        return n;
    }
}
