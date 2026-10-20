"""The server fault fixture releases both sides of a real backend TCP reset."""

import asyncio
import socket
import struct

import pytest

from tests.platform.api.e2e.network_faults import FaultProxy

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("mode", ["healthy", "drop_after"])
async def test_fault_proxy_releases_both_sides_after_upstream_tcp_reset(mode):
    """A real TCP RST can make backend.wait_closed raise during teardown.
    The client must still see EOF and the handler must leave the tracking set,
    without an unhandled callback error that invalidates later fault experiments.
    """

    async def reset(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        writer.transport.abort()

    upstream = await asyncio.start_server(reset, "127.0.0.1", 0)
    loop = asyncio.get_running_loop()
    previous, errors = loop.get_exception_handler(), []
    loop.set_exception_handler(lambda _, context: errors.append(context))
    try:
        async with FaultProxy(
            f"http://127.0.0.1:{upstream.sockets[0].getsockname()[1]}", mode
        ) as proxy:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", int(proxy.url.rsplit(":", 1)[1])
            )
            try:
                writer.write(b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                try:
                    received = await asyncio.wait_for(reader.read(), 1)
                except TimeoutError:
                    pytest.fail("A reset upstream left the client socket hanging")
                assert received == b""
                await asyncio.sleep(0.05)
                assert not proxy.tasks, "Completed handlers must not remain tracked"
                assert not errors, errors
            finally:
                writer.close()
                await writer.wait_closed()
    finally:
        upstream.close()
        await upstream.wait_closed()
        loop.set_exception_handler(previous)
