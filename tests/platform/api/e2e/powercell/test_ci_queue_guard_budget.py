"""Execute the delivered observer in its own process against real slow HTTP peers.

Only the validated HTTPS-to-loopback boundary and alarm duration are adapted:
urllib, sockets, response reads, POSIX delivery and the Python exit code are real.
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import yaml

from throng_api.ci_workflow import render


# Assertions: trickling bytes during identity/watch I/O cannot renew the process
# budget; even an interrupted polling sleep ends with exit 1 before job timeout,
# without retries, reservation/cancellation calls, outputs or credential exposure.
@pytest.mark.parametrize("blocked_stage", ["identity", "watch", "sleep"])
def test_delivered_guard_deadline_interrupts_real_trickling_http(tmp_path, blocked_stage):
    """Slow bytes must cause an ordinary guard failure so GitHub fail-fast cancels its sibling."""
    stopped, requests = threading.Event(), []

    class Peer(BaseHTTPRequestHandler):
        """Send a valid response slowly enough to defeat urllib's per-read socket timeout."""

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond("identity", {"value": "private-identity"})

        def do_POST(self):
            assert self.path.endswith("/watch"), "Observer must not reserve or cancel"
            assert self.headers["Authorization"] == "Bearer private-identity"
            assert json.loads(self.rfile.read(int(self.headers["Content-Length"]))) == {
                "instance": "default"
            }
            self.respond("watch", {"status": "waiting" if blocked_stage == "sleep" else "started"})

        def respond(self, stage, payload):
            requests.append(stage)
            content = json.dumps(payload).encode().ljust(100, b" ")
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                if stage == blocked_stage:
                    for value in content:
                        self.wfile.write(bytes([value]))
                        self.wfile.flush()
                        if stopped.wait(0.025):
                            return
                else:
                    self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected: the real alarm terminates the client mid-response.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    route = SimpleNamespace(
        id="01234567-89ab-cdef-0123-456789abcdef",
        ref="refs/heads/main",
        workflow_path=".github/workflows/ci.yml",
        job_key="test",
        preparation_seconds=300,
        timeout_seconds=60,
    )
    document = yaml.safe_load(render(route, "https://api.example"))
    step = document["jobs"]["test"]["steps"][0]
    script = tmp_path / "delivered-guard.py"
    script.write_text(
        "import signal, urllib.parse, urllib.request\n"
        "real_open = urllib.request.build_opener().open\n"
        "def accelerated_alarm(seconds):\n"
        "    assert seconds == 310\n"
        "    print('armed:310', flush=True)\n"
        "    signal.setitimer(signal.ITIMER_REAL, 0.4)\n"
        "signal.alarm = accelerated_alarm\n"
        "class Boundary:\n"
        "    def open(self, request, timeout):\n"
        "        parts = urllib.parse.urlsplit(request.full_url)\n"
        f"        url = 'http://127.0.0.1:{server.server_port}' + parts.path\n"
        "        forwarded = urllib.request.Request(url, data=request.data, "
        "headers=dict(request.header_items()), method=request.get_method())\n"
        "        return real_open(forwarded, timeout=timeout)\n"
        "urllib.request.build_opener = lambda *handlers: Boundary()\n"
        f"exec(compile({step['run']!r}, 'generated-workflow.py', 'exec'), "
        "{'__name__': '__main__'})\n"
    )
    environment = {
        **os.environ,
        **step["env"],
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "private-request-credential",
    }
    environment.pop("GITHUB_OUTPUT", None)
    environment.pop("POWERCELL_INSTANCE", None)
    began = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=4,
        )
        elapsed = time.monotonic() - began
        assert result.returncode == 1, result.stderr
        assert result.stdout == "armed:310\n"
        assert "RuntimeError: CI preparation timed out" in result.stderr
        assert "private-" not in result.stdout + result.stderr
        assert elapsed < 1.5, "The process deadline must interrupt the socket read or polling sleep"
        assert requests == (["identity"] if blocked_stage == "identity" else ["identity", "watch"])
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
