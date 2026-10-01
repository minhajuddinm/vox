package com.minhaj.vox;

import java.util.ArrayList;
import java.util.List;

/**
 * One voice note, or the marker a deleted note leaves behind, as the notes store keeps it. A plain value holder
 * with the columns of the notes table in windows/notes.py (same names in camel case). Pure Java (no android.* or
 * org.json) so the sync code and its off-device tests can use it; the fields are set directly.
 */
final class Note {
    /** The source of a note recorded on its own (notes.SOURCE_NOTE). */
    static final String SOURCE_NOTE = "voice note";

    /** 32 lowercase hex characters. */
    String id = "";
    String source = SOURCE_NOTE;
    String title = "";
    String text = "";
    /** The transcript before the AI cleanup. */
    String raw = "";
    /** Unix seconds. */
    double createdAt;
    /** Unix seconds; the newer one wins when two devices changed the same note. */
    double updatedAt;
    /** How long the recording was. */
    double secs;
    /** The device that recorded the note. */
    String device = "";
    List<String> tags = new ArrayList<>();
    /** A delete marker: the text fields are empty, the row is kept so a sync can tell the other devices. */
    boolean deleted;
    /** Changed here and not yet accepted by the relay. */
    boolean dirty;
    /** The relay's sequence number for this note, 0 when unknown. */
    long seq;

    /** A copy that shares nothing with this note (the tag list is new). */
    Note copy() {
        Note n = new Note();
        n.id = id;
        n.source = source;
        n.title = title;
        n.text = text;
        n.raw = raw;
        n.createdAt = createdAt;
        n.updatedAt = updatedAt;
        n.secs = secs;
        n.device = device;
        n.tags = new ArrayList<>(tags);
        n.deleted = deleted;
        n.dirty = dirty;
        n.seq = seq;
        return n;
    }
}
