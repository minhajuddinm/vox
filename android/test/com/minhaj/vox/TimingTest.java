package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Plain-Java checks for the timing core (Timing). The shared numbers are also pinned by the timing_* rows of spec/golden.txt. */
public final class TimingTest {
    private static int checks;

    private static void check(String name, boolean ok) {
        checks++;
        if (!ok) {
            System.err.println("FAIL " + name);
            System.exit(1);
        }
    }

    private static Timing full() {
        Timing t = new Timing();
        t.mark("key_down", 1000);
        t.mark("rec_start", 1040);
        t.mark("key_up", 3040);
        t.mark("stt_start", 3050);
        t.mark("stt_done", 3650);
        t.mark("llm_start", 3660);
        t.mark("llm_done", 3960);
        t.mark("inserted", 3990);
        return t;
    }

    private static Timing.Entry entry(long stt, long llm) {
        Map<String, Long> st = new LinkedHashMap<>();
        st.put("stt", stt);
        st.put("llm", llm);
        return new Timing.Entry(st, "m", "l", "p", false);
    }


    private static Timing.Entry modelEntry(String stt, String llm, long sttMs, long llmMs, long total) {
        Map<String, Long> st = new LinkedHashMap<>();
        st.put("stt", sttMs);
        st.put("llm", llmMs);
        st.put("total", total);
        return new Timing.Entry(st, stt, llm, "p", false);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object o) {
        return (Map<String, Object>) o;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> asList(Object o) {
        return (List<Object>) o;
    }

    /** One history row as PlainJson would parse it: numbers are Long, the stages map is a map of numbers. */
    private static Map<String, Object> historyRow(long t, long stt, long llm, long total) {
        Map<String, Object> stages = new LinkedHashMap<>();
        stages.put("stt", stt);
        stages.put("llm", llm);
        stages.put("total", total);
        Map<String, Object> timing = new LinkedHashMap<>();
        timing.put("stages", stages);
        timing.put("stt_model", "w");
        timing.put("llm_model", "l");
        timing.put("provider", "p");
        timing.put("relay", Boolean.FALSE);
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("t", t);
        row.put("app", "notepad");
        row.put("words", 5L);
        row.put("timing", timing);
        return row;
    }

    private static Map<String, Object> rowWithTiming(Object timing) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("timing", timing);
        return row;
    }

