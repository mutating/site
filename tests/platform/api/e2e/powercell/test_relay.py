"""Real TLS/client-certificate and HTTP/2 CONNECT tests against the production relay."""

import asyncio
import hashlib
import socket
import ssl
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import h11
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, ResponseReceived, StreamEnded, WindowUpdated
from h2.exceptions import H2Error
from h2.settings import SettingCodes

from throng_api.app import create_app
from throng_api.models import DeviceCertificate, PhysicalDevice
from throng_api.relay import configuration

from .conftest import certificate

pytestmark = pytest.mark.anyio


class RelayWire:
    """Exercise multiplexing and disconnection on one genuine TLS connection."""

    def __init__(self, reader, writer, info):
        self.reader, self.writer, self.info = reader, writer, info
        self.h2 = H2Connection(H2Configuration(client_side=True, header_encoding="ascii"))
        self.h2.initiate_connection()
        self.flush()

    def flush(self):
        """Send protocol frames without replacing the production transport."""
        self.writer.write(self.h2.data_to_send())

    async def events_until(self, predicate, *, credit=True):
        """Read bounded protocol progress, returning flow-control credit for received bytes."""
        events = []
        async with asyncio.timeout(3):
            while not predicate(events):
                data = await self.reader.read(65536)
                assert data, "Relay disconnected unexpectedly"
                batch = self.h2.receive_data(data)
                for event in batch:
                    if isinstance(event, DataReceived) and credit:
                        self.h2.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                events.extend(batch)
                self.flush()
        return events

    async def connect(self, stream, headers=None):
        """Open a TCP stream to the sole allowed API destination."""
        self.h2.send_headers(
            stream,
            headers
            or [
                (":method", "CONNECT"),
                (":authority", urlsplit(self.info["api_origin"]).hostname + ":80"),
            ],
        )
        self.flush()
        events = await self.events_until(
            lambda events: any(
                isinstance(e, ResponseReceived) and e.stream_id == stream for e in events
            )
        )
        return next(
            int(dict(e.headers)[":status"])
            for e in events
            if isinstance(e, ResponseReceived) and e.stream_id == stream
        )

    async def get(self, stream, target, authorization=None):
        """Send one HTTP request over an authenticated CONNECT stream."""
        assert await self.connect(stream) == 200
        auth = f"Authorization: {authorization}\r\n" if authorization else ""
        self.h2.send_data(
            stream,
            (
                f"GET {target} HTTP/1.1\r\nHost: api.test\r\n{auth}Connection: close\r\n\r\n"
            ).encode(),
        )
        self.flush()
        events = await self.events_until(
            lambda events: any(isinstance(e, StreamEnded) and e.stream_id == stream for e in events)
        )
        data = b"".join(
            e.data for e in events if isinstance(e, DataReceived) and e.stream_id == stream
        )
        return int(data.split(b" ", 2)[1]), data


@pytest.fixture
def relay_wire(tls_context, app, verified_enrollment):
    """Allocate a real reusable relay connection and always close its TLS transport."""

    @asynccontextmanager
    async def open_wire():
        reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            urlsplit(app.state.powercell_relay["url"]).port,
            ssl=tls_context,
            server_hostname="localhost",
        )
        try:
            yield RelayWire(reader, writer, verified_enrollment.order.enrollment.row["relay"])
        finally:
            writer.close()
            await writer.wait_closed()

    return open_wire


@pytest.fixture
def relay_tls(tmp_path, pki, clock):
    """Allocate isolated server TLS material and an unprivileged loopback port."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    leaf = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(pki.root.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(clock.now - timedelta(days=1))
        .not_valid_after(clock.now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(pki.root_key.public_key()),
            critical=False,
        )
        .sign(pki.root_key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "relay.pem", tmp_path / "relay.key"
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return {
        "powercell_relay_url": f"https://localhost:{port}",
        "powercell_relay_certificate": cert_path,
        "powercell_relay_key": key_path,
    }


@pytest.fixture
def app(app, relay_tls):
    """Keep the parent fixture's real Apple-chain validation and add the actual TLS listener."""
    return create_app(app.state.settings.model_copy(update=relay_tls), clock=app.state.clock)


