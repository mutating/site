"""A transparent HTTP boundary exposes truncation to the delivered bounded reader.

The existing chain peer pre-reads HTTP responses without a limit, which changes
CPython's premature-EOF behavior. Here the script receives HTTPResponse itself.
"""

import asyncio
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from .test_ci_chain import chain as chain
from .test_ci_chain import ci_peer as ci_peer
from .test_ci_chain import github as github
from .test_ci_chain import powercell as powercell

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: one premature EOF at issuer/API is a transient transport failure,
# followed by exactly one two-second retry against the same running reservation.
# The delivered guard must succeed without reserving, cancelling or publishing.
@pytest.mark.parametrize("truncated_stage", ["identity", "watch"])
async def test_delivered_guard_retries_truncated_wire_without_pre_read(
    chain, powercell, truncated_stage
):
    """A bounded read of a truncated real response must not fail a healthy workload's guard."""
    await chain.bind()
    chain.agent = powercell
    selection = await chain.bootstrap()
    await chain.start_job(selection)
    before = await chain.state()
    calls = []

    class TruncatedPeer(BaseHTTPRequestHandler):
        """Close a real HTTP body before its advertised Content-Length is satisfied."""

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply()

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.reply()

        def reply(self):
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"{")

    server = ThreadingHTTPServer(("127.0.0.1", 0), TruncatedPeer)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    namespace = {"__name__": "generated_guard_wire"}
    exec(compile(chain.guard_step["run"], "generated-workflow.py", "exec"), namespace)
    environment = {
        **chain.guard_step["env"],
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "workflow-request-token",
    }
    namespace["os"] = SimpleNamespace(environ=environment)
    loop = asyncio.get_running_loop()
    began = chain.clock.now
    namespace["time"] = SimpleNamespace(
        monotonic=lambda: (chain.clock.now - began).total_seconds(),
        sleep=lambda seconds: asyncio.run_coroutine_threadsafe(chain.pump(seconds), loop).result(
            timeout=10
        ),
    )
    original = urllib.request.build_opener(namespace["NoRedirect"]())

    class Boundary:
        """Rewrite only HTTPS/DNS; leave response framing and every read to production code."""

        def open(self, request, timeout):
            parts = urllib.parse.urlsplit(request.full_url)
            stage = (
                "identity" if parts.hostname.endswith(".actions.githubusercontent.com") else "watch"
            )
            calls.append(stage)
            assert len(calls) <= 3, "One truncated response requires only one retry"
            if stage == truncated_stage and calls.count(stage) == 1:
                target = f"http://127.0.0.1:{server.server_port}/truncated"
            elif stage == "identity":
                target = (
                    str(chain.client.base_url).rstrip("/") + "/__test__/github-oidc?" + parts.query
                )
            else:
                assert parts.path.endswith("/watch"), "Observer must not reserve or cancel"
                target = str(chain.client.base_url).rstrip("/") + parts.path
            forwarded = urllib.request.Request(
                target,
                data=request.data,
                headers=dict(request.header_items()),
                method=request.get_method(),
            )
            return original.open(forwarded, timeout=timeout)

    namespace["urllib"] = SimpleNamespace(
        parse=urllib.parse,
        error=urllib.error,
        request=SimpleNamespace(
            Request=urllib.request.Request, build_opener=lambda *args: Boundary()
        ),
    )
    try:
        await asyncio.wait_for(asyncio.to_thread(namespace["main"]), timeout=15)
        assert calls == (
            ["identity", "identity", "watch"]
            if truncated_stage == "identity"
            else ["identity", "watch", "watch"]
        )
        assert chain.clock.now - began == timedelta(seconds=2)
        after = await chain.state()
        assert after.id == before.id and after.status == "running" and chain.peer.jit_count == 1
        assert not any(path.exists() for path in chain.outputs[1:])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
        await chain.finish_job()
        await chain.drain()
