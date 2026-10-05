package com.minhaj.vox;

import java.util.Locale;

/**
 * Where a navigation of the settings page may go. The page holds the Vox bridge (which hands out the API keys and the
 * relay token), so it must never load anything but the bundled files: an https link opens in the browser instead, and
 * everything else is refused. Pure Java, tested by WebNavTest; MainActivity's WebViewClient asks it.
 */
final class WebNav {
    private WebNav() { }

    static final int STAY = 0, BROWSER = 1, BLOCK = 2;

    private static final String ASSETS = "file:///android_asset/";

    static int decide(String url) {
        if (url == null) return BLOCK;
        String u = url.toLowerCase(Locale.ROOT);
        if (u.startsWith(ASSETS) && !u.contains("..") && !u.contains("%2e")) return STAY;   // (a dot written %2e too)
        if (u.startsWith("https://")) return BROWSER;
        return BLOCK;
    }
}
