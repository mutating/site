"""Native macOS HTTP/2 relay terminating into this API, never an open proxy.

Each CONNECT stream gets an in-process HTTP connection with its TLS peer bound
into the ASGI scope. No network listener or forwarded identity header can enter
this path. The original Apple-attested certificate remains the device identity.
"""

import asyncio
import hashlib
import logging
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import uvicorn
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import (
    ConnectionTerminated,
    DataReceived,
    RemoteSettingsChanged,
    RequestReceived,
    StreamEnded,
    StreamReset,
    WindowUpdated,
)
from h2.exceptions import H2Error
from h2.settings import SettingCodes
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState

logger = logging.getLogger("throng.transport")


class RelayHTTPProtocol(H11Protocol):
    def timeout_keep_alive_handler(self):
        logger.info('{"event":"relay_close","reason":"idle_timeout"}')
        super().timeout_keep_alive_handler()


@dataclass(frozen=True)
class RelayPeer:
    certificate_sha256: str


def configuration(settings):
    """Require an explicit HTTPS endpoint and complete operator-owned TLS/CA material."""
    values = (
        settings.powercell_relay_url,
        settings.powercell_relay_certificate,
        settings.powercell_relay_key,
    )
    if not any(values):
        return None
    if not all(values) or not settings.powercell_ca_certificate:
        raise ValueError(
            "Power Cell relay requires its HTTPS URL, TLS certificate/key and device CA"
        )
    url = urlsplit(settings.powercell_relay_url)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
        or (url.port is not None and not 1 <= url.port <= 65535)
    ):
        raise ValueError("Power Cell relay URL must be an HTTPS origin without credentials")
    # This virtual destination exists only inside the relay. Without the installed
    # profile it cannot resolve or fall back to an unauthenticated public endpoint.
    identifier = hashlib.sha256(settings.powercell_relay_url.encode()).hexdigest()[:24]
    return {
        "url": settings.powercell_relay_url,
        "api_origin": f"http://powercell-{identifier}.invalid",
    }


def enrollment_configuration(info, enrollment_id):
    """Give each installed profile its own route so old profiles cannot select another key."""
    if info is None:
        return None
    host = urlsplit(info["api_origin"]).hostname
    return {"url": info["url"], "api_origin": f"http://{UUID(enrollment_id).hex}.{host}"}


