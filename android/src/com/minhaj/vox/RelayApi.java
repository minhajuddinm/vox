package com.minhaj.vox;

import java.util.List;
import java.util.Map;

/**
 * What the sync needs from a relay (relay/relay.py, protocol in documentation/14-relay.md): four calls on plain maps
 * (the JSON objects the relay speaks, see PlainJson for the value types). RelayClient talks to a real relay; the
 * off-device tests use a fake one. Every failure is a {@link RelayError} with a message fit to show the user.
 */
interface RelayApi {
    /**
     * {@code PUT /notes/{id}}: the relay's answer {@code {"note": {...the stored note with "seq"...}, "applied": boolean}}.
     * {@code applied} is false when the relay already has a newer or identical version.
     */
    Map<String, Object> putNote(Map<String, Object> wire) throws RelayError;

    /** {@code GET /changes?since=..&limit=..}: notes and delete markers written after the sequence number {@code since}. */
    Changes changes(long since, int limit) throws RelayError;

    /** {@code GET /profile}: version 0 and empty data before the first save. */
    Profile getProfile() throws RelayError;

    /**
     * {@code PUT /profile} with {@code If-Match: ifMatch}. A RelayError with status 412 means the profile changed on the
     * relay since it was read; the caller reads it again.
     */
    Profile putProfile(long ifMatch, Map<String, Object> data) throws RelayError;

    /** One page of {@code /changes}: the cursor to ask from next time, and whether the relay may have more. */
    final class Changes {
        final List<Map<String, Object>> notes;
        final long next;
        final boolean more;

        Changes(List<Map<String, Object>> notes, long next, boolean more) {
            this.notes = notes;
            this.next = next;
            this.more = more;
        }
    }

    /** The relay's profile document and its version. */
    final class Profile {
        final long version;
        final Map<String, Object> data;

        Profile(long version, Map<String, Object> data) {
            this.version = version;
            this.data = data;
        }
    }

    /**
     * A call that failed. {@code status} is the HTTP status, or 0 when there was none (the network failed, or the
     * answer was not a relay's). {@code message} is plain words for the user and never holds the token.
     */
    final class RelayError extends Exception {
        /** The words for an answer that is not what a Vox relay says (windows/sync.py uses the same). */
        static final String NOT_A_RELAY = "That address did not answer like a Vox relay.";

        private static final long serialVersionUID = 1L;

        final int status;
        final String message;

        RelayError(int status, String message) {
            super(message);
            this.status = status;
            this.message = message;
        }

        /**
         * True when sending the same thing again will always fail: a 4xx other than 401, 403 and 429 (the relay refuses
         * that note itself, so one note must not stop the others). 401 and 403 are the token or the tailnet user, 429 is
         * a rate limit, and 5xx or no status is the relay or the network: those stop the run and are tried again later.
         * The same rule as SyncError.permanent in windows/sync.py.
         */
        boolean permanent() {
            return status >= 400 && status < 500 && status != 401 && status != 403 && status != 429;
        }
    }
}
