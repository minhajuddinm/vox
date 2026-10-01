package com.minhaj.vox;

/** Plain-Java checks for HintGuard: when the "text" an empty field reports is only its placeholder. Exits non-zero on failure. */
public final class HintGuardTest {
    private static void eq(String name, Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) {
        // the platform says so
        eq("showing hint flag", true, HintGuard.isPlaceholder("Whatever", null, true, null));
        eq("text equals hint", true, HintGuard.isPlaceholder("Message", "Message", false, null));
        eq("text equals hint, other case and spaces", true, HintGuard.isPlaceholder(" message ", "Message", false, null));
        eq("text equals content description", true, HintGuard.isPlaceholder("Type a message", null, false, "Type a message"));

        // WhatsApp, Telegram, Signal, Google Messages and friends report the placeholder as plain text, no hint info
        eq("WhatsApp/Telegram Message", true, HintGuard.isPlaceholder("Message", null, false, null));
        eq("Type a message", true, HintGuard.isPlaceholder("Type a message", null, false, null));
        eq("Text message", true, HintGuard.isPlaceholder("Text message", null, false, null));
        eq("Write a message", true, HintGuard.isPlaceholder("Write a message...", null, false, null));
        eq("Slack channel", true, HintGuard.isPlaceholder("Message #general", null, false, null));
        eq("Discord person", true, HintGuard.isPlaceholder("Message @Sam", null, false, null));
        eq("Search", true, HintGuard.isPlaceholder("Search", null, false, null));
        eq("Hindi message", true, HintGuard.isPlaceholder("संदेश", null, false, null));
        eq("ellipsis variants", true, HintGuard.isPlaceholder("Message…", null, false, null));

        // real text must never be dropped
        eq("empty text", false, HintGuard.isPlaceholder("", null, false, null));
        eq("null text", false, HintGuard.isPlaceholder(null, null, false, null));
        eq("ordinary words", false, HintGuard.isPlaceholder("Hello there", null, false, null));
        eq("starts with the word message", false, HintGuard.isPlaceholder("Message me when you land", null, false, null));
        eq("typed text with a hint that differs", false, HintGuard.isPlaceholder("Hello", "Message", false, null));
        eq("long text that merely starts with Message #", false,
                HintGuard.isPlaceholder("Message #general is where we post the weekly update for everyone on the team", null, false, null));
        eq("hint flag false and plain text", false, HintGuard.isPlaceholder("see you at five", "Message", false, "Message"));

        System.out.println("HintGuardTest OK");
    }
}
