"""Tiny real HTTP peer for GitHub transport faults, with deterministic cleanup."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace


def response(writer, body, *, status=200, headers=None):
    """Write complete JSON, or let the caller deliberately truncate its declared body."""
    content = json.dumps(body).encode()
    fields = {"Content-Length": str(len(content)), "Connection": "close", **(headers or {})}
    writer.write(
        f"HTTP/1.1 {status} Test\r\n".encode()
        + b"".join(f"{name}: {value}\r\n".encode() for name, value in fields.items())
        + b"\r\n"
        + content
    )


@asynccontextmanager
async def http_peer(handler):
    """Serve actual TCP requests and await every connection's shutdown at scope exit."""
    connections, tasks, requests = set(), set(), []

    async def serve(reader, writer):
        task = asyncio.current_task()
        connections.add(writer)
        tasks.add(task)
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode().split("\r\n")
            method, path, _ = head[0].split(" ")
            headers = {
                name.lower(): value
                for name, value in (line.split(": ", 1) for line in head[1:] if line)
            }
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            request = SimpleNamespace(method=method, path=path, headers=headers, body=body)
            requests.append(request)
            await handler(request, writer)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            connections.discard(writer)
            tasks.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", requests=requests
        )
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(connections):
            writer.close()
        pending = tuple(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
