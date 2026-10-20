"""Real HTTP framing must survive the bootstrap's bounded response reader."""

import json
import threading
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from throng_api import ci_bootstrap


@pytest.fixture
def peer():
    """Expose raw HTTP framing without replacing urllib's response or reading its body early."""
    state = SimpleNamespace(body=b"", headers={}, calls=0)

    class Handler(BaseHTTPRequestHandler):
        """Write the configured bytes and close the connection, including intentional early EOF."""

        def log_message(self, *args):
            pass

        def do_POST(self):
            state.calls += 1
            assert self.headers["Authorization"] == "Bearer test-identity"
            assert json.loads(self.rfile.read(int(self.headers["Content-Length"]))) == {}
            self.send_response(200)
            for key, value in state.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(state.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/json"
    try:
        yield state
        assert state.calls == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


# Assertions: premature EOF is transport failure even if all received bytes form
# valid JSON; both partial bytes and the exact missing count remain available.
@pytest.mark.parametrize("body", [b"{", b'{"status":"started"}', b""])
def test_bounded_json_read_rejects_a_truncated_content_length(peer, body):
    """Classify a short fixed-length response as retryable, rather than accepting or parsing it."""
    peer.body, peer.headers = body, {"Content-Length": "100"}
    with pytest.raises(IncompleteRead) as error:
        ci_bootstrap.request_json(peer.url, token="test-identity", body={})
    assert error.value.partial == body and error.value.expected == 100 - len(body)


# Assertions: complete fixed-length, chunked and EOF-framed documents all retain
# their supported JSON behavior; a missing length is not an incomplete response.
@pytest.mark.parametrize("framing", ["fixed", "chunked", "eof"])
def test_bounded_json_read_accepts_complete_http_framing(peer, framing):
    """Preserve normal HTTP framing without requiring Content-Length from every provider."""
    content = b'{"status":"started"}'
    if framing == "fixed":
        peer.body, peer.headers = content, {"Content-Length": str(len(content))}
    elif framing == "chunked":
        peer.body = f"{len(content):x}\r\n".encode() + content + b"\r\n0\r\n\r\n"
        peer.headers = {"Transfer-Encoding": "chunked"}
    else:
        peer.body = content
    assert ci_bootstrap.request_json(peer.url, token="test-identity", body={}) == {
        "status": "started"
    }


# Assertions: the 256 KiB response limit wins before any framing/JSON handling;
# retaining bytes beyond that bound cannot turn oversize into a transport retry.
@pytest.mark.parametrize("framing", ["fixed_truncated", "eof"])
def test_bounded_json_read_checks_size_before_missing_bytes(peer, framing):
    """Keep oversized responses permanent even when Content-Length advertises another byte."""
    peer.body = b" " * (256 * 1024 + 1)
    if framing == "fixed_truncated":
        peer.headers = {"Content-Length": str(len(peer.body) + 1)}
    with pytest.raises(ValueError, match="response too large"):
        ci_bootstrap.request_json(peer.url, token="test-identity", body={})


# Assertions: complete but invalid JSON, including a legitimately empty body,
# stays a permanent response failure; transport retry must not hide it.
@pytest.mark.parametrize("body", [b"{", b""])
def test_bounded_json_read_does_not_retry_complete_invalid_json(peer, body):
    """Distinguish malformed application data from a response cut short on the wire."""
    peer.body, peer.headers = body, {"Content-Length": str(len(body))}
    with pytest.raises(json.JSONDecodeError):
        ci_bootstrap.request_json(peer.url, token="test-identity", body={})


# Assertions: chunk framing already detects its own early EOF; the fixed-length
# guard must preserve that error without treating length=None as valid completion.
def test_bounded_json_read_retains_chunked_transport_failure(peer):
    """A broken chunked transfer remains retryable independently of Content-Length."""
    peer.body, peer.headers = b"64\r\n{", {"Transfer-Encoding": "chunked"}
    with pytest.raises(IncompleteRead):
        ci_bootstrap.request_json(peer.url, token="test-identity", body={})
