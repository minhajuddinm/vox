package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * DevicesView beyond what the `devices` rows in spec/golden.txt say (ParityTest runs those): the order, entries that are
 * not objects, values that are not usable numbers, and that the rows carry nothing but name, this, state and ago.
 * tests/test_sync_devices.py checks the same edge cases against the Python twin (windows/sync.py devices_view).
 */
public final class DevicesViewTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    private static Map<String, Object> dev(Object name, Object lastSeen) {
        Map<String, Object> m = new LinkedHashMap<>();
        if (name != null) m.put("name", name);
        if (lastSeen != null) m.put("last_seen", lastSeen);
        return m;
    }

    private static String show(List<DevicesView.Row> rows) {
        StringBuilder sb = new StringBuilder();
        for (DevicesView.Row r : rows) sb.append(r.state).append(';').append(r.thisDevice).append(';').append(r.ago).append(';').append(r.name).append('|');
        return sb.toString();
    }

    private static final double NOW = 1000000.0;

    public static void main(String[] args) {
        // the relay's order is kept, and extra fields (login, requests) are ignored
        Map<String, Object> b = dev("b", NOW - 5);
        b.put("login", "x@y");
        b.put("requests", 3L);
        eq("order and states", "active;false;just now;b|recent;false;67 min ago;a|",
                show(DevicesView.rows(Arrays.<Object>asList(b, dev("a", NOW - 4000)), NOW, "me")));

        // last_seen as a Long (whole seconds in the JSON) works like a Double
        eq("long last_seen", "active;false;2 min ago;d|", show(DevicesView.rows(Arrays.<Object>asList(dev("d", Long.valueOf(999880))), NOW, "me")));

        // a last_seen that is not a usable number is "never"
        Object[] unusable = {"yesterday", Double.NaN, Double.POSITIVE_INFINITY, Boolean.TRUE, new ArrayList<Object>(), new LinkedHashMap<String, Object>(), 0L, -5.0};
        for (Object bad : unusable) eq("never for " + bad, "old;false;never;d|", show(DevicesView.rows(Arrays.<Object>asList(dev("d", bad)), NOW, "me")));
        eq("no last_seen", "old;false;never;d|", show(DevicesView.rows(Arrays.<Object>asList(dev("d", null)), NOW, "me")));

        // entries that are not objects are skipped; a missing or non-text name shows as Unknown device and is never "this"
        List<Object> odd = new ArrayList<>();
        odd.add(dev(null, NOW));
        odd.add(dev(Long.valueOf(7), NOW));
        odd.add("junk");
        odd.add(null);
        odd.add(Long.valueOf(5));
        eq("odd entries", "active;false;just now;Unknown device|active;false;just now;Unknown device|", show(DevicesView.rows(odd, NOW, "")));
        eq("blank my name matches nothing", "active;false;just now;Unknown device|", show(DevicesView.rows(odd.subList(0, 1), NOW, "Unknown device")));

        // nothing, or null, is an empty list
        eq("empty", 0, DevicesView.rows(new ArrayList<Object>(), NOW, "me").size());
        eq("null", 0, DevicesView.rows(null, NOW, "me").size());

        // the clock is the one passed in
        eq("given clock", "active;false;just now;d|", show(DevicesView.rows(Arrays.<Object>asList(dev("d", 100.0)), 100.0, "me")));

        // the Android spelling of a name in the X-Vox-Device header (RelayClient.headerText): one "?" per UTF-16 unit
        eq("ascii name plain", "Pixel 7", DevicesView.asciiName("Pixel 7"));
        eq("ascii name accent", "Caf?", DevicesView.asciiName("Caf\u00e9"));
        eq("ascii name astral", "A??", DevicesView.asciiName("A\uD83D\uDE00"));
        eq("ascii name tab", "a?b", DevicesView.asciiName("a\tb"));

        // the age text: rounding is half up on whole seconds
        eq("age 89", "89 s ago", DevicesView.agoText(89));
        eq("age 90", "2 min ago", DevicesView.agoText(90));
        eq("age 150", "3 min ago", DevicesView.agoText(150));
        eq("age 149", "2 min ago", DevicesView.agoText(149));
        eq("age 5400", "2 h ago", DevicesView.agoText(5400));
        eq("age 129600", "2 days ago", DevicesView.agoText(129600));

        System.out.println("OK: " + checks + " checks passed");
    }
}
