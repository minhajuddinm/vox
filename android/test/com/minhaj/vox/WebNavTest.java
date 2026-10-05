package com.minhaj.vox;

/** Plain-Java checks for WebNav: the settings page never leaves the bundled files while the Vox bridge is attached. Exits non-zero on failure. */
public final class WebNavTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) {
        eq("the bundled page stays", WebNav.STAY, WebNav.decide("file:///android_asset/index.html"));
        eq("an anchor in the bundled page stays", WebNav.STAY, WebNav.decide("file:///android_asset/index.html#notes"));
        eq("an https link opens in the browser", WebNav.BROWSER, WebNav.decide("https://github.com/minhajuddinm/vox"));
        eq("an http link is refused", WebNav.BLOCK, WebNav.decide("http://example.com/"));
        eq("another file is refused", WebNav.BLOCK, WebNav.decide("file:///data/data/com.minhaj.vox/shared_prefs/vox.xml"));
        eq("a path that climbs out of the assets is refused", WebNav.BLOCK, WebNav.decide("file:///android_asset/../shared_prefs/vox.xml"));
        eq("javascript: is refused", WebNav.BLOCK, WebNav.decide("javascript:alert(1)"));
        eq("intent: is refused", WebNav.BLOCK, WebNav.decide("intent://x#Intent;end"));
        eq("data: is refused", WebNav.BLOCK, WebNav.decide("data:text/html,<b>x</b>"));
        eq("upper-case scheme of the page stays", WebNav.STAY, WebNav.decide("FILE:///android_asset/index.html"));
        eq("upper-case https opens in the browser", WebNav.BROWSER, WebNav.decide("HTTPS://example.com"));
        eq("null is refused", WebNav.BLOCK, WebNav.decide(null));
        eq("empty is refused", WebNav.BLOCK, WebNav.decide(""));

        System.out.println("OK: " + checks + " checks passed");
    }
}
