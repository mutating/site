"""Fault injection for OS socket failures that cannot reliably be triggered over TLS."""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from throng_api.relay import RelayHTTPProtocol, RelayServer, _Stream

pytestmark = pytest.mark.anyio


async def test_real_http_idle_timeout_closes_and_records_reason(caplog):
    """The embedded HTTP parser records the actual idle close, including its reason."""
    import uvicorn
    from uvicorn.server import ServerState

    config = uvicorn.Config(lambda scope, receive, send: None, lifespan="off", log_config=None)
    config.load()
    protocol = RelayHTTPProtocol(config, ServerState(), {})
    transport = Mock()
    transport.is_closing.return_value = False
    protocol.transport = transport
    caplog.set_level("INFO", logger="throng.transport")
    protocol.timeout_keep_alive_handler()
    transport.close.assert_called_once()
    assert json.loads(caplog.records[-1].message) == {
        "event": "relay_close",
        "reason": "idle_timeout",
    }


async def test_broken_backend_upload_closes_the_socket(caplog):
    """An OS write failure must release the tunnel instead of retaining a blocked upload."""
    caplog.set_level("INFO", logger="throng.transport")
    writer = SimpleNamespace(write=Mock(side_effect=BrokenPipeError), close=Mock())
    connection = SimpleNamespace(h2=Mock(), flush=Mock())
    stream = _Stream(connection, 1, None, writer)
    stream.incoming.put_nowait((b"request", 7))
    await stream.upload()
    writer.close.assert_called_once()
    rows = [json.loads(row.message) for row in caplog.records if row.name == "throng.transport"]
    assert any(
        row.get("event") == "relay_close" and row.get("reason") == "backend_upload_error"
        for row in rows
    )


async def test_broken_backend_download_cancels_upload_and_removes_stream(caplog):
    """An OS read failure must remove the tunnel and cancel its otherwise waiting upload."""
    caplog.set_level("INFO", logger="throng.transport")
    connection = SimpleNamespace(streams={}, h2=Mock(), flush=Mock())
    writer = SimpleNamespace(close=Mock())
    reader = SimpleNamespace(read=AsyncMock(side_effect=ConnectionResetError))
    stream = _Stream(connection, 1, reader, writer)
    connection.streams[1] = stream
    upload = asyncio.create_task(asyncio.Event().wait())
    stream.tasks = [upload]
    await stream.download()
    await asyncio.gather(upload, return_exceptions=True)
    assert upload.cancelled() and not connection.streams
    writer.close.assert_called_once()
    rows = [json.loads(row.message) for row in caplog.records if row.name == "throng.transport"]
    assert any(
        row.get("event") == "relay_close" and row.get("reason") == "backend_download_error"
        for row in rows
    )


async def test_tls_disconnect_error_does_not_retain_connection():
    """A peer disconnect during TLS closure must still remove the connection from shutdown tracking."""
    server = RelayServer(None, None, None)
    writer = SimpleNamespace(
        get_extra_info=Mock(return_value=SimpleNamespace(selected_alpn_protocol=lambda: None)),
        close=Mock(),
        wait_closed=AsyncMock(side_effect=ConnectionResetError),
        transport=SimpleNamespace(abort=Mock()),
    )
    await server.connected(None, writer)
    assert not server.connections
    writer.close.assert_called_once()


def test_selected_env_file_does_not_inherit_the_developers_relay_settings(tmp_path):
    """An explicitly selected deployment config must not borrow relay keys/ports from the local .env."""
    (tmp_path / ".env").write_text("THRONG_POWERCELL_RELAY_URL=https://unrelated.invalid:8444\n")
    selected = tmp_path / "isolated.env"
    selected.write_text("THRONG_CONTROLLER_ENABLED=false\n")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("THRONG_")}
    environment["THRONG_ENV_FILE"] = str(selected)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from throng_api.config import Settings; s=Settings(); "
            "assert s.powercell_relay_url is None; assert not s.controller_enabled",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
    )
