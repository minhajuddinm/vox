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

    public static void main(String[] args) {
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

        // cancel of a live recording (not queued yet) touches nothing queued
        PendingQueue k = new PendingQueue();
        k.add(e(10, "note"));
        eq("cancel live removes nothing", false, k.remove(20));
        eq("queued survives", 1, k.size());

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
        System.out.println("PendingQueueTest ok");
    }
}