class RelayTransport(httpx.AsyncBaseTransport):
    """Adapt raw CONNECT streams to HTTPX without bypassing TLS or the application."""

    def __init__(self, context, info, *, destination=None):
        self.context, self.info, self.destination = context, info, destination

    async def handle_async_request(self, request):
        endpoint = urlsplit(self.info["url"])
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", endpoint.port, ssl=self.context, server_hostname="localhost"
        )
        h2 = H2Connection(H2Configuration(client_side=True, header_encoding="ascii"))
        h2.initiate_connection()
        destination = self.destination or urlsplit(self.info["api_origin"]).hostname + ":80"
        h2.send_headers(1, [(":method", "CONNECT"), (":authority", destination)])
        writer.write(h2.data_to_send())
        connected = asyncio.get_running_loop().create_future()
        incoming, window = asyncio.Queue(), asyncio.Event()

        async def receive():
            try:
                while chunk := await reader.read(65536):
                    for event in h2.receive_data(chunk):
                        if isinstance(event, ResponseReceived) and not connected.done():
                            connected.set_result(int(dict(event.headers)[":status"]))
                        elif isinstance(event, DataReceived):
                            incoming.put_nowait(event.data)
                            h2.acknowledge_received_data(event.flow_controlled_length, 1)
                        elif isinstance(event, WindowUpdated):
                            window.set()
                        elif isinstance(event, StreamEnded):
                            incoming.put_nowait(None)
                    writer.write(h2.data_to_send())
                if not connected.done():
                    connected.set_exception(httpx.ConnectError("TLS peer rejected"))
                incoming.put_nowait(None)
            except (H2Error, OSError, UnicodeError) as error:
                if not connected.done():
                    connected.set_exception(error)
                incoming.put_nowait(error)

        receiver = asyncio.create_task(receive())
        try:
            status = await asyncio.wait_for(connected, 3)
            if status != 200:
                raise httpx.ProxyError(f"CONNECT rejected: {status}")
            body = await request.aread()
            http = h11.Connection(h11.CLIENT)
            data = http.send(
                h11.Request(
                    method=request.method,
                    target=request.url.raw_path,
                    headers=request.headers.raw,
                )
            )
            data += http.send(h11.Data(data=body)) + http.send(h11.EndOfMessage())
            while data:
                while h2.local_flow_control_window(1) <= 0:
                    window.clear()
                    await asyncio.wait_for(window.wait(), 3)
                size = min(
                    len(data),
                    h2.local_flow_control_window(1),
                    h2.max_outbound_frame_size,
                )
                h2.send_data(1, data[:size])
                writer.write(h2.data_to_send())
                await writer.drain()
                data = data[size:]
            response, chunks = None, []
            while True:
                event = http.next_event()
                if event is h11.NEED_DATA:
                    chunk = await asyncio.wait_for(incoming.get(), 3)
                    if isinstance(chunk, Exception):
                        raise chunk
                    http.receive_data(chunk or b"")
                elif isinstance(event, h11.Response):
                    response = event
                elif isinstance(event, h11.Data):
                    chunks.append(event.data)
                elif isinstance(event, h11.EndOfMessage):
                    return httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=b"".join(chunks),
                    )
                elif isinstance(event, h11.ConnectionClosed):
                    raise httpx.RemoteProtocolError("Relay closed before HTTP response")
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


