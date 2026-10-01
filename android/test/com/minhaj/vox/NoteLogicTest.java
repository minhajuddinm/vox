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

    /** s repeated n times (String.repeat needs Java 11; the app is Java 8). */
    private static String rep(String s, int n) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < n; i++) b.append(s);
        return b.toString();
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

        // cleanTags drops double quotes before it trims, like notes._tags (a quote would break the stored JSON filter)
        eq("tag quotes removed inside", tags("ab"), NoteLogic.cleanTags(tags("a\"b")));
        eq("tag quotes removed around", tags("x"), NoteLogic.cleanTags(tags("\"x\"")));
        eq("tag quotes then trim", tags("a"), NoteLogic.cleanTags(tags(" \" a \" ")));
        eq("tag of only quotes is dropped", tags(), NoteLogic.cleanTags(tags("\"\"", "\" \"")));
        eq("tag repeat after quote removal", tags("a"), NoteLogic.cleanTags(tags("a", "\"a\"")));

        // deviceName: the typed name, else the phone model, else "android-phone"; trimmed the Python way, then cut at
        // 60 code points (sync.device_name; the golden rows devname cover the typed-name cases)
        eq("device name typed", "My phone", NoteLogic.deviceName("My phone", "Pixel 7"));
        eq("device name trimmed", "My phone", NoteLogic.deviceName("  My phone \t", "Pixel 7"));
        eq("device name trimmed like Python strip", "x", NoteLogic.deviceName(" x　", "Pixel 7"));
        eq("device name blank uses the model", "Pixel 7", NoteLogic.deviceName("", "Pixel 7"));
        eq("device name null uses the model", "Pixel 7", NoteLogic.deviceName(null, "Pixel 7"));
        eq("device name blanks use the model", "Pixel 7", NoteLogic.deviceName(" \t ", "Pixel 7"));
        eq("device model is trimmed", "Pixel 7", NoteLogic.deviceName("", "  Pixel 7 \n"));
        eq("device without a model", "android-phone", NoteLogic.deviceName("", ""));
        eq("device with a null model", "android-phone", NoteLogic.deviceName(null, null));
        eq("device with a blank model", "android-phone", NoteLogic.deviceName("  ", " \t"));
        eq("device name 60 kept", rep("a", 60), NoteLogic.deviceName(rep("a", 60), "m"));
        eq("device name 61 cut", rep("a", 60), NoteLogic.deviceName(rep("a", 61), "m"));
        eq("device model cut", rep("m", 60), NoteLogic.deviceName("", rep("m", 70)));
        String grin = new String(Character.toChars(0x1F600));
        String cutAtPair = NoteLogic.deviceName(rep("a", 59) + grin + "b", "m");
        eq("device name cut keeps a whole pair", rep("a", 59) + grin, cutAtPair);
        eq("device name cut is 60 code points", 60, cutAtPair.codePointCount(0, cutAtPair.length()));
        String pairs = NoteLogic.deviceName(rep(grin, 61), "m");
        eq("device name of 61 emoji is 60 emoji", rep(grin, 60), pairs);
        eq("device name has no lone surrogate", false, Character.isHighSurrogate(pairs.charAt(pairs.length() - 1)));
        eq("device name is trimmed before the cut, not after", rep("a", 59) + " ", NoteLogic.deviceName("  " + rep("a", 59) + " b", "m"));

        // periodStart: the "created since" bound of the period filter (Unix seconds; the local midnight for "today")
        java.util.TimeZone utc = java.util.TimeZone.getTimeZone("UTC");
        java.util.TimeZone kolkata = java.util.TimeZone.getTimeZone("Asia/Kolkata");
        java.util.TimeZone newYork = java.util.TimeZone.getTimeZone("America/New_York");
        eq("period null", null, NoteLogic.periodStart(null, 1790000000.0, utc));
        eq("period empty", null, NoteLogic.periodStart("", 1790000000.0, utc));
        eq("period all", null, NoteLogic.periodStart("all", 1790000000.0, utc));
        eq("period unknown", null, NoteLogic.periodStart("banana", 1790000000.0, utc));
        eq("period week", 1790000000.5 - 7 * 86400, NoteLogic.periodStart("week", 1790000000.5, utc));
        eq("period month", 1790000000.5 - 30 * 86400, NoteLogic.periodStart("month", 1790000000.5, utc));
        eq("period today in UTC", 1789948800.0, NoteLogic.periodStart("today", 1790000000.0, utc));
        eq("period today just after midnight", 1790035200.0, NoteLogic.periodStart("today", 1790035201.0, utc));
        eq("period today just before midnight", 1789948800.0, NoteLogic.periodStart("today", 1790035199.0, utc));
        eq("period today is the local midnight", 1789929000.0, NoteLogic.periodStart("today", 1790000000.0, kolkata));
        eq("period today on the spring-forward day", 1772946000.0, NoteLogic.periodStart("today", 1772985600.0, newYork));
        eq("period today on the fall-back day", 1793505600.0, NoteLogic.periodStart("today", 1793552400.0, newYork));

        System.out.println("OK: " + checks + " checks passed");
    }
}
