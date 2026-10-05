package com.minhaj.vox;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Tells whether the "text" an empty field reports is really just its placeholder (the grey "Message" in WhatsApp and
 * Telegram). Typing then must start from an empty field, or the placeholder ends up in front of the dictation.
 * Pure Java (no android.* classes) so the off-device tests run it.
 *
 * Android should flag this itself (isShowingHintText, or text equal to the hint), but many chat apps draw their own
 * placeholder and report it as plain text, so a short list of well-known placeholders is checked as well. The list is
 * deliberately short and exact: wrongly calling real text a placeholder would drop what the user typed, so only a
 * field whose entire text is one of these phrases (or "Message #channel" / "Message @person") is treated as empty.
 */
final class HintGuard {
    private HintGuard() { }

    /** Longest text that can still be a placeholder; anything longer is the user's own text. */
    static final int MAX_LEN = 40;

    private static final Set<String> PLACEHOLDERS = new HashSet<>(Arrays.asList(
            "message", "type a message", "type message", "write a message", "send a message", "text message",
            "rcs message", "chat message", "type here", "write something", "add a comment", "search",
            "संदेश", "संदेश लिखें", "मैसेज", "मैसेज लिखें"));

    /** "Message #general", "Message @Sam", "Message Sam Lee": the marker is required so a sentence is never matched. */
    private static final Pattern MESSAGE_TARGET = Pattern.compile("message [#@]\\S+( \\S+)?");

    /**
     * @param text        what the field reports as its text (may be the placeholder)
     * @param hint        the field's hint text, or null
     * @param showingHint the platform's own flag (isShowingHintText), false when unknown
     * @param contentDesc the field's content description, or null
     */
    static boolean isPlaceholder(CharSequence text, CharSequence hint, boolean showingHint, CharSequence contentDesc) {
        return isPlaceholder(text, hint, showingHint, contentDesc, -1);
    }

    /**
     * The same, with where the caret is ({@code getTextSelectionStart}, -1 when unknown). A caret after the start means
     * the user typed that text (an empty field that reports its placeholder has its caret at 0 or none): it is never
     * treated as a placeholder, unless the platform itself says the hint is showing.
     */
    static boolean isPlaceholder(CharSequence text, CharSequence hint, boolean showingHint, CharSequence contentDesc, int selStart) {
        String t = norm(text);
        if (t.isEmpty()) return false;
        if (showingHint) return true;
        if (selStart > 0) return false;
        if (t.equals(norm(hint)) || t.equals(norm(contentDesc))) return true;
        if (t.length() > MAX_LEN) return false;
        return PLACEHOLDERS.contains(t) || MESSAGE_TARGET.matcher(t).matches();
    }

    /** Lower case, trimmed, with a trailing ellipsis removed; "" for null. */
    static String norm(CharSequence s) {
        if (s == null) return "";
        String t = s.toString().trim().toLowerCase(Locale.ROOT);
        while (t.endsWith("…") || t.endsWith(".")) t = t.substring(0, t.length() - 1).trim();
        return t;
    }
}