@pytest.fixture
def tls_context(tmp_path, pki, verified_enrollment):
    """Use the issued device certificate and its real matching test-only private key."""
    certificate_path, key_path = tmp_path / "device.pem", tmp_path / "device.key"
    certificate_path.write_bytes(
        verified_enrollment.certificate.public_bytes(serialization.Encoding.PEM)
    )
    key_path.write_bytes(
        verified_enrollment.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.create_default_context(cafile=str(pki.root_file))
    # Exercise the same strict certificate checks on every supported Python version.
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    context.set_alpn_protocols(["h2"])
    context.load_cert_chain(certificate_path, key_path)
    return context


@pytest.fixture
async def relay_client(tls_context, app, verified_enrollment):
    """Send native HTTP requests through a real authenticated CONNECT stream."""
    async with httpx.AsyncClient(
        transport=RelayTransport(tls_context, verified_enrollment.order.enrollment.row["relay"]),
        base_url="http://api.test",
        timeout=5,
    ) as client:
        yield client


async def test_profile_scopes_relay_to_virtual_api_and_exact_attested_identity(
    api, enrollment, app
):
    """The downloaded profile grants only the intended API route, with the same ACME key."""
    profile = enrollment.profile
    assert len(profile["PayloadContent"]) == 2
    acme, relay = profile["PayloadContent"]
    assert profile["PayloadScope"] == "System"
    assert relay["PayloadType"] == "com.apple.relay.managed"
    assert relay["MatchDomains"] == [urlsplit(enrollment.row["relay"]["api_origin"]).hostname]
    assert relay["Relays"] == [
        {
            "HTTP2RelayURL": app.state.powercell_relay["url"],
            "PayloadCertificateUUID": acme["PayloadUUID"],
        }
    ]
    assert acme["HardwareBound"] and acme["Attest"] and not acme["AllowAllAppsAccess"]
    reply = await api.get(f"/api/v1/agent-enrollments/{enrollment.row['id']}")
    assert reply.json()["relay"] == enrollment.row["relay"]
    reply = await api.get("/api/v1/agent-capabilities")
    assert "powercell.relay.v1" in reply.json()["extensions"]


async def test_relay_registers_real_attested_mac_and_authenticates_agent_requests(
    relay_client, verified_enrollment, user, app, client
):
    """The actual TLS key replaces request signatures without weakening account/credential checks."""
    enrollment = verified_enrollment.order.enrollment
    target = f"/api/v1/agent-enrollments/{enrollment.row['id']}"
    response = await relay_client.get(target + "/verification", headers=user["headers"])
    assert response.status_code == 200, response.text
    assert (
        response.json()["certificate_sha256"]
        == verified_enrollment.certificate.fingerprint(hashes.SHA256()).hex()
    )
    headers = {**user["headers"], "Idempotency-Key": "relay-finish"}
    response = await relay_client.post(target + "/finish", headers=headers, json={})
    assert response.status_code == 200, response.text
    binding = response.json()
    assert binding["relay"] == enrollment.row["relay"]
    assert (await relay_client.post(target + "/finish", headers=headers, json={})).json() == binding
    async with app.state.db.read() as session:
        record = await session.get(DeviceCertificate, binding["certificate_sha256"])
        physical = await session.get(PhysicalDevice, record.device_id)
        assert (
            physical.identity_hash
            == hashlib.sha256(b"powercell.apple.udid.v1\x0000006000-0012345678901234").hexdigest()
        )
        assert physical.id == binding["device_id"]
    headers = {"Authorization": "Bearer " + binding["credential"]}
    assert (await relay_client.get("/agent/v1/session", headers=headers)).status_code == 200
    assert (await relay_client.get("/agent/v1/session")).status_code == 401
    assert (await client.get("/agent/v1/session", headers=headers)).status_code == 401
    assert (await relay_client.get(target + "/verification")).status_code == 401


async def test_plain_http_headers_cannot_forge_relay_authentication(
    client, verified_enrollment, user
):
    """Public listeners never trust forwarded certificates, fingerprints or proxy claims."""
    target = f"/api/v1/agent-enrollments/{verified_enrollment.order.enrollment.row['id']}/finish"
    digest = verified_enrollment.certificate.fingerprint(hashes.SHA256()).hex()
    for name in [
        "X-PowerCell-Certificate",
        "X-Forwarded-Client-Cert",
        "SSL-Client-Cert",
        "powercell.relay_peer",
    ]:
        reply = await client.post(
            target,
            headers={**user["headers"], "Idempotency-Key": str(uuid4()), name: digest},
            json={},
        )
        assert reply.status_code == 401


@pytest.mark.parametrize("destination", ["127.0.0.1:8000", "example.com:443", "169.254.169.254:80"])
async def test_relay_is_not_an_open_proxy(tls_context, app, destination):
    """Authenticated devices cannot use the listener to connect to arbitrary hosts or internal services."""
    async with httpx.AsyncClient(
        transport=RelayTransport(tls_context, app.state.powercell_relay, destination=destination),
        base_url="http://api.test",
    ) as client:
        with pytest.raises(httpx.ProxyError, match="403"):
            await client.get("/health/ready")


async def test_relay_rejects_device_certificate_from_another_mac(
    relay_client, api, verified_enrollment, tmp_path, app, pki, clock
):
    """A different genuine client key cannot claim the first Mac's attested enrollment."""
    key = ec.generate_private_key(ec.SECP256R1())
    issuer_key = serialization.load_pem_private_key(pki.key_file.read_bytes(), None)
    cert = certificate(key, issuer_key, x509.Name([]), pki.issuer.subject, clock.now)
    cert_path, key_path = tmp_path / "other.pem", tmp_path / "other.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.create_default_context(cafile=str(pki.root_file))
    context.set_alpn_protocols(["h2"])
    context.load_cert_chain(cert_path, key_path)
    target = (
        f"/api/v1/agent-enrollments/{verified_enrollment.order.enrollment.row['id']}/verification"
    )
    async with httpx.AsyncClient(
        transport=RelayTransport(context, verified_enrollment.order.enrollment.row["relay"]),
        base_url="http://api.test",
    ) as wrong:
        reply = await wrong.get(target, headers=api.headers)
        assert reply.status_code == 401


@pytest.mark.parametrize("change", ["expired", "revoked", "not_yet_valid", "missing"])
async def test_each_relay_request_rechecks_device_validity(
    relay_client, bound_device, app, clock, change
):
    """A TLS-authenticated client loses access when its database certificate becomes unusable."""
    binding = bound_device.binding
    headers = {"Authorization": "Bearer " + binding["credential"]}
    assert (await relay_client.get("/agent/v1/session", headers=headers)).status_code == 200
    async with app.state.db.write() as session:
        row = await session.get(DeviceCertificate, binding["certificate_sha256"])
        if change == "expired":
            row.expires_at = clock.now
        elif change == "revoked":
            row.revoked_at = clock.now
        elif change == "missing":
            await session.delete(row)
        else:
            clock.now -= timedelta(days=2)
    response = await relay_client.get("/agent/v1/session", headers=headers)
    assert response.status_code == 401 and response.json()["detail"] == "Invalid device proof"


async def test_verified_connection_does_not_replace_account_ownership(
    relay_client, verified_enrollment, other_user
):
    """Knowing the enrollment and holding its physical key cannot replace its initiating account."""
    target = f"/api/v1/agent-enrollments/{verified_enrollment.order.enrollment.row['id']}"
    response = await relay_client.get(target + "/verification", headers=other_user["headers"])
    assert response.status_code == 404
    response = await relay_client.post(
        target + "/finish",
        headers={**other_user["headers"], "Idempotency-Key": str(uuid4())},
        json={},
    )
    assert response.status_code == 404


async def test_expired_and_pending_enrollments_cannot_be_verified(
    relay_client, api, verified_enrollment, clock
):
    """Transport authentication cannot finish a different unverified attempt or an expired enrollment."""
    response = await api.post(
        "/api/v1/agent-enrollments",
        headers={"Idempotency-Key": str(uuid4())},
        json=verified_enrollment.order.enrollment.payload,
    )
    target = f"/api/v1/agent-enrollments/{response.json()['id']}"
    response = await relay_client.get(target + "/verification", headers=api.headers)
    assert response.status_code == 409
    clock.now += timedelta(seconds=3601)
    target = f"/api/v1/agent-enrollments/{verified_enrollment.order.enrollment.row['id']}"
    assert (
        await relay_client.get(target + "/verification", headers=api.headers)
    ).status_code == 409
    assert (
        await relay_client.post(
            target + "/finish",
            headers={**api.headers, "Idempotency-Key": str(uuid4())},
            json={},
        )
    ).status_code == 409


async def test_relay_flow_control_preserves_large_upload_and_download(relay_client, user):
    """Data larger than both HTTP/2 windows survives backpressure in each direction byte for byte."""
    data = bytes(range(256)) * 2048
    response = await relay_client.post(
        "/api/v1/artifacts?filename=relay.bin",
        headers={**user["headers"], "Content-Type": "application/octet-stream"},
        content=data,
    )
    assert response.status_code == 201, response.text
    assert response.json()["sha256"] == hashlib.sha256(data).hexdigest()
    downloaded = await relay_client.get(
        f"/api/v1/artifacts/{response.json()['id']}/content", headers=user["headers"]
    )
    assert downloaded.status_code == 200 and downloaded.content == data


@pytest.mark.parametrize("failure", ["missing_certificate", "wrong_ca", "wrong_protocol"])
async def test_tls_rejects_unauthenticated_or_incompatible_clients(app, tls_context, pki, failure):
    """The listener requires a trusted client certificate and HTTP/2 before creating API scopes."""
    context = tls_context
    if failure == "missing_certificate":
        context = ssl.create_default_context(cafile=str(pki.root_file))
        context.set_alpn_protocols(["h2"])
    elif failure == "wrong_ca":
        context = ssl.create_default_context(cafile=str(pki.root_file))
        context.set_alpn_protocols(["h2"])
        # The separate server root is deliberately not the accepted device CA.
        key_path = pki.root_file.with_name("untrusted.key")
        key_path.write_bytes(
            pki.root_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context.load_cert_chain(pki.root_file, key_path)
    else:
        context.set_alpn_protocols(["http/1.1"])
    port = urlsplit(app.state.powercell_relay["url"]).port
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=context, server_hostname="localhost"
        )
        try:
            assert await asyncio.wait_for(reader.read(1), 3) == b""
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
    except (ssl.SSLError, ConnectionResetError):
        pass


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8444",
        "https://user@localhost",
        "https://localhost/path",
        "https://localhost/?token=x",
        "https://localhost/#fragment",
        "https://",
        "https://localhost:0",
    ],
)
async def test_relay_configuration_rejects_unsafe_origins(app, url):
    """A relay endpoint cannot carry credentials, insecure TLS, arbitrary paths or invalid ports."""
    with pytest.raises(ValueError):
        configuration(app.state.settings.model_copy(update={"powercell_relay_url": url}))


