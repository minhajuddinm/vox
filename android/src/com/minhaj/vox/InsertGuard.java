package com.minhaj.vox;

/**
 * Decides whether a finished dictation may be typed into the focused field, as pure logic (no android.*). A
 * dictation restored after a restart has the target package "" (its app is unknown): it is never typed, whatever
 * package the focused node reports (a node can report none).
 */
public final class InsertGuard {
    public static final int TYPE = 0, SWITCHED_APPS = 1, NO_TARGET = 2;

    private InsertGuard() { }

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
