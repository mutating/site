"""Disposable loopback HTTP fault proxy; no production routes or settings change."""

import asyncio
from urllib.parse import urlsplit


class FaultProxy:
    def __init__(self, upstream, mode):
        self.upstream, self.mode = urlsplit(upstream), mode
        self.attempts = 0
        self.tasks = set()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.connected, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *args):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def connected(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
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
            if backend:
                backend.close()
                await backend.wait_closed()
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self.tasks.discard(task)
