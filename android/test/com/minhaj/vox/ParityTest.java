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
                    eq(ln, kind, f[1], GroqClient.sanitize(f[0]));
                    break;
                case "looks_valid":
                    eq(ln, kind, f[2], GroqClient.looksValid(f[0], f[1]) ? "true" : "false");
                    break;
                case "replace": {
                    Map<String, String> repl = new LinkedHashMap<>();
                    for (String p : items(f[1], ";")) repl.put(p.substring(0, p.indexOf("=>")), p.substring(p.indexOf("=>") + 2));
                    eq(ln, kind, f[2], GroqClient.applyReplacements(f[0], repl));
                    break;
                }
                case "whisper":
                    eq(ln, kind, f[1], GroqClient.whisperPrompt(items(f[0], "|")));
                    break;
                case "terms": {
                    String dict = f[1].replace("|", "\n");
                    String people = f[0].replace("|", "\n");
                    eq(ln, kind, f[2], String.join("|", Terms.terms(people, dict)));
                    break;
                }
                case "prompt":
                    eq(ln, kind, f[3], GroqClient.systemPrompt(f[0], items(f[1], "|"), f[2]));
                    break;
                case "spoken":
                    eq(ln, kind, f[1], GroqClient.applySpokenCommands(f[0]));
                    break;
                case "models":
                    eq(ln, kind, f[1], Providers.classify(f[0]));
                    break;
                case "silence":
                    eq(ln, kind, f[1], GroqClient.isSilenceHallucination(f[0]) ? "true" : "false");
                    break;
                default:
                    System.err.println("FAIL line " + ln + ": unknown case kind " + kind);
                    System.exit(1);
            }
        }
        System.out.println("OK: " + checks + " golden cases passed");
    }
}
