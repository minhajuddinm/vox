package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Plain-Java checks for MicChoice: the microphone list, its labels and which device the recorder prefers. Exits non-zero on failure. */
public final class MicChoiceTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ": expected <" + expected + "> but got <" + actual + ">");
            System.exit(1);
        }
    }

    private static MicChoice.Candidate c(int type, String name) { return new MicChoice.Candidate(type, name, "handle:" + type + ":" + name); }

    public static void main(String[] args) {
        // key: the type and the product name, never the numeric id of the device
        eq("key is type and name", "3|Wired mic", MicChoice.key(MicChoice.WIRED_HEADSET, "Wired mic"));
        eq("key trims the name", "3|Wired mic", MicChoice.key(MicChoice.WIRED_HEADSET, "  Wired mic "));
        eq("key of a missing name", "15|", MicChoice.key(MicChoice.BUILTIN_MIC, null));
        eq("same name, other type, other key", false, MicChoice.key(MicChoice.USB_DEVICE, "X").equals(MicChoice.key(MicChoice.USB_HEADSET, "X")));

        // label: the name and a short type word
        eq("label built-in", "Pixel 7 (built-in)", MicChoice.label(MicChoice.BUILTIN_MIC, "Pixel 7"));
        eq("label wired", "Headset (wired)", MicChoice.label(MicChoice.WIRED_HEADSET, "Headset"));
        eq("label usb device", "Blue Yeti (USB)", MicChoice.label(MicChoice.USB_DEVICE, "Blue Yeti"));
        eq("label usb headset", "Jabra (USB)", MicChoice.label(MicChoice.USB_HEADSET, "Jabra"));
        eq("label without a name is the type word", "Built-in", MicChoice.label(MicChoice.BUILTIN_MIC, ""));
        eq("label without a name, null", "USB", MicChoice.label(MicChoice.USB_DEVICE, null));

        // isMic: only what can record, and what the page offers
        eq("built-in is a mic", true, MicChoice.isMic(MicChoice.BUILTIN_MIC));
        eq("wired, usb device and usb headset are mics", true,
                MicChoice.isMic(MicChoice.WIRED_HEADSET) && MicChoice.isMic(MicChoice.USB_DEVICE) && MicChoice.isMic(MicChoice.USB_HEADSET));
        // Bluetooth is not offered: setPreferredDevice does not open the Bluetooth voice link, so the recording can be silent
        eq("bluetooth sco is not offered", false, MicChoice.isMic(7));
        eq("bluetooth le headset is not offered", false, MicChoice.isMic(26));
        eq("hearing aid is not offered", false, MicChoice.isMic(23));
        eq("a2dp (output only) is not", false, MicChoice.isMic(8));
        eq("telephony is not", false, MicChoice.isMic(18));

        // options: the page's list, each key once, each label once
        List<MicChoice.Candidate> found = Arrays.asList(
                c(MicChoice.BUILTIN_MIC, "Pixel 7"), c(MicChoice.BUILTIN_MIC, "Pixel 7"),   // two built-in mics with one name
                c(MicChoice.USB_DEVICE, "Dock"), c(MicChoice.USB_HEADSET, "Dock"),         // two keys, one label
                c(7, "Buds"), c(26, "LE Buds"), c(23, "Phonak"), c(8, "Buds speaker"));   // Bluetooth and output-only ones are left out
        List<MicChoice.Option> opts = MicChoice.options(found);
        eq("options: 3 entries", 3, opts.size());
        eq("options: first", "Pixel 7 (built-in)", opts.get(0).label);
        eq("options: usb device", "Dock (USB)", opts.get(1).label);
        eq("options: identical label gets a 2", "Dock (USB) 2", opts.get(2).label);
        eq("options: keys are kept", MicChoice.key(MicChoice.USB_HEADSET, "Dock"), opts.get(2).key);
        eq("options of nothing", 0, MicChoice.options(new ArrayList<MicChoice.Candidate>()).size());
        eq("options of null", 0, MicChoice.options(null).size());

        // pick: the device to prefer, or null for the phone's default
        eq("pick the saved one", "handle:11:Dock", MicChoice.pick(MicChoice.key(MicChoice.USB_DEVICE, "Dock"), found).ref);
        eq("pick: a saved Bluetooth choice falls back to the default", null, MicChoice.pick("7|Buds", found));
        eq("pick: a saved Bluetooth LE choice falls back to the default", null, MicChoice.pick("26|LE Buds", found));
        eq("pick: empty saved key is the default", null, MicChoice.pick("", found));
        eq("pick: null saved key is the default", null, MicChoice.pick(null, found));
        eq("pick: unknown saved key is the default", null, MicChoice.pick("7|Gone", found));
        eq("pick: nothing connected", null, MicChoice.pick(MicChoice.key(MicChoice.USB_DEVICE, "Dock"), new ArrayList<MicChoice.Candidate>()));
        eq("pick: null list", null, MicChoice.pick("7|Buds", null));
        eq("pick: an output-only device is never picked", null, MicChoice.pick("8|Buds speaker", found));

        // labelOfKey: a saved choice that is not connected is still shown
        eq("labelOfKey", "Dock (USB)", MicChoice.labelOfKey("11|Dock"));
        eq("labelOfKey of a Bluetooth key", "Unknown microphone", MicChoice.labelOfKey("7|Buds"));
        eq("labelOfKey of a damaged key", "Unknown microphone", MicChoice.labelOfKey("junk"));

        // usable: a saved Bluetooth choice is the default now, with no "not connected" notice and no entry in the list
        eq("usable keeps a wired key", "11|Dock", MicChoice.usable("11|Dock"));
        eq("usable keeps the default", "", MicChoice.usable(""));
        eq("usable of null", "", MicChoice.usable(null));
        eq("usable drops a Bluetooth sco key", "", MicChoice.usable("7|Buds"));
        eq("usable drops a Bluetooth le key", "", MicChoice.usable("26|Buds"));
        eq("usable drops a hearing aid key", "", MicChoice.usable("23|Phonak"));

        // shouldWarn: the "not connected" notice comes once, not before every dictation
        eq("warn the first time", true, MicChoice.shouldWarn("15|Pixel", null));
        eq("no second warning for the same choice", false, MicChoice.shouldWarn("15|Pixel", "15|Pixel"));
        eq("warn again for another choice", true, MicChoice.shouldWarn("11|Dock", "15|Pixel"));
        eq("never warn for the default", false, MicChoice.shouldWarn("", null));

        System.out.println("OK: " + checks + " checks passed");
    }
}