class RelayServer:
    def __init__(self, app, settings, info):
        self.app, self.settings, self.info = app, settings, info
        self.listener = None
        self.connections = set()
        self.closing = False
        self.http_state = ServerState()

    async def start(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(
            self.settings.powercell_relay_certificate, self.settings.powercell_relay_key
        )
        context.load_verify_locations(self.settings.powercell_ca_certificate)
        context.verify_mode = ssl.CERT_REQUIRED
        context.set_alpn_protocols(["h2"])
        context.options |= ssl.OP_NO_TICKET
        context.num_tickets = 0
        self.listener = await asyncio.start_server(
            self.connected,
            self.settings.powercell_relay_host,
            urlsplit(self.info["url"]).port or 443,
            ssl=context,
            ssl_handshake_timeout=10,
            limit=65536,
        )

    def connected(self, reader, writer):
        # Accept ownership before scheduling: close() may race a queued callback,
        # and cancellation before the first coroutine step cannot run finally.
        if self.closing:
            writer.transport.abort()
            return
        task = asyncio.create_task(self.handle_connection(reader, writer))
        self.connections.add(task)

        def finished(task):
            writer.transport.abort()
            self.connections.discard(task)

        task.add_done_callback(finished)
        return task

    async def handle_connection(self, reader, writer):
        try:
            tls = writer.get_extra_info("ssl_object")
            if tls.selected_alpn_protocol() != "h2":
                return
            peer = RelayPeer(hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest())
            await _Connection(self, reader, writer, peer).run()
        except asyncio.CancelledError:
            # Server shutdown must not wait for a remote TLS close_notify.
            writer.transport.abort()
            raise
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass

    async def close(self):
        self.closing = True
        if self.listener:
            self.listener.close()
        tasks = list(self.connections)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.listener:
            await self.listener.wait_closed()
        # Disconnect transports first, then let already-started API transactions
        # drain before the parent lifespan closes its database.
        await asyncio.gather(*list(self.http_state.tasks), return_exceptions=True)


class _Connection:
    def __init__(self, server, reader, writer, peer):
        self.server, self.reader, self.writer, self.peer = server, reader, writer, peer
        self.h2 = H2Connection(H2Configuration(client_side=False, header_encoding="ascii"))
        self.streams = {}
        self.window = asyncio.Event()

    def flush(self):
        self.writer.write(self.h2.data_to_send())

    async def run(self):
        self.h2.initiate_connection()
        self.h2.update_settings(
            {SettingCodes.MAX_CONCURRENT_STREAMS: 128, SettingCodes.MAX_HEADER_LIST_SIZE: 16384}
        )
        self.flush()
        try:
            while data := await self.reader.read(65536):
                for event in self.h2.receive_data(data):
                    if isinstance(event, RequestReceived):
                        await self.open(event)
                    elif isinstance(event, DataReceived):
                        # Return connection capacity immediately so a stalled
                        # upload cannot prevent another stream's heartbeat.
                        if not event.flow_controlled_length:
                            continue
                        self.h2.increment_flow_control_window(event.flow_controlled_length)
                        stream = self.streams.get(event.stream_id)
                        if stream:
                            stream.incoming.put_nowait((event.data, event.flow_controlled_length))
                    elif isinstance(event, StreamEnded):
                        if stream := self.streams.get(event.stream_id):
                            stream.incoming.put_nowait(None)
                    elif isinstance(event, StreamReset):
                        if stream := self.streams.pop(event.stream_id, None):
                            stream.close()
                    elif isinstance(event, (WindowUpdated, RemoteSettingsChanged)):
                        self.window.set()
                    elif isinstance(event, ConnectionTerminated):
                        return
                self.flush()
                await self.writer.drain()
        except (H2Error, UnicodeError, OSError):
            pass
        finally:
            streams = list(self.streams.values())
            for stream in streams:
                stream.close()
            await asyncio.gather(
                *(task for s in streams for task in s.tasks), return_exceptions=True
            )

    async def open(self, event):
        headers = dict(event.headers)
        authority = headers.get(":authority", "")
        prefix, _, destination = authority.partition(".")
        expected = urlsplit(self.server.info["api_origin"]).hostname + ":80"
        status = None
        if headers.get(":method") != "CONNECT" or ":protocol" in headers:
            status = "405"
        elif (
            destination != expected
            or len(prefix) != 32
            or any(c not in "0123456789abcdef" for c in prefix)
        ):
            status = "403"
        if status:
            self.h2.send_headers(event.stream_id, [(":status", status)], end_stream=True)
            return

        async def application(scope, receive, send):
            scope = {**scope, "powercell.relay_peer": self.peer}
            await self.server.app(scope, receive, send)

        config = uvicorn.Config(
            application,
            lifespan="off",
            proxy_headers=False,
            access_log=False,
            log_level="critical",
            server_header=False,
            date_header=False,
            timeout_keep_alive=self.server.settings.powercell_relay_keep_alive_seconds,
        )
        config.load()
        left, right = socket.socketpair()
        left.setblocking(False)
        right.setblocking(False)
        transport = None
        try:
            transport, _ = await asyncio.get_running_loop().connect_accepted_socket(
                lambda: RelayHTTPProtocol(config, self.server.http_state, {}), left
            )
            reader, writer = await asyncio.open_connection(sock=right)
        except BaseException:
            if transport:
                transport.close()
            else:
                left.close()
            right.close()
            raise
        stream = _Stream(self, event.stream_id, reader, writer)
        self.streams[event.stream_id] = stream
        self.h2.send_headers(event.stream_id, [(":status", "200")])
        self.flush()
        stream.tasks = [
            asyncio.create_task(stream.upload()),
            asyncio.create_task(stream.download()),
        ]

    async def send(self, stream_id, data):
        while data:
            while self.h2.local_flow_control_window(stream_id) <= 0:
                self.window.clear()
                await self.window.wait()
            count = min(
                len(data),
                self.h2.local_flow_control_window(stream_id),
                self.h2.max_outbound_frame_size,
            )
            self.h2.send_data(stream_id, data[:count])
            data = data[count:]
            self.flush()
            await self.writer.drain()


class _Stream:
    def __init__(self, connection, stream_id, reader, writer):
        self.connection, self.id = connection, stream_id
        self.reader, self.writer = reader, writer
        # HTTP/2's unacknowledged per-stream receive window bounds this queue.
        self.incoming = asyncio.Queue()
        self.tasks = []

    def close(self):
        self.writer.close()
        for task in self.tasks:
            task.cancel()

    async def upload(self):
        try:
            while item := await self.incoming.get():
                data, size = item
                self.writer.write(data)
                await self.writer.drain()
                self.connection.h2.increment_flow_control_window(size, self.id)
                self.connection.flush()
            self.writer.write_eof()
        except (H2Error, OSError):
            logger.info('{"event":"relay_close","reason":"backend_upload_error"}')
            self.writer.close()

    async def download(self):
        try:
            while data := await self.reader.read(16384):
                await self.connection.send(self.id, data)
            self.connection.h2.end_stream(self.id)
            self.connection.flush()
        except (H2Error, OSError):
            logger.info('{"event":"relay_close","reason":"backend_download_error"}')
        finally:
            self.writer.close()
            # The receive task may be awaiting another chunk when the backend
            # closes a keep-alive connection or rejects a request early.
            self.tasks[0].cancel()
            self.connection.streams.pop(self.id, None)
