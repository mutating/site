"""Completed and incomplete proxy closes have different abort requirements."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tests.platform.api.e2e.network_faults import FaultProxy, close_connection


class APIProxyCloseStatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_finished_callback_does_not_abort_completed_close(self):
        """The handler has already closed its writer when its done callback
        runs. Tracking must release without a redundant abort raising from the
        callback. A fake EOF exercises the real acceptance/forwarding lifecycle.
        """
        proxy = FaultProxy("http://127.0.0.1:1", "healthy")
        proxy.closing = False
        reader = SimpleNamespace(
            readuntil=AsyncMock(side_effect=asyncio.IncompleteReadError(b"", None))
        )
        writer = SimpleNamespace(
            close=Mock(),
            wait_closed=AsyncMock(),
            is_closing=lambda: True,
            transport=SimpleNamespace(abort=Mock(side_effect=RuntimeError("already closed"))),
        )
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        errors = []
        loop.set_exception_handler(lambda _, context: errors.append(context))
        try:
            proxy.connected(reader, writer)
            await asyncio.gather(*list(proxy.tasks), return_exceptions=True)
            retained = list(proxy.tasks)
        finally:
            proxy.tasks.clear()
            loop.set_exception_handler(previous)
        with self.subTest(result="callback errors"):
            self.assertFalse(errors, [row.get("message") for row in errors])
        with self.subTest(result="tracking"):
            self.assertFalse(retained, "Finished callback retained its completed handler")

    async def test_completed_graceful_close_never_aborts_again(self):
        """wait_closed success proves that no buffered bytes remain. A second
        abort is unnecessary and fails after buffered close on Python 3.11.
        """
        writer = SimpleNamespace(
            close=Mock(),
            wait_closed=AsyncMock(),
            is_closing=lambda: True,
            transport=SimpleNamespace(abort=Mock(side_effect=RuntimeError("already closed"))),
        )
        error = None
        try:
            await close_connection(writer)
        except Exception as failure:
            error = failure
        self.assertIsNone(error, f"A completed close was aborted: {error!r}")
        writer.transport.abort.assert_not_called()
        writer.close.assert_called_once()
        writer.wait_closed.assert_awaited_once()

    async def test_incomplete_closing_transport_is_aborted_after_timeout_or_io_error(self):
        """is_closing is already true after close(), including when the peer
        has stopped consuming buffered bytes. Timeout and I/O failure still
        require abort; a simple is_closing guard would leak that connection.
        """
        for failure in [TimeoutError(), ConnectionResetError()]:
            with self.subTest(failure=type(failure).__name__):
                writer = SimpleNamespace(
                    close=Mock(),
                    wait_closed=AsyncMock(side_effect=failure),
                    is_closing=lambda: True,
                    transport=SimpleNamespace(abort=Mock()),
                )
                await close_connection(writer)
                writer.transport.abort.assert_called_once()

    async def test_cancellation_aborts_incomplete_close_and_propagates(self):
        """Service cancellation must not convert a stalled graceful close
        into a surviving peer, nor swallow the caller's cancellation signal.
        """
        writer = SimpleNamespace(
            close=Mock(),
            wait_closed=AsyncMock(side_effect=asyncio.CancelledError()),
            is_closing=lambda: True,
            transport=SimpleNamespace(abort=Mock()),
        )
        with self.assertRaises(asyncio.CancelledError):
            await close_connection(writer)
        writer.transport.abort.assert_called_once()
