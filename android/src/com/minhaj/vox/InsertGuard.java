package com.minhaj.vox;

/**
 * Decides whether a finished dictation may be typed into the focused field, as pure logic (no android.*). A
 * dictation restored after a restart has the target package "" (its app is unknown): it is never typed, whatever
 * package the focused node reports (a node can report none).
 */
public final class InsertGuard {
    public static final int TYPE = 0, SWITCHED_APPS = 1, NO_TARGET = 2;

    /** Where a finished dictation goes (see {@link #route}). */
    public static final int ROUTE_TYPE = 0, ROUTE_CLIPBOARD = 1, ROUTE_NONE = 2;

    private InsertGuard() { }

    /**
     * @param current     the job is still the current one (false after a cancel or a newer recording: nothing is shown)
     * @param hasListener the accessibility service is attached and can type the text
     */
    public static int route(boolean current, boolean hasListener) {
        if (!current) return ROUTE_NONE;
        return hasListener ? ROUTE_TYPE : ROUTE_CLIPBOARD;
    }

    /**
     * What to tell the user when the cleanup step failed and the rules layer's text is used (noises and spoken commands only), or null for nothing. A job that
     * is no longer current was cancelled (or replaced) while it was cleaning: nothing is typed or saved, so nothing is said.
     */
    public static String cleanupNotice(boolean current, boolean cleanupFailed, boolean note) {
        if (!current || !cleanupFailed) return null;
        return note ? "Cleanup did not work, so Vox saved your words with basic tidying only"
                : "Cleanup did not work, so Vox typed your words with basic tidying only";
    }

    /**
     * What to say when a recording gave no usable words: nothing came back, or only a phrase speech-to-text invents for
     * silence ("Thank you."). A dictation uses the PC app's words (windows/engine.py); a note says that no note was saved.
     */
    public static String emptyResult(boolean note) {
        return note ? "Vox did not hear any words, so no note was saved"
                : "Vox heard no usable words in that recording (a lone \"Thank you\" counts as silence). Speak a little longer, or check the microphone.";
    }

    /** The start of every message about a failed send: first, so a toast cut to two lines still says what to do. */
    static final String KEPT = "Recording kept: tap Retry in the notification.";

    /** The most of a server's own text that a message shows (as RelayClient.MAX_DETAIL). */
    static final int MAX_DETAIL = 200;

    /** A send the server refused (other than the key and the rate limit): its message ("API 400: ..."), capped. */
    public static String sendFailed(String serverMessage) {
        return KEPT + " " + capped(serverMessage);
    }

    /** A send that failed on the network: its message, or the exception's class when it has none. */
    public static String networkFailed(String message, String className) {
        return KEPT + " Network error: " + capped(message == null || message.isEmpty() ? className : message);
    }

    /** Anything else that broke a send (a RuntimeException): only its class, never its text (it can hold the key). */
    public static String crashed(Throwable e) {
        return KEPT + " Vox could not send it (" + e.getClass().getSimpleName() + ").";
    }

    private static String capped(String s) {
        String t = s == null ? "" : s.trim();
        if (t.length() <= MAX_DETAIL) return t;
        int cut = MAX_DETAIL;
        if (Character.isHighSurrogate(t.charAt(cut - 1))) cut--;   // never split a pair
        return t.substring(0, cut) + "...";
    }

    /** What happens to the clipboard after a paste fallback (see {@link #afterPaste}). */
    public static final int CLIP_RESTORE = 0, CLIP_CLEAR = 1;

    /**
     * After the dictation was pasted through the clipboard it must not stay there. The old clip is put back when it could
     * be read; Android 10+ never lets a background service read it, so then the clip is cleared instead (the old one is
     * lost either way: Android gave Vox no copy of it).
     */
    public static int afterPaste(boolean oldClipReadable) {
        return oldClipReadable ? CLIP_RESTORE : CLIP_CLEAR;
    }

    /** Android 13 (API 33) and later can mark a clip as sensitive, so the clipboard preview and keyboards hide it. */
    public static boolean markSensitive(int sdk) {
        return sdk >= 33;
    }

    /** ClipboardManager.clearPrimaryClip exists from Android 9 (API 28); before that an empty clip is put over it. */
    public static boolean canClearClip(int sdk) {
        return sdk >= 28;
    }

    /** The toast when the text was copied because nothing could type it. */
    public static String noListenerMessage() {
        return "Vox could not type (accessibility is off). Text copied to clipboard.";
    }

    /**
     * @param targetPkg the app the dictation was made for: null (not known to matter: typing is allowed), "" (a
     *                  restored dictation: never typed) or a package name
     * @param nodePkg   the package of the focused field, or null when it reports none
     */
    public static int check(String targetPkg, CharSequence nodePkg) {
        if (targetPkg == null) return TYPE;
        if (targetPkg.isEmpty()) return NO_TARGET;
        if (nodePkg != null && !targetPkg.contentEquals(nodePkg)) return SWITCHED_APPS;
        return TYPE;
    }

    /** The toast for a refusal ("Copied" means the text is on the clipboard). */
    public static String message(int verdict) {
        return verdict == NO_TARGET ? "Copied; paste it where you want it" : "You switched apps. Dictation copied to clipboard.";
    }
}
