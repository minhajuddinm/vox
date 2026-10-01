package com.minhaj.vox;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import java.util.TimeZone;

/** Plain-Java checks for OverlayDiag, the bubble diagnostics ring buffer and its event text. Exits non-zero on failure. */
public final class OverlayDiagTest {
    private static final TimeZone UTC = TimeZone.getTimeZone("UTC");

    private static void eq(String name, Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    /** 2026-10-01 12:03:45 UTC in epoch milliseconds, plus {@code sec} seconds. */
    private static long t(int sec) {
        return 1790856225000L + sec * 1000L;
    }

    public static void main(String[] args) throws Exception {
        // an empty buffer
        OverlayDiag d = new OverlayDiag(50, null);
        eq("empty", 0, d.events().size());
        eq("empty last", 0, d.last(20).size());

        // events keep their order; last(n) is newest first
        d.record(t(0), OverlayDiag.SERVICE_CONNECTED, "");
        d.record(t(1), OverlayDiag.SCREEN_OFF, "");
        d.record(t(2), OverlayDiag.SCREEN_ON, "");
        eq("size", 3, d.events().size());
        eq("oldest first", OverlayDiag.SERVICE_CONNECTED, d.events().get(0).kind);
        List<OverlayDiag.Event> last2 = d.last(2);
        eq("last(2) size", 2, last2.size());
        eq("last(2) newest first", OverlayDiag.SCREEN_ON, last2.get(0).kind);
        eq("last(2) second", OverlayDiag.SCREEN_OFF, last2.get(1).kind);
        eq("last(n) larger than the buffer", 3, d.last(99).size());

        // the ring keeps only the newest 50
        OverlayDiag ring = new OverlayDiag(OverlayDiag.CAPACITY, null);
        eq("capacity is 50", 50, OverlayDiag.CAPACITY);
        for (int i = 0; i < 120; i++) ring.record(t(i), OverlayDiag.CONFIG_CHANGE, "n" + i);
        eq("ring size", 50, ring.events().size());
        eq("ring oldest kept", "n70", ring.events().get(0).detail);
        eq("ring newest", "n119", ring.events().get(49).detail);

        // the same event again in a row is counted, not repeated (a screen flicker must not push the real cause out)
        OverlayDiag co = new OverlayDiag(50, null);
        co.record(t(0), OverlayDiag.PACKAGE_CHANGE, "replaced");
        co.record(t(5), OverlayDiag.PACKAGE_CHANGE, "replaced");
        co.record(t(9), OverlayDiag.PACKAGE_CHANGE, "replaced");
        eq("coalesced size", 1, co.events().size());
        eq("coalesced count", 3, co.events().get(0).count);
        eq("coalesced keeps the latest time", t(9), co.events().get(0).time);
        co.record(t(10), OverlayDiag.PACKAGE_CHANGE, "removed");
        eq("a different detail is a new event", 2, co.events().size());
        co.record(t(11), OverlayDiag.PACKAGE_CHANGE, "replaced");
        eq("only a repeat in a row is merged", 3, co.events().size());

        // event text: time (given zone), a plain sentence, the detail, the count
        eq("text connected", "12:03:45 Accessibility service connected",
                new OverlayDiag.Event(t(0), OverlayDiag.SERVICE_CONNECTED, "", 1).text(UTC));
        eq("text add", "12:03:46 Bubble added: mic bubble, text field focused",
                new OverlayDiag.Event(t(1), OverlayDiag.OVERLAY_ADD, "mic bubble, text field focused", 1).text(UTC));
        eq("text remove", "12:03:47 Bubble removed: note bubble, service unbound",
                new OverlayDiag.Event(t(2), OverlayDiag.OVERLAY_REMOVE, "note bubble, service unbound", 1).text(UTC));
        eq("text failed", "12:03:45 Bubble could not be added: mic bubble, WindowManager refused (BadTokenException)",
                new OverlayDiag.Event(t(0), OverlayDiag.OVERLAY_FAILED,
                        "mic bubble, WindowManager refused (BadTokenException)", 1).text(UTC));
        eq("text only typing hide", "12:03:45 Bubble hidden by only-typing: no text field focused",
                new OverlayDiag.Event(t(0), OverlayDiag.ONLY_TYPING_HIDE, "no text field focused", 1).text(UTC));
        eq("text only typing show", "12:03:45 Bubble shown by only-typing: text field focused",
                new OverlayDiag.Event(t(0), OverlayDiag.ONLY_TYPING_SHOW, "text field focused", 1).text(UTC));
        eq("text screen off", "12:03:45 Screen off", new OverlayDiag.Event(t(0), OverlayDiag.SCREEN_OFF, "", 1).text(UTC));
        eq("text screen on", "12:03:45 Screen on", new OverlayDiag.Event(t(0), OverlayDiag.SCREEN_ON, "", 1).text(UTC));
        eq("text user present", "12:03:45 Phone unlocked", new OverlayDiag.Event(t(0), OverlayDiag.USER_PRESENT, "", 1).text(UTC));
        eq("text config", "12:03:45 Screen configuration changed: rotated",
                new OverlayDiag.Event(t(0), OverlayDiag.CONFIG_CHANGE, "rotated", 1).text(UTC));
        eq("text package", "12:03:45 An app was updated, installed or removed",
                new OverlayDiag.Event(t(0), OverlayDiag.PACKAGE_CHANGE, "", 1).text(UTC));
        eq("text unbound", "12:03:45 Accessibility service unbound",
                new OverlayDiag.Event(t(0), OverlayDiag.SERVICE_UNBOUND, "", 1).text(UTC));
        eq("text destroyed", "12:03:45 Accessibility service destroyed",
                new OverlayDiag.Event(t(0), OverlayDiag.SERVICE_DESTROYED, "", 1).text(UTC));
        eq("text interrupted", "12:03:45 Accessibility service interrupted",
                new OverlayDiag.Event(t(0), OverlayDiag.SERVICE_INTERRUPTED, "", 1).text(UTC));
        eq("text count", "12:03:45 Screen on (x3)", new OverlayDiag.Event(t(0), OverlayDiag.SCREEN_ON, "", 3).text(UTC));
        eq("text unknown kind", "12:03:45 future_kind: x", new OverlayDiag.Event(t(0), "future_kind", "x", 1).text(UTC));
        eq("text zone", "14:03:45 Screen on",
                new OverlayDiag.Event(t(0), OverlayDiag.SCREEN_ON, "", 1).text(TimeZone.getTimeZone("GMT+2")));

        // details are one short line: tabs and newlines become spaces, very long text is cut
        OverlayDiag clean = new OverlayDiag(50, null);
        clean.record(t(0), OverlayDiag.CONFIG_CHANGE, "a\tb\nc\r\nd");
        eq("detail sanitised", "a b c  d", clean.events().get(0).detail);
        StringBuilder longText = new StringBuilder();
        for (int i = 0; i < 500; i++) longText.append('x');
        clean.record(t(1), OverlayDiag.CONFIG_CHANGE, longText.toString());
        eq("detail cut", OverlayDiag.MAX_DETAIL, clean.events().get(1).detail.length());
        clean.record(t(2), OverlayDiag.CONFIG_CHANGE, null);
        eq("null detail", "", clean.events().get(2).detail);
        clean.record(t(3), null, "no kind");
        eq("null kind", "unknown", clean.events().get(3).kind);

        // the file: written on every event, read back by the next process, newest 50 kept
        File dir = Files.createTempDirectory("vox-diag").toFile();
        File f = new File(dir, "overlay_diag.log");
        try {
            OverlayDiag a = OverlayDiag.open(f, 50);
            a.record(t(0), OverlayDiag.SERVICE_CONNECTED, "");
            a.record(t(1), OverlayDiag.OVERLAY_ADD, "mic bubble, only-typing is off");
            a.record(t(2), OverlayDiag.OVERLAY_ADD, "mic bubble, only-typing is off");
            OverlayDiag b = OverlayDiag.open(f, 50);   // "the service was killed and came back"
            eq("reload size", 2, b.events().size());
            eq("reload kind", OverlayDiag.OVERLAY_ADD, b.events().get(1).kind);
            eq("reload detail", "mic bubble, only-typing is off", b.events().get(1).detail);
            eq("reload count", 2, b.events().get(1).count);
            eq("reload time", t(2), b.events().get(1).time);
            b.record(t(3), OverlayDiag.SERVICE_UNBOUND, "");
            eq("reload then record", 3, OverlayDiag.open(f, 50).events().size());

            // a smaller capacity on reload keeps the newest
            eq("reload capacity", 1, OverlayDiag.open(f, 1).events().size());
            eq("reload capacity newest", OverlayDiag.SERVICE_UNBOUND, OverlayDiag.open(f, 1).events().get(0).kind);

            // a damaged file never breaks the service: bad lines are skipped
            Files.write(f.toPath(), ("garbage\n" + "12\tscreen_on\t1\t\n" + "x\ty\n" + "\n" + "13\tscreen_off\tNaN\t\n"
                    + "14\tscreen_off\t2\tok\n").getBytes(StandardCharsets.UTF_8));
            OverlayDiag bad = OverlayDiag.open(f, 50);
            eq("damaged file: good lines survive", 2, bad.events().size());
            eq("damaged file: kinds", OverlayDiag.SCREEN_ON, bad.events().get(0).kind);
            eq("damaged file: second", "ok", bad.events().get(1).detail);

            // a missing file and an unwritable path are both fine
            eq("missing file", 0, OverlayDiag.open(new File(dir, "nope.log"), 50).events().size());
            OverlayDiag nowhere = OverlayDiag.open(new File(new File(dir, "no-such-dir"), "x.log"), 50);
            nowhere.record(t(0), OverlayDiag.SCREEN_ON, "");
            eq("unwritable path keeps memory", 1, nowhere.events().size());
        } finally {
            File[] left = dir.listFiles();
            if (left != null) for (File x : left) x.delete();
            dir.delete();
        }

        // why the mic bubble is shown or hidden (the only-typing rule, in words) and which event it is
        eq("busy reason", "recording or working", OverlayDiag.micReason(true, true, false));
        eq("only typing off reason", "only-typing is off", OverlayDiag.micReason(false, false, false));
        eq("field reason", "text field focused", OverlayDiag.micReason(false, true, true));
        eq("no field reason", "no text field focused", OverlayDiag.micReason(false, true, false));
        eq("hide by only-typing", OverlayDiag.ONLY_TYPING_HIDE, OverlayDiag.micKind(false, false, true, false));
        eq("show by only-typing", OverlayDiag.ONLY_TYPING_SHOW, OverlayDiag.micKind(true, false, true, true));
        eq("show while busy is a plain add", OverlayDiag.OVERLAY_ADD, OverlayDiag.micKind(true, true, true, false));
        eq("show with only-typing off is a plain add", OverlayDiag.OVERLAY_ADD, OverlayDiag.micKind(true, false, false, false));

        // D2: always show, screen off and the watchdog
        eq("busy beats the screen", "recording or working", OverlayDiag.micReason(true, true, false, false, false));
        eq("screen off reason", "screen is off", OverlayDiag.micReason(false, true, true, true, false));
        eq("always show reason", "always show is on", OverlayDiag.micReason(false, true, false, true, true));
        eq("always show beats only-typing in the words", "always show is on", OverlayDiag.micReason(false, false, false, true, true));
        eq("five-argument reason falls back to the old words", "no text field focused", OverlayDiag.micReason(false, true, false, false, true));
        eq("always show hides nothing by only-typing", OverlayDiag.OVERLAY_ADD, OverlayDiag.micKind(true, false, true, false, true, true));
        eq("screen off is a plain remove", OverlayDiag.OVERLAY_REMOVE, OverlayDiag.micKind(false, false, true, true, false, false));
        eq("only-typing still has its own pair", OverlayDiag.ONLY_TYPING_HIDE, OverlayDiag.micKind(false, false, true, false, false, true));
        eq("watchdog label", "12:03:45 Bubble put back by the watchdog: mic bubble, window was gone",
                new OverlayDiag.Event(t(0), OverlayDiag.WATCHDOG_REPAIR, "mic bubble, window was gone", 1).text(UTC));

        // the service line on the card
        eq("service connected", "Connected: the bubble can be drawn", OverlayDiag.serviceLine(true, true));
        eq("service connected, setting unknown", "Connected: the bubble can be drawn", OverlayDiag.serviceLine(false, true));
        eq("service killed", "Switched on but not running: Android stopped it. Switch it off and on in Accessibility settings",
                OverlayDiag.serviceLine(true, false));
        eq("service off", "Off: turn Vox on in Accessibility settings", OverlayDiag.serviceLine(false, false));
        eq("battery ok", "Vox is not restricted by battery optimisation", OverlayDiag.batteryLine(true));
        eq("battery restricted", "Battery optimisation may stop Vox in the background", OverlayDiag.batteryLine(false));

        // the text the copy button puts on the clipboard
        OverlayDiag r = new OverlayDiag(50, null);
        r.record(t(0), OverlayDiag.SERVICE_CONNECTED, "");
        r.record(t(1), OverlayDiag.SCREEN_OFF, "");
        String rep = OverlayDiag.report("Android 14 (API 34)", "Connected", "Not restricted", r.last(20), UTC);
        eq("report", "Vox bubble diagnostics\n"
                + "Android 14 (API 34)\n"
                + "Service: Connected\n"
                + "Battery: Not restricted\n"
                + "Last 2 events, newest first:\n"
                + "12:03:46 Screen off\n"
                + "12:03:45 Accessibility service connected\n", rep);
        eq("report without events", "Vox bubble diagnostics\nA\nService: S\nBattery: B\nNo events recorded yet.\n",
                OverlayDiag.report("A", "S", "B", new OverlayDiag(50, null).last(20), UTC));

        System.out.println("OverlayDiagTest ok");
    }
}
