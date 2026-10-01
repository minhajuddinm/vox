package com.minhaj.vox;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.TimeZone;

/**
 * Bubble diagnostics: a ring buffer of the last 50 things that can make the floating bubble appear or disappear
 * (service connected or unbound, bubble added or removed and why, screen on or off, unlock, configuration change,
 * an app installed or removed, the only-typing rule hiding or showing it), kept in memory and in a small text file
 * so the history survives the service being killed. Pure Java (no android.* classes) so the off-device tests run it.
 *
 * The event text never holds what the user typed or dictated, and a package change carries no package name. The file
 * lives in the app's private folder and is never sent anywhere; the user can copy the report from the Settings card.
 *
 * File format, one event per line: {@code time-ms TAB kind TAB count TAB detail}. A line that does not parse is
 * skipped, so a damaged file never stops the service from starting.
 */
final class OverlayDiag {
    static final int CAPACITY = 50;
    static final int MAX_DETAIL = 160;
    /** The file name inside the app's private files folder. */
    static final String FILE_NAME = "overlay_diag.log";

    static final String SERVICE_CONNECTED = "service_connected";
    static final String SERVICE_UNBOUND = "service_unbound";
    static final String SERVICE_DESTROYED = "service_destroyed";
    static final String SERVICE_INTERRUPTED = "service_interrupted";
    static final String OVERLAY_ADD = "overlay_add";
    static final String OVERLAY_REMOVE = "overlay_remove";
    static final String OVERLAY_FAILED = "overlay_failed";
    /** The watchdog found a bubble it thought was on screen with its window gone, and added it again. */
    static final String WATCHDOG_REPAIR = "overlay_repaired";
    static final String ONLY_TYPING_HIDE = "only_typing_hide";
    static final String ONLY_TYPING_SHOW = "only_typing_show";
    static final String SCREEN_ON = "screen_on";
    static final String SCREEN_OFF = "screen_off";
    static final String USER_PRESENT = "user_present";
    static final String CONFIG_CHANGE = "config_change";
    static final String PACKAGE_CHANGE = "package_change";
    /** Typing found a field whose text looked like a placeholder or a short text with the caret at the start (no text is stored). */
    static final String INSERT_PROBE = "insert_probe";

    /** One recorded event. {@code count} is above 1 when the same event happened again straight away (time is the last one). */
    static final class Event {
        final long time;
        final String kind;
        final String detail;
        final int count;

        Event(long time, String kind, String detail, int count) {
            this.time = time;
            this.kind = kind;
            this.detail = detail;
            this.count = count;
        }

        /** The one line shown on the card and in the copied report, e.g. {@code 12:03:45 Bubble added: mic bubble, ...}. */
        String text(TimeZone zone) {
            SimpleDateFormat f = new SimpleDateFormat("HH:mm:ss");
            f.setTimeZone(zone);
            StringBuilder b = new StringBuilder(f.format(new Date(time))).append(' ').append(label(kind));
            if (!detail.isEmpty()) b.append(": ").append(detail);
            if (count > 1) b.append(" (x").append(count).append(')');
            return b.toString();
        }
    }

    private static String label(String kind) {
        switch (kind) {
            case SERVICE_CONNECTED: return "Accessibility service connected";
            case SERVICE_UNBOUND: return "Accessibility service unbound";
            case SERVICE_DESTROYED: return "Accessibility service destroyed";
            case SERVICE_INTERRUPTED: return "Accessibility service interrupted";
            case OVERLAY_ADD: return "Bubble added";
            case OVERLAY_REMOVE: return "Bubble removed";
            case OVERLAY_FAILED: return "Bubble could not be added";
            case WATCHDOG_REPAIR: return "Bubble put back by the watchdog";
            case ONLY_TYPING_HIDE: return "Bubble hidden by only-typing";
            case ONLY_TYPING_SHOW: return "Bubble shown by only-typing";
            case SCREEN_ON: return "Screen on";
            case SCREEN_OFF: return "Screen off";
            case USER_PRESENT: return "Phone unlocked";
            case CONFIG_CHANGE: return "Screen configuration changed";
            case PACKAGE_CHANGE: return "An app was updated, installed or removed";
            default: return kind;
        }
    }

    private final int capacity;
    private final File file;
    private final ArrayList<Event> events = new ArrayList<>();

    OverlayDiag(int capacity, File file) {
        this.capacity = Math.max(1, capacity);
        this.file = file;
    }

    /** The buffer backed by {@code file}: loads what an earlier run left there (newest {@code capacity} lines), writes on every event. */
    static OverlayDiag open(File file, int capacity) {
        OverlayDiag d = new OverlayDiag(capacity, file);
        d.load();
        return d;
    }

    private static OverlayDiag shared;

    /** The one buffer of this process (the service writes, the Settings bridge reads). Opens the file on first use. */
    static synchronized OverlayDiag shared(File file) {
        if (shared == null) shared = open(file, CAPACITY);
        return shared;
    }

