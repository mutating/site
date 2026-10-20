"""Disposable loopback HTTP fault proxy; no production routes or settings change."""

import asyncio
from urllib.parse import urlsplit


async def close_connection(writer):
    """Graceful close is bounded when a peer stops consuming buffered bytes."""
    writer.close()
    try:
        async with asyncio.timeout(0.2):
            await writer.wait_closed()
    except (OSError, TimeoutError):
        pass
    finally:
        writer.transport.abort()


class FaultProxy:
    def __init__(self, upstream, mode):
        self.upstream, self.mode = urlsplit(upstream), mode
        self.attempts = 0
        self.tasks = set()

    async def __aenter__(self):
        self.closing = False
        self.server = await asyncio.start_server(self.connected, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *args):
        self.closing = True
        self.server.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.server.wait_closed()

    def connected(self, reader, writer):
        # Own the task before its coroutine can run. A late callback creates no
        # new task, while a cancelled-before-start task still releases its peer.
        if self.closing:
            writer.transport.abort()
            return
        task = asyncio.create_task(self.forward(reader, writer))
        self.tasks.add(task)

        def finished(task):
            writer.transport.abort()
            self.tasks.discard(task)

        task.add_done_callback(finished)

    async def forward(self, reader, writer):
        backend = None
        try:
            async with asyncio.timeout(15):
                header = await reader.readuntil(b"\r\n\r\n")
                lines = header.split(b"\r\n")
                headers = dict(line.split(b":", 1) for line in lines[1:] if b":" in line)
                length = next(
                    (int(v) for k, v in headers.items() if k.lower() == b"content-length"),
                    0,
                )
                self.attempts += 1
                fault = self.mode if self.attempts == 1 else "healthy"
                if fault == "cut_upload":
                    await reader.readexactly(min(1, length))
                    return
                body = await reader.readexactly(length)
                if fault == "unavailable":
                    writer.write(
                        b"HTTP/1.1 503 Service Unavailable\r\n"
                        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                    await writer.drain()
                    return
                if fault == "drop_before":
                    return
                if fault == "blackhole":
                    await asyncio.sleep(1)
                    return
                if fault == "delay_request":
                    await asyncio.sleep(0.3)
                response, backend = await asyncio.open_connection(
                    self.upstream.hostname, self.upstream.port
                )
                forwarded = b"\r\n".join(
                    line for line in lines if line and not line.lower().startswith(b"connection:")
                )
                backend.write(forwarded + b"\r\nConnection: close\r\n\r\n" + body)
                await backend.drain()
                received = await response.read()
                if fault == "drop_after":
                    return
                if fault == "delay_response":
                    await asyncio.sleep(0.3)
                if fault == "truncate_response":
                    received = received[:-1]
                writer.write(received)
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            await asyncio.gather(*(close_connection(stream) for stream in [backend, writer] if stream))
