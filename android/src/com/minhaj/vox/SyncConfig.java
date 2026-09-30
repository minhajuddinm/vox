package com.minhaj.vox;

import java.util.Map;

/**
 * The settings side of the relay sync: the profile fields of this phone and the switch that decides whether the
 * provider settings and API keys travel. Prefs (through ProfileMap) is what the app uses; the off-device tests use a
 * map in memory. Pure Java so the sync stays testable without a device.
 */
interface SyncConfig {
    /** The setting "also share my provider settings and API keys". Read at the start of every profile attempt. */
    boolean syncKeys();

    /**
     * This phone's shared settings as relay profile fields (ProfileMap.toProfile): every field it has, key fields
     * included. The engine decides which of them travel, so a key field is never sent while {@link #syncKeys} is off.
     */
    Map<String, Object> readProfile();

    /** Saves settings received from the relay (only the fields that differ from what this phone has, in relay form). */
    void writeProfile(Map<String, Object> received);
}
