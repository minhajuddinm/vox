package com.minhaj.vox;

import java.util.Map;
import java.util.regex.Pattern;

/**
 * What the Test connection button reports, decided from the HTTP status of {@code GET /health} and its parsed answer.
 * The twin of {@code relay_check} in windows/sync.py; the {@code relaycheck} rows of spec/golden.txt run both. Pure Java:
 * no android.*, no org.json and no network, so the tests run off-device (RelayClient.check does the request).
 */
final class RelayCheck {
    private RelayCheck() {
    }

    /** The most notes the count shows; a larger number is cut to it (both apps, so a silly answer reads the same). */
    static final long MAX_NOTES = 1000000000L;

    private static final Pattern VERSION_OK = Pattern.compile("[0-9A-Za-z.+_-]{1,20}");

    /** One test's outcome. */
    static final class Result {
        /** A relay answered and took the token. */
        final boolean ok;
        /** A relay answered at all: a 401 or 403 is a relay's own refusal and counts; any other failure does not. */
        final boolean reachable;
        /** The token was accepted (a 403 means it was: the relay checks the token before the tailnet owner). */
        final boolean tokenOk;
        /** The name this device sent (it is what the relay lists it as). */
        final String deviceName;
        /** "0.2" or "": kept only as 1 to 20 of letters, digits and . + _ - from an answer that is ok. */
        final String relayVersion;
        /** Notes the relay holds, 0 when it did not say or when not ok. */
        final long notes;
        /** Plain words for the person: "Connected. The relay holds 3 notes." or the failure. */
        final String message;

        Result(boolean ok, boolean reachable, boolean tokenOk, String deviceName, String relayVersion, long notes, String message) {
            this.ok = ok;
            this.reachable = reachable;
            this.tokenOk = tokenOk;
            this.deviceName = deviceName;
            this.relayVersion = relayVersion;
            this.notes = notes;
            this.message = message;
        }
    }

    /**
     * @param status  the HTTP status of /health, 0 when nothing answered
     * @param health  the parsed JSON answer (PlainJson), or null when there was none usable
     * @param device  the name the asking call sent
     * @param failure the words for a failed request (a RelayError's message), used for every status that is not a 2xx
     */
    static Result of(int status, Map<String, Object> health, String device, String failure) {
        if (status >= 200 && status < 300) {
            if (health == null || !Boolean.TRUE.equals(health.get("ok"))) {
                return new Result(false, false, false, device, "", 0, RelayApi.RelayError.NOT_A_RELAY);
            }
            long notes = 0;
            Object n = health.get("notes");
            if (n instanceof Number) {
                double d = ((Number) n).doubleValue();
                if (!Double.isNaN(d) && !Double.isInfinite(d)) notes = Math.max(0, Math.min(MAX_NOTES, (long) d));
            }
            Object v = health.get("version");
            String version = v instanceof String ? ((String) v).trim() : "";
            if (!VERSION_OK.matcher(version).matches()) version = "";
            return new Result(true, true, true, device, version, notes, "Connected. The relay holds " + notes + " notes.");
        }
        String message = failure == null || failure.isEmpty() ? RelayApi.RelayError.NOT_A_RELAY : failure;
        if (status == 401 || status == 403) return new Result(false, true, status == 403, device, "", 0, message);
        return new Result(false, false, false, device, "", 0, message);
    }
}
