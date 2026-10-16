"""User-authorized, single-certificate enrollment and ACME identity profiles."""

import base64
import hashlib
import plistlib
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select

from throng_api.device_security import public_key_bytes, verify_proof
from throng_api.execution import remember, replay
from throng_api.models import (
    CPU_RESOURCE_ID,
    MEMORY_RESOURCE_ID,
    AgentCredential,
    AgentEnrollment,
    Computer,
    ComputerOwnership,
    DeviceCertificate,
    PhysicalDevice,
    new_id,
)
from throng_api.powercell import bounded_body
from throng_api.powercell_schemas import Binding, EnrollmentCreate, EnrollmentStatus
from throng_api.relay import enrollment_configuration
from throng_api.resource_ledger import add_resource
from throng_api.security import (
    access_token_scheme,
    authenticate,
    digest,
    owner_values,
    personal,
    secret,
)
from throng_api.workload_images import IMAGE_ALIASES

router = APIRouter(
    prefix="/v1", tags=["Power Cell enrollment"], dependencies=[Depends(access_token_scheme)]
)
public_router = APIRouter(tags=["Power Cell capabilities"])
EXTENSIONS = [
    "powercell.enrollment.v1",
    "powercell.capacity.v1",
    "powercell.eviction.v1",
    "powercell.diagnostics.v1",
    "powercell.isolation.v1",
]


class Authority:
    """Load operator-managed signing material once; never include it in responses or logs."""

    def __init__(self, settings):
        url = urlsplit(settings.powercell_acme_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise ValueError(
                "THRONG_POWERCELL_ACME_URL must be an absolute HTTPS URL without credentials/query/fragment"
            )
        self.base = settings.powercell_acme_url.rstrip("/")
        self.cert = x509.load_pem_x509_certificate(settings.powercell_ca_certificate.read_bytes())
        self.key = serialization.load_pem_private_key(
            settings.powercell_ca_key.read_bytes(), password=None
        )
        if (
            not isinstance(self.key, ec.EllipticCurvePrivateKey)
            or public_key_bytes(self.key.public_key()) != public_key_bytes(self.cert.public_key())
            or not self.cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        ):
            raise ValueError("Power Cell CA requires a matching EC CA certificate and private key")
        self.fernet = Fernet(
            base64.urlsafe_b64encode(hashlib.sha256(settings.powercell_secret.encode()).digest())
        )
        self.days = settings.device_certificate_days

    def endpoint(self, enrollment, operation):
        return f"{self.base}/{enrollment.id}/{enrollment.acme_secret}/{operation}"

    def issue(self, enrollment, csr, now):
        if not self.cert.not_valid_before_utc <= now < self.cert.not_valid_after_utc:
            raise ValueError("Power Cell CA is not currently valid")
        identifier = enrollment.id.encode()
        permanent = x509.OtherName(
            x509.ObjectIdentifier("1.3.6.1.5.5.7.8.3"),
            b"\x30"
            + bytes([len(identifier) + 2])
            + b"\x0c"
            + bytes([len(identifier)])
            + identifier,
        )
        return (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Power Cell {enrollment.id}")])
            )
            .issuer_name(self.cert.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(min(now + timedelta(days=self.days), self.cert.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(True, False, False, False, False, False, False, False, False),
                critical=True,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName([permanent]), critical=False)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.cert.public_key()),
                critical=False,
            )
            .sign(self.key, hashes.SHA256())
        )


def configure_authority(settings):
    fields = (
        settings.powercell_acme_url,
        settings.powercell_ca_certificate,
        settings.powercell_ca_key,
        settings.powercell_secret,
    )
    if not any(fields):
        return None
    if not all(fields):
        raise ValueError(
            "Configure all four THRONG_POWERCELL_ACME_URL/CA_CERTIFICATE/CA_KEY/SECRET settings"
        )
    return Authority(settings)


def authority(request):
    if request.app.state.powercell_authority is None:
        raise HTTPException(503, "Power Cell certificate authority is not configured")
    return request.app.state.powercell_authority


@public_router.get("/v1/agent-capabilities")
async def capabilities(request: Request):
    configured = request.app.state.powercell_authority is not None
    return {
        "protocol_version": 1,
        "extensions": (EXTENSIONS if configured else EXTENSIONS[1:])
        + (["powercell.relay.v1"] if request.app.state.powercell_relay else [])
        + ["powercell.images.v1"],
        "runtime": "powercell-ubuntu-arm64-v1",
        "images": ["ubuntu:24.04"],
        "image_families": ["ghcr.io/actions/actions-runner"],
        "image_aliases": IMAGE_ALIASES,
        "attestation_required": True,
    }