    public static void main(String[] args) {
        Map<String, Long> st = full().stages();
        check("stage keys and order", new ArrayList<>(st.keySet()).equals(Arrays.asList(Timing.STAGES)));
        check("start", st.get("start") == 40);
        check("rec", st.get("rec") == 2000);
        check("stt", st.get("stt") == 600);
        check("llm", st.get("llm") == 300);
        check("insert", st.get("insert") == 30);
        check("total", st.get("total") == 950);

        Timing skipped = new Timing();
        skipped.mark("key_down", 0);
        skipped.mark("key_up", 1000);
        skipped.mark("stt_done", 1500);
        skipped.mark("inserted", 1530);
        check("skipped cleanup: llm is 0", skipped.stages().get("llm") == 0);
        check("skipped cleanup: insert from stt_done", skipped.stages().get("insert") == 30);

        boolean allZero = true;
        for (long v : new Timing().stages().values()) allZero &= v == 0;
        check("no marks, all zero", allZero);

        Timing back = new Timing();
        back.mark("key_down", 500);
        back.mark("rec_start", 400);
        check("backwards clock never negative", back.stages().get("start") == 0);

        boolean refused = false;
        try {
            new Timing().mark("nope", 1);
        } catch (IllegalArgumentException e) {
            refused = true;
        }
        check("unknown mark refused", refused);

        final long[] ticks = {5000, 7500};
        final int[] i = {0};
        Timing clocked = new Timing(new Timing.Clock() {
            @Override
            public long nowMs() {
                return ticks[i[0]++];
            }
        });
        clocked.mark("key_down");
        clocked.mark("rec_start");
        check("clock marks", clocked.stages().get("start") == 2500 && clocked.get("key_down") == 5000);
        clocked.mark("key_down", 1);
        check("a later mark replaces", clocked.get("key_down") == 1 && clocked.has("key_down") && !clocked.has("stt_done"));

        Timing.Entry e = full().entry("w", "l", "groq", true);
        check("entry fields", e.sttModel.equals("w") && e.llmModel.equals("l") && e.provider.equals("groq") && e.relay
                && e.stages.get("total") == 950);
        check("historyMap keys are the Windows entry keys", new ArrayList<>(Timing.historyMap(e).keySet())
                .equals(Arrays.asList("stages", "stt_model", "llm_model", "provider", "relay")));

        check("median empty", Timing.median(new ArrayList<Long>()) == 0 && Timing.p90(new ArrayList<Long>()) == 0);
        List<Long> xs = new ArrayList<>(Arrays.asList(3L, 1L, 2L));
        check("median odd", Timing.median(xs) == 2);
        check("input not changed", xs.equals(Arrays.asList(3L, 1L, 2L)));
        check("median even rounds down", Timing.median(Arrays.asList(1L, 2L, 3L, 10L)) == 2);

        Map<String, Long> big = new LinkedHashMap<>();
        big.put("rec", 9000L);
        big.put("total", 5000L);
        check("biggest ignores rec and total", Timing.biggest(big).isEmpty());
        big.put("llm", 5L);
        check("biggest picks llm", Timing.biggest(big).equals("llm"));
        check("biggest of nothing", Timing.biggest(new LinkedHashMap<String, Long>()).isEmpty());

        check("format -5", Timing.formatMs(-5).equals("0 ms"));
        check("format 1450", Timing.formatMs(1450).equals("1.5 s"));

        List<Timing.Entry> es = new ArrayList<>();
        es.add(entry(500, 0));
        es.add(entry(700, 400));
        es.add(entry(900, 0));
        es.add(null);
        es.add(new Timing.Entry(null, "", "", "", false));
        Timing.Summary s = Timing.summarize(es, 50);
        check("summary count ignores bad entries", s.count == 3);
        check("summary skips skipped llm", s.median("llm") == 400 && s.p90("llm") == 400);
        check("summary stt", s.median("stt") == 700 && s.biggest.equals("stt"));
        check("summary newest n", Timing.summarize(es.subList(0, 3), 1).median("stt") == 900);
        check("summary n=0", Timing.summarize(es, 0).count == 0);
        check("summary of none", Timing.summarize(new ArrayList<Timing.Entry>(), 50).biggest.isEmpty());

        // ---- per model pair
        List<Timing.Entry> ms = new ArrayList<>();
        ms.add(modelEntry("w", "a", 500, 300, 900));
        ms.add(modelEntry("w", "b", 700, 900, 1700));
        ms.add(modelEntry("w", "a", 600, 500, 1200));
        ms.add(modelEntry("w", "a", 800, 0, 800));
        ms.add(null);
        List<Timing.ModelRow> rows = Timing.byModel(ms, 50);
        check("byModel groups, most used first", rows.size() == 2 && rows.get(0).llmModel.equals("a") && rows.get(0).count == 3
                && rows.get(1).llmModel.equals("b") && rows.get(1).count == 1);
        check("byModel medians skip a cleanup that did not run", rows.get(0).stt == 600 && rows.get(0).llm == 400 && rows.get(0).total == 900);
        check("byModel newest n", Timing.byModel(ms.subList(0, 4), 1).get(0).llm == 0 && Timing.byModel(ms.subList(0, 4), 1).size() == 1);
        List<Timing.Entry> ties = new ArrayList<>();
        ties.add(modelEntry("b", "x", 1, 0, 0));
        ties.add(modelEntry("a", "x", 1, 0, 0));
        ties.add(new Timing.Entry(entry(2, 0).stages, null, null, "", false));
        List<Timing.ModelRow> tr = Timing.byModel(ties, 50);
        check("byModel ties by name, missing model is empty", tr.size() == 3 && tr.get(0).sttModel.isEmpty() && tr.get(1).sttModel.equals("a")
                && tr.get(2).sttModel.equals("b"));
        check("byModel of nothing", Timing.byModel(new ArrayList<Timing.Entry>(), 50).isEmpty() && Timing.byModel(ms, 0).isEmpty());

        // ---- the Speed card's data from history rows (oldest first), as the page gets it from the bridge
        List<Object> hist = new ArrayList<>();
        Map<String, Object> old = new LinkedHashMap<>();
        old.put("t", 1L);
        hist.add(old);   // an entry without timing
        for (int k = 0; k < 12; k++) hist.add(historyRow(100 + k, 500 + k, 300, 900));
        hist.add(null);
        hist.add("junk");
        Map<String, Object> v = Timing.speedView(hist, 50, 10);
        check("speedView count and biggest", ((Long) v.get("count")) == 12 && "stt".equals(v.get("biggest")));
        Map<String, Object> sv = asMap(v.get("stages"));
        check("speedView has every stage", sv.size() == Timing.STAGES.length && ((Long) asMap(sv.get("stt")).get("median")) == 505);
        List<Object> last = asList(v.get("last"));
        check("speedView last 10, newest first", last.size() == 10 && ((Number) asMap(last.get(0)).get("t")).longValue() == 111
                && ((Number) asMap(last.get(9)).get("t")).longValue() == 102);
        Map<String, Object> l0 = asMap(last.get(0));
        check("speedView last fields", "w".equals(l0.get("stt_model")) && "notepad".equals(l0.get("app")) && ((Long) asMap(l0.get("stages")).get("stt")) == 511);
        check("speedView models", asList(v.get("models")).size() == 1 && ((Long) asMap(asList(v.get("models")).get(0)).get("count")) == 12);
        Map<String, Object> none = Timing.speedView(new ArrayList<Object>(), 50, 10);
        check("speedView of nothing", ((Long) none.get("count")) == 0 && "".equals(none.get("biggest")) && asList(none.get("last")).isEmpty()
                && asList(none.get("models")).isEmpty() && asMap(none.get("stages")).size() == Timing.STAGES.length);
        check("speedView ignores a timing without stages", ((Long) Timing.speedView(Arrays.<Object>asList(rowWithTiming("x")), 50, 10).get("count")) == 0);
        System.out.println("OK: " + checks + " checks passed");
    }
}
