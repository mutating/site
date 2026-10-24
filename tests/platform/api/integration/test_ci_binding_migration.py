"""Existing CI configuration and executions survive the editable-binding migration."""

import asyncio

import anyio
import pytest
from alembic import command as alembic
from sqlalchemy import select, text

from tests.platform.api.e2e.powercell.test_repository_bindings import (
    catalog_peer as catalog_peer_fixture,
)
from tests.platform.api.e2e.powercell.test_repository_bindings import (
    ci_peer as ci_peer_fixture,
)
from tests.platform.api.e2e.powercell.test_repository_bindings import (
    create,
    execution,
)
from tests.platform.api.e2e.powercell.test_repository_bindings import (
    github as github_fixture,
)
from throng_api.models import CIRoute, CIRouteRevision

catalog_peer = catalog_peer_fixture
ci_peer = ci_peer_fixture
github = github_fixture
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


async def test_legacy_ci_bindings_are_imported_without_changing_scope_or_execution_history(
    api, catalog_peer, app, clock, http_server, migration_database, alembic_config
):
    """Upgrade backfills an auditable legacy snapshot; downgrade must preserve routes, state and runs."""
    binding = await create(
        api, workflow_paths=None, workflow_path=".github/workflows/deploy.yml", job_key="deploy"
    )
    await execution(app, clock, binding)
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0014")
    async with migration_database.engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT workflow_path FROM ci_routes"))
        ).scalar() == ".github/workflows/deploy.yml"
        assert (
            await connection.execute(text("SELECT COUNT(*) FROM ci_reservations"))
        ).scalar() == 1
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with migration_database.read() as session:
        route = await session.get(CIRoute, binding["id"])
        history = (await session.scalars(select(CIRouteRevision))).one()
        assert route.authorized_by == route.created_by == binding["created_by"]
        assert route.workflow_paths is None and route.repository_binding is False
        assert route.workflow_profiles == {}
        assert route.revision == 1 and route.state_artifact_id == binding["state_artifact_id"]
        assert history.action == "imported" and history.configuration["workflow_paths"] is None
        assert history.configuration["user_id"] == binding["user_id"]
        assert history.configuration["memory_bytes"] == binding["memory_bytes"]
        assert history.configuration["enabled"] is True


async def test_workflow_profile_upgrade_preserves_default_policy_and_immutable_history(
    api, catalog_peer, app, clock, http_server, migration_database, alembic_config
):
    """Adding profiles must keep existing resource defaults, route identity and audit/started-run evidence intact."""
    binding = await create(api, cpu_millis=750, memory_bytes=1024**3, preparation_seconds=120)
    await execution(app, clock, binding, started=True)
    old_history = (await api.get(f"/v1/github/routes/{binding['id']}/revisions")).json()
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0015")
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with migration_database.read() as session:
        route = await session.get(CIRoute, binding["id"])
        history = (await session.scalars(select(CIRouteRevision))).one()
        assert route.workflow_profiles == {} and route.revision == 1
        assert (route.cpu_millis, route.memory_bytes, route.preparation_seconds) == (
            750,
            1024**3,
            120,
        )
        assert history.configuration == old_history["revisions"][0]["configuration"]
        assert (
            await session.execute(
                text("SELECT COUNT(*) FROM ci_reservations WHERE started_at IS NOT NULL")
            )
        ).scalar() == 1
