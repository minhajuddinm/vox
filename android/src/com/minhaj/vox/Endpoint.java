package com.minhaj.vox;

import java.net.Inet4Address;
import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.URI;
import java.net.UnknownHostException;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Rules for the server address (the "base URL" of the speech and cleanup API).
 * The API key and your voice go to this address, so plain http is only allowed for private hosts:
 * this phone, the local network and Tailscale. Pure Java so it can be tested off-device.
 */
final class Endpoint {
    private Endpoint() { }

    /** Trims blanks and a trailing slash; blank stays blank. */
    static String normalize(String url) {
        String u = url == null ? "" : url.trim();
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        return u;
    }

    /** Null when the address is acceptable, otherwise a short message for the user. */
    static String error(String url) {
        String u = normalize(url);
        if (u.isEmpty()) return null;
        String scheme;
        String host;
        try {
            URI uri = new URI(u);
            scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
            host = uri.getHost();
        } catch (Exception e) {
            return "The server address must start with http:// or https://";
        }
        if ((!scheme.equals("http") && !scheme.equals("https")) || host == null || host.isEmpty()) {
            return "The server address must start with http:// or https://";
        }
        if (scheme.equals("http") && !isPrivateHost(host)) {
            return "Plain http is only allowed for this phone, your local network or Tailscale. Use https:// for other servers.";
        }
        return null;
    }

    static boolean isPrivateHost(String host) {
        if (host == null) return false;
        String h = host.trim().toLowerCase(Locale.ROOT);
        if (h.startsWith("[") && h.endsWith("]")) h = h.substring(1, h.length() - 1);
        while (h.endsWith(".")) h = h.substring(0, h.length() - 1);
        if (h.isEmpty()) return false;

        int[] v4 = ipv4(h);
        if (v4 != null) {
            int a = v4[0], b = v4[1];
            return a == 127 || a == 10
                    || (a == 172 && b >= 16 && b <= 31)
                    || (a == 192 && b == 168)
                    || (a == 169 && b == 254)
                    || (a == 100 && b >= 64 && b <= 127);   // Tailscale (CGNAT range)
        }
        if (h.indexOf(':') >= 0) {                          // IPv6 literal
            // ::1, fc00::/7 (unique local) and fe80::/10 (link local): the first group must be written with all four digits.
            // Nothing else, so 6to4 (2002::/16), Teredo (2001::/32) and IPv4-mapped addresses need https.
            return h.equals("::1") || h.matches("f[cd][0-9a-f]{2}:.*") || h.matches("fe[89ab][0-9a-f]:.*");
        }
        // Not a name when the last label is a number: a resolver reads 134744072 or 0x08080808 as 8.8.8.8 (golden rows, SEC-3).
        if (NUMERIC_LABEL.matcher(h.substring(h.lastIndexOf('.') + 1)).matches()) return false;
        // A name: single-label names, .local/.lan and Tailscale MagicDNS names. Where they lead is checked again before
        // each request (resolvedError): a foreign network can answer for them.
        return h.indexOf('.') < 0 || h.endsWith(".local") || h.endsWith(".lan") || h.endsWith(".ts.net");
    }

    private static final Pattern NUMERIC_LABEL = Pattern.compile("[0-9]+|0x[0-9a-f]*");

    /** Looks a host name up; a test can swap it for a fake. */
    interface Resolver {
        InetAddress[] lookup(String host) throws UnknownHostException;
    }

    static volatile Resolver resolver = InetAddress::getAllByName;

    /**
     * For a plain http address: null when every address its host resolves to is private (privateAddress), otherwise a
     * short message. Always null for https and for a name that does not resolve (the request then fails by itself).
     * error() only sees the name, and a hotel's DNS or a spoofed LLMNR/mDNS answer can send it anywhere; this runs just
     * before each request (it does a lookup, so never on the main thread). The connection looks the name up again, which
     * Android answers from its short cache, so it lands on an address checked here (vox_core.PrivatePeerConnection
     * checks the connected address itself).
     */
    static String resolvedError(String url) {
        String host;
        try {
            URI uri = new URI(normalize(url));
            if (!"http".equalsIgnoreCase(uri.getScheme())) return null;
            host = uri.getHost();
        } catch (Exception e) {
            return null;   // error() has already refused it
        }
        if (host == null || host.isEmpty()) return null;
        if (host.startsWith("[") && host.endsWith("]")) host = host.substring(1, host.length() - 1);
        InetAddress[] found;
        try {
            found = resolver.lookup(host);
        } catch (UnknownHostException e) {
            return null;
        }
        for (InetAddress a : found) {
            if (!privateAddress(a)) {
                // the words of vox_core.PLAIN_HTTP_ELSEWHERE, with "this phone"
                return "Plain http only goes to this phone, your local network or Tailscale, and this name led somewhere else. Use https:// or the address in numbers.";
            }
        }
        return null;
    }

    /** True for a resolved address plain http may go to: the same ranges as isPrivateHost (an IPv4-mapped one by its IPv4). */
    static boolean privateAddress(InetAddress a) {
        byte[] b = a.getAddress();
        if (a instanceof Inet6Address && b.length == 16) {
            boolean mapped = true;
            for (int i = 0; i < 12; i++) mapped &= b[i] == (i < 10 ? 0 : (byte) 0xff);
            if (!mapped) {
                if (a.isLoopbackAddress()) return true;
                int b0 = b[0] & 0xff, b1 = b[1] & 0xff;
                return (b0 & 0xfe) == 0xfc || (b0 == 0xfe && (b1 & 0xc0) == 0x80);   // fc00::/7, fe80::/10
            }
            b = new byte[]{b[12], b[13], b[14], b[15]};
        } else if (!(a instanceof Inet4Address) || b.length != 4) {
            return false;
        }
        int x = b[0] & 0xff, y = b[1] & 0xff;
        return x == 127 || x == 10 || (x == 172 && y >= 16 && y <= 31) || (x == 192 && y == 168)
                || (x == 169 && y == 254) || (x == 100 && y >= 64 && y <= 127);
    }

    /** The four numbers of a dotted IPv4 address, or null when the text is not one. */
    private static int[] ipv4(String h) {
        String[] parts = h.split("\\.", -1);
        if (parts.length != 4) return null;
        int[] out = new int[4];
        for (int i = 0; i < 4; i++) {
            if (parts[i].isEmpty() || parts[i].length() > 3) return null;
            for (int k = 0; k < parts[i].length(); k++) {
                if (!Character.isDigit(parts[i].charAt(k))) return null;
            }
            out[i] = Integer.parseInt(parts[i]);
            if (out[i] > 255) return null;
        }
        return out;
    }
}
