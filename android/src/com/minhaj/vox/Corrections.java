package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

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
        int i = 0, j = 0;
        for (int[] block : matchingBlocks(a, b)) {   // the "replace" opcodes of difflib, in order
            if (i < block[0] && j < block[1]) consider(out, aRaw, a, b, i, block[0], j, block[1], maxWords);
            i = block[0] + block[2];
            j = block[1] + block[2];
        }
        return out;
    }

    /**
     * difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks(), ported so that the phone and the PC split
     * an edit into the same swaps: {i, j, size} blocks in order, ending with {len(a), len(b), 0}.
     */
    static List<int[]> matchingBlocks(String[] a, String[] b) {
        Map<String, List<Integer>> b2j = new HashMap<>();
        for (int j = 0; j < b.length; j++) {
            List<Integer> at = b2j.get(b[j]);
            if (at == null) b2j.put(b[j], at = new ArrayList<>());
            at.add(j);
        }
        List<int[]> blocks = new ArrayList<>();
        List<int[]> queue = new ArrayList<>();
        queue.add(new int[] {0, a.length, 0, b.length});
        while (!queue.isEmpty()) {
            int[] q = queue.remove(queue.size() - 1);
            int[] x = longestMatch(a, b, b2j, q[0], q[1], q[2], q[3]);
            if (x[2] > 0) {
                blocks.add(x);
                if (q[0] < x[0] && q[2] < x[1]) queue.add(new int[] {q[0], x[0], q[2], x[1]});
                if (x[0] + x[2] < q[1] && x[1] + x[2] < q[3]) queue.add(new int[] {x[0] + x[2], q[1], x[1] + x[2], q[3]});
            }
        }
        Collections.sort(blocks, (p, r) -> p[0] != r[0] ? Integer.compare(p[0], r[0])
                : p[1] != r[1] ? Integer.compare(p[1], r[1]) : Integer.compare(p[2], r[2]));
        List<int[]> out = new ArrayList<>();
        int i1 = 0, j1 = 0, k1 = 0;
        for (int[] x : blocks) {   // adjacent blocks become one
            if (i1 + k1 == x[0] && j1 + k1 == x[1]) {
                k1 += x[2];
            } else {
                if (k1 > 0) out.add(new int[] {i1, j1, k1});
                i1 = x[0]; j1 = x[1]; k1 = x[2];
            }
        }
        if (k1 > 0) out.add(new int[] {i1, j1, k1});
        out.add(new int[] {a.length, b.length, 0});
        return out;
    }

    /** difflib's find_longest_match without junk: the first longest common run of a[alo:ahi] and b[blo:bhi]. */
    private static int[] longestMatch(String[] a, String[] b, Map<String, List<Integer>> b2j, int alo, int ahi, int blo, int bhi) {
        int besti = alo, bestj = blo, bestsize = 0;
        Map<Integer, Integer> j2len = new HashMap<>();
        for (int i = alo; i < ahi; i++) {
            Map<Integer, Integer> newj2len = new HashMap<>();
            List<Integer> js = b2j.get(a[i]);
            if (js != null) {
                for (int j : js) {
                    if (j < blo) continue;
                    if (j >= bhi) break;
                    Integer prev = j2len.get(j - 1);
                    int k = (prev == null ? 0 : prev) + 1;
                    newj2len.put(j, k);
                    if (k > bestsize) { besti = i - k + 1; bestj = j - k + 1; bestsize = k; }
                }
            }
            j2len = newj2len;
        }
        while (besti > alo && bestj > blo && a[besti - 1].equals(b[bestj - 1])) { besti--; bestj--; bestsize++; }
        while (besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize].equals(b[bestj + bestsize])) bestsize++;
        return new int[] {besti, bestj, bestsize};
    }

    private static void consider(List<String[]> out, String[] aRaw, String[] a, String[] b,
                                 int i1, int i2, int j1, int j2, int maxWords) {
        int dels = i2 - i1, ins = j2 - j1;
        if (dels == 0 || ins == 0 || dels > maxWords || ins > maxWords) return;
        String wrong = ApiClient.pyStrip(join(a, i1, i2)), right = ApiClient.pyStrip(join(b, j1, j2));
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
