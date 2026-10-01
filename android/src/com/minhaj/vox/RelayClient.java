package com.minhaj.vox;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UnsupportedEncodingException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Talks to a relay (relay/relay.py) over HTTP with HttpURLConnection and PlainJson: the four calls of RelayApi, plus
 * {@link #check} for the Test connection button. Every request carries {@code Authorization: Bearer <token>} and
 * {@code X-Vox-Device: <name>}; the token goes only to the address it was made for, and redirects are never followed
 * (a Location header could otherwise take it to another server). Every failure is a RelayError in plain words (the
 * words of windows/sync.py): a network failure says Tailscale, a 401 the token, a 403 the tailnet user, any other
 * status its number and whatever the relay said about it. The message never holds the token or the address.
 * No android.* and no org.json, so it is tested off-device against a real HTTP server.
 */
final class RelayClient implements RelayApi {
    /** Seconds to connect, and to wait for an answer, in milliseconds (windows/sync.py TIMEOUT is 15 s). */
    static final int TIMEOUT_MS = 15000;

    /** The largest answer read into memory. */
    static final int MAX_ANSWER = 32 * 1024 * 1024;

    /** The most of a relay's own error text that is shown. */
    static final int MAX_DETAIL = 200;

    private final String base;
    private final String token;
    private final String device;

    RelayClient(String baseUrl, String token, String device) {
        this.base = Endpoint.normalize(baseUrl);
        this.token = token == null ? "" : token.trim();
        this.device = headerText(device);
    }

    // ------------------------------------------------------------------ settings

    /**
     * Why this relay address or token cannot be used, or "" (windows/sync.py problem, with two more checks that make
     * a confusing failure plain: the address is used as the start of every path, so it cannot carry a ? or #, and the
     * token goes into a header, so it cannot carry spaces or anything but printable ASCII). A path in the address is
     * allowed (a relay published under one).
     */
    static String problem(String url, String token) {
        String err = Endpoint.error(url);
        if (err != null) return err;
        String u = Endpoint.normalize(url);
        if (u.isEmpty()) return "Enter the relay address.";
        if (u.indexOf('?') >= 0 || u.indexOf('#') >= 0) return "The relay address must not have a ? or # in it.";
        String t = token == null ? "" : token.trim();
        if (t.isEmpty()) return "Enter the relay token.";
        for (int i = 0; i < t.length(); i++) {
            char c = t.charAt(i);
            if (c < 0x21 || c > 0x7E) return "The relay token has a space or another character that cannot be sent.";
        }
        return "";
    }

    /**
     * The outcome of {@link #check}: whether it worked, what to tell the user either way, and what the test learned
     * (RelayCheck.Result): {@code reachable}, {@code tokenOk}, {@code deviceName} (as the relay lists this phone),
     * {@code relayVersion} ("" when unknown) and {@code notes}.
     */
    static final class Check {
        final boolean ok;
        final String message;
        final boolean reachable;
        final boolean tokenOk;
        final String deviceName;
        final String relayVersion;
        final long notes;

        Check(RelayCheck.Result r) {
            this.ok = r.ok;
            this.message = r.message;
            this.reachable = r.reachable;
            this.tokenOk = r.tokenOk;
            this.deviceName = r.deviceName;
            this.relayVersion = r.relayVersion;
            this.notes = r.notes;
        }
    }

    /**
     * Can this address and token reach a relay? (sync.test_relay; the decision is RelayCheck.of). Never throws. Makes no
     * request when the settings are unusable. The relay lists this phone under the name as it goes into the header, and
     * that is the name reported.
     */
    static Check check(String url, String token, String device) {
        String sent = headerText(device);
        String err = problem(url, token);
        if (!err.isEmpty()) return new Check(RelayCheck.of(0, null, sent, err));
        try {
            return new Check(RelayCheck.of(200, asMap(new RelayClient(url, token, device).call("GET", "/health", null, null)), sent, ""));
        } catch (RelayError e) {
            return new Check(RelayCheck.of(e.status, null, sent, e.message));
        }
    }

    /** The outcome of {@link #listDevices}: the rows for the Devices card, or why there are none. */
    static final class DeviceList {
        final boolean ok;
        /** Plain words, "" when ok. */
        final String error;
        /** Empty when not ok. */
        final List<DevicesView.Row> rows;

        DeviceList(boolean ok, String error, List<DevicesView.Row> rows) {
            this.ok = ok;
            this.error = error;
            this.rows = rows;
        }
    }

    /**
     * The devices that have used the relay as rows for the Devices card (windows/sync.py devices_for_ui). Never throws; a
     * failure is an empty list and the reason. {@code now} is Unix seconds, {@code device} the name this phone sends
     * (the row with that name is "this phone"). Makes no request when the settings are unusable.
     */
    static DeviceList listDevices(String url, String token, String device, double now) {
        String err = problem(url, token);
        if (!err.isEmpty()) return new DeviceList(false, err, new ArrayList<DevicesView.Row>());
        try {
            return new DeviceList(true, "", DevicesView.rows(new RelayClient(url, token, device).devices(), now, device));
        } catch (RelayError e) {
            return new DeviceList(false, e.message, new ArrayList<DevicesView.Row>());
        }
    }

    /** {@code GET /devices}: the relay's device objects ({@code name}, {@code last_seen}, ...), newest first. */
    List<Map<String, Object>> devices() throws RelayError {
        Map<String, Object> m;
        try {
            m = asMap(call("GET", "/devices", null, null));
        } catch (RelayError e) {
            if (e.status == 404) throw new RelayError(404, "This relay is too old to list devices. Update relay.py on it.");
            throw e;
        }
        if (m == null || !(m.get("devices") instanceof List)) throw new RelayError(0, RelayError.NOT_A_RELAY);
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object item : (List<?>) m.get("devices")) {
            Map<String, Object> d = asMap(item);
            if (d == null) throw new RelayError(0, RelayError.NOT_A_RELAY);
            out.add(d);
        }
        return out;
    }

    // ------------------------------------------------------------------ RelayApi

    @Override
    public Map<String, Object> putNote(Map<String, Object> wire) throws RelayError {
        Map<String, Object> out = asMap(call("PUT", "/notes/" + encode(String.valueOf(wire.get("id"))), null, wire));
        if (out == null) throw new RelayError(0, RelayError.NOT_A_RELAY);
        return out;
    }

    @Override
    public Changes changes(long since, int limit) throws RelayError {
        Map<String, Object> m = asMap(call("GET", "/changes?since=" + since + "&limit=" + limit, null, null));
        if (m == null || !(m.get("notes") instanceof List) || !(m.get("next") instanceof Number)) throw new RelayError(0, RelayError.NOT_A_RELAY);
        List<Map<String, Object>> notes = new ArrayList<>();
        for (Object item : (List<?>) m.get("notes")) {
            Map<String, Object> note = asMap(item);
            if (note == null) throw new RelayError(0, RelayError.NOT_A_RELAY);
            notes.add(note);
        }
        return new Changes(notes, ((Number) m.get("next")).longValue(), Boolean.TRUE.equals(m.get("more")));
    }

    @Override
    public Profile getProfile() throws RelayError {
        return profile(call("GET", "/profile", null, null));
    }

    @Override
    public Profile putProfile(long ifMatch, Map<String, Object> data) throws RelayError {
        return profile(call("PUT", "/profile", String.valueOf(ifMatch), data));
    }

    private static Profile profile(Object json) throws RelayError {
        Map<String, Object> m = asMap(json);
        Map<String, Object> data = m == null ? null : asMap(m.get("data"));
        if (data == null || !(m.get("version") instanceof Number)) throw new RelayError(0, RelayError.NOT_A_RELAY);
        return new Profile(((Number) m.get("version")).longValue(), data);
    }

    // ------------------------------------------------------------------ one request

    /**
     * One request and its answer as parsed JSON. A status outside 200 to 299 is a RelayError with that status; so is
     * an answer that is not JSON (status 0) and a network failure (status 0).
     */
    private Object call(String method, String path, String ifMatch, Object body) throws RelayError {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(base + path).openConnection();
            c.setConnectTimeout(TIMEOUT_MS);
            c.setReadTimeout(TIMEOUT_MS);
            c.setInstanceFollowRedirects(false);
            c.setUseCaches(false);
            c.setRequestMethod(method);
            c.setRequestProperty("Authorization", "Bearer " + token);
            c.setRequestProperty("X-Vox-Device", device);
            c.setRequestProperty("Accept", "application/json");
            if (ifMatch != null) c.setRequestProperty("If-Match", ifMatch);
            if (body != null) {
                // Not setFixedLengthStreamingMode: in streaming mode a 401 answer makes HttpURLConnection throw instead of
                // reporting the status. The body is at most about a megabyte, so it is sent from memory with its Content-Length.
                byte[] bytes = PlainJson.stringify(body).getBytes(StandardCharsets.UTF_8);
                c.setRequestProperty("Content-Type", "application/json");
                c.setDoOutput(true);
                OutputStream out = c.getOutputStream();
                try {
                    out.write(bytes);
                } finally {
                    out.close();
                }
            }
            int status = c.getResponseCode();
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            String text = "";
            if (in != null) {
                try {
                    text = readAll(in);
                } finally {
                    in.close();
                }
            }
            if (status < 200 || status > 299) throw new RelayError(status, failure(status, errorDetail(text)));
            try {
                return PlainJson.parse(text);
            } catch (IllegalArgumentException notJson) {
                throw new RelayError(0, RelayError.NOT_A_RELAY);
            }
        } catch (IOException e) {
            // the class name only: a message could hold the address
            throw new RelayError(0, "Cannot reach the relay (is Tailscale running?): " + e.getClass().getSimpleName());
        }
    }

    private static String readAll(InputStream in) throws IOException, RelayError {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) != -1) {
            out.write(buf, 0, n);
            if (out.size() > MAX_ANSWER) throw new RelayError(0, "The relay's answer was too large.");
        }
        return new String(out.toByteArray(), StandardCharsets.UTF_8);
    }

    // ------------------------------------------------------------------ messages

    /** The words for a failed request (windows/sync.py _call), with what the relay said about it in brackets. */
    static String failure(int status, String detail) {
        if (status == 401) return "The relay refused the token.";
        if (status == 403) return "The relay belongs to another Tailscale user.";
        return "The relay answered HTTP " + status + (detail == null || detail.isEmpty() ? "." : " (" + detail + ").");
    }

    /**
     * What a relay's error body says, tidied for one line: {@code {"error": "text"}} (the relay's own errors, and
     * 411, 413, 429, 503 of a proxy) or the OpenAI shape {@code {"error": {"message": "text"}}} (502 of the proxy).
     * "" when the body is neither. Control characters and runs of blanks become one space, trailing dots go, and
     * more than {@link #MAX_DETAIL} characters are cut.
     */
    static String errorDetail(String body) {
        if (body == null || body.isEmpty()) return "";
        Object json;
        try {
            json = PlainJson.parse(body);
        } catch (IllegalArgumentException notJson) {
            return "";
        }
        if (!(json instanceof Map)) return "";
        Object err = ((Map<?, ?>) json).get("error");
        if (err instanceof Map) err = ((Map<?, ?>) err).get("message");
        if (!(err instanceof String)) return "";
        String s = (String) err;
        StringBuilder sb = new StringBuilder();
        boolean gap = false;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (Character.isISOControl(c) || Character.isWhitespace(c)) {
                gap = true;
            } else {
                if (gap && sb.length() > 0) sb.append(' ');
                gap = false;
                sb.append(c);
            }
        }
        int end = sb.length();
        while (end > 0 && sb.charAt(end - 1) == '.') end--;
        sb.setLength(end);
        if (sb.length() > MAX_DETAIL) {
            int cut = MAX_DETAIL;
            if (Character.isHighSurrogate(sb.charAt(cut - 1))) cut--;   // never split a pair
            sb.setLength(cut);
            sb.append("...");
        }
        return sb.toString();
    }

    // ------------------------------------------------------------------ helpers

    /** A header value that is safe to send: printable ASCII only, anything else becomes "?" (a line break could end the header). */
    private static String headerText(String s) {
        StringBuilder sb = new StringBuilder();
        String t = s == null ? "" : s;
        for (int i = 0; i < t.length(); i++) {
            char c = t.charAt(i);
            sb.append(c >= 0x20 && c <= 0x7E ? c : '?');
        }
        return sb.toString();
    }

    private static String encode(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (UnsupportedEncodingException e) {
            throw new IllegalStateException(e);   // UTF-8 is always there
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object o) {
        return o instanceof Map ? (Map<String, Object>) o : null;
    }
}
