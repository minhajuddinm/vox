"""Smoke test for a standalone relay binary (run by the relay-exe CI job; also fine by hand).

    python tools/relay_smoke.py path/to/vox-relay-linux-x64

1. `<binary> --help` must exit 0.
2. The binary is started on 127.0.0.1 with a temporary data folder, a free port and --show-token.
3. GET /health without the token must answer 401, with the token 200 and {"ok": true}.
4. The binary is stopped. Any failure exits 1 and prints what the binary wrote.
Standard library only.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

START_TIMEOUT = 90   # a --onefile binary unpacks itself first; antivirus can make that slow on Windows


def get(url, token=None):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    exe = os.path.abspath(argv[1])
    h = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=120)
    if h.returncode != 0 or "--data-dir" not in h.stdout:
        print(f"FAIL: --help exit {h.returncode}\n{h.stdout}\n{h.stderr}")
        return 1
    print("--help ok")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    data = tempfile.mkdtemp(prefix="vox-relay-smoke-")
    proc = subprocess.Popen([exe, "--data-dir", data, "--port", str(port), "--show-token"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines, token = [], []

    def reader():
        for line in proc.stdout:
            lines.append(line.rstrip())
            if line.startswith("Token:"):
                token.append(line.split(":", 1)[1].strip())
    threading.Thread(target=reader, daemon=True).start()

    ok = False
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline and not (token and proc.poll() is None and _up(base)):
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        if proc.poll() is not None:
            print(f"FAIL: the relay exited early with {proc.returncode}")
        elif not token:
            print("FAIL: no token was printed")
        else:
            code, _ = get(base + "/health")
            if code != 401:
                print(f"FAIL: /health without a token answered {code}, expected 401")
            else:
                code, body = get(base + "/health", token[0])
                if code == 200 and json.loads(body).get("ok") is True:
                    print("/health ok:", body.strip())
                    ok = True
                else:
                    print(f"FAIL: /health with the token answered {code}: {body}")
    finally:
        if proc.poll() is None:
            if sys.platform == "win32":   # a --onefile exe is a launcher plus the real relay: end the whole tree
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
        if not ok:
            print("--- what the relay printed ---")
            print("\n".join(l for l in lines if not l.startswith("Token:")))
    return 0 if ok else 1


def _up(base):
    try:
        get(base + "/health")
        return True
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(main(sys.argv))