async def test_partial_relay_configuration_cannot_silently_disable_verification(app):
    """All relay TLS values and the attested-device trust anchor are mandatory together."""
    for field in [
        "powercell_relay_url",
        "powercell_relay_certificate",
        "powercell_relay_key",
        "powercell_ca_certificate",
    ]:
        with pytest.raises(ValueError):
            configuration(app.state.settings.model_copy(update={field: None}))


async def test_revocation_is_enforced_on_an_already_authenticated_tls_connection(
    relay_wire, bound_device, app, clock
):
    """An established TLS session cannot retain authorization after its certificate is revoked."""
    async with relay_wire() as wire:
        credential = "Bearer " + bound_device.binding["credential"]
        assert (await wire.get(1, "/agent/v1/session", credential))[0] == 200
        async with app.state.db.write() as session:
            row = await session.get(DeviceCertificate, bound_device.binding["certificate_sha256"])
            row.revoked_at = clock.now
        assert (await wire.get(3, "/agent/v1/session", credential))[0] == 401


async def test_abandoned_upload_and_reset_do_not_block_other_streams(relay_wire):
    """An incomplete command upload must not prevent a concurrent heartbeat or the next connection."""
    async with relay_wire() as wire:
        assert await wire.connect(1) == 200
        # Leave HTTP incomplete, then cancel it while another stream remains usable.
        wire.h2.send_data(1, b"POST /api/v1/artifacts HTTP/1.1\r\nHost: api.test\r\n")
        wire.flush()
        assert (await wire.get(3, "/health/ready"))[0] == 200
        wire.h2.reset_stream(1)
        wire.flush()
        assert (await wire.get(5, "/health/ready"))[0] == 200
        # A client half-close of an empty stream also leaves the connection usable.
        assert await wire.connect(7) == 200
        wire.h2.send_data(7, b"", end_stream=True)
        wire.flush()
        assert (await wire.get(9, "/health/ready"))[0] == 200


