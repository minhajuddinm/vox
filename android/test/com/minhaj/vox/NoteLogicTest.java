package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * Plain-Java checks for NoteLogic beyond the golden rows (title, ftsq, remotewins in spec/golden.txt, run by
 * ParityTest). Run by CI, exits non-zero on failure.
 */
public final class NoteLogicTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    /** Every character Python's str.split() treats as a separator (str.isspace(), Unicode 15.1). */
    private static final int[] PY_SPACE = {
        0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0, 0x1680,
        0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A,
        0x2028, 0x2029, 0x202F, 0x205F, 0x3000};

    private static List<String> tags(String... t) {
        return new ArrayList<>(Arrays.asList(t));
    }

    public static void main(String[] args) {
        eq("PUSH_BATCH", 100, NoteLogic.PUSH_BATCH);

        // autoTitle: first 7 words, "..." when there are more (rows of spec/golden.txt cover the typical inputs)
        eq("title of null", "", NoteLogic.autoTitle(null));
        eq("title of blanks", "", NoteLogic.autoTitle(" \t\n "));
        eq("title keeps exactly 7", "a b c d e f g", NoteLogic.autoTitle("a b c d e f g"));
        eq("title cuts the 8th", "a b c d e f g...", NoteLogic.autoTitle("a b c d e f g h"));
        eq("title cuts a long note", "a b c d e f g...", NoteLogic.autoTitle("a b c d e f g h i j k l m n o p"));
        eq("title of a non-BMP word", "😀 x", NoteLogic.autoTitle("😀 x"));
        for (int cp : PY_SPACE) {
            String c = new String(Character.toChars(cp));
            eq("separator U+" + Integer.toHexString(cp), "a b", NoteLogic.autoTitle("a" + c + "b"));
            eq("padding U+" + Integer.toHexString(cp), "a", NoteLogic.autoTitle(c + "a" + c));
        }
        for (String glue : new String[] {"​", "⁠", "﻿", "᠎", "_", "­"}) {
            eq("not a separator " + Integer.toHexString(glue.charAt(0)), "a" + glue + "b", NoteLogic.autoTitle("a" + glue + "b"));
        }

        // ftsQuery: every word a quoted prefix token
        eq("fts of null", "", NoteLogic.ftsQuery(null));
        eq("fts of empty", "", NoteLogic.ftsQuery(""));
        eq("fts of words", "\"hello\"* \"world\"*", NoteLogic.ftsQuery("  hello,   world! "));
        eq("fts drops quotes", "\"x\"* \"OR\"* \"y\"*", NoteLogic.ftsQuery("x\" OR \"y"));
        eq("fts of punctuation only", "", NoteLogic.ftsQuery("*()-:^"));

        // searchWords: what the LIKE fallback of the notes store matches one by one (ftsQuery is built from the same words)
        eq("words of null", tags(), NoteLogic.searchWords(null));
        eq("words of empty", tags(), NoteLogic.searchWords(""));
        eq("words split on punctuation", tags("hello", "world"), NoteLogic.searchWords("  hello,   world! "));
        eq("words keep underscore and digits", tags("a_1", "b", "c"), NoteLogic.searchWords("a_1 b-c"));
        eq("words drop quotes", tags("x", "OR", "y"), NoteLogic.searchWords("x\" OR \"y"));
        eq("words of punctuation only", tags(), NoteLogic.searchWords("*()-:^"));
        eq("words keep accents", tags("café", "naïve"), NoteLogic.searchWords("café naïve"));
        eq("words skip an emoji", tags("x"), NoteLogic.searchWords("😀 x"));

        // strip: Python's str.strip() (used for the text and the title a note is saved with)
        eq("strip of null", "", NoteLogic.strip(null));
        eq("strip of blanks", "", NoteLogic.strip(" \t\n　"));
        eq("strip keeps the inside", "a  b", NoteLogic.strip("  a  b \n"));
        for (int cp : PY_SPACE) {
            String c = new String(Character.toChars(cp));
            eq("strip U+" + Integer.toHexString(cp), "a b", NoteLogic.strip(c + "a b" + c));
        }

        // remoteWins: a note from the relay replaces the local copy only when it is strictly newer (or new)
        eq("unknown id, live note", true, NoteLogic.remoteWins(false, 0, 5, false));
        eq("unknown id, delete marker is ignored", false, NoteLogic.remoteWins(false, 0, 5, true));
        eq("newer wins", true, NoteLogic.remoteWins(true, 5, 6, false));
        eq("tie keeps local", false, NoteLogic.remoteWins(true, 6, 6, false));
        eq("older loses", false, NoteLogic.remoteWins(true, 7, 6, false));
        eq("newer delete wins", true, NoteLogic.remoteWins(true, 5, 6, true));
        eq("older delete loses", false, NoteLogic.remoteWins(true, 7, 6, true));
        eq("epoch seconds with fractions", true, NoteLogic.remoteWins(true, 1790000000.5, 1790000000.75, false));
        eq("same epoch instant", false, NoteLogic.remoteWins(true, 1790000000.5, 1790000000.5, false));

        // cleanTags: trim, drop empties, drop repeats (first one stays), at most 20
        eq("tags of null", tags(), NoteLogic.cleanTags(null));
        eq("tags unchanged", tags("a", "b"), NoteLogic.cleanTags(tags("a", "b")));
        eq("tags trimmed", tags("a", "b"), NoteLogic.cleanTags(tags(" a ", "\tb\n")));
        eq("tags trimmed like Python strip", tags("x"), NoteLogic.cleanTags(tags(" x　")));
        eq("tags drop empties and nulls", tags("a"), NoteLogic.cleanTags(tags("", "  ", null, "a")));
        eq("tags drop repeats keeping order", tags("a", "b", "c"), NoteLogic.cleanTags(tags("a", "b", "a", "c", "b")));
        eq("tags repeat after trimming", tags("a"), NoteLogic.cleanTags(tags("a", " a")));
        eq("tags are case sensitive", tags("Work", "work"), NoteLogic.cleanTags(tags("Work", "work")));

        List<String> many = new ArrayList<>();
        for (int i = 0; i < 25; i++) many.add("t" + i);
        List<String> first20 = new ArrayList<>(many.subList(0, 20));
        eq("tags capped at 20, in order", first20, NoteLogic.cleanTags(many));
        eq("tags input untouched", 25, many.size());
        List<String> repeated = new ArrayList<>();
        for (int round = 0; round < 3; round++) for (int i = 0; i < 10; i++) repeated.add("t" + i);
        eq("repeats do not use up the cap", new ArrayList<>(many.subList(0, 10)), NoteLogic.cleanTags(repeated));
        List<String> in = tags("a", "a");
        List<String> out = NoteLogic.cleanTags(in);
        out.add("changed");
        eq("result is a new list", tags("a", "a"), in);
        eq("empty result is a new list", Collections.emptyList(), NoteLogic.cleanTags(tags("", " ")));

        System.out.println("OK: " + checks + " checks passed");
    }
}
