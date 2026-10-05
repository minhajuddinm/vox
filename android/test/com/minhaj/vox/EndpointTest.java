package com.minhaj.vox;

/** Plain-Java checks for Endpoint (the server address rules). Run by CI, exits non-zero on failure. */
public final class EndpointTest {
    private static int checks;

    private static void check(String name, boolean ok) {
        checks++;
        if (!ok) {
            System.err.println("FAIL " + name);
            System.exit(1);
        }
    }

    public static void main(String[] args) {
        String[] privateHosts = {
            "localhost", "127.0.0.1", "[::1]", "10.1.2.3", "172.16.0.5", "172.31.255.255", "192.168.1.20",
            "100.64.0.1", "100.101.102.103", "100.127.255.254", "laptop", "my-laptop.your-tailnet.ts.net",
            "printer.local", "nas.lan", "169.254.1.1", "fd7a:115c:a1e0::1"
        };
        for (String h : privateHosts) check("private " + h, Endpoint.isPrivateHost(h));

        String[] publicHosts = {
            "api.groq.com", "example.com", "8.8.8.8", "100.63.255.255", "100.128.0.1", "172.32.0.1",
            "192.169.0.1", "evil.ts.net.example.com", "1.2.3.4.5", "999.1.1.1", "", null
        };
        for (String h : publicHosts) check("public " + h, !Endpoint.isPrivateHost(h));

        check("blank is fine", Endpoint.error("") == null && Endpoint.error(null) == null && Endpoint.error("  ") == null);
        check("groq default is fine", Endpoint.error(ApiClient.DEFAULT_BASE) == null);
        check("https anywhere", Endpoint.error("https://whisper.example.com/v1") == null);
        check("http on Tailscale ip", Endpoint.error("http://100.90.1.2:8000/v1") == null);
        check("http on lan name", Endpoint.error("http://laptop:8000/v1") == null);
        check("http public refused", Endpoint.error("http://whisper.example.com/v1") != null);
        check("ftp refused", Endpoint.error("ftp://laptop/v1") != null);
        check("no scheme refused", Endpoint.error("laptop:8000/v1") != null);
        check("no host refused", Endpoint.error("http://") != null);

        check("normalize trims slash", "http://laptop:8000/v1".equals(Endpoint.normalize(" http://laptop:8000/v1/ ")));
        check("normalize blank", "".equals(Endpoint.normalize(null)));

        // bf-e SEC-3: numbers are not names, and a plain http name must lead to private addresses only
        for (String h : new String[]{"134744072", "0x08080808", "127.1", "2130706433", "0x"}) check("numeric " + h, !Endpoint.isPrivateHost(h));
        check("numeric host needs https", Endpoint.error("http://134744072:8000/v1") != null);
        Endpoint.Resolver real = Endpoint.resolver;
        try {
            Endpoint.resolver = host -> new java.net.InetAddress[]{java.net.InetAddress.getByName("192.168.1.9"), java.net.InetAddress.getByName("fd7a:115c:a1e0::1")};
            check("private answers are fine", Endpoint.resolvedError("http://gpu-pc:8000/v1") == null);
            Endpoint.resolver = host -> new java.net.InetAddress[]{java.net.InetAddress.getByName("10.0.0.2"), java.net.InetAddress.getByName("8.8.8.8")};
            check("one public answer is refused", Endpoint.resolvedError("http://gpu-pc:8000/v1") != null);
            check("https is not looked up", Endpoint.resolvedError("https://gpu-pc/v1") == null);
            Endpoint.resolver = host -> { throw new java.net.UnknownHostException(host); };
            check("an unknown name is left to the request", Endpoint.resolvedError("http://gpu-pc/v1") == null);
        } finally {
            Endpoint.resolver = real;
        }
        String[][] addresses = {
            {"127.0.0.1", "true"}, {"::1", "true"}, {"fe80::1", "true"}, {"fd00::5", "true"}, {"100.100.1.1", "true"},
            {"::ffff:192.168.1.2", "true"}, {"8.8.8.8", "false"}, {"::ffff:8.8.8.8", "false"}, {"2606:4700::1111", "false"},
            {"100.128.0.1", "false"}, {"fec0::1", "false"}
        };
        for (String[] a : addresses) {
            try {
                check("address " + a[0], Endpoint.privateAddress(java.net.InetAddress.getByName(a[0])) == a[1].equals("true"));
            } catch (java.net.UnknownHostException e) {
                check("address " + a[0] + " parses", false);
            }
        }

        System.out.println("OK: " + checks + " checks passed");
    }
}
