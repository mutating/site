"""A real test CA and HTTP ACME client; cryptographic verification is never bypassed."""

import base64
import hashlib
import json
import plistlib
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import cbor2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509.oid import NameOID

from throng_api.app import create_app
from throng_api.device_security import NONCE_OID, SEP_OID, SERIAL_OID, SIP_OID, UDID_OID


def b64(data):
    """Encode the RFC 7515 wire representation without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwk(key):
    """Describe a real EC account key using its public coordinates only."""
    numbers = key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64(numbers.x.to_bytes(32, "big")),
        "y": b64(numbers.y.to_bytes(32, "big")),
    }


def jws(key, url, nonce, payload, *, kid=None, patch=None):
    """Sign request bytes without sending or wrapping HTTP calls."""
    protected = {
        "alg": "ES256",
        "url": url,
        "nonce": nonce,
        **({"kid": kid} if kid else {"jwk": jwk(key)}),
        **(patch or {}),
    }
    encoded = b64(json.dumps(protected).encode())
    body = "" if payload is None else b64(json.dumps(payload).encode())
    signature = key.sign((encoded + "." + body).encode(), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(signature)
    return {
        "protected": encoded,
        "payload": body,
        "signature": b64(r.to_bytes(32, "big") + s.to_bytes(32, "big")),
    }


def proof_headers(key, certificate, method, target, body, headers, now, *, patch=None):
    """Produce the exact signed envelope used by Swift, allowing explicit adversarial mutations."""
    proof = {
        "version": 1,
        "certificate_sha256": certificate.fingerprint(hashes.SHA256()).hex(),
        "method": method,
        "target": target,
        "timestamp": int(now.timestamp()),
        "nonce": str(uuid4()),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "authorization_sha256": hashlib.sha256(
            headers.get("Authorization", "").encode()
        ).hexdigest(),
        "session": headers.get("X-Agent-Session"),
        "idempotency_key": headers.get("Idempotency-Key"),
        **(patch or {}),
    }
    raw = json.dumps(proof).encode()
    return {
        **headers,
        "X-PowerCell-Proof": base64.b64encode(raw).decode(),
        "X-PowerCell-Signature": base64.b64encode(
            key.sign(raw, ec.ECDSA(hashes.SHA256()))
        ).decode(),
    }


def certificate(key, issuer_key, subject, issuer, now, *, ca=False, extensions=()):
    """Issue a real X.509 chain with proper CA constraints and key usage."""
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, ca, ca, False, False), critical=True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
    )
    for oid, value in extensions:
        builder = builder.add_extension(x509.UnrecognizedExtension(oid, value), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def attestation(
    pki, key, token, now, *, udid=b"00006000-0012345678901234", nonce=None, extensions=None
):
    """Create an Apple-shaped certificate under the isolated test trust root."""
    values = (
        extensions
        if extensions is not None
        else [
            (UDID_OID, udid),
            (SERIAL_OID, b"MAC-SERIAL"),
            (SEP_OID, b"1.0"),
            (SIP_OID, b"\x01"),
            (NONCE_OID, nonce if nonce is not None else hashlib.sha256(token.encode()).digest()),
        ]
    )
    leaf = certificate(
        key,
        pki.intermediate_key,
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Attested Mac")]),
        pki.intermediate.subject,
        now,
        extensions=values,
    )
    return b64(
        cbor2.dumps(
            {
                "fmt": "apple",
                "attStmt": {
                    "x5c": [
                        leaf.public_bytes(serialization.Encoding.DER),
                        pki.intermediate.public_bytes(serialization.Encoding.DER),
                    ]
                },
            }
        )
    )


@pytest.fixture
def pki(tmp_path, clock):
    """Generate separate issuer and attestation roots; no private fixture keys leave the test directory."""
    root_key, intermediate_key, issuer_key = [
        ec.generate_private_key(ec.SECP256R1()) for _ in range(3)
    ]
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test attestation root")])
    intermediate_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Test attestation intermediate")]
    )
    issuer_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Power Cell CA")])
    root = certificate(root_key, root_key, root_name, root_name, clock.now, ca=True)
    intermediate = certificate(
        intermediate_key, root_key, intermediate_name, root_name, clock.now, ca=True
    )
    issuer = certificate(issuer_key, issuer_key, issuer_name, issuer_name, clock.now, ca=True)
    root_file, issuer_file, key_file = [
        tmp_path / name for name in ["root.pem", "issuer.pem", "issuer.key"]
    ]
    root_file.write_bytes(root.public_bytes(serialization.Encoding.PEM))
    issuer_file.write_bytes(issuer.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        issuer_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return SimpleNamespace(
        root=root,
        root_key=root_key,
        intermediate=intermediate,
        intermediate_key=intermediate_key,
        root_file=root_file,
        issuer=issuer,
        issuer_file=issuer_file,
        key_file=key_file,
    )


@pytest.fixture
def app(settings, clock, pki, monkeypatch):
    """Run the actual application with an explicit disposable PKI trust root."""
    from throng_api import device_security

    monkeypatch.setattr(device_security, "APPLE_ROOT", pki.root_file)
    monkeypatch.setattr(
        device_security, "APPLE_ROOT_SHA256", pki.root.fingerprint(hashes.SHA256()).hex()
    )
    configured = settings.model_copy(
        update={
            "powercell_acme_url": "https://enroll.example/acme/powercell",
            "powercell_ca_certificate": pki.issuer_file,
            "powercell_ca_key": pki.key_file,
            "powercell_secret": "test-only-recovery-secret-" + str(uuid4()),
        }
    )
    return create_app(configured, clock=lambda: clock.now)


@pytest.fixture
async def enrollment(api):
    """Obtain a pending enrollment and the exact restricted profile over authenticated HTTP."""
    payload = {
        "installation_id": str(uuid4()),
        "name": "My Mac",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
        "isolation_modes": ["vm", "shared"],
    }
    key = str(uuid4())
    response = await api.post(
        "/api/v1/agent-enrollments", headers={"Idempotency-Key": key}, json=payload
    )
    assert response.status_code == 201, response.text
    row = response.json()
    response = await api.get(f"/api/v1/agent-enrollments/{row['id']}/profile")
    assert response.status_code == 200, response.text
    profile = plistlib.loads(response.content)
    url = profile["PayloadContent"][0]["DirectoryURL"]
    return SimpleNamespace(
        row=row,
        payload=payload,
        key=key,
        profile=profile,
        base=url.rsplit("/", 1)[0],
        path=urlsplit(url).path.rsplit("/", 1)[0],
    )


@pytest.fixture
async def acme_order(client, enrollment):
    """Register a real JWS account and order through the live ACME service."""
    key = ec.generate_private_key(ec.SECP256R1())
    response = await client.get(enrollment.path + "/directory")
    assert response.status_code == 200, response.text
    response = await client.post(
        enrollment.path + "/new-account",
        json=jws(
            key,
            enrollment.base + "/new-account",
            response.headers["Replay-Nonce"],
            {"termsOfServiceAgreed": True},
        ),
    )
    assert response.status_code == 201, response.text
    account = response.headers["Location"]
    response = await client.post(
        enrollment.path + "/new-order",
        json=jws(
            key,
            enrollment.base + "/new-order",
            response.headers["Replay-Nonce"],
            {"identifiers": [{"type": "permanent-identifier", "value": enrollment.row["id"]}]},
            kid=account,
        ),
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        enrollment.path + "/authorization",
        json=jws(
            key,
            enrollment.base + "/authorization",
            response.headers["Replay-Nonce"],
            None,
            kid=account,
        ),
    )
    assert response.status_code == 200, response.text
    return SimpleNamespace(
        enrollment=enrollment,
        key=key,
        account=account,
        nonce=response.headers["Replay-Nonce"],
        token=response.json()["challenges"][0]["token"],
    )


@pytest.fixture
async def verified_enrollment(client, acme_order, pki, clock):
    """Validate a real certificate chain/freshness and finalize a CSR with its attested key."""
    order = acme_order
    e = order.enrollment
    key = ec.generate_private_key(ec.SECP256R1())
    statement = attestation(pki, key, order.token, clock.now)
    response = await client.post(
        e.path + "/challenge",
        json=jws(
            order.key, e.base + "/challenge", order.nonce, {"attObj": statement}, kid=order.account
        ),
    )
    assert response.status_code == 200, response.text
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, e.row["id"])]))
        .sign(key, hashes.SHA256())
    )
    response = await client.post(
        e.path + "/finalize",
        json=jws(
            order.key,
            e.base + "/finalize",
            response.headers["Replay-Nonce"],
            {"csr": b64(csr.public_bytes(serialization.Encoding.DER))},
            kid=order.account,
        ),
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        e.path + "/certificate",
        json=jws(
            order.key,
            e.base + "/certificate",
            response.headers["Replay-Nonce"],
            None,
            kid=order.account,
        ),
    )
    assert response.status_code == 200, response.text
    return SimpleNamespace(
        order=order,
        key=key,
        certificate=x509.load_pem_x509_certificate(response.content),
        nonce=response.headers["Replay-Nonce"],
        csr=csr,
    )


@pytest.fixture
async def bound_device(api, verified_enrollment, user, clock):
    """Finish user enrollment with a genuine possession signature and preserve the issuance retry key."""
    identity = verified_enrollment
    target = f"/api/v1/agent-enrollments/{identity.order.enrollment.row['id']}/finish"
    headers = {**user["headers"], "Idempotency-Key": str(uuid4())}
    response = await api.post(
        target,
        headers=proof_headers(
            identity.key, identity.certificate, "POST", target, b"{}", headers, clock.now
        ),
        content=b"{}",
    )
    assert response.status_code == 200, response.text
    return SimpleNamespace(
        identity=identity, binding=response.json(), target=target, headers=headers
    )
