"""Clients can distinguish expired credentials from invalid ones during real execution."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from tests.platform.api.token_helpers import move_to_legacy
from throng_api.models import Command

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("owner", ["personal", "company"])
@pytest.mark.parametrize("state", ["unknown", "expired", "revoked", "revoked_expired"])
async def test_command_submission_reports_a_stable_authentication_code(
    api, client, user, company, isolate, company_isolate, app, clock, caplog, owner, state
):
    """Real command submission and preflight must distinguish expiry from invalidity for both owners; revoked-and-expired tokens stay invalid and failures enqueue no command."""
    target = isolate if owner == "personal" else company_isolate
    issued = await api.post(
        "/v1/api-tokens",
        json={
            "user_id" if owner == "personal" else "company_id": user["id"]
            if owner == "personal"
            else company["id"],
            "name": "Execution",
            "scopes": ["read", "execute"],
            "expires_in_seconds": 60,
        },
    )
    assert issued.status_code == 201, issued.text
    token = issued.json()
    if state.startswith("revoked"):
        assert (
            await api.patch(f"/v1/access-tokens/{token['id']}", json={"revoke": True})
        ).status_code == 200
    if "expired" in state:
        clock.now = datetime.fromisoformat(token["expires_at"])
    raw = "thr_unknown" if state == "unknown" else token["token"]
    headers = {"Authorization": "Bearer " + raw, "Idempotency-Key": str(uuid4())}
    async with app.state.db.read() as session:
        before = await session.scalar(select(func.count()).select_from(Command))
    caplog.clear()
    for response in (
        await client.post(
            f"/v1/isolates/{target['id']}/commands", headers=headers, json={"command": "echo hello"}
        ),
        await client.get("/v1/auth/check", headers=headers),
    ):
        assert response.status_code == 401, response.text
        assert response.json() == (
            {"code": "token_expired", "detail": "Token has expired"}
            if state == "expired"
            else {"code": "token_invalid", "detail": "Invalid token"}
        )
        assert raw not in response.text and raw not in caplog.text
        if response.request.method == "GET":
            assert response.headers["cache-control"] == "no-store"
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(Command)) == before


@pytest.mark.parametrize("kind", ["session", "legacy"])
@pytest.mark.parametrize("state", ["expired", "revoked_expired"])
async def test_session_and_historical_credentials_use_the_same_error_contract(
    api, client, user, app, clock, isolate, kind, state
):
    """Expiry classification must work across independent credential stores without mistaking a revoked old login or legacy token for a merely expired credential."""
    if kind == "session":
        token = user["login"]["token"]
        raw = user["login"]["access_token"]
        if state == "revoked_expired":
            assert (await api.delete(f"/v1/auth/sessions/{token['id']}")).status_code == 204
    else:
        response = await api.post(
            "/v1/api-tokens",
            json={
                "user_id": user["id"],
                "name": "History",
                "scopes": ["execute", "read"],
                "expires_in_seconds": 60,
            },
        )
        assert response.status_code == 201
        token = response.json()
        raw = token["token"]
        async with app.state.db.write() as session:
            legacy = await move_to_legacy(session, token["id"])
            if state == "revoked_expired":
                legacy.revoked_at = clock.now
    clock.now = datetime.fromisoformat(token["expires_at"]) + timedelta(seconds=1)
    response = await client.post(
        f"/v1/isolates/{isolate['id']}/commands",
        headers={"Authorization": "Bearer " + raw, "Idempotency-Key": str(uuid4())},
        json={"command": "echo hello"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == ("token_expired" if state == "expired" else "token_invalid")


async def test_execution_openapi_documents_both_authentication_codes(client):
    """Generated clients need the same explicit 401 response contract for command submission and token preflight."""
    schema = (await client.get("/openapi.json")).json()
    for path, method in [("/v1/isolates/{isolate_id}/commands", "post"), ("/v1/auth/check", "get")]:
        assert schema["paths"][path][method]["responses"]["401"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/TokenAuthenticationError"}
    assert set(
        schema["components"]["schemas"]["TokenAuthenticationError"]["properties"]["code"]["enum"]
    ) == {"token_invalid", "token_expired"}
