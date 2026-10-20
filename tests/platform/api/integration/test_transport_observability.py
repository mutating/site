"""Structured transport logs include failures and exclude all request secrets."""

import json
import logging
import runpy
from types import SimpleNamespace

import pytest

from throng_api import transport
from throng_api.config import Settings
from throng_api.transport import RequestLog

pytestmark = pytest.mark.anyio


def test_loading_transport_again_does_not_duplicate_log_handlers():
    """Multiple embedded application loaders keep one copy of each transport log record."""
    logger = logging.getLogger("throng.transport")
    handlers = list(logger.handlers)
    runpy.run_path(transport.__file__)
    assert logger.handlers == handlers


async def test_route_parameters_and_authentication_are_never_logged(caplog):
    """Capability-bearing URLs are represented by route templates, not secret values."""
    caplog.set_level("INFO", logger="throng.transport")

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"private response"})

    async def send(message):
        pass

    await RequestLog(app)(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer private-token")],
            "method": "GET",
            "path": "/capabilities/private-capability",
            "route": SimpleNamespace(path="/capabilities/{secret}"),
        },
        None,
        send,
    )
    row = json.loads(caplog.records[-1].message)
    assert row["path"] == "/capabilities/{secret}"
    assert "private" not in caplog.records[-1].message


async def test_websocket_scope_passes_through_without_http_instrumentation():
    """HTTP request logging must not intercept websocket or lifespan messages."""
    calls = []

    async def app(scope, receive, send):
        calls.append(scope)

    scope = {"type": "websocket"}
    await RequestLog(app)(scope, None, None)
    assert calls == [scope]


async def test_uncaught_application_failure_is_logged_without_request_payload(caplog):
    """An exception before response headers still records a correlated failed attempt."""
    caplog.set_level("INFO", logger="throng.transport")

    async def app(scope, receive, send):
        raise RuntimeError("secret exception payload")

    with pytest.raises(RuntimeError):
        await RequestLog(app)(
            {
                "type": "http",
                "headers": [(b"x-request-id", b"\xff")],
                "method": "POST",
                "path": "/agent/v1/tasks",
            },
            None,
            None,
        )
    row = json.loads(caplog.records[-1].message)
    assert row["status"] == 500 and row["duration_ms"] >= 0
    assert "secret" not in caplog.records[-1].message


def test_relay_keepalive_is_explicit_and_bounded():
    """The relay idle timeout accommodates measured outages without an unbounded lifetime."""
    assert Settings(_env_file=None).powercell_relay_keep_alive_seconds == 30
    for invalid in [0, 301]:
        with pytest.raises(ValueError):
            Settings(powercell_relay_keep_alive_seconds=invalid, _env_file=None)
