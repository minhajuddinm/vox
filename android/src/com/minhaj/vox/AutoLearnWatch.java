package com.minhaj.vox;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.function.LongSupplier;

/**
 * Watches one text field for a while after Vox typed into it, for the user's corrections. Same rules as the Watch class in
 * windows/autolearn.py; pure Java, tested off-device. Not thread safe: VoxAccessibilityService calls it on the main thread.
 *
 * arm() when Vox typed into an app; observe(app, text) with the field's whole text whenever it may have changed (null when
 * it cannot be read); end() when the watch must stop. Each returns the corrections found then, each once per watch. The
 * watch ends by itself after AUTO_LEARN_WINDOW_S, when the app changes, when the field is emptied (sent), when the typed
 * text is gone from it or when the field shrinks to under half of it. A text is analysed once it has stayed the same for
 * SETTLE_MS, and the last snapshot (the latest text that still held the typed text) is analysed once more when the watch
 * ends, so a fix made just before pressing Send still counts. The snapshot is kept in memory only, never stored or logged.
 */
final class AutoLearnWatch {
    /** The longest a watch lasts after Vox typed (autolearn.AUTO_LEARN_WINDOW_S). */
    static final int AUTO_LEARN_WINDOW_S = 180;
    /** The text must stay unchanged this long before it is analysed (autolearn.SETTLE_S). */
    static final long SETTLE_MS = 1500;

    private final LongSupplier clockMs;
    private boolean armed;
    private String app;
    private String inserted = "";
    private long armedAt;
    private String snapshot;
    private long changedAt;
    private boolean dirty;
    private final Set<String> reported = new HashSet<>();

    /** @param clockMs milliseconds of a clock that does not jump (SystemClock.elapsedRealtime on the phone) */
    AutoLearnWatch(LongSupplier clockMs) {
        this.clockMs = clockMs;
    }

    boolean isArmed() {
        return armed && clockMs.getAsLong() - armedAt <= AUTO_LEARN_WINDOW_S * 1000L;
    }

    /** The app (package) being watched, or null. */
    String app() { return armed ? app : null; }

    /** Start watching app for fixes of inserted; a watch still running ends first (its corrections are returned). */
    List<String[]> arm(String app, String inserted) {
        List<String[]> out = end();
        if (inserted != null && !inserted.trim().isEmpty()) {
            this.armed = true;
            this.app = app;
            this.inserted = inserted;
            this.armedAt = clockMs.getAsLong();
        }
        return out;
    }

    /** Stop watching: the last snapshot is analysed once more and then dropped. */
    List<String[]> end() {
        List<String[]> out = armed && dirty ? analyse() : new ArrayList<String[]>();
        armed = false;
        app = null;
        inserted = "";
        snapshot = null;
        dirty = false;
        reported.clear();
        return out;
    }

    List<String[]> observe(String app, String current) {
        if (!armed) return new ArrayList<>();
        long now = clockMs.getAsLong();
        if (now - armedAt > AUTO_LEARN_WINDOW_S * 1000L || (app == null ? this.app != null : !app.equals(this.app))) return end();
        String text = current == null ? "" : current;
        String t = text.trim(), ins = inserted.trim();
        if (t.isEmpty() || 2 * t.codePointCount(0, t.length()) < ins.codePointCount(0, ins.length())
                || text.codePointCount(0, text.length()) > AutoLearn.MAX_TEXT
                || AutoLearn.locate(inserted, text) == null) {
            return end();   // sent, cleared, moved away or unreadable: the last snapshot still counts
        }
        List<String[]> out = new ArrayList<>();
        if (!text.equals(snapshot)) {
            if (dirty && now - changedAt >= SETTLE_MS) out = analyse();   // the text that was quiet until now
            snapshot = text;
            changedAt = now;
            dirty = true;
        } else if (dirty && now - changedAt >= SETTLE_MS) {
            out = analyse();
        }
        return out;
    }

    private List<String[]> analyse() {
        dirty = false;
        List<String[]> out = new ArrayList<>();
        for (String[] p : AutoLearn.detect(inserted, snapshot == null ? "" : snapshot)) {
            if (reported.add(p[0].toLowerCase(Locale.ROOT))) out.add(p);
        }
        return out;
    }
}