def enrollment_status(row, now, relay=None):
    status = "verified" if row.certificate_sha256 else "pending_attestation"
    if row.expires_at <= now and not row.computer_id:
        status = "expired"
    return {
        "id": row.id,
        "status": status,
        "expires_at": row.expires_at,
        "certificate_sha256": row.certificate_sha256,
        "relay": enrollment_configuration(relay, row.id),
    }


async def enrollment_for_user(session, request, enrollment_id, scope="read"):
    principal = await authenticate(session, request, scope)
    user_id = personal(principal)
    row = await session.get(AgentEnrollment, str(enrollment_id))
    if not row or row.actor_id != user_id:
        raise HTTPException(404, "Enrollment not found")
    return row, principal


@router.post("/agent-enrollments", status_code=201, response_model=EnrollmentStatus)
async def create_enrollment(
    data: EnrollmentCreate, request: Request, idempotency_key: str = Header()
):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        actor_id = personal(principal)
        authority(request)
        await owner_values(session, principal, data.company_id, admin=True)
        payload = data.model_dump(mode="json")
        previous = await replay(session, principal.key, "enrollment", idempotency_key, payload)
        now = request.app.state.clock()
        if previous:
            return enrollment_status(
                await session.get(AgentEnrollment, previous), now, request.app.state.powercell_relay
            )
        count = await session.scalars(
            select(AgentEnrollment).where(
                AgentEnrollment.actor_id == actor_id, AgentEnrollment.expires_at > now
            )
        )
        if len(count.all()) >= 20:
            raise HTTPException(429, "Too many pending enrollments")
        row = AgentEnrollment(
            id=new_id(),
            actor_id=actor_id,
            installation_id=str(data.installation_id),
            request=payload,
            acme_secret=secrets.token_urlsafe(32),
            challenge_token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + timedelta(seconds=request.app.state.settings.enrollment_seconds),
        )
        session.add(row)
        await session.flush()
        remember(session, principal.key, "enrollment", idempotency_key, payload, row.id)
        return enrollment_status(row, now, request.app.state.powercell_relay)


