package com.minhaj.vox;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * What the Devices card shows, from the relay's device list ({@code GET /devices}, parsed by PlainJson): one row per
 * device, in the relay's order (newest first). The twin of {@code devices_view} in windows/sync.py; the {@code devices}
 * rows of spec/golden.txt run both. Pure Java: no android.* and no org.json, so the tests run off-device.
 */
final class DevicesView {
    private DevicesView() {
    }

    /** Seen less than this many seconds ago: "active". */
    static final long ACTIVE_SECS = 600;

    /** Seen less than this many seconds ago: "recent"; older, or never: "old". */
    static final long RECENT_SECS = 86400;

    /** The name shown for a device that came without one. */
    static final String UNKNOWN_DEVICE = "Unknown device";

    /** One device as the card shows it. */
    static final class Row {
        final String name;
        /** True for the device that is asking (this phone, this PC). */
        final boolean thisDevice;
        /** "active", "recent" or "old". */
        final String state;
        /** "just now", "5 min ago", ... or "never". */
        final String ago;

        Row(String name, boolean thisDevice, String state, String ago) {
            this.name = name;
            this.thisDevice = thisDevice;
            this.state = state;
            this.ago = ago;
        }
    }

    /**
     * The rows for the relay's devices. {@code devices} holds the JSON objects of the list (a Map with "name" and
     * "last_seen" in Unix seconds; other fields are ignored); an entry that is not a Map is skipped. {@code now} is Unix
     * seconds, {@code myName} the name this device sends to the relay. A device is "this" when its name is {@code myName}
     * or the spelling the relay got from the header ({@link #asciiName}). A missing name shows as "Unknown device" and is
     * never "this"; a last_seen that is not a usable number (missing, text, not finite, zero or less, a Boolean) is
     * "never" and "old"; a time in the future counts as now.
     */
    static List<Row> rows(List<?> devices, double now, String myName) {
        String me = myName == null ? "" : myName.trim();
        Set<String> mine = new HashSet<>();
        if (!me.isEmpty()) {
            mine.add(me);
            mine.add(asciiName(me));
        }
        List<Row> out = new ArrayList<>();
        if (devices == null) return out;
        for (Object item : devices) {
            if (!(item instanceof Map)) continue;
            Map<?, ?> d = (Map<?, ?>) item;
            Object n = d.get("name");
            String name = n instanceof String ? ((String) n).trim() : "";
            Object seen = d.get("last_seen");
            double t = seen instanceof Number ? ((Number) seen).doubleValue() : 0;
            boolean usable = seen instanceof Number && !Double.isNaN(t) && !Double.isInfinite(t) && t > 0;
            String state;
            String ago;
            if (usable) {
                long age = (long) Math.max(0, now - t);
                state = age >= RECENT_SECS ? "old" : age < ACTIVE_SECS ? "active" : "recent";
                ago = agoText(age);
            } else {
                state = "old";
                ago = "never";
            }
            out.add(new Row(name.isEmpty() ? UNKNOWN_DEVICE : name, !name.isEmpty() && mine.contains(name), state, ago));
        }
        return out;
    }

    /** A device name as this app puts it in the X-Vox-Device header (RelayClient.headerText): every char outside printable ASCII becomes "?". */
    static String asciiName(String s) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            sb.append(c >= 0x20 && c <= 0x7E ? c : '?');
        }
        return sb.toString();
    }

    /**
     * "just now", "40 s ago", "5 min ago", "3 h ago" or "2 days ago" for an age in whole seconds (windows/sync.py
     * ago_text; the JavaScript twin is agoText in ui-shared/common.js). Rounding is half up, in integers.
     */
    static String agoText(long secs) {
        if (secs < 10) return "just now";
        if (secs < 90) return secs + " s ago";
        if (secs < 5400) return (2 * secs + 60) / 120 + " min ago";
        if (secs < 129600) return (2 * secs + 3600) / 7200 + " h ago";
        return (2 * secs + 86400) / 172800 + " days ago";
    }
}
