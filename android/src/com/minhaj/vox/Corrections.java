package com.minhaj.vox;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Finds the word replacements a user made when fixing a dictation, so they can be added to the dictionary
 * as "wrong => right". Same rules as suggest_corrections in windows/vox_core.py. Pure Java, tested off-device.
 */
final class Corrections {
    private static final String EDGE = ".,;:!?\"'()[]{}";
    private static final int MAX_TOKENS = 1500;   // the diff table is tokens x tokens

    private Corrections() { }

    /** Returns [wrong, right] pairs. Only swaps of up to maxWords words count; added or removed words do not. */
    static List<String[]> suggest(String original, String edited, int maxWords) {
        List<String[]> out = new ArrayList<>();
        String[] aRaw = tokens(original), bRaw = tokens(edited);
        if (aRaw.length > MAX_TOKENS || bRaw.length > MAX_TOKENS) return out;
        String[] a = strip(aRaw), b = strip(bRaw);
        int n = a.length, m = b.length;

        int[][] lcs = new int[n + 1][m + 1];   // longest common run of words from i and j onwards
        for (int i = n - 1; i >= 0; i--) {
            for (int j = m - 1; j >= 0; j--) {
                lcs[i][j] = a[i].equals(b[j]) ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
            }
        }

        int i = 0, j = 0;
        while (i < n || j < m) {
            if (i < n && j < m && a[i].equals(b[j])) { i++; j++; continue; }
            int i1 = i, j1 = j;   // a run of differences starts here
            while ((i < n || j < m) && !(i < n && j < m && a[i].equals(b[j]))) {
                if (j >= m || (i < n && lcs[i + 1][j] >= lcs[i][j + 1])) i++; else j++;
            }
            consider(out, aRaw, a, b, i1, i, j1, j, maxWords);
        }
        return out;
    }

    private static void consider(List<String[]> out, String[] aRaw, String[] a, String[] b,
                                 int i1, int i2, int j1, int j2, int maxWords) {
        int dels = i2 - i1, ins = j2 - j1;
        if (dels == 0 || ins == 0 || dels > maxWords || ins > maxWords) return;
        String wrong = join(a, i1, i2).trim(), right = join(b, j1, j2).trim();
        if (wrong.length() < 2 || right.isEmpty() || wrong.equals(right)) return;
        boolean startsSentence = i1 == 0 || ".?!".indexOf(aRaw[i1 - 1].charAt(aRaw[i1 - 1].length() - 1)) >= 0;
        if (wrong.toLowerCase(Locale.ROOT).equals(right.toLowerCase(Locale.ROOT)) && startsSentence) return;
        for (String[] p : out) if (p[0].equals(wrong) && p[1].equals(right)) return;
        out.add(new String[] {wrong, right});
    }

    /** Python's str.split(): runs of non-space characters, the no-break and ideographic spaces counting as spaces. */
    private static String[] tokens(String s) {
        List<String> out = new ArrayList<>();
        int start = -1, n = s == null ? 0 : s.length();
        for (int i = 0; i <= n; i++) {
            boolean space = i == n || ApiClient.isPyWhitespace(s.charAt(i));
            if (space && start >= 0) { out.add(s.substring(start, i)); start = -1; }
            else if (!space && start < 0) start = i;
        }
        return out.toArray(new String[0]);
    }

    private static String[] strip(String[] raw) {
        String[] out = new String[raw.length];
        for (int k = 0; k < raw.length; k++) out[k] = stripEdges(raw[k]);
        return out;
    }

    private static String stripEdges(String t) {
        int s = 0, e = t.length();
        while (s < e && EDGE.indexOf(t.charAt(s)) >= 0) s++;
        while (e > s && EDGE.indexOf(t.charAt(e - 1)) >= 0) e--;
        return t.substring(s, e);
    }

    private static String join(String[] t, int from, int to) {
        StringBuilder sb = new StringBuilder();
        for (int k = from; k < to; k++) {
            if (sb.length() > 0) sb.append(' ');
            sb.append(t[k]);
        }
        return sb.toString();
    }
}
