package com.minhaj.vox;

import java.net.URI;
import java.util.Locale;

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
        // A name: single-label names, .local/.lan and Tailscale MagicDNS names never leave the private network.
        return h.indexOf('.') < 0 || h.endsWith(".local") || h.endsWith(".lan") || h.endsWith(".ts.net");
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