async def test_non_connect_requests_and_malformed_frames_cannot_enter_api(relay_wire):
    """Reject unsupported proxy methods and corrupted HTTP/2 without exposing an API transport."""
    async with relay_wire() as wire:
        assert (
            await wire.connect(
                1,
                [
                    (":method", "GET"),
                    (":scheme", "https"),
                    (":authority", "localhost"),
                    (":path", "/health/ready"),
                ],
            )
            == 405
        )
        assert (await wire.get(3, "/health/ready"))[0] == 200
        # A PING frame on a nonzero stream violates HTTP/2 and must terminate the connection.
        wire.writer.write(b"\x00\x00\x08\x06\x00\x00\x00\x00\x01" + bytes(8))
        async with asyncio.timeout(3):
            while await wire.reader.read(65536):
                pass
    async with relay_wire() as wire:
        assert (await wire.get(1, "/health/ready"))[0] == 200


async def test_client_goaway_disconnects_open_streams_cleanly(relay_wire):
    """Client shutdown cancels an unfinished tunnel without breaking later connections."""
    async with relay_wire() as wire:
        assert await wire.connect(1) == 200
        wire.h2.close_connection()
        wire.flush()
        async with asyncio.timeout(3):
            while await wire.reader.read(65536):
                pass
    async with relay_wire() as wire:
        assert (await wire.get(1, "/health/ready"))[0] == 200


