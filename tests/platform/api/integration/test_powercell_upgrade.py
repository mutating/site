"""Compatibility and trust boundaries for databases created before Power Cell enrollment."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from throng_api.execution import fingerprint
from throng_api.models import Computer, Idempotency

pytestmark = pytest.mark.anyio


async def test_old_powercell_rows_cannot_keep_legacy_bearer_authentication(
    app, agent_api, computer
):
    """A Power Cell runtime registered before migration must enroll instead of retaining unsigned access."""
    async with app.state.db.write() as session:
        row = await session.get(Computer, computer["id"])
        row.runtime = "powercell-ubuntu-arm64-v1"
    response = await agent_api.get("/agent/v1/session")
    assert response.status_code == 401
    assert response.json()["detail"] == "Power Cell requires attested enrollment"


async def test_optional_isolation_fields_preserve_preupgrade_idempotency(api, app, isolate_payload):
    """A stored pre-extension request hash still recovers its isolate after a response was lost across upgrade."""
    key = str(uuid4())
    response = await api.post(
        "/api/v1/isolates", headers={"Idempotency-Key": key}, json=isolate_payload
    )
    assert response.status_code == 202
    isolate = response.json()
    legacy_payload = {"company_id": None, **isolate_payload}
    async with app.state.db.write() as session:
        row = await session.scalar(
            select(Idempotency).where(
                Idempotency.key == key, Idempotency.operation == "create_isolate"
            )
        )
        # This is the persisted representation from the former v1 schema.
        row.request_hash = fingerprint(legacy_payload)
    for fields in [{}, {"isolation_mode": None, "retry_allowed": True}]:
        response = await api.post(
            "/api/v1/isolates", headers={"Idempotency-Key": key}, json={**isolate_payload, **fields}
        )
        assert response.status_code == 202 and response.json()["id"] == isolate["id"]
    response = await api.post(
        "/api/v1/isolates",
        headers={"Idempotency-Key": key},
        json={**isolate_payload, "retry_allowed": False},
    )
    assert response.status_code == 409
