"""Recovery revokes real attested device certificates without requiring the lost private key."""

from datetime import timedelta
from uuid import uuid4

import pytest

from .conftest import proof_headers

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("expired", [False, True])
async def test_account_recovery_revokes_attested_binding(api, client, bound_device, clock, expired):
    """A fresh account login can detach an expired or inaccessible identity while retaining device history."""
    device = bound_device
    if expired:
        clock.now += timedelta(days=366)
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "  correct horse battery staple  "},
        )
        assert response.status_code == 200
        api.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    target = f"/api/v1/computers/{device.binding['computer_id']}/unbind"
    for _ in range(2):
        response = await api.post(
            target, headers={"Idempotency-Key": "owner-recovery"}, json={"cleanup_confirmed": True}
        )
        assert response.status_code == 200, response.text
    state = (await api.get(f"/api/v1/computers/{device.binding['computer_id']}")).json()
    assert state["device_id"] == device.binding["device_id"]
    assert state["certificate_sha256"] == device.binding["certificate_sha256"]
    assert state["session_id"] is None
    # Even the original private key no longer authorizes this revoked binding.
    target = "/agent/v1/session"
    headers = {
        "Authorization": "Bearer " + device.binding["credential"],
        "Idempotency-Key": str(uuid4()),
        "Content-Type": "application/json",
    }
    body = b'{"clean_start":true}'
    response = await client.post(
        target,
        headers=proof_headers(
            device.identity.key,
            device.identity.certificate,
            "POST",
            target,
            body,
            headers,
            clock.now,
        ),
        content=body,
    )
    assert response.status_code == 401
    # The old certificate cannot recover the credential by replaying enrollment finish, either.
    target = device.target
    response = await api.post(
        target,
        headers=proof_headers(
            device.identity.key,
            device.identity.certificate,
            "POST",
            target,
            b"{}",
            {
                "Authorization": api.headers["Authorization"],
                "Idempotency-Key": device.headers["Idempotency-Key"],
                "Content-Type": "application/json",
            },
            clock.now,
        ),
        content=b"{}",
    )
    assert response.status_code in {401, 409}
