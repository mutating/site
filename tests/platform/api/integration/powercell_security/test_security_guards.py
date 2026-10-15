"""Real cryptography and database fault states complement the HTTP enrollment scenarios."""

import base64
import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import cbor2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import delete

from tests.platform.api.e2e.powercell.conftest import attestation, certificate, proof_headers
from throng_api import device_security
from throng_api.device_security import NONCE_OID, SEP_OID, SERIAL_OID, SIP_OID, UDID_OID
from throng_api.enrollment import configure_authority
from throng_api.models import (
    AgentEnrollment,
    ComputerOwnership,
    DeviceCertificate,
    ProtocolNonce,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "changes",
    [
        {"powercell_acme_url": "https://example.org/acme/powercell", "powercell_ca_key": None},
        {"powercell_acme_url": "http://example.org/acme/powercell"},
        {"powercell_acme_url": "https://user@example.org/acme/powercell"},
        {"powercell_acme_url": "https://example.org/acme/powercell?bad=1"},
        {"powercell_ca_key": "wrong-key"},
    ],
)
async def test_issuer_configuration_fails_closed(app, pki, tmp_path, changes):
    """Partial, insecure and mismatched issuer configuration cannot create an operational authority."""
    if changes.get("powercell_ca_key") == "wrong-key":
        file = tmp_path / "wrong.key"
        file.write_bytes(
            ec.generate_private_key(ec.SECP256R1()).private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        changes = {**changes, "powercell_ca_key": file}
    with pytest.raises(ValueError):
        configure_authority(app.state.settings.model_copy(update=changes))


@pytest.mark.parametrize(
    "case", ["format", "chain", "anchor", "curve", "blank_identity", "blank_sep"]
)
async def test_attestation_structure_and_certified_properties(app, pki, clock, monkeypatch, case):
    """Untrusted structure, trust-anchor changes, unsupported keys and blank certified properties are rejected."""
    token = "fresh challenge"
    key = ec.generate_private_key(ec.SECP384R1() if case == "curve" else ec.SECP256R1())
    fields = [
        (UDID_OID, b"" if case == "blank_identity" else b"MAC"),
        (SERIAL_OID, b"SERIAL"),
        (SEP_OID, b"" if case == "blank_sep" else b"1"),
        (SIP_OID, b"\x01"),
        (NONCE_OID, hashlib.sha256(token.encode()).digest()),
    ]
    encoded = base64.urlsafe_b64decode(
        attestation(pki, key, token, clock.now, extensions=fields) + "=="
    )
    if case == "format":
        encoded = cbor2.dumps({"fmt": "not-apple"})
    if case == "chain":
        encoded = cbor2.dumps({"fmt": "apple", "attStmt": {"x5c": []}})
    if case == "anchor":
        monkeypatch.setattr(device_security, "APPLE_ROOT_SHA256", "0" * 64)
    with pytest.raises(ValueError):
        device_security.validate_attestation(encoded, token, clock.now)


@pytest.mark.parametrize("content", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
async def test_security_json_rejects_ambiguous_and_nonfinite_inputs(content):
    """Signed JSON cannot rely on parser disagreements about duplicate members or nonfinite numbers."""
    with pytest.raises(ValueError):
        device_security.strict_json(content)


@pytest.mark.parametrize("state", ["expired", "revoked", "future", "missing"])
async def test_certificate_lifetime_and_revocation_are_enforced(
    client, bound_device, app, clock, state
):
    """Possession alone cannot authorize an expired, revoked, absent or not-yet-valid stored identity."""
    identity = bound_device.identity
    sha = bound_device.binding["certificate_sha256"]
    async with app.state.db.write() as session:
        row = await session.get(DeviceCertificate, sha)
        if state == "expired":
            row.expires_at = clock.now
        if state == "revoked":
            row.revoked_at = clock.now
        if state == "missing":
            await session.delete(row)
        if state == "future":
            cert = certificate(
                identity.key,
                identity.key,
                identity.certificate.subject,
                identity.certificate.subject,
                clock.now + timedelta(days=3),
            )
            row.pem = cert.public_bytes(serialization.Encoding.PEM)
    headers = {"Authorization": "Bearer " + bound_device.binding["credential"]}
    response = await client.get(
        "/agent/v1/session",
        headers=proof_headers(
            identity.key, identity.certificate, "GET", "/agent/v1/session", b"", headers, clock.now
        ),
    )
    assert response.status_code == 401


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_expired_nonce_can_be_reused_only_with_a_fresh_signature(
    client, bound_device, app, clock
):
    """An expired persisted replay marker is replaced under the same transaction as fresh verification."""
    identity = bound_device.identity
    nonce = str(uuid4())
    async with app.state.db.write() as session:
        session.add(
            ProtocolNonce(
                namespace=bound_device.binding["certificate_sha256"],
                value=nonce,
                expires_at=clock.now - timedelta(seconds=1),
            )
        )
    headers = {"Authorization": "Bearer " + bound_device.binding["credential"]}
    response = await client.get(
        "/agent/v1/session",
        headers=proof_headers(
            identity.key,
            identity.certificate,
            "GET",
            "/agent/v1/session",
            b"",
            headers,
            clock.now,
            patch={"nonce": nonce},
        ),
    )
    assert response.status_code == 200


async def test_revocation_between_proof_and_handler_is_rechecked(
    client, bound_device, app, clock, monkeypatch
):
    """Credential use is checked again after the separately committed proof transaction."""
    original = device_security.verify_proof

    async def revoke_after_proof(session, request, certificate_hash, **kwargs):
        await original(session, request, certificate_hash, **kwargs)
        row = await session.get(DeviceCertificate, certificate_hash)
        row.revoked_at = clock.now

    monkeypatch.setattr(device_security, "verify_proof", revoke_after_proof)
    identity = bound_device.identity
    headers = {"Authorization": "Bearer " + bound_device.binding["credential"]}
    response = await client.get(
        "/agent/v1/session",
        headers=proof_headers(
            identity.key, identity.certificate, "GET", "/agent/v1/session", b"", headers, clock.now
        ),
    )
    assert response.status_code == 401


async def test_signed_diagnostics_are_bounded_without_a_session(client, bound_device, app, clock):
    """Device verification also covers deferred uploads and enforces their smaller body limit."""
    identity = bound_device.identity
    identifier = str(uuid4())
    target = "/agent/v1/diagnostics/" + identifier
    headers = {"Authorization": "Bearer " + bound_device.binding["credential"]}
    content = json.dumps(
        {"id": identifier, "created": clock.now.isoformat(), "message": "late error"}
    ).encode()
    response = await client.put(
        target,
        headers=proof_headers(
            identity.key, identity.certificate, "PUT", target, content, headers, clock.now
        ),
        content=content,
    )
    assert response.status_code == 200, response.text
    app.state.settings.max_diagnostic_bytes = 10
    response = await client.put(
        target,
        headers=proof_headers(
            identity.key, identity.certificate, "PUT", target, content, headers, clock.now
        ),
        content=content,
    )
    assert response.status_code == 413


async def test_persisted_credentials_are_encrypted_and_revoked_finish_is_not_recoverable(
    api, bound_device, app, clock
):
    """Enrollment stores encrypted retry material and refuses recovery after normal credential rotation."""
    identity = bound_device.identity
    async with app.state.db.read() as session:
        row = await session.get(AgentEnrollment, identity.order.enrollment.row["id"])
        assert bound_device.binding["credential"].encode() not in row.credential_encrypted
    response = await api.post(f"/v1/computers/{bound_device.binding['computer_id']}/credentials")
    assert response.status_code == 201
    response = await api.post(
        bound_device.target,
        headers=proof_headers(
            identity.key,
            identity.certificate,
            "POST",
            bound_device.target,
            b"{}",
            bound_device.headers,
            clock.now,
        ),
        content=b"{}",
    )
    assert response.status_code == 409


async def test_enrollment_finish_rejects_unverified_and_invalid_retry_input(
    api, user, verified_enrollment, enrollment, clock
):
    """Finalization requires a verified enrollment, an empty body and a bounded idempotency key."""
    identity = verified_enrollment
    target = f"/v1/agent-enrollments/{enrollment.row['id']}/finish"
    headers = {**user["headers"], "Idempotency-Key": "x" * 129}
    response = await api.post(
        target,
        headers=proof_headers(
            identity.key, identity.certificate, "POST", target, b"{}", headers, clock.now
        ),
        content=b"{}",
    )
    assert response.status_code == 422
    response = await api.post(
        target, headers={"Idempotency-Key": str(uuid4())}, content=b'{"extra":true}'
    )
    assert response.status_code == 422
    clock.now += timedelta(hours=2)
    response = await api.post(target, headers={"Idempotency-Key": str(uuid4())}, content=b"{}")
    assert response.status_code == 409


async def test_enrollment_quota_bounds_pending_records(api, enrollment):
    """An authenticated user cannot accumulate an unbounded number of live ACME challenges."""
    for i in range(20):
        response = await api.post(
            "/v1/agent-enrollments",
            headers={"Idempotency-Key": str(uuid4())},
            json={**enrollment.payload, "installation_id": str(uuid4())},
        )
        assert response.status_code == (201 if i < 19 else 429)


@pytest.mark.parametrize("membership", ["admin"], indirect=True)
async def test_diagnostics_do_not_follow_transferred_ownership(
    api, other_api, client, agent, computer, company, membership, clock
):
    """A new computer owner cannot read failure logs captured for the previous owner."""
    identifier = str(uuid4())
    response = await client.put(
        "/agent/v1/diagnostics/" + identifier,
        headers=agent["headers"],
        json={"id": identifier, "created": clock.now.isoformat(), "message": "private old log"},
    )
    assert response.status_code == 200
    response = await api.patch(
        f"/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200
    response = await other_api.get(f"/v1/computers/{computer['id']}/diagnostics/{identifier}")
    assert response.status_code == 404


async def test_ownerless_cleanup_and_diagnostic_failure(client, agent, app, computer, clock):
    """A partially detached computer can clean up, but cannot create ownerless diagnostic content."""
    async with app.state.db.write() as session:
        await session.execute(
            delete(ComputerOwnership).where(ComputerOwnership.computer_id == computer["id"])
        )
    identifier = str(uuid4())
    response = await client.put(
        "/agent/v1/diagnostics/" + identifier,
        headers=agent["headers"],
        json={"id": identifier, "created": clock.now.isoformat(), "message": "no owner"},
    )
    assert response.status_code == 409
    response = await client.post(
        "/agent/v1/unbind",
        headers={**agent["headers"], "Idempotency-Key": str(uuid4())},
        json={"cleanup_confirmed": True},
    )
    assert response.status_code == 200


async def test_expired_issuer_cannot_sign_even_a_verified_key(app, clock):
    """A configured CA ceases issuance at its own expiry, regardless of requested certificate duration."""
    from types import SimpleNamespace

    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .sign(key, hashes.SHA256())
    )
    with pytest.raises(ValueError, match="not currently valid"):
        app.state.powercell_authority.issue(
            SimpleNamespace(id=str(uuid4())), csr, clock.now + timedelta(days=3651)
        )


async def test_capacity_requires_advertised_isolation_and_complete_inventory(
    api, client, agent, computer, computer_resources, app
):
    """Invalid local defaults or deleted inventory cannot be accepted as a new scheduler policy."""
    from throng_api.models import Computer

    async with app.state.db.write() as session:
        row = await session.get(Computer, computer["id"])
        row.isolation_modes = ["vm"]
    payload = {
        "revision": 0,
        "accepting_work": True,
        "mode": "idle",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
        "running_commands": 0,
        "cpu_used_millis": 0,
        "memory_used_bytes": 0,
        "default_isolation": "shared",
    }
    response = await client.post(
        "/agent/v1/capacity",
        headers={**agent["headers"], "Idempotency-Key": str(uuid4())},
        json=payload,
    )
    assert response.status_code == 422
    response = await api.delete(f"/v1/computer-resources/{computer_resources['cpu']['id']}")
    assert response.status_code == 204
    response = await client.post(
        "/agent/v1/capacity",
        headers={**agent["headers"], "Idempotency-Key": str(uuid4())},
        json={**payload, "default_isolation": "vm"},
    )
    assert response.status_code == 409


async def test_scheduler_skips_an_unadvertised_isolation_mode(
    api, client, agent, app, computer, isolate_payload
):
    """An unsupported plugin mode stays unassigned without blocking a compatible isolate behind it."""
    from throng_api.models import Computer

    async with app.state.db.write() as session:
        row = await session.get(Computer, computer["id"])
        row.isolation_modes = ["vm"]
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={**isolate_payload, "isolation_mode": "shared"},
    )
    assert response.status_code == 202
    waiting = response.json()["id"]
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={**isolate_payload, "isolation_mode": "vm"},
    )
    assert response.status_code == 202
    compatible = response.json()["id"]
    response = await client.post(
        "/agent/v1/tasks/claim", headers=agent["headers"], json={"wait_seconds": 2}
    )
    assert response.status_code == 200
    assert [t["isolate_id"] for t in response.json()["tasks"]] == [compatible]
    assert (await api.get("/v1/isolates/" + waiting)).json()["computer_id"] is None
