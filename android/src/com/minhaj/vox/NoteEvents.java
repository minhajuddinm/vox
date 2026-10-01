package com.minhaj.vox;

import java.util.concurrent.CopyOnWriteArrayList;

/**
 * The "a voice note was saved" hook. DictationService calls {@link #fireSaved()} after it has stored a note; whoever
 * wants to react (the relay sync, later a screen that shows the notes) registers a Runnable with
 * {@link #addSavedListener(Runnable)}. Kept apart from DictationService so the sender does not need to know the
 * receivers, and pure Java so the off-device tests can run it.
 *
 * The list is thread-safe: listeners may be added or removed from any thread, also while an event is being
 * delivered (that delivery uses the listeners as they were when it started). Listeners run on the thread that calls
 * {@code fireSaved}, which is a background thread, so they must be quick and must not touch views.
 */
final class NoteEvents {
    private static final CopyOnWriteArrayList<Runnable> SAVED = new CopyOnWriteArrayList<>();

    private NoteEvents() { }

    /** Runs {@code r} after every saved note. Adding the same Runnable again has no effect. */
    static void addSavedListener(Runnable r) {
        if (r != null) SAVED.addIfAbsent(r);
    }

    /** Stops calling {@code r}; does nothing when it was not added. */
    static void removeSavedListener(Runnable r) {
        SAVED.remove(r);
    }

    /** Tells every listener that a note was saved. A listener that fails does not stop the others or the caller. */
    static void fireSaved() {
        for (Runnable r : SAVED) {
            try {
                r.run();
            } catch (RuntimeException e) {
                // the note is already saved; one broken listener must not hide that from the rest
            }
        }
    }
}
