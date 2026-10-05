"""SEC-9: an AI server's answer is read up to 8 MB (vox_core.read_capped, as the relay and the phone do). A broken or
hostile server that streams gigabytes gets an error instead of filling the memory of the engine thread. Real local
HTTP servers on 127.0.0.1 only."""
import http.server
import threading

import pytest

import vox_core as core


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    body = b"{}"
    chunked = False
    hits = []

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if self.chunked:   # no Content-Length: the size is known only by reading
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for i in range(0, len(self.body), 1 << 20):
                    part = self.body[i:i + (1 << 20)]
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
                self.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass   # the client stopped reading
            return
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        try:
            self.wfile.write(self.body)
        except OSError:
            pass

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    _Handler.hits = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _serve(body, chunked=False):
    _Handler.body, _Handler.chunked = body, chunked


@pytest.mark.real_session
@pytest.mark.parametrize("chunked", [False, True])
def test_an_answer_over_8_mb_is_refused_and_not_sent_again(server, chunked):
    _, base = server
    _serve(b'{"text": "' + b"a" * (core.MAX_ANSWER_BYTES + 10) + b'"}', chunked)
    with pytest.raises(core.ApiError) as e:
        core.post_with_retry(base + "/v1/audio/transcriptions", data={"x": "1"}, timeout=10)
    assert "over 8 MB" in str(e.value) and e.value.code == 0
    assert _Handler.hits == ["/v1/audio/transcriptions"]   # an error answer, not a dropped connection: no retry


@pytest.mark.real_session
@pytest.mark.parametrize("chunked", [False, True])
def test_an_answer_up_to_the_cap_is_read_whole(server, chunked):
    _, base = server
    text = "b" * (core.MAX_ANSWER_BYTES - 20)
    _serve(('{"text": "%s"}' % text).encode(), chunked)
    r = core.post_with_retry(base + "/v1/audio/transcriptions", data={"x": "1"}, timeout=10)
    assert core.check_response(r)["text"] == text


@pytest.mark.real_session
def test_a_huge_cleanup_answer_falls_back_to_the_rules_layer(server, monkeypatch):
    _, base = server
    _serve(b'{"choices": [{"message": {"content": "' + b"c" * (core.MAX_ANSWER_BYTES + 1) + b'"}}]}')
    cfg = dict(core.DEFAULT_CONFIG, base_url=base + "/v1", api_key="k", cleanup_min_words=1)
    r = core.process_text(cfg, "please send the report to the team today", "notepad.exe", "Notepad")
    assert not r.cleaned and "over 8 MB" in r.cleanup_error
    assert r.text == "Please send the report to the team today."


@pytest.mark.parametrize("error", ["broken", "over"])
def test_a_body_that_breaks_off_drops_the_connection_too(error):
    # review 7: only the over-cap error closed the answer; a body that broke off halfway left the connection open
    import requests
    r, closed = requests.Response(), []

    def chunks(size):
        yield b"x" * 10
        if error == "broken":
            raise requests.exceptions.ChunkedEncodingError("connection broken")
        yield b"y" * 100

    r.iter_content, r.close = chunks, lambda: closed.append(True)
    with pytest.raises(requests.exceptions.ChunkedEncodingError if error == "broken" else core.ApiError):
        core.read_capped(r, cap=50)
    assert closed == [True]


def test_a_stand_in_answer_of_a_test_passes_through():
    class R:
        status_code = 200
    r = R()
    assert core.read_capped(r) is r