async def test_api_shutdown_closes_authenticated_connections_and_pending_streams(
    relay_wire, http_server
):
    """Stopping the API must stop its relay too, including unfinished uploads."""
    async with relay_wire() as wire:
        assert await wire.connect(1) == 200
        wire.h2.send_data(1, b"POST /api/v1/artifacts HTTP/1.1\r\nHost: api.test\r\n")
        wire.flush()
        http_server.server.should_exit = True
        _, pending = await asyncio.wait([http_server.task], timeout=3)
        assert not pending, "API shutdown retained a relay connection"
        async with asyncio.timeout(3):
            while await wire.reader.read(65536):
                pass


@pytest.mark.parametrize("stage", ["accept", "stream"])
async def test_backend_socket_failure_closes_both_ends_without_breaking_listener(
    relay_wire, monkeypatch, stage
):
    """Failure while opening an in-process transport must not leak sockets or kill the TLS listener."""
    loop = asyncio.get_running_loop()
    original_pair, pairs = socket.socketpair, []
    original_open = asyncio.open_connection
    accepted = []
    original_accept = loop.connect_accepted_socket

    def capture_pair():
        pair = original_pair()
        pairs.append(pair)
        return pair

    async def unavailable(*args, **kwargs):
        raise OSError("Simulated unavailable backend socket")

    async def failed_stream(*args, **kwargs):
        if "sock" in kwargs:
            raise OSError("Simulated unavailable backend stream")
        return await original_open(*args, **kwargs)

    async def capture_accepted(*args, **kwargs):
        transport, protocol = await original_accept(*args, **kwargs)
        accepted.append(protocol)
        return transport, protocol

    with monkeypatch.context() as patch:
        patch.setattr(socket, "socketpair", capture_pair)
        patch.setattr(
            loop, "connect_accepted_socket", unavailable if stage == "accept" else capture_accepted
        )
        if stage == "stream":
            patch.setattr(asyncio, "open_connection", failed_stream)
        async with relay_wire() as wire:
            with pytest.raises(AssertionError, match="disconnected"):
                await wire.connect(1)
        assert pairs and all(s.fileno() == -1 for pair in pairs for s in pair)
        assert all(not protocol.connections for protocol in accepted)
    async with relay_wire() as wire:
        assert (await wire.get(1, "/health/ready"))[0] == 200


