package com.minhaj.vox;

/** What one sync run did (windows/sync.py sync_once returns the same four things as a dict). */
final class SyncResult {
    /** Notes and delete markers sent to the relay and accepted. */
    final int pushed;
    /** Notes and delete markers received from the relay that changed this phone. */
    final int pulled;
    /** Words for the user when something went wrong, "" when the run went through. */
    final String error;
    /** What happened to the profile: "" (nothing), "sent", "received" or "both"; "" when the run stopped before it. */
    final String profile;

    SyncResult(int pushed, int pulled, String error, String profile) {
        this.pushed = pushed;
        this.pulled = pulled;
        this.error = error == null ? "" : error;
        this.profile = profile == null ? "" : profile;
    }

    /** A run that did nothing because of {@code error}. */
    static SyncResult failed(String error) {
        return new SyncResult(0, 0, error, "");
    }

    boolean ok() {
        return error.isEmpty();
    }
}
