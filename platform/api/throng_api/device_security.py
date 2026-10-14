"""Apple trust validation and request possession proofs. No client-selected trust anchors."""

import base64
import binascii
import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import cbor2
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException, Request
from OpenSSL import crypto
from sqlalchemy import select

from throng_api.models import (
    AgentCredential,
    AgentMutation,
    Computer,
    DeviceCertificate,
    ProtocolNonce,
)
from throng_api.security import bearer, digest

APPLE_ROOT = Path(__file__).with_name("certificates") / "apple-enterprise-attestation.pem"
APPLE_ROOT_SHA256 = "ccf59ef8fcb3017d97f8b5fa6fa90e7a3f9283f76b55ac6cf6eda8b8b949f05b"
SERIAL_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.9.1")
UDID_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.9.2")
NONCE_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.11.1")
SEP_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.10.2")
SIP_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.13.1")


def strict_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON member")
            result[key] = value
        return result

    return json.loads(
        data,
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def public_key_bytes(key):
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def validate_attestation(encoded, token, now):
    """Validate Apple's ACME attestation envelope using OpenSSL's full path validator.

    Apple uses SHA256(token), unlike the generic draft's keyAuthorization construction.
    Apple extension values here are raw bytes, not nested App Attest ASN.1 objects.
    """
    stream = io.BytesIO(encoded)
    envelope = cbor2.CBORDecoder(stream).decode()
    if stream.read(1) or not isinstance(envelope, dict) or envelope.get("fmt") != "apple":
        raise ValueError("Expected an Apple attestation")
    chain = envelope.get("attStmt", {}).get("x5c")
    if not isinstance(chain, list) or not 2 <= len(chain) <= 5:
        raise ValueError("Invalid attestation chain")
    certificates = [x509.load_der_x509_certificate(item) for item in chain]
    root = x509.load_pem_x509_certificate(APPLE_ROOT.read_bytes())
    if root.fingerprint(hashes.SHA256()).hex() != APPLE_ROOT_SHA256:
        raise ValueError("Apple trust anchor integrity failure")
    store = crypto.X509Store()
    store.add_cert(crypto.X509.from_cryptography(root))
    store.set_time(now)
    context = crypto.X509StoreContext(
        store,
        crypto.X509.from_cryptography(certificates[0]),
        [crypto.X509.from_cryptography(cert) for cert in certificates[1:]],
    )
    context.verify_certificate()
    leaf = certificates[0]
    key = leaf.public_key()
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("Expected an attested P-256 key")

    def extension(oid):
        return leaf.extensions.get_extension_for_oid(oid).value.value

    if extension(NONCE_OID) != hashlib.sha256(token.encode()).digest():
        raise ValueError("Attestation freshness does not match this enrollment")
    udid, serial = extension(UDID_OID), extension(SERIAL_OID)
    if not udid or not serial or len(udid) > 128 or len(serial) > 128:
        raise ValueError("Missing certified hardware identity")
    # A Mac attestation has SIP and Secure Enclave properties. No claims from the request are used.
    if not extension(SEP_OID) or not extension(SIP_OID):
        raise ValueError("Missing Mac/Secure Enclave properties")
    identity = hashlib.sha256(b"powercell.apple.udid.v1\x00" + udid).hexdigest()
    return identity, public_key_bytes(key)


async def verify_proof(session, request, certificate_hash, *, allow_revoked=False):
    """Called under the protocol write lock; successful nonces are durable before dispatch."""
    now = request.app.state.clock()
    from throng_api.relay import RelayPeer

    peer = request.scope.get("powercell.relay_peer")
    if isinstance(peer, RelayPeer):
        certificate = await session.get(DeviceCertificate, certificate_hash)
        if (
            peer.certificate_sha256 != certificate_hash
            or not certificate
            or certificate.expires_at <= now
            or (certificate.revoked_at and not allow_revoked)
            or x509.load_pem_x509_certificate(certificate.pem).not_valid_before_utc > now
        ):
            raise HTTPException(401, "Invalid device proof")
        request.state.device_certificate = certificate_hash
        return
    window = request.app.state.settings.device_proof_seconds
    try:
        raw = base64.b64decode(request.headers.get("x-powercell-proof", ""), validate=True)
        signature = base64.b64decode(
            request.headers.get("x-powercell-signature", ""), validate=True
        )
        if not raw or len(raw) > 8192 or not signature or len(signature) > 128:
            raise ValueError("Missing or oversized proof")
        proof = strict_json(raw)
        target = request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
        if request.scope["query_string"]:
            target += "?" + request.scope["query_string"].decode("ascii")
        if not isinstance(proof, dict) or set(proof) != {
            "version",
            "certificate_sha256",
            "method",
            "target",
            "timestamp",
            "nonce",
            "body_sha256",
            "authorization_sha256",
            "session",
            "idempotency_key",
        }:
            raise ValueError("Unexpected proof fields")
        if (
            type(proof["version"]) is not int
            or proof["version"] != 1
            or proof["certificate_sha256"] != certificate_hash
            or proof["method"] != request.method
            or proof["target"] != target
            or type(proof["timestamp"]) is not int
            or abs(now.timestamp() - proof["timestamp"]) > window
            or proof["body_sha256"] != hashlib.sha256(await request.body()).hexdigest()
            or proof["authorization_sha256"] != digest(request.headers.get("authorization", ""))
            or proof["session"] != request.headers.get("x-agent-session")
            or proof["idempotency_key"] != request.headers.get("idempotency-key")
        ):
            raise ValueError("Proof does not match this request")
        nonce = str(UUID(proof["nonce"]))
        certificate = await session.get(DeviceCertificate, certificate_hash)
        if (
            not certificate
            or certificate.expires_at <= now
            or (certificate.revoked_at and not allow_revoked)
        ):
            raise ValueError("Certificate is expired or revoked")
        cert = x509.load_pem_x509_certificate(certificate.pem)
        if cert.not_valid_before_utc > now:
            raise ValueError("Certificate not yet valid")
        cert.public_key().verify(signature, raw, ec.ECDSA(hashes.SHA256()))
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        binascii.Error,
        InvalidSignature,
    ) as error:
        raise HTTPException(401, "Invalid device proof") from error
    identity = (certificate_hash, nonce)
    previous = await session.get(ProtocolNonce, identity)
    if previous and previous.expires_at > now:
        raise HTTPException(401, "Device proof nonce has already been used")
    if previous:
        await session.delete(previous)
        await session.flush()
    # Keep until this timestamp can no longer be accepted, including future-skewed proofs.
    session.add(
        ProtocolNonce(
            namespace=certificate_hash,
            value=nonce,
            expires_at=now + timedelta(seconds=2 * window + 1),
        )
    )
    request.state.device_certificate = certificate_hash


async def agent_device_proof(request: Request):
    """FastAPI dependency shared by every agent operation, including session-free uploads."""
    from throng_api.powercell import bounded_body

    certificate_hash = None
    tombstone = False
    async with request.app.state.db.read() as session:
        credential_hash = digest(bearer(request))
        credential = await session.scalar(
            select(AgentCredential).where(AgentCredential.token_hash == credential_hash)
        )
        if credential:
            computer = await session.get(Computer, credential.computer_id)
            certificate_hash = computer.certificate_sha256 if computer else None
        elif request.url.path == "/agent/v1/unbind":
            row = await session.get(
                AgentMutation,
                (credential_hash, "unbind", request.headers.get("idempotency-key", "")),
            )
            if row and row.expires_at > request.app.state.clock():
                certificate_hash, tombstone = row.certificate_sha256, True
    if certificate_hash:
        limit = request.app.state.settings.max_artifact_bytes
        if "/diagnostics/" in request.url.path:
            limit = request.app.state.settings.max_diagnostic_bytes
        await bounded_body(request, limit)
        async with request.app.state.db.write() as session:
            await verify_proof(session, request, certificate_hash, allow_revoked=tombstone)