async def test_invalid_relay_tls_configuration_fails_startup(app, tmp_path):
    """A broken server certificate fails startup instead of exposing a weaker relay."""
    invalid = tmp_path / "invalid.pem"
    invalid.write_text("not a TLS certificate")
    broken = create_app(
        app.state.settings.model_copy(update={"powercell_relay_certificate": invalid})
    )
    with pytest.raises(ssl.SSLError):
        async with broken.router.lifespan_context(broken):
            pytest.fail("Invalid TLS material must not start a relay")


async def test_slow_download_backpressure_does_not_block_other_connects(relay_wire):
    """A receiver that stops granting data capacity must not block independent CONNECT streams."""
    async with relay_wire() as wire:
        wire.h2.update_settings({SettingCodes.INITIAL_WINDOW_SIZE: 16})
        wire.flush()
        assert await wire.connect(1) == 200
        wire.h2.send_data(
            1, b"GET /health/ready HTTP/1.1\r\nHost: api.test\r\nConnection: close\r\n\r\n"
        )
        wire.flush()
        first = await wire.events_until(
            lambda events: any(isinstance(e, DataReceived) for e in events), credit=False
        )
        assert sum(len(e.data) for e in first if isinstance(e, DataReceived)) == 16
        assert await wire.connect(3) == 200
        wire.h2.increment_flow_control_window(4096, 1)
        wire.flush()
        rest = await wire.events_until(
            lambda events: any(isinstance(e, StreamEnded) and e.stream_id == 1 for e in events)
        )
        body = b"".join(e.data for e in first + rest if isinstance(e, DataReceived))
        assert body.startswith(b"HTTP/1.1 200") and b'"status":"ready"' in body


async def test_data_and_reset_after_refused_connect_do_not_open_a_backend(relay_wire):
    """Late DATA, END_STREAM and RESET on rejected destinations do not affect valid API streams."""
    async with relay_wire() as wire:
        headers = [(":method", "CONNECT"), (":authority", "forbidden.invalid:80")]
        assert await wire.connect(1, headers) == 403
        wire.h2.send_data(1, b"late bytes", end_stream=True)
        assert await wire.connect(3, headers) == 403
        wire.h2.reset_stream(3)
        wire.flush()
        assert (await wire.get(5, "/health/ready"))[0] == 200


async def test_old_profile_routes_cannot_select_a_new_enrollments_identity(api, enrollment):
    """Each enrollment has its own hostname; replay keeps that route stable and reinstall gets another."""
    first = enrollment.row["relay"]
    response = await api.post(
        "/api/v1/agent-enrollments",
        headers={"Idempotency-Key": str(uuid4())},
        json=enrollment.payload,
    )
    second = response.json()["relay"]
    assert second["url"] == first["url"] and second["api_origin"] != first["api_origin"]
    response = await api.post(
        "/api/v1/agent-enrollments",
        headers={"Idempotency-Key": enrollment.key},
        json=enrollment.payload,
    )
    assert response.json()["relay"] == first
