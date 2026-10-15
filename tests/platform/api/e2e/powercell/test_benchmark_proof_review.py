"""Session-free benchmark uploads retain real device possession verification."""

import asyncio
import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from .conftest import proof_headers

pytestmark = pytest.mark.anyio

TARGET = "/agent/v1/benchmark-results"
BODY = json.dumps(
    {
        "benchmarker_version": "0.0.2",
        "results": [
            {"benchmark_name": "single_core", "number": 15},
            {"benchmark_name": "multi_core", "number": 40},
        ],
    }
).encode()


def headers_for(device):
    return {
        "Authorization": "Bearer " + device.binding["credential"],
        "Content-Type": "application/json",
    }


async def test_attested_benchmark_upload_before_session_requires_fresh_proof_on_retry(
    api, client, bound_device, clock
):
    device = bound_device
    signed = proof_headers(
        device.identity.key,
        device.identity.certificate,
        "PUT",
        TARGET,
        BODY,
        headers_for(device),
        clock.now,
    )
    responses = await asyncio.gather(
        *(client.put(TARGET, headers=signed, content=BODY) for _ in range(2))
    )
    assert sorted(response.status_code for response in responses) == [200, 401], [
        response.text for response in responses
    ]
    rows = next(response.json() for response in responses if response.status_code == 200)
    assert len(rows) == 2
    assert {row["computer_id"] for row in rows} == {device.binding["computer_id"]}
    replay = next(response for response in responses if response.status_code == 401)
    assert replay.json() == {"detail": "Device proof nonce has already been used"}

    response = await client.put(
        TARGET,
        headers=proof_headers(
            device.identity.key,
            device.identity.certificate,
            "PUT",
            TARGET,
            BODY,
            headers_for(device),
            clock.now,
        ),
        content=BODY,
    )
    assert response.status_code == 200, response.text
    assert response.json() == rows
    state = (await api.get(f"/v1/computers/{device.binding['computer_id']}")).json()
    assert state["online"] is False
    assert state["session_id"] is None
    response = await api.get(f"/v1/computers/{device.binding['computer_id']}/benchmark-results")
    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()} == {row["id"] for row in rows}


@pytest.mark.parametrize("attack", ["missing_proof", "unrelated_private_key", "altered_results"])
async def test_session_free_upload_rejects_stolen_credential_or_modified_measurement(
    api, client, bound_device, clock, attack
):
    device = bound_device
    headers = headers_for(device)
    body = BODY
    if attack != "missing_proof":
        key = (
            ec.generate_private_key(ec.SECP256R1())
            if attack == "unrelated_private_key"
            else device.identity.key
        )
        headers = proof_headers(
            key, device.identity.certificate, "PUT", TARGET, BODY, headers, clock.now
        )
        if attack == "altered_results":
            body = BODY.replace(b'"number": 15', b'"number": 999')
            assert body != BODY
    response = await client.put(TARGET, headers=headers, content=body)
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Invalid device proof"}
    response = await api.get(
        f"/v1/computers/{device.binding['computer_id']}/benchmark-results",
        params={"latest_only": False},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_owner_unbind_rejects_signed_benchmark_upload_and_retains_old_measurements(
    api, client, bound_device, clock
):
    device = bound_device
    response = await client.put(
        TARGET,
        headers=proof_headers(
            device.identity.key,
            device.identity.certificate,
            "PUT",
            TARGET,
            BODY,
            headers_for(device),
            clock.now,
        ),
        content=BODY,
    )
    assert response.status_code == 200, response.text
    original = response.json()
    response = await api.post(
        f"/v1/computers/{device.binding['computer_id']}/unbind",
        headers={"Idempotency-Key": str(uuid4())},
        json={"cleanup_confirmed": True},
    )
    assert response.status_code == 200, response.text
    altered = BODY.replace(b'"number": 15', b'"number": 999')
    response = await client.put(
        TARGET,
        headers=proof_headers(
            device.identity.key,
            device.identity.certificate,
            "PUT",
            TARGET,
            altered,
            headers_for(device),
            clock.now,
        ),
        content=altered,
    )
    assert response.status_code == 401, response.text
    response = await api.get(f"/v1/computers/{device.binding['computer_id']}/benchmark-results")
    assert response.status_code == 200, response.text
    assert {row["id"]: row for row in response.json()} == {row["id"]: row for row in original}


async def test_expired_certificate_cannot_upload_with_current_signed_proof(
    api, client, bound_device, clock
):
    device = bound_device
    clock.now = device.identity.certificate.not_valid_after_utc
    response = await client.put(
        TARGET,
        headers=proof_headers(
            device.identity.key,
            device.identity.certificate,
            "PUT",
            TARGET,
            BODY,
            headers_for(device),
            clock.now,
        ),
        content=BODY,
    )
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Invalid device proof"}
    # The owner token expired too; obtain a current owner token to inspect durable state.
    response = await client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "password": "  correct horse battery staple  "},
    )
    assert response.status_code == 200, response.text
    response = await api.get(
        f"/v1/computers/{device.binding['computer_id']}/benchmark-results",
        headers={"Authorization": "Bearer " + response.json()["access_token"]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []
