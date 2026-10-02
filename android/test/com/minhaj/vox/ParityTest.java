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

    /** Golden audio (see segcuts in spec/golden.txt): | separated runs, each a letter and a length in milliseconds. */
    private static byte[] segAudio(String runs) {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        for (String r : items(runs, "|")) {
            int v = r.charAt(0) == 't' ? 8000 : r.charAt(0) == 'q' ? 899 : r.charAt(0) == 'n' ? 900 : 0;
            int n = Integer.parseInt(r.substring(1)) * 16;
            for (int i = 0; i < n; i++) { out.write(v & 0xff); out.write((v >> 8) & 0xff); }
        }
        return out.toByteArray();
    }

    /** min_ms|max_ms|pause_ms, runs, block => piece lengths in bytes and the rest, as a|b/rest. */
    private static String segCuts(String params, String runs, int block) {
        String[] p = params.split("\\|");
        Segmenter seg = new Segmenter(Integer.parseInt(p[0]) / 1000.0, Integer.parseInt(p[1]) / 1000.0, Integer.parseInt(p[2]) / 1000.0);
        byte[] pcm = segAudio(runs);
        StringBuilder b = new StringBuilder();
        long total = 0;
        for (int i = 0; i < pcm.length; i += block) {
            for (byte[] piece : seg.feed(pcm, i, Math.min(block, pcm.length - i))) {
                b.append(b.length() == 0 ? "" : "|").append(piece.length);
                total += piece.length;
            }
        }
        byte[] rest = seg.rest();
        if (total + rest.length != pcm.length) throw new IllegalStateException("audio was lost or repeated");
        return b + "/" + rest.length;
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

    /**
     * Golden history rows (see timing_view in spec/golden.txt) as the history holds them: a map per row. The "timing" of a
     * timed row is made by Timing.historyMap, the same call Prefs.addHistory writes it with, so a renamed key on the writing
     * side fails here and not only on a phone.
     */
    private static List<Object> historyRows(String rows) {
        List<Object> out = new ArrayList<>();
        for (String r : items(rows, ";")) {
            String[] p = r.split("@", 7);
            Map<String, Object> h = new LinkedHashMap<>();
            if (!p[0].isEmpty()) h.put("t", Long.parseLong(p[0]));
            if (!p[1].isEmpty()) h.put("app", p[1]);
            if (!p[2].isEmpty()) h.put("words", Long.parseLong(p[2]));
            if (p.length == 4) {
                h.put("timing", "x");
            } else if (p.length == 7) {
                h.put("timing", Timing.historyMap(new Timing.Entry(kv(p[6]), p[3], p[4], "p", p[5].equals("1"))));
            }
            out.add(h);
        }
        return out;
    }

    /** The `last_seen;name` items of a devices row as PlainJson would give them: "~" leaves last_seen out, a number is a Double, other text stays text. */
    private static List<Map<String, Object>> relayDevices(String field) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (String item : items(field, "|")) {
            int i = item.indexOf(';');
            Map<String, Object> d = new LinkedHashMap<>();
            d.put("name", item.substring(i + 1));
            String seen = item.substring(0, i);
            if (!seen.equals("~")) {
                Object v = seen;
                try {
                    v = Double.valueOf(seen);
                } catch (NumberFormatException text) {
                    // stays text
                }
                d.put("last_seen", v);
            }
            out.add(d);
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    private static String timingView(String rows, int n, int last) {
        Map<String, Object> v = Timing.speedView(historyRows(rows), n, last);
        Map<String, Object> stages = (Map<String, Object>) v.get("stages");
        StringBuilder b = new StringBuilder("count=" + v.get("count") + " biggest=" + v.get("biggest"));
        for (String k : Timing.STAGES) {
            Map<String, Object> m = (Map<String, Object>) stages.get(k);
            b.append(' ').append(k).append('=').append(m.get("median")).append('/').append(m.get("p90"));
        }
        StringBuilder models = new StringBuilder();
        for (Object o : (List<Object>) v.get("models")) {
            Map<String, Object> m = (Map<String, Object>) o;
            models.append(models.length() == 0 ? "" : ";").append(m.get("stt_model")).append('+').append(m.get("llm_model"))
                    .append(" n=").append(m.get("count")).append(" stt=").append(m.get("stt")).append(" llm=").append(m.get("llm"))
                    .append(" total=").append(m.get("total"));
        }
        StringBuilder recent = new StringBuilder();
        for (Object o : (List<Object>) v.get("last")) {
            Map<String, Object> m = (Map<String, Object>) o;
            Map<String, Object> st = (Map<String, Object>) m.get("stages");
            StringBuilder s = new StringBuilder();
            for (String k : Timing.STAGES) s.append(s.length() == 0 ? "" : ",").append(k).append('=').append(st.containsKey(k) ? st.get(k) : 0L);
            recent.append(recent.length() == 0 ? "" : "|").append(((Number) m.get("t")).longValue()).append('@').append(m.get("app")).append('@')
                    .append(((Number) m.get("words")).longValue()).append('@').append(m.get("stt_model")).append('@').append(m.get("llm_model"))
                    .append('@').append(Boolean.TRUE.equals(m.get("relay")) ? 1 : 0).append('@').append(s);
        }
        return b + " models=" + models + " last=" + recent;
    }

    private static String devicesText(List<DevicesView.Row> rows) {
        StringBuilder sb = new StringBuilder();
        for (DevicesView.Row r : rows) {
            if (sb.length() > 0) sb.append('|');
            sb.append(r.state).append(';').append(r.thisDevice ? "true" : "false").append(';').append(r.ago).append(';').append(r.name);
        }
        return sb.toString();
    }

    /** The `k=v;k=v` fields of a relaycheck row as PlainJson would give them: s:text is a String, n:number a Long (or a Double when written with a point), b:true / b:false a Boolean; empty is no usable answer (null). */
    private static Map<String, Object> healthAnswer(String field) {
        if (field.isEmpty()) return null;
        Map<String, Object> out = new LinkedHashMap<>();
        for (String item : field.split(";")) {
            int eq = item.indexOf('='), colon = item.indexOf(':', eq);
            String key = item.substring(0, eq), text = item.substring(colon + 1);
            char type = item.charAt(eq + 1);
            out.put(key, type == 's' ? text : type == 'b' ? (Object) Boolean.valueOf(text.equals("true"))
                    : text.contains(".") ? (Object) Double.valueOf(text) : (Object) Long.valueOf(text));
        }
        return out;
    }

    private static String relayCheckText(RelayCheck.Result r) {
        return r.ok + ";" + r.reachable + ";" + r.tokenOk + ";" + r.relayVersion + ";" + r.notes;
    }

    /** wrong=>right pairs separated by ; */
    private static List<String[]> pairs(String field) {
        List<String[]> out = new ArrayList<>();
        for (String p : items(field, ";")) out.add(new String[] {p.substring(0, p.indexOf("=>")), p.substring(p.indexOf("=>") + 2)});
        return out;
    }

    private static String pairsText(List<String[]> ps) {
        StringBuilder sb = new StringBuilder();
        for (String[] p : ps) sb.append(sb.length() == 0 ? "" : ";").append(p[0]).append("=>").append(p[1]);
        return sb.toString();
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
                case "whisperctx":   // terms, context => the speech-to-text prompt of a piece of a long recording
                    eq(ln, kind, f[2], ApiClient.whisperPromptWith(items(f[0], "|"), f[1]));
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
                case "promptstrength":
                    eq(ln, kind, f[5], ApiClient.systemPrompt(f[1], items(f[2], "|"), f[3], f[4], f[0]));
                    break;
                case "context":
                    eq(ln, kind, f[1], ApiClient.cleanContext(f[0]));
                    break;
                case "rules":   // text => my_cleanup_rules made safe for the prompt
                    eq(ln, kind, f[1], ApiClient.cleanRules(f[0]));
                    break;
                case "promptrules":   // strength, style, terms, app, About you, my cleanup rules => the cleanup prompt
                    eq(ln, kind, f[6], ApiClient.systemPrompt(f[1], items(f[2], "|"), f[3], f[4], f[0], f[5]));
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
                case "privatehost":   // host, whether plain http may go there
                    eq(ln, kind, f[1], Endpoint.isPrivateHost(f[0]) ? "true" : "false");
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
                case "timing_view":   // history rows, n, last => the whole Speed card as one line
                    eq(ln, kind, f[3], timingView(f[0], Integer.parseInt(f[1]), Integer.parseInt(f[2])));
                    break;
                case "segcuts":   // min_ms|max_ms|pause_ms, runs, block => piece lengths / rest length
                    eq(ln, kind, f[3], segCuts(f[0], f[1], Integer.parseInt(f[2])));
                    break;
                case "devices":   // now, this device's name, the relay's devices, the rows the card shows
                    eq(ln, kind, f[3], devicesText(DevicesView.rows(relayDevices(f[2]), Double.parseDouble(f[0]), f[1])));
                    break;
                case "relaycheck":   // HTTP status of /health (0 = no answer), the answer's fields, ok;reachable;token_ok;relay_version;notes
                    eq(ln, kind, f[2], relayCheckText(RelayCheck.of(Integer.parseInt(f[0]), healthAnswer(f[1]), "dev", "failure")));
                    break;
                case "bubbleclamp": {   // x, y, screen w, screen h, bubble w, bubble h, expected "x,y"
                    int[] p = BubbleLogic.clamp(Integer.parseInt(f[0]), Integer.parseInt(f[1]), Integer.parseInt(f[2]),
                            Integer.parseInt(f[3]), Integer.parseInt(f[4]), Integer.parseInt(f[5]));
                    eq(ln, kind, f[6], p[0] + "," + p[1]);
                    break;
                }
                case "bubbleshow":   // only typing, always show, field focused, screen on, service ready, expected
                    eq(ln, kind, f[5], BubbleLogic.shouldShow(f[0].equals("true"), f[1].equals("true"), f[2].equals("true"),
                            f[3].equals("true"), f[4].equals("true")) ? "true" : "false");
                    break;
                case "bubbleaction":   // wanted, shown, window still attached, expected none|add|remove|repair
                    eq(ln, kind, f[3], BubbleLogic.action(f[0].equals("true"), f[1].equals("true"), f[2].equals("true")));
                    break;
                case "fidelity":   // strength, raw, cleaned, whether the cleanup kept enough of the spoken words
                    eq(ln, kind, f[3], Fidelity.ok(f[1], f[2], f[0]) ? "true" : "false");
                    break;
                case "tokens":   // text, its word tokens joined by |
                    eq(ln, kind, f[1], String.join("|", Fidelity.wordTokens(f[0])));
                    break;
                case "recall":   // raw, cleaned, share of raw's words still in cleaned (3 decimals)
                    eq(ln, kind, f[2], String.format(java.util.Locale.ROOT, "%.3f", Fidelity.wordRecall(f[0], f[1])));
                    break;
                case "cleanstrength":   // the stored setting, the strength it means
                    eq(ln, kind, f[1], Fidelity.cleanStrength(f[0]));
                    break;
                case "fallback":   // raw words, the text used when the fidelity guard rejects the cleanup
                    eq(ln, kind, f[1], ApiClient.fallbackText(f[0]));
                    break;
                case "fuzzydict":   // terms, text, the text with the dictionary's spellings applied
                    eq(ln, kind, f[2], Terms.fuzzy(f[1], items(f[0], "|")));
                    break;
                case "notebubble":   // persistent switch, note recording, note being saved, expected
                    eq(ln, kind, f[3], NoteBubbleLogic.visible(f[0].equals("true"), f[1].equals("true"), f[2].equals("true")) ? "true" : "false");
                    break;
                case "autocorrect":   // text Vox typed, the field's whole text now, the corrections found
                    eq(ln, kind, f[2], pairsText(AutoLearn.detect(f[0], f[1])));
                    break;
                case "autolearn": {   // replacements, words, pairs found => the replacements and words added
                    AutoLearn.Learned l = AutoLearn.learn(pairs(f[0]), items(f[1], "|"), pairs(f[2]));
                    eq(ln, kind, f[3] + " / " + f[4], pairsText(l.replacements) + " / " + String.join("|", l.words));
                    break;
                }
                default:
                    System.err.println("FAIL line " + ln + ": unknown case kind " + kind);
                    System.exit(1);
            }
        }
        System.out.println("OK: " + checks + " golden cases passed");
    }
}
