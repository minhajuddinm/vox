package com.minhaj.vox;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

/**
 * Plain-Java checks for the list pass beyond the shared golden rows (kind structure): running it twice changes nothing on
 * every golden row, the setting is read like the Python one, and every word that is not a cue stays, in order. The same
 * checks are in tests/test_structure.py for the Python twin.
 */
public final class StructureTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

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

    public static void main(String[] args) throws Exception {
        eq("mode banana", "auto", Structure.mode("banana"));
        eq("mode null", "auto", Structure.mode(null));
        eq("mode lists", "lists", Structure.mode(" Lists "));
        eq("mode off", "off", Structure.mode("OFF"));

        int rows = 0;
        for (String line : Files.readAllLines(Paths.get("spec/golden.txt"), StandardCharsets.UTF_8)) {
            if (!line.startsWith("structure\t")) continue;
            String[] f = line.split("\t", -1);
            String once = Structure.format(unesc(f[3]), f[1], f[2]);
            eq("twice changes nothing: " + f[3], once, Structure.format(once, f[1], f[2]));
            rows++;
        }
        if (rows < 30) eq("golden structure rows", ">= 30", String.valueOf(rows));

        String text = "So my plan is, first, we fix the login bug, second, we write the tests, third, we ship it on Friday.";
        String out = Structure.format(text, "auto", "formal");
        List<String> spoken = new ArrayList<>();
        for (String w : Fidelity.wordTokens(text)) if (!w.equals("first") && !w.equals("second") && !w.equals("third")) spoken.add(w);
        List<String> typed = new ArrayList<>();
        for (String w : Fidelity.wordTokens(out)) if (!w.matches("[0-9]+")) typed.add(w);
        eq("every other word kept in order", spoken, typed);
        eq("null stays null", null, Structure.format(null, "auto", "neutral"));
        eq("null style is neutral", "1. Milk\n2. Eggs", Structure.format("First, milk. Second, eggs.", "auto", null));
        System.out.println("OK: " + checks + " checks");
    }
}
