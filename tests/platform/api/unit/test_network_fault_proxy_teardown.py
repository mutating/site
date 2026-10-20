"""A fault fixture must not wedge its test runner during a network stall."""

import asyncio
import unittest

from tests.platform.api.e2e.network_faults import FaultProxy


class ProxyTeardownTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_upload_to_nonreading_upstream_has_bounded_cleanup(self):
        """TCP close flushes queued bytes and can itself block when a peer has
        stopped reading. Teardown must abort this pending upload, release the
        client and remove its handler rather than await an impossible flush.
        """
        accepted = asyncio.Event()
        backend_writers = []
        backend_tasks = []

        async def stuck(reader, writer):
            backend_tasks.append(asyncio.current_task())
            backend_writers.append(writer)
            await reader.readuntil(b"\r\n\r\n")
            accepted.set()
            await asyncio.Event().wait()

        upstream = await asyncio.start_server(stuck, "127.0.0.1", 0)
        proxy = await FaultProxy(
            f"http://127.0.0.1:{upstream.sockets[0].getsockname()[1]}", "healthy"
        ).__aenter__()
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", int(proxy.url.rsplit(":", 1)[1])
        )
        size = 32 * 1024 * 1024
        writer.write(
            f"PUT / HTTP/1.1\r\nHost: localhost\r\nContent-Length: {size}\r\n\r\n".encode()
            + b"x" * size
        )
        sender = asyncio.create_task(writer.drain())
        stopped = False
        try:
            await asyncio.wait_for(accepted.wait(), 5)
            await asyncio.sleep(0.1)
            try:
                await asyncio.wait_for(proxy.__aexit__(None, None, None), 1)
                stopped = True
            except TimeoutError:
                pass
            self.assertTrue(
                stopped, "Fault-proxy shutdown waited for an upstream that stopped reading"
            )
            self.assertFalse(proxy.tasks, "Teardown leaked its handler tracking entry")
        finally:
            writer.transport.abort()
            for backend in backend_writers:
                backend.transport.abort()
            for task in [sender, *backend_tasks, *proxy.tasks]:
                task.cancel()
            await asyncio.gather(sender, *backend_tasks, *proxy.tasks, return_exceptions=True)
            upstream.close()
            await upstream.wait_closed()

    async def test_late_accepted_handler_cannot_delay_shutdown(self):
        """A peer can connect before its handler enters the tracking set.
        Closing the listener must make that late callback release its own socket,
        even though teardown's task snapshot could not include it yet.
        """
        proxy = await FaultProxy("http://127.0.0.1:1", "healthy").__aenter__()
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", proxy.server.sockets[0].getsockname()[1]
        )
        stopped = False
        try:
            try:
                await asyncio.wait_for(proxy.__aexit__(), 0.5)
                stopped = True
            except TimeoutError:
                pass
            self.assertTrue(stopped, "Late accepted callback blocked server.wait_closed")
            self.assertFalse(proxy.tasks, "A handler survived the proxy context")
        finally:
            writer.transport.abort()
            for task in list(proxy.tasks):
                task.cancel()
            await asyncio.gather(*proxy.tasks, return_exceptions=True)
            proxy.server.close()
            await proxy.server.wait_closed()
