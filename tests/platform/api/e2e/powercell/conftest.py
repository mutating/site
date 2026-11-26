"""Shared Power Cell registration fixtures without hardware attestation."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from throng_api.app import create_app


@pytest.fixture
def app(settings, clock):
    """Run the real API with an explicit deployment-style enrollment secret."""
    configured = settings.model_copy(
        update={"enrollment_secret": "test-enrollment-secret-" + uuid4().hex}
    )
    return create_app(configured, clock=lambda: clock.now)


def proof_headers(_key, _certificate, _method, _target, _body, headers, _now, **_kwargs):
    """Legacy callers now send ordinary bearer requests; no proof headers exist."""
    return dict(headers)


async def register_device(api, device_id, **overrides):
    """Create and finish a registration for a device already linked at login."""
    key = str(uuid4())
    payload = {
        "device_id": str(device_id),
        "name": "My Mac",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
        "isolation_modes": ["vm", "shared"],
        **overrides,
    }
    response = await api.post(
        "/v1/agent-enrollments", headers={"Idempotency-Key": key}, json=payload
    )
    assert response.status_code == 201, response.text
    enrollment = response.json()
    target = f"/v1/agent-enrollments/{enrollment['id']}/finish"
    headers = {"Idempotency-Key": "finish-" + enrollment["id"]}
    response = await api.post(target, headers=headers, json={})
    assert response.status_code == 200, response.text
    return SimpleNamespace(
        enrollment=enrollment,
        binding=response.json(),
        target=target,
        headers=headers,
        device_id=str(device_id),
        identity=SimpleNamespace(key=None, certificate=None),
    )


@pytest.fixture
async def enrollment(api):
    """Create a pending registration whose device was announced by the account."""
    device_id = uuid4()
    response = await api.post(
        "/v1/auth/login",
        json={
            "login": "alice",
            "password": "  correct horse battery staple  ",
            "device_id": str(device_id),
        },
    )
    assert response.status_code == 200, response.text
    payload = {
        "device_id": str(device_id),
        "name": "My Mac",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
        "isolation_modes": ["vm", "shared"],
    }
    key = str(uuid4())
    response = await api.post(
        "/v1/agent-enrollments", headers={"Idempotency-Key": key}, json=payload
    )
    assert response.status_code == 201, response.text
    return SimpleNamespace(row=response.json(), payload=payload, key=key, device_id=device_id)


@pytest.fixture
async def bound_device(api, user):
    """Register one computer for Alice and put it in her personal pool.

    Most session, CI and pool tests predate the explicit unassigned state and
    describe an installation that already chose a pool. The registration tests
    exercise the unassigned default separately.
    """
    device_id = uuid4()
    response = await api.post(
        "/v1/auth/login",
        json={
            "login": "alice",
            "password": "  correct horse battery staple  ",
            "device_id": str(device_id),
        },
    )
    assert response.status_code == 200, response.text
    device = await register_device(api, device_id)
    moved = await api.patch(
        f"/v1/connected-computers/{device.binding['computer_id']}/pool",
        json={"company_id": None, "expected_company_id": None},
    )
    assert moved.status_code == 200, moved.text
    return device


@pytest.fixture
async def verified_enrollment(bound_device):
    """Compatibility alias for tests written around the old verified step."""
    return bound_device
