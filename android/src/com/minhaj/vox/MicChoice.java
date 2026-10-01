package com.minhaj.vox;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * The microphone choice as pure logic (no android.*): which input devices the page offers, how they are named, and which
 * connected one the recorder prefers. The device is remembered by a key made of its type and product name, never by
 * its numeric id (Android hands out a new id every time a device connects). An empty key means the phone's default.
 * The type numbers are those of android.media.AudioDeviceInfo (they never change), so this class needs no Android jar.
 */
public final class MicChoice {
    public static final int BUILTIN_MIC = 15, WIRED_HEADSET = 3, USB_DEVICE = 11, USB_HEADSET = 22,
            BLUETOOTH_SCO = 7, BLE_HEADSET = 26, HEARING_AID = 23;

    /** The one-line notice when the saved microphone is not connected. */
    public static final String NOT_CONNECTED = "Chosen microphone not connected, using the phone's";

    private MicChoice() { }

    /** An input device Android reports: its type, product name and the platform object itself ({@code ref}, opaque here). */
    public static final class Candidate {
        public final int type;
        public final String name;
        public final Object ref;

        public Candidate(int type, String name, Object ref) { this.type = type; this.name = name; this.ref = ref; }
    }

    /** One line of the page's list. */
    public static final class Option {
        public final String key, label;

        Option(String key, String label) { this.key = key; this.label = label; }
    }

    /** True for the types that are microphones the page offers (the output-only types are not). */
    public static boolean isMic(int type) {
        return type == BUILTIN_MIC || type == WIRED_HEADSET || type == USB_DEVICE || type == USB_HEADSET
                || type == BLUETOOTH_SCO || type == BLE_HEADSET || type == HEARING_AID;
    }

    /** The saved form of a choice: the type, a bar and the trimmed product name. */
    public static String key(int type, String name) {
        return type + "|" + (name == null ? "" : name.trim());
    }

    private static String word(int type) {
        switch (type) {
            case BUILTIN_MIC: return "built-in";
            case WIRED_HEADSET: return "wired";
            case USB_DEVICE: case USB_HEADSET: return "USB";
            case BLUETOOTH_SCO: case BLE_HEADSET: return "Bluetooth";
            case HEARING_AID: return "hearing aid";
            default: return "other";
        }
    }

    /** The product name and a short type word, for example "Pixel 7 (built-in)"; just the word, capitalised, when there is no name. */
    public static String label(int type, String name) {
        String w = word(type), n = name == null ? "" : name.trim();
        if (n.isEmpty()) return Character.toUpperCase(w.charAt(0)) + w.substring(1);
        return n + " (" + w + ")";
    }

    /** The label of a saved key, also for a device that is not connected now. */
    public static String labelOfKey(String key) {
        int bar = key == null ? -1 : key.indexOf('|');
        if (bar < 1) return "Unknown microphone";
        try {
            int type = Integer.parseInt(key.substring(0, bar));
            return isMic(type) ? label(type, key.substring(bar + 1)) : "Unknown microphone";
        } catch (NumberFormatException e) {
            return "Unknown microphone";
        }
    }

    /**
     * The list for the page: the microphones among the candidates, each key once (a phone can report several built-in
     * microphones under one name), each label once (a second one gets " 2", a third " 3").
     */
    public static List<Option> options(List<Candidate> candidates) {
        List<Option> out = new ArrayList<>();
        if (candidates == null) return out;
        Set<String> keys = new HashSet<>(), labels = new HashSet<>();
        for (Candidate c : candidates) {
            if (!isMic(c.type)) continue;
            String k = key(c.type, c.name);
            if (!keys.add(k)) continue;
            String base = label(c.type, c.name), l = base;
            for (int n = 2; !labels.add(l); n++) l = base + " " + n;
            out.add(new Option(k, l));
        }
        return out;
    }

    /** The connected device the saved key names, or null (use the phone's default) for an empty, unknown or not connected key. */
    public static Candidate pick(String savedKey, List<Candidate> connected) {
        if (savedKey == null || savedKey.isEmpty() || connected == null) return null;
        for (Candidate c : connected) {
            if (isMic(c.type) && key(c.type, c.name).equals(savedKey)) return c;
        }
        return null;
    }

    /** True when the "not connected" notice has not been shown yet for this saved choice (the default never warns). */
    public static boolean shouldWarn(String savedKey, String warnedFor) {
        return savedKey != null && !savedKey.isEmpty() && !savedKey.equals(warnedFor);
    }
}
