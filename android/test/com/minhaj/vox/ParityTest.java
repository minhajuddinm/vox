package com.minhaj.vox;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Runs spec/golden.txt against the Java helpers. tests/test_parity.py runs the same file against the Python
 * ones, so the two implementations cannot drift apart unnoticed. Usage: ParityTest path/to/golden.txt
 */
public final class ParityTest {
    private static int checks;

    private static String unesc(String s) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\\' && i + 1 < s.length()) {
                char n = s.charAt(++i);
                out.append(n == 'n' ? '\n' : n == 't' ? '\t' : n);
            } else {
                out.append(c);
            }
        }
        return out.toString();
    }

    private static List<String> items(String field, String sep) {
        List<String> out = new ArrayList<>();
        for (String x : field.split(java.util.regex.Pattern.quote(sep), -1)) if (!x.isEmpty()) out.add(x);
        return out;
    }

    /** ProfileMerge.merge3 on one field whose value on each side is a string, or "~" when the field is absent there. */
    private static String mergedValue(String base, String local, String remote) {
        Object v = ProfileMerge.merge3(absentIfTilde(base), absentIfTilde(local), absentIfTilde(remote));
        return v == null ? "~" : (String) v;
    }

    private static String absentIfTilde(String v) {
        return v.equals("~") ? null : v;
    }

    /** A | separated list of whole numbers. */
    private static List<Long> numbers(String field) {
        List<Long> out = new ArrayList<>();
        for (String x : items(field, "|")) out.add(Long.parseLong(x));
        return out;
    }

    /** A comma separated k=v map of whole numbers (timing marks or stages), in the order written. */
    private static Map<String, Long> kv(String field) {
        Map<String, Long> out = new LinkedHashMap<>();
        for (String p : items(field, ",")) out.put(p.substring(0, p.indexOf('=')), Long.parseLong(p.substring(p.indexOf('=') + 1)));
        return out;
    }

    private static String stagesText(Map<String, Long> st) {
        StringBuilder b = new StringBuilder();
        for (String k : Timing.STAGES) b.append(b.length() == 0 ? "" : ",").append(k).append('=').append(st.get(k));
        return b.toString();
    }

    private static String timingStages(String marks) {
        Timing t = new Timing();
        for (Map.Entry<String, Long> m : kv(marks).entrySet()) t.mark(m.getKey(), m.getValue());
        return stagesText(t.stages());
    }

    private static String timingSummary(String entries, int n) {
        List<Timing.Entry> list = new ArrayList<>();
        for (String e : items(entries, ";")) list.add(new Timing.Entry(kv(e), "", "", "", false));
        Timing.Summary s = Timing.summarize(list, n);
        StringBuilder b = new StringBuilder("count=" + s.count + " biggest=" + s.biggest);
        for (String k : Timing.STAGES) b.append(' ').append(k).append('=').append(s.median(k)).append('/').append(s.p90(k));
        return b.toString();
    }

    /** entries are voice@cleanup@stages maps separated by ;  => one "voice+cleanup n=count stt=median llm=median total=median" per pair, joined by ;. */
    private static String timingModels(String entries, int n) {
        List<Timing.Entry> list = new ArrayList<>();
        for (String e : items(entries, ";")) {
            String[] p = e.split("@", 3);
            list.add(new Timing.Entry(kv(p[2]), p[0], p[1], "", false));
        }
        StringBuilder b = new StringBuilder();
        for (Timing.ModelRow r : Timing.byModel(list, n)) {
            b.append(b.length() == 0 ? "" : ";").append(r.sttModel).append('+').append(r.llmModel).append(" n=").append(r.count)
                    .append(" stt=").append(r.stt).append(" llm=").append(r.llm).append(" total=").append(r.total);
        }
        return b.toString();
    }

    private static void eq(int line, String kind, String expected, String actual) {
        checks++;
        if (!expected.equals(actual)) {
            System.err.println("FAIL line " + line + " (" + kind + "):\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) throws IOException {
        List<String> lines = Files.readAllLines(Paths.get(args[0]), StandardCharsets.UTF_8);
        for (int n = 0; n < lines.size(); n++) {
            String line = lines.get(n);
            if (line.isEmpty() || line.startsWith("#")) continue;
            String[] raw = line.split("\t", -1);
            String kind = raw[0];
            String[] f = new String[raw.length - 1];
            for (int i = 1; i < raw.length; i++) f[i - 1] = unesc(raw[i]);
            int ln = n + 1;
            switch (kind) {
                case "sanitize":
                    eq(ln, kind, f[1], ApiClient.sanitize(f[0]));
                    break;
                case "looks_valid":
                    eq(ln, kind, f[2], ApiClient.looksValid(f[0], f[1]) ? "true" : "false");
                    break;
                case "replace": {
                    Map<String, String> repl = new LinkedHashMap<>();
                    for (String p : items(f[1], ";")) repl.put(p.substring(0, p.indexOf("=>")), p.substring(p.indexOf("=>") + 2));
                    eq(ln, kind, f[2], ApiClient.applyReplacements(f[0], repl));
                    break;
                }
                case "whisper":
                    eq(ln, kind, f[1], ApiClient.whisperPrompt(items(f[0], "|")));
                    break;
                case "terms": {
                    String dict = f[1].replace("|", "\n");
                    String people = f[0].replace("|", "\n");
                    eq(ln, kind, f[2], String.join("|", Terms.terms(people, dict)));
                    break;
                }
                case "prompt":
                    eq(ln, kind, f[3], ApiClient.systemPrompt(f[0], items(f[1], "|"), f[2]));
                    break;
                case "spoken":
                    eq(ln, kind, f[1], ApiClient.applySpokenCommands(f[0]));
                    break;
                case "promptctx":
                    eq(ln, kind, f[4], ApiClient.systemPrompt(f[0], items(f[1], "|"), f[2], f[3]));
                    break;
                case "context":
                    eq(ln, kind, f[1], ApiClient.cleanContext(f[0]));
                    break;
                case "level":
                    eq(ln, kind, f[1], String.format(java.util.Locale.ROOT, "%.3f", Pcm.levelFromRms(Double.parseDouble(f[0]))));
                    break;
                case "models":
                    eq(ln, kind, f[1], Providers.classify(f[0]));
                    break;
                case "silence":
                    eq(ln, kind, f[1], ApiClient.isSilenceHallucination(f[0]) ? "true" : "false");
                    break;
                case "gate":
                    eq(ln, kind, f[4], ApiClient.needsCleanup(f[0], f[1], "true".equals(f[2]), f[3]) ? "true" : "false");
                    break;
                case "title":
                    eq(ln, kind, f[1], NoteLogic.autoTitle(f[0]));
                    break;
                case "ftsq":
                    eq(ln, kind, f[1], NoteLogic.ftsQuery(f[0]));
                    break;
                case "remotewins":
                    eq(ln, kind, f[4], NoteLogic.remoteWins(f[0].equals("true"), Double.parseDouble(f[1]),
                            Double.parseDouble(f[2]), f[3].equals("true")) ? "true" : "false");
                    break;
                case "merge3":
                    eq(ln, kind, f[3], mergedValue(f[0], f[1], f[2]));
                    break;
                case "profilefields":
                    eq(ln, kind, f[1], String.join("|", f[0].equals("keys") ? ProfileMerge.KEY_FIELDS : ProfileMerge.SHARED_FIELDS));
                    break;
                case "devname":   // a typed, non-blank name: the model argument is never used
                    eq(ln, kind, f[1], NoteLogic.deviceName(f[0], "model"));
                    break;
                case "permanent":   // a refusal that will come back every time (4xx except 401, 403, 429)
                    eq(ln, kind, f[1], new RelayApi.RelayError(Integer.parseInt(f[0]), "x").permanent() ? "true" : "false");
                    break;
                case "proxyurl":   // relay_url, role, address (blank when no relay address)
                    eq(ln, kind, f[2], Providers.proxyUrl(f[0], f[1]));
                    break;
                case "retry":   // status (0 = no answer), request timeout, via the relay, whether the same request is sent again
                    eq(ln, kind, f[3], ApiClient.retryable(Integer.parseInt(f[0]), f[1].equals("true"), f[2].equals("true")) ? "true" : "false");
                    break;
                case "timing_median":
                    eq(ln, kind, f[1], String.valueOf(Timing.median(numbers(f[0]))));
                    break;
                case "timing_p90":
                    eq(ln, kind, f[1], String.valueOf(Timing.p90(numbers(f[0]))));
                    break;
                case "timing_biggest":
                    eq(ln, kind, f[1], Timing.biggest(kv(f[0])));
                    break;
                case "timing_format":
                    eq(ln, kind, f[1], Timing.formatMs(Long.parseLong(f[0])));
                    break;
                case "timing_stages":   // marks (ms) => stages
                    eq(ln, kind, f[1], timingStages(f[0]));
                    break;
                case "timing_summary":   // entries (stages maps separated by ;), n => count, biggest and median/p90 per stage
                    eq(ln, kind, f[2], timingSummary(f[0], Integer.parseInt(f[1])));
                    break;
                case "timing_models":   // entries (voice@cleanup@stages, separated by ;), n => one line per model pair
                    eq(ln, kind, f[2], timingModels(f[0], Integer.parseInt(f[1])));
                    break;
                default:
                    System.err.println("FAIL line " + ln + ": unknown case kind " + kind);
                    System.exit(1);
            }
        }
        System.out.println("OK: " + checks + " golden cases passed");
    }
}
