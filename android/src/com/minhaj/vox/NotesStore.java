package com.minhaj.vox;

import android.content.Context;
import android.database.Cursor;
import android.database.SQLException;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.database.sqlite.SQLiteStatement;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * The voice notes on the phone: one SQLite database ({@code notes.db} in the app's private database folder).
 * A literal port of windows/notes.py: the same tables, columns and SQL, the same meaning of {@code dirty}
 * (changed here and not yet accepted by the relay), {@code seq} (the relay's number for the note) and the marker a
 * deleted note leaves behind. Every decision (title, search words, which side of a sync wins, tag clean-up) is made
 * by NoteLogic, which the off-device tests and spec/golden.txt pin; this class only runs the SQL.
 *
 * The Android SQLite cannot run in the local JVM tests, so this class is checked only by the compile
 * ({@code javatest.cmd compile}) and on a device, and a reviewer should read it line by line against notes.py.
 *
 * Search uses FTS5 when this phone's SQLite has it (tried once, when the database is created) and LIKE otherwise.
 * Methods may be called from any thread: SQLite serializes the writes, and each change is one transaction.
 */
final class NotesStore extends SQLiteOpenHelper implements SyncStore {
    private static final String DB_NAME = "notes.db";
    private static final int DB_VERSION = 1;

    private static NotesStore instance;

    /** True when the database has the notes_fts table. Set by onOpen, before any method below reads it. */
    private volatile boolean useFts;

    /** The one store of the app: a single helper per file keeps a single connection pool. */
    static synchronized NotesStore get(Context c) {
        if (instance == null) instance = new NotesStore(c.getApplicationContext());
        return instance;
    }

    NotesStore(Context c) {
        super(c, DB_NAME, null, DB_VERSION);
        setWriteAheadLoggingEnabled(true);
    }

    // ------------------------------------------------------------------ schema (notes.py _SCHEMA and _connect)

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE IF NOT EXISTS notes ("
                + "id TEXT PRIMARY KEY, "
                + "source TEXT NOT NULL DEFAULT 'voice note', "
                + "title TEXT NOT NULL DEFAULT '', "
                + "text TEXT NOT NULL DEFAULT '', "
                + "raw TEXT NOT NULL DEFAULT '', "
                + "created_at REAL NOT NULL, "
                + "updated_at REAL NOT NULL, "
                + "secs REAL NOT NULL DEFAULT 0, "
                + "device TEXT NOT NULL DEFAULT '', "
                + "tags TEXT NOT NULL DEFAULT '[]', "
                + "deleted INTEGER NOT NULL DEFAULT 0, "
                + "dirty INTEGER NOT NULL DEFAULT 1, "    // 1 = changed here and not yet sent to the relay
                + "seq INTEGER NOT NULL DEFAULT 0)");     // the relay's sequence number for this note, 0 when unknown
        db.execSQL("CREATE INDEX IF NOT EXISTS notes_created ON notes(created_at)");
        db.execSQL("CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)");
        try {
            db.execSQL("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(id UNINDEXED, title, text)");
        } catch (SQLException e) {
            // this SQLite has no FTS5: there is no notes_fts table, so onOpen sets useFts = false and search uses LIKE
        }
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        // version 1 is the only one so far
    }

    @Override
    public void onOpen(SQLiteDatabase db) {
        useFts = hasFts(db);
    }

    /** notes._has_fts: whether the notes_fts table exists. */
    private static boolean hasFts(SQLiteDatabase db) {
        Cursor c = db.rawQuery("SELECT 1 FROM sqlite_master WHERE name = 'notes_fts'", null);
        try {
            return c.moveToFirst();
        } finally {
            c.close();
        }
    }

    private SQLiteDatabase db() {
        return getWritableDatabase();
    }

    // ------------------------------------------------------------------ helpers

    private static double nowSecs() {
        return System.currentTimeMillis() / 1000.0;
    }

    private static String nz(String s) {
        return s == null ? "" : s;
    }

    /** Tags as the JSON list kept in the tags column (notes.py json.dumps). */
    private static String tagsJson(List<String> tags) {
        return new JSONArray(NoteLogic.cleanTags(tags)).toString();
    }

    private static List<String> parseTags(String json) {
        List<String> out = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(json == null || json.isEmpty() ? "[]" : json);
            for (int i = 0; i < a.length(); i++) out.add(a.getString(i));
        } catch (JSONException e) {
            // a damaged value counts as no tags
        }
        return out;
    }

    /** notes._row: one row of {@code SELECT *} (or {@code n.*}) as a Note. */
    private static Note row(Cursor c) {
        Note n = new Note();
        n.id = c.getString(c.getColumnIndexOrThrow("id"));
        n.source = c.getString(c.getColumnIndexOrThrow("source"));
        n.title = c.getString(c.getColumnIndexOrThrow("title"));
        n.text = c.getString(c.getColumnIndexOrThrow("text"));
        n.raw = c.getString(c.getColumnIndexOrThrow("raw"));
        n.createdAt = c.getDouble(c.getColumnIndexOrThrow("created_at"));
        n.updatedAt = c.getDouble(c.getColumnIndexOrThrow("updated_at"));
        n.secs = c.getDouble(c.getColumnIndexOrThrow("secs"));
        n.device = c.getString(c.getColumnIndexOrThrow("device"));
        n.tags = parseTags(c.getString(c.getColumnIndexOrThrow("tags")));
        n.deleted = c.getInt(c.getColumnIndexOrThrow("deleted")) != 0;
        n.dirty = c.getInt(c.getColumnIndexOrThrow("dirty")) != 0;
        n.seq = c.getLong(c.getColumnIndexOrThrow("seq"));
        return n;
    }

    private static List<Note> rows(Cursor c) {
        List<Note> out = new ArrayList<>();
        try {
            while (c.moveToNext()) out.add(row(c));
        } finally {
            c.close();
        }
        return out;
    }

    /** notes._index: the note's title and text in the search index (a no-op without FTS5). */
    private void index(SQLiteDatabase db, String id, String title, String text) {
        if (useFts) {
            db.execSQL("DELETE FROM notes_fts WHERE id = ?", new Object[]{id});
            db.execSQL("INSERT INTO notes_fts (id, title, text) VALUES (?, ?, ?)", new Object[]{id, title, text});
        }
    }

    // ------------------------------------------------------------------ notes (notes.py add, get, update, delete, search, count)

    /**
     * Saves a note and returns its id (32 lowercase hex characters). The text is trimmed; a blank title becomes the
     * first words of the text.
     */
    String add(String text, String raw, double secs, String source, String device, List<String> tags, String title) {
        String body = NoteLogic.strip(text);
        String heading = NoteLogic.strip(title);
        if (heading.isEmpty()) heading = NoteLogic.autoTitle(body);
        double now = nowSecs();
        String id = UUID.randomUUID().toString().replace("-", "");
        SQLiteDatabase db = db();
        db.beginTransaction();
        try {
            db.execSQL("INSERT INTO notes (id, source, title, text, raw, created_at, updated_at, secs, device, tags) "
                    + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    new Object[]{id, source == null ? Note.SOURCE_NOTE : source, heading, body, nz(raw), now, now,
                            Math.round(secs * 10) / 10.0, nz(device), tagsJson(tags)});
            index(db, id, heading, body);
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
        return id;
    }

    /**
     * True for a null or empty id or key. rawQuery passes its arguments as strings and Android throws
     * IllegalArgumentException for a null one, so every query that binds an id or a key checks this first.
     */
    private static boolean blank(String s) {
        return s == null || s.isEmpty();
    }

    /** The note, or null when there is none with that id (or the id is null or empty) or it was deleted. */
    Note get(String id) {
        if (blank(id)) return null;
        Cursor c = db().rawQuery("SELECT * FROM notes WHERE id = ? AND deleted = 0", new String[]{id});
        try {
            return c.moveToFirst() ? row(c) : null;
        } finally {
            c.close();
        }
    }

    /**
     * Changes a note; null keeps the current title, text or tags. Returns the note as it is now, or null when the
     * note does not exist (also for a null or empty id), like notes.update.
     */
    Note update(String id, String title, String text, List<String> tags) {
        if (blank(id)) return null;
        SQLiteDatabase db = db();
        db.beginTransaction();
        try {
            Cursor c = db.rawQuery("SELECT title, text, tags FROM notes WHERE id = ? AND deleted = 0", new String[]{id});
            String newTitle, newText, newTags;
            try {
                if (!c.moveToFirst()) return null;
                newTitle = title == null ? c.getString(0) : NoteLogic.strip(title);
                newText = text == null ? c.getString(1) : NoteLogic.strip(text);
                newTags = tags == null ? c.getString(2) : tagsJson(tags);
            } finally {
                c.close();
            }
            db.execSQL("UPDATE notes SET title = ?, text = ?, tags = ?, updated_at = ?, dirty = 1 WHERE id = ?",
                    new Object[]{newTitle, newText, newTags, nowSecs(), id});
            index(db, id, newTitle, newText);
            Cursor r = db.rawQuery("SELECT * FROM notes WHERE id = ?", new String[]{id});
            Note updated;
            try {
                updated = r.moveToFirst() ? row(r) : null;
            } finally {
                r.close();
            }
            db.setTransactionSuccessful();
            return updated;
        } finally {
            db.endTransaction();
        }
    }

    /**
     * Removes a note's content and keeps a marker row (deleted = 1, dirty = 1) so a sync can tell the other devices.
     * True when a note was deleted (false for a missing or already deleted note and for a null or empty id).
     */
    boolean delete(String id) {
        if (blank(id)) return false;
        SQLiteDatabase db = db();
        db.beginTransaction();
        try {
            SQLiteStatement st = db.compileStatement("UPDATE notes SET deleted = 1, title = '', text = '', raw = '', tags = '[]', updated_at = ?, dirty = 1 "
                    + "WHERE id = ? AND deleted = 0");
            boolean deleted;
            try {
                st.bindDouble(1, nowSecs());
                st.bindString(2, id);
                deleted = st.executeUpdateDelete() > 0;
            } finally {
                st.close();
            }
            if (useFts) db.execSQL("DELETE FROM notes_fts WHERE id = ?", new Object[]{id});
            db.setTransactionSuccessful();
            return deleted;
        } finally {
            db.endTransaction();
        }
    }

    /**
     * Newest first. Every word of {@code q} must appear (as a word start with FTS5, anywhere with LIKE). The other
     * filters apply when given: {@code source} (non-empty), {@code since} (created at or after, Unix seconds),
     * {@code until} (created before) and {@code tag} (non-empty).
     */
    List<Note> search(String q, String source, Double since, Double until, String tag, int limit) {
        SQLiteDatabase db = db();
        List<String> tokens = NoteLogic.searchWords(q);
        StringBuilder where = new StringBuilder("n.deleted = 0");
        String join = "";
        List<String> args = new ArrayList<>();
        if (!tokens.isEmpty() && useFts) {
            join = "JOIN notes_fts f ON f.id = n.id";
            where.append(" AND notes_fts MATCH ?");
            args.add(NoteLogic.ftsQuery(q));
        } else {
            for (String t : tokens) {
                where.append(" AND (n.title LIKE ? OR n.text LIKE ?)");
                args.add("%" + t + "%");
                args.add("%" + t + "%");
            }
        }
        if (source != null && !source.isEmpty()) {
            where.append(" AND n.source = ?");
            args.add(source);
        }
        if (since != null) {
            where.append(" AND n.created_at >= ?");
            args.add(String.valueOf(since));
        }
        if (until != null) {
            where.append(" AND n.created_at < ?");
            args.add(String.valueOf(until));
        }
        if (tag != null && !tag.isEmpty()) {
            where.append(" AND n.tags LIKE ?");
            args.add("%" + JSONObject.quote(tag) + "%");   // the tag as it is written inside the JSON list
        }
        return rows(db.rawQuery("SELECT n.* FROM notes n " + join + " WHERE " + where
                + " ORDER BY n.created_at DESC LIMIT " + limit, args.toArray(new String[0])));
    }

    /** How many notes there are (delete markers not counted). */
    int count() {
        Cursor c = db().rawQuery("SELECT COUNT(*) FROM notes WHERE deleted = 0", null);
        try {
            c.moveToFirst();
            return c.getInt(0);
        } finally {
            c.close();
        }
    }

    // ------------------------------------------------------------------ sync with a relay (notes.py, see SyncStore)

    /** The value kept under {@code k}, or {@code d} when there is none (also for a null or empty key). */
    @Override
    public String getMeta(String k, String d) {
        if (blank(k)) return d;
        Cursor c = db().rawQuery("SELECT value FROM sync_meta WHERE key = ?", new String[]{k});
        try {
            return c.moveToFirst() ? c.getString(0) : d;
        } finally {
            c.close();
        }
    }

    /** Keeps {@code v} under {@code k}; a null value is stored as "" (the column is NOT NULL) and a null or empty key is ignored. */
    @Override
    public void setMeta(String k, String v) {
        if (blank(k)) return;
        db().execSQL("INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?, ?)", new Object[]{k, nz(v)});
    }

    @Override
    public List<Note> dirtyNotes(int limit) {
        return rows(db().rawQuery("SELECT * FROM notes WHERE dirty = 1 ORDER BY updated_at LIMIT " + limit, null));
    }

    /** Only clears the flag when the note was not changed again while it was being sent (its updated_at is still the sent one). */
    @Override
    public void markSynced(String id, double sentUpdatedAt, long seq) {
        db().execSQL("UPDATE notes SET dirty = 0, seq = ? WHERE id = ? AND updated_at = ?",
                new Object[]{seq, id, sentUpdatedAt});
    }

    /**
     * Merges one note (or delete marker) received from the relay: NoteLogic.remoteWins decides whether it replaces
     * the local copy. The copy that is stored is clean (dirty = 0). True when the local copy changed. A null note, or
     * one with a null or empty id, changes nothing.
     */
    @Override
    public boolean applyRemote(Note n) {
        if (n == null || blank(n.id)) return false;
        SQLiteDatabase db = db();
        boolean changed = false;
        db.beginTransaction();
        try {
            Cursor c = db.rawQuery("SELECT updated_at FROM notes WHERE id = ?", new String[]{n.id});
            boolean hasLocal;
            double local = 0;
            try {
                hasLocal = c.moveToFirst();
                if (hasLocal) local = c.getDouble(0);
            } finally {
                c.close();
            }
            if (!NoteLogic.remoteWins(hasLocal, local, n.updatedAt, n.deleted)) {
                // the same version (for example our own send coming back): nothing to change but its sequence number
                if (hasLocal && local == n.updatedAt) {
                    db.execSQL("UPDATE notes SET seq = ? WHERE id = ?", new Object[]{n.seq, n.id});
                }
            } else {
                boolean deleted = n.deleted;
                db.execSQL("INSERT OR REPLACE INTO notes (id, source, title, text, raw, created_at, updated_at, secs, device, tags, deleted, dirty, seq) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                        new Object[]{n.id, n.source == null || n.source.isEmpty() ? Note.SOURCE_NOTE : n.source,
                                deleted ? "" : nz(n.title), deleted ? "" : nz(n.text), deleted ? "" : nz(n.raw),
                                n.createdAt, n.updatedAt, n.secs, nz(n.device),
                                deleted ? "[]" : tagsJson(n.tags), deleted ? 1L : 0L, n.seq});
                if (useFts) {
                    db.execSQL("DELETE FROM notes_fts WHERE id = ?", new Object[]{n.id});
                    if (!deleted) {
                        db.execSQL("INSERT INTO notes_fts (id, title, text) VALUES (?, ?, ?)",
                                new Object[]{n.id, nz(n.title), nz(n.text)});
                    }
                }
                changed = true;
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
        return changed;
    }
}
