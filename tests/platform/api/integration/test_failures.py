"""Database constraints and failure injection supplement the public HTTP e2e suite."""

import asyncio

import anyio
import pytest
from alembic import command as alembic
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from throng_api.models import AccessToken, ComputerOwnership, TokenOwnership, User

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("uncertain_commit", [RuntimeError, asyncio.CancelledError], indirect=True)
@pytest.mark.parametrize("agent_upload", [False, True])
async def test_committed_artifact_survives_lost_commit_acknowledgement(
    api, agent_api, run_task, uncertain_commit, agent_upload, settings
):
    """A post-COMMIT exception cannot delete persisted content, including after an agent upload retry."""
    path = f"/agent/v1/tasks/{run_task['id']}/artifacts/stdout" if agent_upload else "/v1/artifacts"
    response = await (
        agent_api.put(path, content=b"durable output")
        if agent_upload
        else api.post(path, content=b"durable output")
    )
    assert response.status_code == 500
    uncertain_commit["enabled"] = False
    artifacts = (await api.get("/v1/artifacts")).json()
    artifact = next(row for row in artifacts if row["size_bytes"] == 14)
    if agent_upload:
        response = await agent_api.put(path, content=b"durable output")
        assert response.status_code == 200 and response.json()["id"] == artifact["id"]
    response = await api.get(f"/v1/artifacts/{artifact['id']}/content")
    assert response.status_code == 200 and response.content == b"durable output"
    assert len(list(settings.storage_path.iterdir())) == 2


@pytest.mark.parametrize("both", [False, True])
async def test_ownership_xor_is_enforced_below_the_http_layer(app, user, company, computer, both):
    """Database constraints reject zero or two owners even when API validation is bypassed."""
    values = {"user_id": user["id"], "company_id": company["id"]} if both else {}
    for model, extra in [
        (TokenOwnership, {}),
        (ComputerOwnership, {"computer_id": computer["id"]}),
    ]:
        with pytest.raises(IntegrityError):
            async with app.state.db.write() as session:
                if model is ComputerOwnership:
                    row = await session.get(ComputerOwnership, computer["ownership"]["id"])
                    await session.delete(row)
                    await session.flush()
                session.add(model(**values, **extra))
                await session.flush()
    async with app.state.db.read() as session:
        assert await session.get(ComputerOwnership, computer["ownership"]["id"]) is not None


async def test_credentials_are_hashed_at_rest(app, user):
    """The database stores Argon2 password hashes and token digests instead of recoverable secrets."""
    async with app.state.db.read() as session:
        row = await session.get(User, user["id"])
        token = await session.scalar(
            select(AccessToken).where(AccessToken.id == user["login"]["token"]["id"])
        )
        assert row.password_hash.startswith("$argon2id$")
        assert row.password_hash != user["password"]
        assert user["login"]["access_token"] not in token.token_hash
        assert len(token.token_hash) == 64


async def test_migrations_round_trip_without_model_drift(alembic_config):
    """Fresh schemas, downgrade to base and re-upgrade all agree with the SQLAlchemy models."""
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "base")
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)


async def test_readiness_detects_stalled_controller_and_recovers(client, broken_controller):
    """Readiness reports scheduler failure while liveness stays healthy, then recovers on the next valid tick."""
    assert (await client.get("/health/live")).status_code == 200
    response = await client.get("/health/ready")
    assert (
        response.status_code == 503
        and response.json()["detail"] == "Controller is not making progress"
    )
    broken_controller["enabled"] = False
    for _ in range(100):
        response = await client.get("/health/ready")
        if response.status_code == 200:
            break
        await asyncio.sleep(0.01)
    assert response.status_code == 200


async def test_missing_artifact_storage_returns_unavailable(api, missing_artifact_file):
    """An artifact row whose bytes are unavailable returns 503 while its metadata remains readable."""
    path = f"/v1/artifacts/{missing_artifact_file['id']}"
    assert (await api.get(path)).status_code == 200
    response = await api.get(path + "/content")
    assert (
        response.status_code == 503
        and response.json()["detail"] == "Artifact storage is unavailable"
    )


@pytest.mark.parametrize("settings", [{"isolate_ttl_seconds": 10}], indirect=True)
async def test_api_guards_expired_work_before_controller_observes_expiry(
    api, agent_api, clock, isolate, run_task, paused_controller
):
    """API delivery and launch guards enforce TTL even while the controller has not changed isolate status."""
    from datetime import timedelta

    clock.now += timedelta(seconds=11)
    state = (await api.get(f"/v1/isolates/{isolate['id']}")).json()
    assert state["status"] == "ready"
    assert (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ] == []
    for suffix in ["ack", "started"]:
        assert (
            await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/{suffix}")
        ).status_code == 409


@pytest.mark.parametrize("settings", [{"isolate_ttl_seconds": 10}], indirect=True)
async def test_expired_provisioning_cannot_finish_before_controller_tick(
    api, agent_api, clock, create_task, paused_provisioning_controller
):
    """A successful create report cannot resurrect an expired but not yet processed provisioning isolate."""
    from datetime import timedelta

    clock.now += timedelta(seconds=11)
    state = (await api.get(f"/v1/isolates/{create_task['isolate_id']}")).json()
    assert state["status"] == "provisioning"
    assert (
        await agent_api.post(f"/agent/v1/tasks/{create_task['id']}/complete", json={})
    ).status_code == 409
    assert (await api.get(f"/v1/isolates/{create_task['isolate_id']}")).json()[
        "status"
    ] == "provisioning"


async def test_missing_database_sentinel_fails_readiness_and_writes(
    client, missing_coordination_row
):
    """A missing migration sentinel keeps liveness healthy but refuses readiness and serialized writes."""
    assert (await client.get("/health/live")).status_code == 200
    response = await client.get("/health/ready")
    assert response.status_code == 503 and response.json()["detail"] == "Database unavailable"
    with pytest.raises(RuntimeError, match="not initialized"):
        async with missing_coordination_row.write():
            pytest.fail("A write transaction cannot proceed without the coordination row")


async def test_startup_rejects_a_database_without_its_migration_sentinel(
    settings, clock, missing_coordination_row
):
    """Application startup fails before serving requests when its migration sentinel is missing."""
    from throng_api.app import create_app

    application = create_app(settings, clock=lambda: clock.now)
    with pytest.raises(RuntimeError, match="not initialized"):
        async with application.router.lifespan_context(application):
            pytest.fail("An uninitialized application must not enter its serving lifespan")


@pytest.mark.parametrize("mutation", ["password", "delete"])
async def test_login_cannot_issue_tokens_after_concurrent_credential_change(
    api, client, user, paused_login, mutation
):
    """A successful old password verification cannot issue a token after a concurrent reset or account deletion."""
    async with asyncio.TaskGroup() as group:
        pending = group.create_task(
            client.post(
                "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
            )
        )
        await asyncio.wait_for(paused_login["entered"].wait(), timeout=5)
        if mutation == "password":
            assert (
                await api.patch(
                    f"/v1/users/{user['id']}", json={"password": "new concurrent password"}
                )
            ).status_code == 200
        else:
            assert (await api.delete(f"/v1/users/{user['id']}")).status_code == 204
        paused_login["resume"].set()
    assert pending.result().status_code == 401
    assert pending.result().json()["detail"] == "Credentials changed; retry login"
