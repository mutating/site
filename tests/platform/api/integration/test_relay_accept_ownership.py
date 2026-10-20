"""Relay shutdown owns accepted TCP peers even across callback scheduling races.

The loopback socket is real. Only TLS metadata is supplied at the boundary: these
cases exercise connection ownership, independently of TLS certificate validation,
which remains covered by the relay's existing full TLS/HTTP2 E2E tests.
"""
import asyncio
import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from throng_api.relay import RelayServer


class RelayAcceptOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_socket(self, mode):
        server = RelayServer(None, None, None)
        peer_ready, listener_closed = asyncio.Event(), asyncio.Event()
        peers, tasks, held = [], [], []
        tls = SimpleNamespace(selected_alpn_protocol=lambda: "h2", getpeercert=lambda **kw: b"test-only")

        def invoke(reader, writer):
            result = server.connected(reader, writer)
            if inspect.iscoroutine(result):
                result = asyncio.create_task(result)
            if isinstance(result, asyncio.Task):
                tasks.append(result)
            else:
                tasks.extend(server.connections)
            if mode == "cancel_before_start":
                for task in tasks:
                    task.cancel()

        def accepted(reader, writer):
            peers.append(writer)
            extra = writer.get_extra_info
            writer.get_extra_info = lambda name, default=None: tls if name == "ssl_object" else extra(name, default)
            if mode == "late":
                held.append((reader, writer))
            else:
                invoke(reader, writer)
            peer_ready.set()

        server.listener = await asyncio.start_server(accepted, "127.0.0.1", 0)
        close_listener = server.listener.close
        def close():
            close_listener(); listener_closed.set()
        server.listener.close = close
        reader, writer = await asyncio.open_connection("127.0.0.1", server.listener.sockets[0].getsockname()[1])
        await asyncio.wait_for(peer_ready.wait(), 1)
        result = {}
        closing = None
        try:
            if mode == "late":
                closing = asyncio.create_task(server.close())
                await asyncio.wait_for(listener_closed.wait(), 1)
                before = set(asyncio.all_tasks())
                invoke(*held[0])
                result["spawned"] = len(set(asyncio.all_tasks()) - before)
                try:
                    await asyncio.wait_for(asyncio.shield(closing), .3)
                    result["closed"] = True
                except TimeoutError:
                    result["closed"] = False
            else:
                try:
                    result["eof"] = await asyncio.wait_for(reader.read(1), .3) == b""
                except TimeoutError:
                    result["eof"] = False
            result["pending"] = [task for task in tasks if not task.done()]
            result["tracked"] = list(server.connections)
        finally:
            # RED must release all of its own sockets and tasks before asserting.
            writer.transport.abort()
            for peer in peers:
                peer.transport.abort()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if closing:
                closing.cancel(); await asyncio.gather(closing, return_exceptions=True)
            server.listener.close(); await server.listener.wait_closed()
        return result

    async def test_late_accepted_callback_creates_no_work_after_relay_close(self):
        """An accepted event queued before close may arrive after the shutdown
        snapshot. It must abort immediately, without adding an unowned handler.
        """
        result = await self.exercise_socket("late")
        self.assertEqual(result["spawned"], 0)
        self.assertTrue(result["closed"])
        self.assertFalse(result["pending"])
        self.assertFalse(result["tracked"])

    async def test_cancel_before_handler_first_step_releases_tcp_peer(self):
        """Cancellation before the first coroutine step cannot execute finally;
        the synchronous accept owner must still close the accepted TCP peer.
        """
        result = await self.exercise_socket("cancel_before_start")
        self.assertTrue(result["eof"], "A cancelled-before-start relay task leaked its peer")
        self.assertFalse(result["tracked"])

    async def test_cancellation_during_tls_close_drops_tracking(self):
        """Shutdown may cancel a handler already awaiting TLS close_notify.
        Tracking must be removed even when cancellation interrupts its finally.
        """
        entered = asyncio.Event()
        async def wait_closed():
            entered.set(); await asyncio.Event().wait()
        writer = SimpleNamespace(
            get_extra_info=Mock(return_value=SimpleNamespace(selected_alpn_protocol=lambda: None)),
            close=Mock(), wait_closed=wait_closed, transport=SimpleNamespace(abort=Mock()),
        )
        server = RelayServer(None, None, None)
        result = server.connected(None, writer)
        task = asyncio.create_task(result) if inspect.iscoroutine(result) else result
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel(); await asyncio.gather(task, return_exceptions=True)
        retained = list(server.connections)
        server.connections.clear()
        self.assertFalse(retained, "Cancelled TLS closure retained the completed connection")
        writer.transport.abort.assert_called()
