package com.minhaj.vox;

import java.util.List;

/**
 * What the relay sync needs from the notes on this device: the five store functions windows/sync.py calls on
 * windows/notes.py (dirty_notes, mark_synced, apply_remote, get_meta, set_meta). NotesStore implements it on the
 * phone; the off-device tests use an in-memory one. Pure Java so the sync code stays testable without a device.
 */
interface SyncStore {
    /** Notes and delete markers changed here since they were last sent, oldest change first, at most {@code limit}. */
    List<Note> dirtyNotes(int limit);

    /**
     * The relay has this version of the note and numbered it {@code seq}. Clears the dirty flag only when the note
     * still has the {@code updated_at} that was sent (it was not changed again while it was being sent).
     */
    void markSynced(String id, double sentUpdatedAt, long seq);

    /** Merges a note or delete marker received from the relay (see NoteLogic.remoteWins). True when the local copy changed. */
    boolean applyRemote(Note n);

    /** A sync setting kept next to the notes (relay_cursor, profile_version, profile_snapshot); {@code d} when unset. */
    String getMeta(String k, String d);

    void setMeta(String k, String v);

    /**
     * Marks every note and every delete marker as not yet sent (dirty), so the next push sends them all. Used when the
     * app is pointed at another relay, which has none of them.
     */
    void markAllDirty();
}
