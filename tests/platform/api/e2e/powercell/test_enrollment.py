"""ACME enrollment interoperability and attacks against device-bound credentials."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .conftest import attestation, b64, jws, proof_headers

pytestmark = pytest.mark.anyio


async def test_profile_capabilities_and_enrollment_ownership(
    client, api, other_api, enrollment, clock
):
    """The authorized profile contains only a nonextractable attested identity and cannot be read by another account."""
    response = await client.get("/v1/agent-capabilities")
    assert len(response.json()["extensions"]) == 5 and response.json()["attestation_required"]
    profile = enrollment.profile
    assert profile["PayloadType"] == "Configuration" and len(profile["PayloadContent"]) == 1
    assert profile["PayloadScope"] == "User"
    acme = profile["PayloadContent"][0]
    assert (
        acme["PayloadType"] == "com.apple.security.acme"
        and acme["Attest"]
        and acme["HardwareBound"]
    )
    assert not acme["KeyIsExtractable"] and not acme["AllowAllAppsAccess"]
    assert acme["KeyType"] == "ECSECPrimeRandom" and acme["KeySize"] == 256
    for suffix in ["", "/profile"]:
        response = await other_api.get(f"/v1/agent-enrollments/{enrollment.row['id']}" + suffix)
        assert response.status_code == 404
    response = await api.post(
        "/v1/agent-enrollments",
        headers={"Idempotency-Key": enrollment.key},
        json=enrollment.payload,
    )
    assert response.status_code == 201 and response.json() == enrollment.row
    response = await api.post(
        "/v1/agent-enrollments",
        headers={"Idempotency-Key": enrollment.key},
        json={**enrollment.payload, "name": "different"},
    )
    assert response.status_code == 409
    clock.now += timedelta(hours=2)
    assert (await api.get(f"/v1/agent-enrollments/{enrollment.row['id']}")).json()[
        "status"
    ] == "expired"
    assert (
        await api.get(f"/v1/agent-enrollments/{enrollment.row['id']}/profile")
    ).status_code == 409
    assert (await client.get(enrollment.path + "/directory")).status_code == 401


async def test_attested_finish_requires_key_and_retries_recover_same_credential(
    api, other_api, user, bound_device, clock
):
    """Even verified enrollment needs its private key, and exact signed retries recover only the original binding."""
    device = bound_device
    identity = device.identity
    response = await api.post(device.target, headers=device.headers, content=b"{}")
    assert response.status_code == 401
    headers = proof_headers(
        identity.key, identity.certificate, "POST", device.target, b"{}", device.headers, clock.now
    )
    response = await api.post(device.target, headers=headers, content=b"{}")
    assert response.status_code == 200 and response.json() == device.binding
    response = await api.post(device.target, headers=headers, content=b"{}")
    assert response.status_code == 401
    different = {**device.headers, "Idempotency-Key": str(uuid4())}
    response = await api.post(
        device.target,
        headers=proof_headers(
            identity.key, identity.certificate, "POST", device.target, b"{}", different, clock.now
        ),
        content=b"{}",
    )
    assert response.status_code == 409
    response = await other_api.post(
        device.target, headers={"Idempotency-Key": str(uuid4())}, content=b"{}"
    )
    assert response.status_code == 404
    response = await api.get(device.target.removesuffix("/finish") + "/profile")
    assert response.status_code == 409


async def test_signed_session_capacity_and_revoked_unbind_retry(client, bound_device, clock):
    """Copied credentials cannot start sessions; signed logout retries remain possible after revocation."""
    device = bound_device
    identity = device.identity
    headers = {
        "Authorization": "Bearer " + device.binding["credential"],
        "Content-Type": "application/json",
        "Idempotency-Key": str(uuid4()),
    }
    target = "/agent/v1/session"
    body = b'{"clean_start":true}'
    response = await client.post(target, headers=headers, content=body)
    assert response.status_code == 401
    signed = proof_headers(
        identity.key, identity.certificate, "POST", target, body, headers, clock.now
    )
    responses = await asyncio.gather(
        *(client.post(target, headers=signed, content=body) for _ in range(2))
    )
    assert sorted(r.status_code for r in responses) == [200, 401], [r.text for r in responses]
    session = next(r.json() for r in responses if r.status_code == 200)
    headers["X-Agent-Session"] = session["session_id"]
    target = "/agent/v1/capacity"
    body = json.dumps(
        {
            "revision": 0,
            "accepting_work": True,
            "mode": "idle",
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
            "running_commands": 0,
            "cpu_used_millis": 0,
            "memory_used_bytes": 0,
        }
    ).encode()
    response = await client.post(
        target,
        headers=proof_headers(
            identity.key, identity.certificate, "POST", target, body, headers, clock.now
        ),
        content=body,
    )
    assert response.status_code == 200, response.text
    target = "/agent/v1/unbind"
    body = b'{"cleanup_confirmed":true}'
    for _ in range(2):
        response = await client.post(
            target,
            headers=proof_headers(
                identity.key, identity.certificate, "POST", target, body, headers, clock.now
            ),
            content=body,
        )
        assert response.status_code == 200, response.text
    response = await client.post(target, headers=headers, content=body)
    assert response.status_code == 401
    response = await client.get(
        "/agent/v1/session",
        headers=proof_headers(
            identity.key, identity.certificate, "GET", "/agent/v1/session", b"", headers, clock.now
        ),
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "patch",
    [
        {"version": True},
        {"version": 2},
        {"timestamp": 0},
        {"timestamp": True},
        {"nonce": "invalid"},
        {"certificate_sha256": "0" * 64},
        {"method": "POST"},
        {"target": "/agent/v1/heartbeat"},
        {"body_sha256": "0" * 64},
        {"authorization_sha256": "0" * 64},
        {"session": str(uuid4())},
        {"idempotency_key": "different"},
        {"extra": True},
    ],
)
async def test_request_proof_rejects_context_substitution(client, bound_device, clock, patch):
    """Every signed context field and timestamp is validated against the actual HTTP request."""
    identity = bound_device.identity
    target = "/agent/v1/session"
    headers = {"Authorization": "Bearer " + bound_device.binding["credential"]}
    signed = proof_headers(
        identity.key, identity.certificate, "GET", target, b"", headers, clock.now, patch=patch
    )
    response = await client.get(target, headers=signed)
    assert response.status_code == 401, response.text


@pytest.mark.parametrize(
    "mutation", ["wrong_nonce", "untrusted_root", "wrong_key", "malformed", "missing_identity"]
)
async def test_attestation_and_csr_cannot_substitute_device(
    client, api, acme_order, pki, clock, mutation
):
    """Only a fresh trusted device chain followed by a matching CSR can become verified."""
    order = acme_order
    e = order.enrollment
    key = ec.generate_private_key(ec.SECP256R1())
    kwargs = {}
    if mutation == "wrong_nonce":
        kwargs["nonce"] = b"0" * 32
    if mutation == "missing_identity":
        kwargs["extensions"] = []
    if mutation == "untrusted_root":
        pki.intermediate_key = ec.generate_private_key(ec.SECP256R1())
    statement = attestation(pki, key, order.token, clock.now, **kwargs)
    if mutation == "malformed":
        statement = b64(b"not CBOR")
    response = await client.post(
        e.path + "/challenge",
        json=jws(
            order.key, e.base + "/challenge", order.nonce, {"attObj": statement}, kid=order.account
        ),
    )
    if mutation == "wrong_key":
        assert response.status_code == 200
        other_key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([]))
            .sign(other_key, hashes.SHA256())
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
    assert response.status_code == 400, response.text
    response = await api.get(f"/v1/agent-enrollments/{e.row['id']}")
    assert response.json()["status"] == "pending_attestation"


async def test_acme_nonces_signatures_and_identifiers_are_bound(client, acme_order):
    """ACME rejects nonce replay, unsigned requests, altered targets and unrelated identifiers."""
    order = acme_order
    e = order.enrollment
    payload = jws(order.key, e.base + "/order", order.nonce, None, kid=order.account)
    response = await client.post(e.path + "/order", json=payload)
    assert response.status_code == 200
    response = await client.post(e.path + "/order", json=payload)
    assert response.status_code == 400 and response.json()["type"].endswith(":badNonce")
    nonce = response.headers["Replay-Nonce"]
    payload = jws(order.key, e.base + "/order", nonce, None, kid=order.account)
    response = await client.post(e.path + "/authorization", json=payload)
    assert response.status_code == 401
    payload = jws(order.key, e.base + "/order", nonce, None, kid=order.account)
    payload["signature"] = b64(b"0" * 64)
    response = await client.post(e.path + "/order", json=payload)
    assert response.status_code == 401
    response = await client.post(
        e.path + "/new-order",
        json=jws(
            order.key,
            e.base + "/new-order",
            nonce,
            {"identifiers": [{"type": "dns", "value": "example.org"}]},
            kid=order.account,
        ),
    )
    assert response.status_code == 400
    response = await client.post(e.path + "/order", json={})
    assert response.status_code == 401


async def test_proof_query_and_signature_are_checked(client, bound_device, clock):
    """Proofs cover the encoded query and reject a signature from another key."""
    identity = bound_device.identity
    target = "/agent/v1/session?original=%2F"
    headers = {"Authorization": "Bearer " + bound_device.binding["credential"]}
    signed = proof_headers(
        identity.key, identity.certificate, "GET", target, b"", headers, clock.now
    )
    assert (await client.get(target, headers=signed)).status_code == 200
    signed = proof_headers(
        identity.key, identity.certificate, "GET", target, b"", headers, clock.now
    )
    assert (await client.get("/agent/v1/session?original=/", headers=signed)).status_code == 401
    signed = proof_headers(
        ec.generate_private_key(ec.SECP256R1()),
        identity.certificate,
        "GET",
        target,
        b"",
        headers,
        clock.now,
    )
    assert (await client.get(target, headers=signed)).status_code == 401
    signed["X-PowerCell-Proof"] = "!"
    assert (await client.get(target, headers=signed)).status_code == 401


async def test_reinstallation_and_another_account_share_one_physical_identity(
    client, other_api, other_user, bound_device, pki, clock
):
    """Another installation/account receives independent credentials while the Apple-certified device ID remains stable."""
    response = await other_api.post(
        "/v1/agent-enrollments",
        headers={"Idempotency-Key": str(uuid4())},
        json={**bound_device.identity.order.enrollment.payload, "installation_id": str(uuid4())},
    )
    assert response.status_code == 201
    enrollment = response.json()
    response = await other_api.get(f"/v1/agent-enrollments/{enrollment['id']}/profile")
    import plistlib
    from urllib.parse import urlsplit

    base = plistlib.loads(response.content)["PayloadContent"][0]["DirectoryURL"].rsplit("/", 1)[0]
    endpoint = urlsplit(base).path
    account_key = ec.generate_private_key(ec.SECP256R1())
    device_key = ec.generate_private_key(ec.SECP256R1())
    response = await client.head(endpoint + "/nonce")
    response = await client.post(
        endpoint + "/new-account",
        json=jws(account_key, base + "/new-account", response.headers["Replay-Nonce"], {}),
    )
    assert response.status_code == 201
    account = response.headers["Location"]
    response = await client.post(
        endpoint + "/new-order",
        json=jws(
            account_key,
            base + "/new-order",
            response.headers["Replay-Nonce"],
            {"identifiers": [{"type": "permanent-identifier", "value": enrollment["id"]}]},
            kid=account,
        ),
    )
    assert response.status_code == 201
    response = await client.post(
        endpoint + "/authorization",
        json=jws(
            account_key,
            base + "/authorization",
            response.headers["Replay-Nonce"],
            None,
            kid=account,
        ),
    )
    token = response.json()["challenges"][0]["token"]
    response = await client.post(
        endpoint + "/challenge",
        json=jws(
            account_key,
            base + "/challenge",
            response.headers["Replay-Nonce"],
            {"attObj": attestation(pki, device_key, token, clock.now)},
            kid=account,
        ),
    )
    assert response.status_code == 200
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .sign(device_key, hashes.SHA256())
    )
    response = await client.post(
        endpoint + "/finalize",
        json=jws(
            account_key,
            base + "/finalize",
            response.headers["Replay-Nonce"],
            {"csr": b64(csr.public_bytes(serialization.Encoding.DER))},
            kid=account,
        ),
    )
    assert response.status_code == 200
    response = await client.post(
        endpoint + "/certificate",
        json=jws(
            account_key, base + "/certificate", response.headers["Replay-Nonce"], None, kid=account
        ),
    )
    certificate = x509.load_pem_x509_certificate(response.content)
    target = f"/v1/agent-enrollments/{enrollment['id']}/finish"
    headers = {**other_user["headers"], "Idempotency-Key": str(uuid4())}
    response = await other_api.post(
        target,
        headers=proof_headers(device_key, certificate, "POST", target, b"{}", headers, clock.now),
        content=b"{}",
    )
    assert response.status_code == 200, response.text
    assert response.json()["device_id"] == bound_device.binding["device_id"]
    assert response.json()["computer_id"] != bound_device.binding["computer_id"]
    assert response.json()["credential"] != bound_device.binding["credential"]
    # Deleting either binding cannot delete the shared physical billing identity.
    binding = response.json()
    headers = {
        "Authorization": "Bearer " + binding["credential"],
        "Content-Type": "application/json",
        "Idempotency-Key": str(uuid4()),
    }
    target = "/agent/v1/unbind"
    body = b'{"cleanup_confirmed":true}'
    response = await client.post(
        target,
        headers=proof_headers(device_key, certificate, "POST", target, body, headers, clock.now),
        content=body,
    )
    assert response.status_code == 200
    headers = {"Authorization": "Bearer " + bound_device.binding["credential"]}
    target = "/agent/v1/session"
    response = await client.get(
        target,
        headers=proof_headers(
            bound_device.identity.key,
            bound_device.identity.certificate,
            "GET",
            target,
            b"",
            headers,
            clock.now,
        ),
    )
    assert response.status_code == 200