@router.get("/agent-enrollments/{enrollment_id}", response_model=EnrollmentStatus)
async def get_enrollment(enrollment_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        row, _ = await enrollment_for_user(session, request, enrollment_id)
        return enrollment_status(row, request.app.state.clock(), request.app.state.powercell_relay)


@router.get("/agent-enrollments/{enrollment_id}/profile", response_class=Response)
async def profile(enrollment_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        row, _ = await enrollment_for_user(session, request, enrollment_id)
        issuer = authority(request)
        if row.expires_at <= request.app.state.clock() or row.computer_id:
            raise HTTPException(409, "Enrollment is no longer pending")
        prefix = f"tech.mutating.powercell.{row.id}"
        acme = {
            "PayloadType": "com.apple.security.acme",
            "PayloadVersion": 1,
            "PayloadIdentifier": prefix + ".identity",
            "PayloadUUID": str(uuid5(NAMESPACE_URL, prefix + ".identity")),
            "PayloadDisplayName": "Power Cell device identity",
            "DirectoryURL": issuer.endpoint(row, "directory"),
            "ClientIdentifier": row.id,
            "Subject": [[["CN", f"Power Cell {row.id}"]]],
            "KeyType": "ECSECPrimeRandom",
            "KeySize": 256,
            "HardwareBound": True,
            "Attest": True,
            "KeyIsExtractable": False,
            "AllowAllAppsAccess": False,
            "ExtendedKeyUsage": ["1.3.6.1.5.5.7.3.2"],
            "UsageFlags": 1,
        }
        document = {
            "PayloadType": "Configuration",
            "PayloadVersion": 1,
            "PayloadIdentifier": prefix,
            "PayloadUUID": row.id,
            "PayloadDisplayName": "Power Cell device verification",
            "PayloadScope": "User",
            "PayloadContent": [acme],
        }
        if relay := enrollment_configuration(request.app.state.powercell_relay, row.id):
            # The built-in relay requires its ACME key in the system keychain.
            document["PayloadScope"] = "System"
            document["PayloadContent"].append(
                {
                    "PayloadType": "com.apple.relay.managed",
                    "PayloadVersion": 1,
                    "PayloadIdentifier": prefix + ".relay",
                    "PayloadUUID": str(uuid5(NAMESPACE_URL, prefix + ".relay")),
                    "PayloadDisplayName": "Power Cell authenticated API connection",
                    "MatchDomains": [urlsplit(relay["api_origin"]).hostname],
                    "Relays": [
                        {
                            "HTTP2RelayURL": relay["url"],
                            "PayloadCertificateUUID": acme["PayloadUUID"],
                        }
                    ],
                }
            )
        return Response(
            plistlib.dumps(document),
            media_type="application/x-apple-aspen-config",
            headers={"Cache-Control": "no-store"},
        )


@router.get("/agent-enrollments/{enrollment_id}/verification", response_model=EnrollmentStatus)
async def verify_enrolled_device(enrollment_id: UUID, request: Request):
    """Confirm that this connection holds the key certified for this user's enrollment."""
    async with request.app.state.db.write() as session:
        row, _ = await enrollment_for_user(session, request, enrollment_id)
        now = request.app.state.clock()
        if not row.certificate_sha256 or (not row.computer_id and row.expires_at <= now):
            raise HTTPException(409, "Enrollment requires a fresh verified attestation")
        await verify_proof(session, request, row.certificate_sha256)
        return enrollment_status(row, now, request.app.state.powercell_relay)


@router.post("/agent-enrollments/{enrollment_id}/finish", response_model=Binding)
async def finish_enrollment(enrollment_id: UUID, request: Request, idempotency_key: str = Header()):
    content = await bounded_body(request, 1024)
    async with request.app.state.db.write() as session:
        row, principal = await enrollment_for_user(session, request, enrollment_id, "manage")
        issuer = authority(request)
        if content not in (b"", b"{}"):
            raise HTTPException(422, "Enrollment finish accepts only an empty object")
        now = request.app.state.clock()
        if not row.certificate_sha256 or (not row.computer_id and row.expires_at <= now):
            raise HTTPException(409, "Enrollment requires a fresh verified attestation")
        await verify_proof(session, request, row.certificate_sha256)
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise HTTPException(422, "Idempotency-Key of 1 to 128 characters is required")
        certificate = await session.get(DeviceCertificate, row.certificate_sha256)
        if row.computer_id:
            if row.finish_key != idempotency_key:
                raise HTTPException(409, "Enrollment has already been bound")
            credential = issuer.fernet.decrypt(row.credential_encrypted).decode()
            current = await session.scalar(
                select(AgentCredential).where(AgentCredential.computer_id == row.computer_id)
            )
            if not current or current.token_hash != digest(credential):
                raise HTTPException(409, "Enrollment credential has been replaced or revoked")
        else:
            owner = await owner_values(session, principal, row.request["company_id"], admin=True)
            values = {
                key: row.request[key]
                for key in (
                    "name",
                    "os",
                    "architecture",
                    "runtime",
                    "disk_bytes",
                    "isolation_modes",
                )
            }
            computer = Computer(
                **values,
                registered_by_user_id=principal.actor_id,
                resources=[],
                accepting_work=False,
                device_id=certificate.device_id,
                certificate_sha256=certificate.sha256,
            )
            session.add(computer)
            await session.flush()
            session.add(ComputerOwnership(computer_id=computer.id, **owner))
            await session.flush()
            for type_id, amount in (
                (CPU_RESOURCE_ID, row.request["cpu_millis"]),
                (MEMORY_RESOURCE_ID, row.request["memory_bytes"]),
            ):
                await add_resource(session, computer, type_id, amount, now, principal.actor_id)
            credential = secret("agt_")
            session.add(AgentCredential(computer_id=computer.id, token_hash=digest(credential)))
            row.computer_id, row.finish_key = computer.id, idempotency_key
            row.credential_encrypted = issuer.fernet.encrypt(credential.encode())
        return {
            "computer_id": row.computer_id,
            "credential": credential,
            "device_id": certificate.device_id,
            "certificate_sha256": certificate.sha256,
            "relay": enrollment_configuration(request.app.state.powercell_relay, row.id),
        }


async def record_certificate(session, row, certificate):
    device = await session.scalar(
        select(PhysicalDevice).where(PhysicalDevice.identity_hash == row.identity_hash)
    )
    if not device:
        device = PhysicalDevice(identity_hash=row.identity_hash)
        session.add(device)
        await session.flush()
    row.certificate_sha256 = certificate.fingerprint(hashes.SHA256()).hex()
    session.add(
        DeviceCertificate(
            sha256=row.certificate_sha256,
            device_id=device.id,
            enrollment_id=row.id,
            pem=certificate.public_bytes(serialization.Encoding.PEM),
            expires_at=certificate.not_valid_after_utc,
        )
    )
