package com.minhaj.vox;

/**
 * The pure rules of the voice note bubble: when it is on screen and how its recording timer reads. Pure Java (no
 * android.* classes) so the off-device tests and the golden rows (kind notebubble in spec/golden.txt) run it.
 * VoxAccessibilityService is the adapter.
 */
final class NoteBubbleLogic {
    private NoteBubbleLogic() { }

    /**
     * Is the note bubble wanted (before the screen and service checks the adapter adds, see BubbleLogic.shouldShow's
     * callers). It is always there while the "note_bubble" switch is on; otherwise it shows up by itself while a note
     * is being recorded or saved, from whichever side the note was started (bubble, notification, tile, app). The
     * adapter counts the moments just after a save, while the green check or the red ! is still showing, as saving.
     */
    static boolean visible(boolean persistentPref, boolean noteRecording, boolean noteSaving) {
        return persistentPref || noteRecording || noteSaving;
    }

    /** The recording time as shown on the bubble: m:ss, or h:mm:ss from one hour; whole seconds, rounded down. */
    static String timer(long ms) {
        long s = Math.max(0L, ms) / 1000;
        long h = s / 3600, m = s / 60 % 60;
        return h > 0 ? String.format(java.util.Locale.ROOT, "%d:%02d:%02d", h, m, s % 60) : String.format(java.util.Locale.ROOT, "%d:%02d", m, s % 60);
    }
}
