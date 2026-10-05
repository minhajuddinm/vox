package com.minhaj.vox;

import java.util.List;

/** Plain-Java checks for PendingQueue. Run by CI, exits non-zero on failure. */
public final class PendingQueueTest {
    private static void eq(String name, Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static PendingQueue.Entry e(long id, String dest) { return new PendingQueue.Entry(id, "p", "l", dest); }

    private static final long DAY = 24L * 60 * 60 * 1000;

    public static void main(String[] args) throws Exception {
        // oldest first: next() is the oldest and stays until removed
        PendingQueue q = new PendingQueue();
        eq("empty next", null, q.next());
        eq("empty summary", null, q.summary());
        q.add(e(100, "note"));
        q.add(e(200, "dictation"));
        q.add(e(300, "note"));
        eq("size", 3, q.size());
        eq("oldest first", 100L, q.next().id);
        eq("next does not remove", 100L, q.next().id);
        eq("summary", "3 recordings kept", q.summary());
        q.remove(100);
        eq("after remove next", 200L, q.next().id);
        eq("remove unknown", false, q.remove(999));
        eq("size 2", 2, q.size());
        q.remove(200);
        eq("one kept has no count line", null, q.summary());

        // a new recording never replaces an unsent one
        PendingQueue n = new PendingQueue();
        n.add(e(1, "note"));
        n.add(e(2, "dictation"));
        eq("both kept", 2, n.size());
        eq("first still there", "note", n.get(1).dest);

        // cap drops the oldest
        PendingQueue c = new PendingQueue();
        for (int i = 1; i <= PendingQueue.MAX_KEPT; i++) eq("no drop " + i, 0, c.add(e(i, "note")).size());
        List<PendingQueue.Entry> dropped = c.add(e(6, "note"));
        eq("one dropped", 1, dropped.size());
        eq("dropped is oldest", 1L, dropped.get(0).id);
        eq("size capped", PendingQueue.MAX_KEPT, c.size());
        eq("new oldest", 2L, c.next().id);
        eq("newest kept", true, c.get(6) != null);

        // cancel rules: only a fresh, already-queued recording is discarded
        PendingQueue k = new PendingQueue();
        k.add(e(10, "note"));
        k.beginRecording();
        eq("cancel while recording discards nothing", 0L, k.onCancel());
        k.beginRetry(10);
        eq("cancel during retry discards nothing", 0L, k.onCancel());
        k.add(e(20, "dictation"));
        k.beginFresh(20);
        eq("cancel of fresh queued entry returns only it", 20L, k.onCancel());
        eq("second cancel returns nothing", 0L, k.onCancel());
        k.beginFresh(20);
        k.endJob();
        eq("cancel after a failed send discards nothing", 0L, k.onCancel());
        k.beginFresh(20);
        k.beginRecording();
        eq("new recording after fresh job resets it", 0L, k.onCancel());

        // age purge: older than 7 days goes, exactly 7 days and newer stay
        long now = 100 * DAY;
        PendingQueue a = new PendingQueue();
        a.add(e(now - 8 * DAY, "note"));
        a.add(e(now - 7 * DAY, "dictation"));
        a.add(e(now - 1, "note"));
        List<PendingQueue.Entry> old = a.purgeOlder(now);
        eq("purged one", 1, old.size());
        eq("purged the old one", now - 8 * DAY, old.get(0).id);
        eq("two left", 2, a.size());

        // late arrival keeps order
        PendingQueue o = new PendingQueue();
        o.add(e(30, "note"));
        o.add(e(10, "note"));
        eq("sorted by id", 10L, o.next().id);

        // clear
        eq("clear returns all", 2, o.clear().size());
        eq("clear empties", 0, o.size());

        // a failed Retry moves that entry to the back, so the next Retry tries the next one
        PendingQueue r = new PendingQueue();
        r.add(e(1, "note"));
        r.add(e(2, "dictation"));
        r.add(e(3, "note"));
        r.beginRetry(1);
        eq("failed retry rotates", true, r.onSendFailed(1));
        eq("next is the next one", 2L, r.next().id);
        eq("rotation keeps every entry", 3, r.size());
        eq("one failure counted", 1, r.failures(1));
        r.endJob();
        r.beginRetry(2);
        r.onSendFailed(2);
        r.endJob();
        eq("then the third", 3L, r.next().id);
        r.beginRetry(3);
        r.onSendFailed(3);
        r.endJob();
        eq("round again, back to the first", 1L, r.next().id);
        // a fresh recording that fails its first send is not a Retry: no rotation, no count
        r.add(e(4, "note"));
        r.beginFresh(4);
        eq("fresh failure does not rotate", false, r.onSendFailed(4));
        eq("fresh failure not counted", 0, r.failures(4));
        eq("a failure of some other entry does not rotate", false, r.onSendFailed(999));
        r.endJob();
        eq("no job: nothing rotates", false, r.onSendFailed(1));
        eq("order kept", 1L, r.next().id);

        // three failed retries park the entry: Retry skips it, the text says how many are stuck
        PendingQueue s = new PendingQueue();
        s.add(e(1, "note"));
        eq("none stuck", 0, s.stuck());
        for (int i = 1; i <= PendingQueue.MAX_RETRIES; i++) {
            eq("still retryable before try " + i, 1L, s.next() == null ? -1L : s.next().id);
            s.beginRetry(1);
            s.onSendFailed(1);
            s.endJob();
        }
        eq("failures capped", PendingQueue.MAX_RETRIES, s.failures(1));
        eq("parked: nothing to retry", null, s.next());
        eq("one stuck", 1, s.stuck());
        eq("parked entry is kept", 1, s.size());
        eq("summary with all stuck", "1 recording kept, 1 stuck", s.summary());
        s.add(e(2, "dictation"));
        eq("a newer entry is retried before the parked one", 2L, s.next().id);
        eq("summary with some stuck", "2 recordings kept, 1 stuck", s.summary());
        s.beginRetry(2);
        eq("a failed retry of the newer entry rotates it behind", true, s.onSendFailed(2));
        s.endJob();
        eq("the parked one is not retried", 2L, s.next().id);
        eq("stuck still one", 1, s.stuck());
        eq("cleared entries are gone", 2, s.clear().size());
        eq("stuck after clear", 0, s.stuck());

        // the cap drops the oldest by age, not the one at the front of the retry order
        PendingQueue d = new PendingQueue();
        for (int i = 1; i <= PendingQueue.MAX_KEPT; i++) d.add(e(i, "note"));
        d.beginRetry(1);
        d.onSendFailed(1);
        d.endJob();
        eq("1 went to the back", 2L, d.next().id);
        List<PendingQueue.Entry> gone = d.add(e(6, "note"));
        eq("cap drops one", 1, gone.size());
        eq("it is the oldest by age", 1L, gone.get(0).id);
        eq("retry order kept", 2L, d.next().id);

        // file names
        eq("file name", "vox_pending_123_note.wav", PendingQueue.fileName(e(123, "note")));
        PendingQueue.Entry p = PendingQueue.parseFileName("vox_pending_123_dictation.wav");
        eq("parse id", 123L, p.id);
        eq("parse dest", "dictation", p.dest);
        eq("parse pkg is empty not null", "", p.pkg);
        eq("old single slot name", null, PendingQueue.parseFileName("vox_pending.wav"));
        eq("bad dest", null, PendingQueue.parseFileName("vox_pending_5_other.wav"));
        eq("bad id", null, PendingQueue.parseFileName("vox_pending_x_note.wav"));
        eq("other file", null, PendingQueue.parseFileName("something.wav"));
        eq("null name", null, PendingQueue.parseFileName(null));
        // migrate: unsent recordings move out of the cache folder (Android may empty it) into the app's own folder
        java.io.File cacheDir = java.nio.file.Files.createTempDirectory("vox-pq-cache").toFile();
        java.io.File filesDir = java.nio.file.Files.createTempDirectory("vox-pq-files").toFile();
        String keptName = PendingQueue.fileName(e(1234, "note"));
        for (String fn : new String[]{keptName, "vox_pending.wav", "other.txt"}) java.nio.file.Files.write(new java.io.File(cacheDir, fn).toPath(), new byte[]{1, 2, 3});
        eq("migrate counts the recordings it moved", 2, PendingQueue.migrate(cacheDir, filesDir));
        eq("migrate moved the recording", true, new java.io.File(filesDir, keptName).exists() && !new java.io.File(cacheDir, keptName).exists());
        eq("migrate moved the old single slot", true, new java.io.File(filesDir, "vox_pending.wav").exists());
        eq("migrate leaves other files", true, new java.io.File(cacheDir, "other.txt").exists() && !new java.io.File(filesDir, "other.txt").exists());
        eq("migrate keeps the content", 3L, new java.io.File(filesDir, keptName).length());
        eq("migrate with nothing left", 0, PendingQueue.migrate(cacheDir, filesDir));
        eq("migrate from a missing folder", 0, PendingQueue.migrate(new java.io.File(cacheDir, "nope"), filesDir));

        // sweepUploads: temp upload files a kill left behind go, fresh ones and other files stay
        java.io.File cache = java.nio.file.Files.createTempDirectory("vox-pq-sweep").toFile();
        long sweepNow = System.currentTimeMillis(), tenMin = 10 * 60 * 1000L;
        java.io.File oldUp = new java.io.File(cache, "vox-up-1.wav"), freshUp = new java.io.File(cache, "vox-up-2.m4a"), keep = new java.io.File(cache, "keep.wav");
        for (java.io.File f : new java.io.File[]{oldUp, freshUp, keep}) java.nio.file.Files.write(f.toPath(), new byte[]{1});
        oldUp.setLastModified(sweepNow - 2 * tenMin);
        freshUp.setLastModified(sweepNow);
        keep.setLastModified(sweepNow - 2 * tenMin);
        eq("sweepUploads counts what it deleted", 1, PendingQueue.sweepUploads(cache, sweepNow, tenMin));
        eq("sweepUploads deleted the old temp upload", false, oldUp.exists());
        eq("sweepUploads kept the fresh temp upload", true, freshUp.exists());
        eq("sweepUploads kept other files", true, keep.exists());
        eq("sweepUploads from a missing folder", 0, PendingQueue.sweepUploads(new java.io.File(cache, "nope"), sweepNow, tenMin));

        // a fresh recording is not capped until its own send fails (AND-4): a send that works must not cost the oldest kept one
        PendingQueue f = new PendingQueue();
        for (int i = 1; i <= PendingQueue.MAX_KEPT; i++) f.add(e(i, "note"));
        f.addFresh(e(6, "dictation"));
        f.beginFresh(6);
        eq("fresh: nothing dropped while it is sent", PendingQueue.MAX_KEPT + 1, f.size());
        eq("fresh: the oldest is still kept", true, f.get(1) != null);
        f.remove(6);   // sent
        f.endJob();
        eq("fresh sent: the five kept are all still there", PendingQueue.MAX_KEPT, f.size());
        eq("fresh sent: oldest first", 1L, f.next().id);
        eq("fresh sent: trim drops nothing", 0, f.trim().size());
        // the same, but the send fails: now the cap applies and the oldest goes
        f.addFresh(e(7, "dictation"));
        f.beginFresh(7);
        eq("fresh failed: not counted as a retry", false, f.onSendFailed(7));
        List<PendingQueue.Entry> cut = f.trim();
        eq("fresh failed: one dropped", 1, cut.size());
        eq("fresh failed: the oldest dropped", 1L, cut.get(0).id);
        eq("fresh failed: the failed one is kept for Retry", true, f.get(7) != null);
        eq("fresh failed: capped", PendingQueue.MAX_KEPT, f.size());
        // a cancel of the fresh one still discards only it
        f.addFresh(e(8, "dictation"));
        f.beginFresh(8);
        eq("fresh cancelled: its id is discarded", 8L, f.onCancel());

        System.out.println("PendingQueueTest ok");
    }
}
