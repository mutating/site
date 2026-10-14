"""Restricted RFC 8555 service: one account/order/Apple identity per enrollment.

No DNS issuance, arbitrary CSR extensions, caller-supplied CAs or proxy trust headers.
The enrollment's random directory capability authorizes only its single identity.
"""

import base64
import binascii
import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

import cbor2
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from OpenSSL.crypto import X509StoreContextError

from throng_api.device_security import public_key_bytes, strict_json, validate_attestation
from throng_api.enrollment import authority, record_certificate
from throng_api.models import AgentEnrollment, DeviceCertificate, ProtocolNonce
from throng_api.powercell import bounded_body

router = APIRouter(prefix="/acme/powercell", tags=["Power Cell ACME"])


class AcmeError(Exception):
    def __init__(self, detail, code="malformed", status=400):
        self.detail, self.code, self.status = detail, code, status


def unb64(value):
    if not isinstance(value, str) or len(value) > 512 * 1024:
        raise ValueError("Invalid base64url value")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def jwk_key(jwk, algorithm):
    if (
        algorithm == "ES256"
        and set(jwk) == {"kty", "crv", "x", "y"}
        and jwk["kty"] == "EC"
        and jwk["crv"] == "P-256"
    ):
        return ec.EllipticCurvePublicNumbers(
            int.from_bytes(unb64(jwk["x"]), "big"),
            int.from_bytes(unb64(jwk["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
    if algorithm == "RS256" and set(jwk) == {"kty", "n", "e"} and jwk["kty"] == "RSA":
        key = rsa.RSAPublicNumbers(
            int.from_bytes(unb64(jwk["e"]), "big"), int.from_bytes(unb64(jwk["n"]), "big")
        ).public_key()
        if not 2048 <= key.key_size <= 4096:
            raise ValueError("Unsupported RSA account key size")
        return key
    raise ValueError("Unsupported account algorithm or JWK")


async def authenticate_jws(session, request, row, issuer, operation, content):
    try:
        envelope = strict_json(content)
        if set(envelope) != {"protected", "payload", "signature"}:
            raise ValueError("Expected flattened JWS")
        protected = strict_json(unb64(envelope["protected"]))
        identity = "jwk" if operation == "new-account" else "kid"
        if set(protected) != {"alg", "nonce", "url", identity} or protected[
            "url"
        ] != issuer.endpoint(row, operation):
            raise ValueError("JWS protected headers do not match this endpoint")
        if identity == "kid" and (
            not row.account_jwk or protected["kid"] != issuer.endpoint(row, "account")
        ):
            raise ValueError("Unknown ACME account")
        if not isinstance(protected["nonce"], str) or not 1 <= len(protected["nonce"]) <= 64:
            raise ValueError("Invalid nonce")
        jwk = protected["jwk"] if identity == "jwk" else row.account_jwk
        key = jwk_key(jwk, protected["alg"])
        signature = unb64(envelope["signature"])
        signed = (envelope["protected"] + "." + envelope["payload"]).encode()
        if isinstance(key, ec.EllipticCurvePublicKey):
            if len(signature) != 64:
                raise ValueError("Invalid ES256 signature size")
            signature = utils.encode_dss_signature(
                int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
            )
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        else:
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        nonce = await session.get(ProtocolNonce, ("acme:" + row.id, protected["nonce"]))
        if not nonce or nonce.expires_at <= request.app.state.clock():
            raise AcmeError("Fetch a new replay nonce", "badNonce")
        await session.delete(nonce)
        payload = unb64(envelope["payload"])
        return (strict_json(payload) if payload else None), jwk
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        binascii.Error,
        InvalidSignature,
    ) as error:
        raise AcmeError("Invalid ACME signed request", "unauthorized", 401) from error


def challenge(row, issuer):
    result = {
        "type": "device-attest-01",
        "url": issuer.endpoint(row, "challenge"),
        "status": "valid" if row.attested_key else "pending",
        "token": row.challenge_token,
    }
    return result


def order(row, issuer):
    return {
        "status": "valid" if row.certificate_sha256 else "ready" if row.attested_key else "pending",
        "expires": row.expires_at.isoformat(),
        "identifiers": [{"type": "permanent-identifier", "value": row.id}],
        "authorizations": [issuer.endpoint(row, "authorization")],
        "finalize": issuer.endpoint(row, "finalize"),
        **({"certificate": issuer.endpoint(row, "certificate")} if row.certificate_sha256 else {}),
    }


async def dispatch(session, request, row, issuer, operation, payload, jwk):
    if operation == "new-account":
        if not isinstance(payload, dict) or set(payload) - {
            "termsOfServiceAgreed",
            "contact",
            "onlyReturnExisting",
        }:
            raise AcmeError("Invalid account request")
        if row.account_jwk and row.account_jwk != jwk:
            raise AcmeError("Enrollment already has an account", "unauthorized", 401)
        if payload.get("onlyReturnExisting") and not row.account_jwk:
            raise AcmeError("Account not found", "accountDoesNotExist")
        created = row.account_jwk is None
        row.account_jwk = jwk
        return (
            {"status": "valid", "orders": issuer.endpoint(row, "orders")},
            201 if created else 200,
            {"Location": issuer.endpoint(row, "account")},
        )
    if operation == "new-order":
        if payload != {"identifiers": [{"type": "permanent-identifier", "value": row.id}]}:
            raise AcmeError("Order identifier must match this enrollment", "rejectedIdentifier")
        row.order_created = True
        return order(row, issuer), 201, {"Location": issuer.endpoint(row, "order")}
    if operation == "account":
        if payload not in (None, {}):
            raise AcmeError("Account updates are not supported")
        return {"status": "valid", "orders": issuer.endpoint(row, "orders")}, 200, {}
    if operation == "orders":
        if payload is not None:
            raise AcmeError("Use POST-as-GET")
        return {"orders": [issuer.endpoint(row, "order")] if row.order_created else []}, 200, {}
    if not row.order_created:
        raise AcmeError("Order not found", "unauthorized", 401)
    if operation in {"order", "authorization", "certificate"} and payload is not None:
        raise AcmeError("Use POST-as-GET")
    if operation == "order":
        return order(row, issuer), 200, {}
    if operation == "authorization":
        return (
            {
                "status": "valid" if row.attested_key else "pending",
                "expires": row.expires_at.isoformat(),
                "identifier": {"type": "permanent-identifier", "value": row.id},
                "challenges": [challenge(row, issuer)],
            },
            200,
            {},
        )
    if operation == "challenge":
        if payload is None:
            return challenge(row, issuer), 200, {}
        if not isinstance(payload, dict) or set(payload) != {"attObj"}:
            raise AcmeError("Apple attObj is required", "badAttestationStatement")
        try:
            identity, key = validate_attestation(
                unb64(payload["attObj"]), row.challenge_token, request.app.state.clock()
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            x509.ExtensionNotFound,
            cbor2.CBORDecodeError,
            X509StoreContextError,
        ) as error:
            raise AcmeError(
                "Apple attestation validation failed", "badAttestationStatement"
            ) from error
        if row.attested_key and (row.attested_key != key or row.identity_hash != identity):
            raise AcmeError("Attestation already bound to another key", "badAttestationStatement")
        row.attested_key, row.identity_hash = key, identity
        return challenge(row, issuer), 200, {}
    if operation == "finalize":
        if not row.attested_key:
            raise AcmeError("Complete attestation before finalizing", "orderNotReady")
        try:
            if not isinstance(payload, dict) or set(payload) != {"csr"}:
                raise ValueError("CSR required")
            encoded = unb64(payload["csr"])
            csr = x509.load_der_x509_csr(encoded)
            if not csr.is_signature_valid or public_key_bytes(csr.public_key()) != row.attested_key:
                raise ValueError("CSR key must be the attested key")
            checksum = hashlib.sha256(encoded).hexdigest()
            if row.csr_sha256 and row.csr_sha256 != checksum:
                raise ValueError("Order was finalized with a different CSR")
            if not row.certificate_sha256:
                certificate = issuer.issue(row, csr, request.app.state.clock())
                await record_certificate(session, row, certificate)
                row.csr_sha256 = checksum
        except (ValueError, TypeError, KeyError) as error:
            raise AcmeError("Invalid CSR or unavailable issuer", "badCSR") from error
        return order(row, issuer), 200, {"Location": issuer.endpoint(row, "order")}
    if operation == "certificate" and row.certificate_sha256:
        certificate = await session.get(DeviceCertificate, row.certificate_sha256)
        return certificate.pem + issuer.cert.public_bytes(serialization.Encoding.PEM), 200, {}
    raise AcmeError("Unknown or unavailable ACME operation")


@router.get("/{enrollment_id}/{directory_secret}/{operation}")
@router.head("/{enrollment_id}/{directory_secret}/{operation}")
@router.post("/{enrollment_id}/{directory_secret}/{operation}")
async def acme(enrollment_id: UUID, directory_secret: str, operation: str, request: Request):
    issuer = authority(request)
    content = await bounded_body(request, 512 * 1024)
    async with request.app.state.db.write() as session:
        row = await session.get(AgentEnrollment, str(enrollment_id))
        now = request.app.state.clock()
        if (
            not row
            or not secrets.compare_digest(row.acme_secret, directory_secret)
            or row.expires_at <= now
        ):
            return JSONResponse(
                {
                    "type": "urn:ietf:params:acme:error:unauthorized",
                    "detail": "Unknown or expired enrollment",
                },
                status_code=401,
                media_type="application/problem+json",
            )
        nonce = secrets.token_urlsafe(32)
        session.add(
            ProtocolNonce(
                namespace="acme:" + row.id, value=nonce, expires_at=now + timedelta(minutes=10)
            )
        )
        headers = {
            "Replay-Nonce": nonce,
            "Cache-Control": "no-store",
            "Link": "<" + issuer.endpoint(row, "directory") + '>;rel="index"',
        }
        try:
            if request.method == "GET" and operation == "directory":
                body = {
                    "newNonce": issuer.endpoint(row, "nonce"),
                    "newAccount": issuer.endpoint(row, "new-account"),
                    "newOrder": issuer.endpoint(row, "new-order"),
                }
                return JSONResponse(body, headers=headers)
            if request.method in {"HEAD", "GET"} and operation == "nonce":
                return Response(
                    status_code=200 if request.method == "HEAD" else 204, headers=headers
                )
            if request.method != "POST":
                raise AcmeError("Use POST with a signed JWS", status=405)
            payload, jwk = await authenticate_jws(session, request, row, issuer, operation, content)
            body, status, extra = await dispatch(
                session, request, row, issuer, operation, payload, jwk
            )
            headers.update(extra)
            if isinstance(body, bytes):
                return Response(
                    body,
                    status_code=status,
                    media_type="application/pem-certificate-chain",
                    headers=headers,
                )
            return JSONResponse(body, status_code=status, headers=headers)
        except AcmeError as error:
            # Commit consumed request nonce even when a validly signed operation is rejected.
            return JSONResponse(
                {"type": "urn:ietf:params:acme:error:" + error.code, "detail": error.detail},
                status_code=error.status,
                media_type="application/problem+json",
                headers=headers,
            )
