package com.minhaj.vox;

import java.util.Arrays;

/** Plain-Java checks for the Note value class. Run by CI, exits non-zero on failure. */
public final class NoteTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) {
        // a new note has the same defaults as a row of notes.py (empty text fields, source "voice note", nothing pending)
        Note n = new Note();
        eq("source constant", "voice note", Note.SOURCE_NOTE);
        eq("id", "", n.id);
        eq("source", "voice note", n.source);
        eq("title", "", n.title);
        eq("text", "", n.text);
        eq("raw", "", n.raw);
        eq("createdAt", 0.0, n.createdAt);
        eq("updatedAt", 0.0, n.updatedAt);
        eq("secs", 0.0, n.secs);
        eq("device", "", n.device);
        eq("tags", 0, n.tags.size());
        eq("deleted", false, n.deleted);
        eq("dirty", false, n.dirty);
        eq("seq", 0L, n.seq);

        // copy: every field, and a tag list of its own
        n.id = "abc";
        n.source = "meeting";
        n.title = "T";
        n.text = "body";
        n.raw = "raw body";
        n.createdAt = 1790000000.5;
        n.updatedAt = 1790000001.25;
        n.secs = 12.5;
        n.device = "Pixel 7";
        n.tags.addAll(Arrays.asList("a", "b"));
        n.deleted = true;
        n.dirty = true;
        n.seq = 42;
        Note c = n.copy();
        eq("copy id", "abc", c.id);
        eq("copy source", "meeting", c.source);
        eq("copy title", "T", c.title);
        eq("copy text", "body", c.text);
        eq("copy raw", "raw body", c.raw);
        eq("copy createdAt", 1790000000.5, c.createdAt);
        eq("copy updatedAt", 1790000001.25, c.updatedAt);
        eq("copy secs", 12.5, c.secs);
        eq("copy device", "Pixel 7", c.device);
        eq("copy tags", Arrays.asList("a", "b"), c.tags);
        eq("copy deleted", true, c.deleted);
        eq("copy dirty", true, c.dirty);
        eq("copy seq", 42L, c.seq);
        c.tags.add("c");
        c.title = "changed";
        eq("copy has its own tag list", Arrays.asList("a", "b"), n.tags);
        eq("copy has its own fields", "T", n.title);

        System.out.println("OK: " + checks + " checks passed");
    }
}
