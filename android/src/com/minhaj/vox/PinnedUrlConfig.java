package com.minhaj.vox;

import java.util.Map;

/**
 * A {@link SyncConfig} whose relay address is fixed to the one the run started with. A run builds its client from the
 * address and the engine ties the sync state to the address: if the user saves another address between the two reads,
 * the run would talk to relay A while resetting the state for relay B. Pinning the address makes one run use one
 * address; the next run reads the new one. Everything else is passed on to the wrapped config.
 */
final class PinnedUrlConfig implements SyncConfig {
    private final SyncConfig inner;
    private final String url;

    PinnedUrlConfig(SyncConfig inner, String url) {
        this.inner = inner;
        this.url = url;
    }

    @Override public String relayUrl() { return url; }
    @Override public boolean syncKeys() { return inner.syncKeys(); }
    @Override public Map<String, Object> readProfile() { return inner.readProfile(); }
    @Override public void writeProfile(Map<String, Object> received) { inner.writeProfile(received); }
}
