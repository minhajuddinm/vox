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

    /** One line of the per-model table: the medians of the entries that used this pair of models. */
    static final class ModelRow {
        final String sttModel;
        final String llmModel;
        int count;
        long stt, llm, total;

        ModelRow(String sttModel, String llmModel) {
            this.sttModel = sttModel;
            this.llmModel = llmModel;
        }
    }

    /**
     * Medians per pair of models over the newest n entries (oldest first, like summarize): the most used pair first, a
     * tie by voice model name, then cleanup model name. A stage that is 0 is left out of the median as in summarize.
     * Twin of timing.by_model; the timing_models rows of spec/golden.txt pin them together.
     */
    static List<ModelRow> byModel(List<Entry> entries, int n) {
        Map<String, List<Entry>> groups = new LinkedHashMap<>();
        if (n > 0) {
            for (Entry e : entries.subList(Math.max(0, entries.size() - n), entries.size())) {
                if (e == null || e.stages == null) continue;
                String key = e.sttModel.length() + ":" + e.sttModel + "|" + e.llmModel;
                List<Entry> g = groups.get(key);
                if (g == null) {
                    g = new ArrayList<>();
                    groups.put(key, g);
                }
                g.add(e);
            }
        }
        List<ModelRow> rows = new ArrayList<>();
        for (List<Entry> g : groups.values()) {
            Summary s = summarize(g, g.size());
            ModelRow r = new ModelRow(g.get(0).sttModel, g.get(0).llmModel);
            r.count = s.count;
            r.stt = s.median("stt");
            r.llm = s.median("llm");
            r.total = s.median("total");
            rows.add(r);
        }
        Collections.sort(rows, new java.util.Comparator<ModelRow>() {
            @Override
            public int compare(ModelRow a, ModelRow b) {
                if (a.count != b.count) return a.count > b.count ? -1 : 1;
                int c = a.sttModel.compareTo(b.sttModel);
                return c != 0 ? c : a.llmModel.compareTo(b.llmModel);
            }
        });
        return rows;
    }

    /**
     * The "timing" value of a history row as plain maps: the keys stages, stt_model, llm_model, provider and relay, the same
     * as windows/timing.py Timing.entry writes and speedView reads. Prefs.timingJson turns it into JSON, so the key names
     * live here, where the offline tests (ParityTest, TimingTest) can reach them.
     */
    static Map<String, Object> historyMap(Entry e) {
        Map<String, Object> o = new LinkedHashMap<>();
        o.put("stages", new LinkedHashMap<String, Object>(e.stages));
        o.put("stt_model", e.sttModel);
        o.put("llm_model", e.llmModel);
        o.put("provider", e.provider);
        o.put("relay", e.relay);
        return o;
    }

    private static long asLong(Object o) {
        return o instanceof Number ? ((Number) o).longValue() : 0L;
    }

    private static String asText(Object o) {
        return o instanceof String ? (String) o : "";
    }

    /** The Entry of one history row's "timing" value (a map as PlainJson parses it), or null when it has no stages map. */
    @SuppressWarnings("unchecked")
    private static Entry entryOf(Object timing) {
        if (!(timing instanceof Map)) return null;
        Map<String, Object> t = (Map<String, Object>) timing;
        if (!(t.get("stages") instanceof Map)) return null;
        Map<String, Long> stages = new LinkedHashMap<>();
        for (Map.Entry<String, Object> kv : ((Map<String, Object>) t.get("stages")).entrySet()) stages.put(kv.getKey(), asLong(kv.getValue()));
        return new Entry(stages, asText(t.get("stt_model")), asText(t.get("llm_model")), asText(t.get("provider")), Boolean.TRUE.equals(t.get("relay")));
    }

    /**
     * Everything the Speed card shows, as plain maps and lists ready for PlainJson.stringify, from the history rows
     * (oldest first; each a Map with an optional "timing" map). Twin of timing.speed_view: the same keys. Rows without
     * a timing (older ones, or made with history off) are ignored. Keys: count, biggest, stages {stage: {median, p90}},
     * models [{stt_model, llm_model, count, stt, llm, total}], last [newest first: t, app, words, stt_model,
     * llm_model, relay, stages].
     */
    @SuppressWarnings("unchecked")
    static Map<String, Object> speedView(List<Object> history, int n, int last) {
        List<Map<String, Object>> rows = new ArrayList<>();
        List<Entry> entries = new ArrayList<>();
        for (Object h : history) {
            if (!(h instanceof Map)) continue;
            Entry e = entryOf(((Map<String, Object>) h).get("timing"));
            if (e == null) continue;
            rows.add((Map<String, Object>) h);
            entries.add(e);
        }
        Summary s = summarize(entries, n);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("count", (long) s.count);
        out.put("biggest", s.biggest);
        Map<String, Object> stages = new LinkedHashMap<>();
        for (String name : STAGES) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("median", s.median(name));
            m.put("p90", s.p90(name));
            stages.put(name, m);
        }
        out.put("stages", stages);
        List<Object> models = new ArrayList<>();
        for (ModelRow r : byModel(entries, n)) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("stt_model", r.sttModel);
            m.put("llm_model", r.llmModel);
            m.put("count", (long) r.count);
            m.put("stt", r.stt);
            m.put("llm", r.llm);
            m.put("total", r.total);
            models.add(m);
        }
        out.put("models", models);
        List<Object> recent = new ArrayList<>();
        for (int i = entries.size() - 1; i >= 0 && recent.size() < last; i--) {
            Map<String, Object> row = rows.get(i);
            Entry e = entries.get(i);
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("t", row.get("t") == null ? (Object) 0L : row.get("t"));
            m.put("app", asText(row.get("app")));
            m.put("words", row.get("words") == null ? (Object) 0L : row.get("words"));
            m.put("stt_model", e.sttModel);
            m.put("llm_model", e.llmModel);
            m.put("relay", e.relay);
            m.put("stages", new LinkedHashMap<String, Object>(e.stages));
            recent.add(m);
        }
        out.put("last", recent);
        return out;
    }
}