    /** Adds an event. The same kind and detail again straight after itself is counted, not repeated. Never throws. */
    synchronized void record(long time, String kind, String detail) {
        String k = kind == null || kind.isEmpty() ? "unknown" : oneLine(kind);
        String d = oneLine(detail == null ? "" : detail);
        if (d.length() > MAX_DETAIL) d = d.substring(0, MAX_DETAIL);
        int n = events.size();
        if (n > 0) {
            Event last = events.get(n - 1);
            if (last.kind.equals(k) && last.detail.equals(d)) {
                events.set(n - 1, new Event(time, k, d, last.count + 1));
                save();
                return;
            }
        }
        events.add(new Event(time, k, d, 1));
        while (events.size() > capacity) events.remove(0);
        save();
    }

    /** Every event, oldest first. */
    synchronized List<Event> events() {
        return new ArrayList<>(events);
    }

    /** The newest {@code n} events, newest first. */
    synchronized List<Event> last(int n) {
        List<Event> out = new ArrayList<>();
        for (int i = events.size() - 1; i >= 0 && out.size() < n; i--) out.add(events.get(i));
        return out;
    }

    private static String oneLine(String s) {
        return s.replace('\t', ' ').replace('\r', ' ').replace('\n', ' ');
    }

    // ------------------------------------------------------------- file

    private void load() {
        if (file == null || !file.isFile()) return;
        try (BufferedReader r = Files.newBufferedReader(file.toPath(), StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                String[] p = line.split("\t", 4);
                if (p.length < 3 || p[1].isEmpty()) continue;
                try {
                    long time = Long.parseLong(p[0]);
                    int count = Integer.parseInt(p[2]);
                    if (count < 1) continue;
                    events.add(new Event(time, p[1], p.length > 3 ? p[3] : "", count));
                } catch (NumberFormatException skip) {
                    // a damaged line
                }
            }
        } catch (IOException | RuntimeException e) {
            // an unreadable file just means no history
        }
        while (events.size() > capacity) events.remove(0);
    }

    private void save() {
        if (file == null) return;
        try {
            StringBuilder b = new StringBuilder();
            for (Event e : events) {
                b.append(e.time).append('\t').append(e.kind).append('\t').append(e.count).append('\t').append(e.detail).append('\n');
            }
            File tmp = new File(file.getPath() + ".tmp");
            Files.write(tmp.toPath(), b.toString().getBytes(StandardCharsets.UTF_8));
            Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException | RuntimeException e) {
            // diagnostics must never break the bubble; the in-memory list still works
        }
    }

    // ------------------------------------------------------------- the words

    /** Why the mic bubble is wanted or not (the only-typing rule of VoxAccessibilityService.refreshVisibility). */
    static String micReason(boolean busy, boolean onlyTyping, boolean fieldFocused) {
        return micReason(busy, onlyTyping, fieldFocused, false, true);
    }

    /** The same with the "Always show the bubble" setting and whether the screen is on (BubbleLogic.shouldShow decides the rest). */
    static String micReason(boolean busy, boolean onlyTyping, boolean fieldFocused, boolean alwaysShow, boolean screenOn) {
        if (busy) return "recording or working";
        if (!screenOn) return "screen is off";
        if (alwaysShow) return "always show is on";
        if (!onlyTyping) return "only-typing is off";
        return fieldFocused ? "text field focused" : "no text field focused";
    }

    /** The event for the mic bubble being shown ({@code show}) or hidden: only-typing's own pair when that rule decided, else a plain add or remove. */
    static String micKind(boolean show, boolean busy, boolean onlyTyping, boolean fieldFocused) {
        return micKind(show, busy, onlyTyping, fieldFocused, false, true);
    }

    /** The same when "Always show the bubble" or a dark screen may have decided instead of the only-typing rule. */
    static String micKind(boolean show, boolean busy, boolean onlyTyping, boolean fieldFocused, boolean alwaysShow, boolean screenOn) {
        if (!busy && onlyTyping && !alwaysShow && screenOn) return show ? ONLY_TYPING_SHOW : ONLY_TYPING_HIDE;
        return show ? OVERLAY_ADD : OVERLAY_REMOVE;
    }

    /** The "is the accessibility service alive" line: switched on in Android's settings versus actually connected. */
    static String serviceLine(boolean enabledInSettings, boolean connected) {
        if (connected) return "Connected: the bubble can be drawn";
        if (enabledInSettings) {
            return "Switched on but not running: Android stopped it. Switch it off and on in Accessibility settings";
        }
        return "Off: turn Vox on in Accessibility settings";
    }

    static String batteryLine(boolean ignoringOptimisations) {
        return ignoringOptimisations ? "Vox is not restricted by battery optimisation"
                : "Battery optimisation may stop Vox in the background";
    }

    /** The text of the Copy button: a short header, then the events newest first (as passed in). */
    static String report(String deviceLine, String serviceLine, String batteryLine, List<Event> newestFirst, TimeZone zone) {
        StringBuilder b = new StringBuilder("Vox bubble diagnostics\n");
        b.append(deviceLine).append('\n');
        b.append("Service: ").append(serviceLine).append('\n');
        b.append("Battery: ").append(batteryLine).append('\n');
        if (newestFirst.isEmpty()) {
            b.append("No events recorded yet.\n");
        } else {
            b.append("Last ").append(newestFirst.size()).append(" events, newest first:\n");
            for (Event e : newestFirst) b.append(e.text(zone)).append('\n');
        }
        return b.toString();
    }
}
