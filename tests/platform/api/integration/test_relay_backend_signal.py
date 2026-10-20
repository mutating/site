"""CONNECT errors reach the peer, independently of backend socket cleanup.

Both HTTP/2 peers use the real h2 state machine. Only backend read/write/drain
failures are injected; there are no network settings, external hosts or processes.
RFC 9113 section 8.5 requires CONNECT_ERROR for an abnormal backend TCP close.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.errors import ErrorCodes
from h2.events import StreamEnded, StreamReset

from throng_api.relay import _Stream


def connected_stream():
    """Keep a second real stream available to detect connection-wide damage."""
    client = H2Connection(H2Configuration(client_side=True, header_encoding="ascii"))
    server = H2Connection(H2Configuration(client_side=False, header_encoding="ascii"))
    client.initiate_connection()
    server.initiate_connection()
    server.receive_data(client.data_to_send())
    client.receive_data(server.data_to_send())
    for stream_id in (1, 3):
        client.send_headers(stream_id, [(":method", "CONNECT"), (":authority", "local.invalid:80")])
    server.receive_data(client.data_to_send())
    for stream_id in (1, 3):
        server.send_headers(stream_id, [(":status", "200")])
    client.receive_data(server.data_to_send())
    received = []

    def flush():
        received.extend(client.receive_data(server.data_to_send()))

    connection = SimpleNamespace(h2=server, streams={}, flush=flush)
    writer = SimpleNamespace(write=Mock(), drain=AsyncMock(), write_eof=Mock(), close=Mock())
    reader = SimpleNamespace(read=AsyncMock(return_value=b""))
    stream = _Stream(connection, 1, reader, writer)
    connection.streams[1] = stream
    return client, server, connection, stream, received


async def download_without_leak(stream):
    """The assertion must follow cleanup even when the candidate regresses."""
    upload = asyncio.create_task(asyncio.Event().wait())
    stream.tasks = [upload]
    try:
        await stream.download()
    finally:
        upload.cancel()
        await asyncio.gather(upload, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["read", "write", "drain"])
async def test_backend_socket_failure_resets_only_the_failed_connect(boundary, caplog):
    """Read and write failures must become immediate remote errors, not timeouts.

    RST_STREAM CONNECT_ERROR preserves interrupted-content semantics. Sending
    successful EOF could falsely certify a response without a Content-Length;
    closing the whole connection would break an unrelated heartbeat tunnel.
    """
    caplog.set_level("INFO", logger="throng.transport")
    client, server, connection, stream, received = connected_stream()
    if boundary == "read":
        stream.reader.read.side_effect = ConnectionResetError
        await download_without_leak(stream)
    else:
        getattr(stream.writer, boundary).side_effect = BrokenPipeError
        stream.incoming.put_nowait((b"request", 7))
        await stream.upload()
    resets = [event for event in received if isinstance(event, StreamReset)]
    assert [(event.stream_id, event.error_code) for event in resets] == [
        (1, ErrorCodes.CONNECT_ERROR)
    ]
    assert not any(isinstance(event, StreamEnded) for event in received)
    stream.writer.close.assert_called_once()
    reason = "backend_download_error" if boundary == "read" else "backend_upload_error"
    assert any(f'"reason":"{reason}"' in row.message for row in caplog.records)
    client.send_data(3, b"unrelated heartbeat")
    assert any(
        getattr(event, "data", None) == b"unrelated heartbeat"
        for event in server.receive_data(client.data_to_send())
    )


@pytest.mark.anyio
async def test_backend_eof_remains_a_clean_stream_end():
    """An ordinary FIN is END_STREAM, not a retry-worthy CONNECT_ERROR."""
    _, _, connection, stream, received = connected_stream()
    await download_without_leak(stream)
    assert [event.stream_id for event in received if isinstance(event, StreamEnded)] == [1]
    assert not any(isinstance(event, StreamReset) for event in received)
    assert not connection.streams
    stream.writer.close.assert_called_once()


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["read", "write"])
@pytest.mark.parametrize("race", ["peer_reset", "transport_write_failure"])
async def test_backend_failure_cleanup_survives_a_simultaneously_closed_peer(boundary, race):
    """Reporting an error is best effort after the peer has already gone away.

    A second reset of a closed h2 stream raises StreamClosedError. A failed outer
    socket write can also raise while flushing the reset. Neither may interrupt
    backend cleanup or become an unhandled stream-task exception.
    """
    client, server, connection, stream, _ = connected_stream()
    if race == "peer_reset":
        client.reset_stream(1)
        server.receive_data(client.data_to_send())
    else:
        connection.flush = Mock(side_effect=BrokenPipeError)
    error = None
    try:
        if boundary == "read":
            stream.reader.read.side_effect = ConnectionResetError
            await download_without_leak(stream)
        else:
            stream.writer.write.side_effect = BrokenPipeError
            stream.incoming.put_nowait((b"request", 7))
            await stream.upload()
    except Exception as failure:
        error = failure
    assert error is None, f"Error signaling interrupted cleanup: {type(error).__name__}"
    stream.writer.close.assert_called_once()
