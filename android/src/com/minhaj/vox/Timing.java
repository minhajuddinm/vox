package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Where the time goes in one dictation. Twin of windows/timing.py: a Timing records named marks (monotonic
 * milliseconds); stages() turns them into the six stage durations; summarize() takes the newest N entries and gives the
 * median and 90th percentile of each stage and the biggest one. Pure Java (no android.* or org.json) so the off-device
 * tests can run it; nothing here touches the network or the disk. The shared numbers are pinned by the timing_*
 * rows of spec/golden.txt (tests/test_parity.py runs them against timing.py, ParityTest against this class).
 *
 * Stages (milliseconds, never negative, 0 when a mark is missing): start = key_down to rec_start, rec = rec_start to
 * key_up, stt = stt_start to stt_done, llm = llm_start to llm_done (0 when cleanup was skipped), insert = llm_done (or
 * stt_done when cleanup was skipped) to inserted, total = key_up to inserted.
 */
final class Timing {
    static final String[] MARKS = {"key_down", "rec_start", "key_up", "stt_start", "stt_done", "llm_start", "llm_done", "inserted"};
    static final String[] STAGES = {"start", "rec", "stt", "llm", "insert", "total"};
    /** "biggest" picks among the stages the app can work on: rec is the person speaking and total is the sum of the rest. */
    private static final String[] BIGGEST_CANDIDATES = {"start", "stt", "llm", "insert"};

    /** Milliseconds from a monotonic clock (on the phone: SystemClock.elapsedRealtime). */
    interface Clock {
        long nowMs();
    }

    private static final Clock NANO_CLOCK = new Clock() {
        @Override
        public long nowMs() {
            return System.nanoTime() / 1000000L;
        }
    };

    private final Clock clock;
    private final Map<String, Long> marks = new LinkedHashMap<>();

    Timing() {
        this(NANO_CLOCK);
    }

    Timing(Clock clock) {
        this.clock = clock == null ? NANO_CLOCK : clock;
    }

    /** Record a mark now. A later mark with the same name replaces the earlier one. */
    synchronized void mark(String name) {
        mark(name, clock.nowMs());
    }

    synchronized void mark(String name, long atMs) {
        boolean known = false;
        for (String m : MARKS) known |= m.equals(name);
        if (!known) throw new IllegalArgumentException("unknown timing mark: " + name);
        marks.put(name, atMs);
    }

    synchronized Long get(String name) {
        return marks.get(name);
    }

    synchronized boolean has(String name) {
        return marks.containsKey(name);
    }

    /** b - a in whole milliseconds, 0 when either is missing or the clock ran backwards. */
    private static long span(Long a, Long b) {
        if (a == null || b == null) return 0;
        return Math.max(0L, b - a);
    }

    synchronized Map<String, Long> stages() {
        Map<String, Long> m = marks;
        boolean llmRan = m.containsKey("llm_start") && m.containsKey("llm_done");
        Long insertFrom = llmRan ? m.get("llm_done") : m.get("stt_done");
        Map<String, Long> out = new LinkedHashMap<>();
        out.put("start", span(m.get("key_down"), m.get("rec_start")));
        out.put("rec", span(m.get("rec_start"), m.get("key_up")));
        out.put("stt", span(m.get("stt_start"), m.get("stt_done")));
        out.put("llm", llmRan ? span(m.get("llm_start"), m.get("llm_done")) : 0L);
        out.put("insert", span(insertFrom, m.get("inserted")));
        out.put("total", span(m.get("key_up"), m.get("inserted")));
        return out;
    }

    /** What goes into the history and into summarize. */
    Entry entry(String sttModel, String llmModel, String provider, boolean relay) {
        return new Entry(stages(), sttModel, llmModel, provider, relay);
    }

    static final class Entry {
        final Map<String, Long> stages;
        final String sttModel;
        final String llmModel;
        final String provider;
        final boolean relay;

        Entry(Map<String, Long> stages, String sttModel, String llmModel, String provider, boolean relay) {
            this.stages = stages;
            this.sttModel = sttModel == null ? "" : sttModel;
            this.llmModel = llmModel == null ? "" : llmModel;
            this.provider = provider == null ? "" : provider;
            this.relay = relay;
        }
    }

    /** The result of summarize: the number of entries used, the biggest stage ("" if none) and median/p90 per stage. */
    static final class Summary {
        int count;
        String biggest = "";
        final Map<String, long[]> stats = new LinkedHashMap<>();   // stage -> {median, p90}

        long median(String stage) {
            long[] s = stats.get(stage);
            return s == null ? 0 : s[0];
        }

        long p90(String stage) {
            long[] s = stats.get(stage);
            return s == null ? 0 : s[1];
        }
    }

    private static List<Long> sorted(List<Long> values) {
        List<Long> xs = new ArrayList<>(values);   // the caller's list is not changed
        Collections.sort(xs);
        return xs;
    }

    /** Middle value of a list of non-negative whole numbers; the two middle ones average (rounded down). 0 when empty. */
    static long median(List<Long> values) {
        List<Long> xs = sorted(values);
        int n = xs.size();
        if (n == 0) return 0;
        if (n % 2 == 1) return xs.get(n / 2);
        return (xs.get(n / 2 - 1) + xs.get(n / 2)) / 2;
    }

    /** 90th percentile by nearest rank: the ceil(0.9 * n)-th smallest value. 0 when empty. */
    static long p90(List<Long> values) {
        List<Long> xs = sorted(values);
        int n = xs.size();
        if (n == 0) return 0;
        return xs.get((9 * n + 9) / 10 - 1);
    }

    /** Name of the largest of start/stt/llm/insert ("" when all are 0 or missing); a tie goes to the earlier stage. */
    static String biggest(Map<String, Long> stages) {
        String best = "";
        long bestMs = 0;
        for (String name : BIGGEST_CANDIDATES) {
            Long v = stages.get(name);
            if (v != null && v > bestMs) {
                best = name;
                bestMs = v;
            }
        }
        return best;
    }

    /** 850 -> "850 ms", 1449 -> "1.4 s" (one decimal, halves round up). */
    static String formatMs(long ms) {
        ms = Math.max(0L, ms);
        if (ms < 1000) return ms + " ms";
        long tenths = (ms + 50) / 100;
        return (tenths / 10) + "." + (tenths % 10) + " s";
    }

    /**
     * Median and p90 of every stage over the newest n entries (the list is oldest first). A stage that is 0 in an entry
     * did not run (cleanup skipped) or was not measured, so it is left out of that stage's numbers. Entries without a
     * stages map (and null entries) are ignored; count is the number of entries used.
     */
    static Summary summarize(List<Entry> entries, int n) {
        List<Map<String, Long>> used = new ArrayList<>();
        if (n > 0) {
            for (Entry e : entries.subList(Math.max(0, entries.size() - n), entries.size())) {
                if (e != null && e.stages != null) used.add(e.stages);
            }
        }
        Summary s = new Summary();
        Map<String, Long> medians = new LinkedHashMap<>();
        for (String name : STAGES) {
            List<Long> vals = new ArrayList<>();
            for (Map<String, Long> st : used) {
                Long v = st.get(name);
                if (v != null && v > 0) vals.add(v);
            }
            long med = median(vals);
            s.stats.put(name, new long[] {med, p90(vals)});
            medians.put(name, med);
        }
        s.biggest = biggest(medians);
        s.count = used.size();
        return s;
    }
}
