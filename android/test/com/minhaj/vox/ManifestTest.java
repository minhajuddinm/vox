package com.minhaj.vox;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

/** Plain-Java checks of android/AndroidManifest.xml read as text (the runner starts in the repository root). Exits non-zero on failure. */
public final class ManifestTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    /** The text of the first {@code <activity ...>} start tag whose name is {@code name}, or null. */
    private static String activityTag(String xml, String name) {
        for (int i = xml.indexOf("<activity"); i >= 0; i = xml.indexOf("<activity", i + 1)) {
            int end = xml.indexOf('>', i);
            String tag = xml.substring(i, end + 1);
            if (tag.contains("android:name=\"" + name + "\"")) return tag;
        }
        return null;
    }

    public static void main(String[] args) throws Exception {
        String xml = new String(Files.readAllBytes(Paths.get("android/AndroidManifest.xml")), StandardCharsets.UTF_8);
        String main = activityTag(xml, ".MainActivity");
        eq("the manifest has a MainActivity", true, main != null);
        // Rotating the phone must not recreate the screen: the page would reload and an unsaved note edit (held only in the page) would be lost.
        eq("MainActivity handles a rotation itself", true, main != null && main.matches("(?s).*android:configChanges=\"[^\"]*orientation[^\"]*\".*"));
        eq("MainActivity handles a screen size change itself", true, main != null && main.matches("(?s).*android:configChanges=\"[^\"]*screenSize[^\"]*\".*"));
        // The bar colours come from isDark() in onCreate, so a dark-mode flip must still recreate the screen.
        eq("MainActivity does not swallow uiMode changes", false, main != null && main.matches("(?s).*android:configChanges=\"[^\"]*uiMode[^\"]*\".*"));

        System.out.println("OK: " + checks + " checks passed");
    }
}
