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
            "100.64.0.1", "100.101.102.103", "100.127.255.254", "laptop", "laptop-uv.tail1234.ts.net",
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

        System.out.println("OK: " + checks + " checks passed");
    }
}
