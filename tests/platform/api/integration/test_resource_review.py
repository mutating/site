"""Regression checks for resource review: bounded database work and concurrent ownership changes."""

import asyncio
from datetime import timedelta

import anyio
import pytest
from alembic import command as alembic
from sqlalchemy import inspect, text

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("limit", [1, 12])
async def test_inventory_loads_type_metadata_in_one_query(
    api, large_resource_inventory, resource_query_observer, limit
):
    """A larger inventory page still fetches type descriptions in one SQL query rather than one round trip per resource."""
    response = await api.get(
        f"/api/v1/computers/{large_resource_inventory['id']}/resources", params={"limit": limit}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == limit
    assert all(row["resource_type"]["id"] == row["resource_type_id"] for row in response.json())
    assert len(resource_query_observer["catalog_queries"]) == 1


@pytest.mark.parametrize("limit", [1, 50])
async def test_history_loads_type_metadata_once_for_the_whole_page(
    api, user, resource_history_series, resource_query_observer, limit
):
    """Page size does not multiply catalog queries, and types introduced later stay absent from earlier graph points."""
    response = await api.get(
        f"/api/v1/users/{user['id']}/resources/history",
        params={
            "from": (resource_history_series["start"] - timedelta(seconds=1)).isoformat(),
            "to": (resource_history_series["end"] + timedelta(seconds=1)).isoformat(),
            "limit": limit,
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["initial"] == []
    assert len(report["points"]) == min(limit, 14)
    assert len(resource_query_observer["catalog_queries"]) == 1
    assert {row["code"] for row in report["points"][0]["resources"]} == {"cpu", "memory"}
    if limit > 1:
        assert {row["code"]: row["total"] for row in report["points"][-1]["resources"]} == {
            "cpu": 2012,
            "memory": 2048,
            "gpu": 4,
        }


async def test_baseline_materializes_only_the_last_snapshot_per_resource(
    api, user, resource_history_series, resource_query_observer
):
    """A report after many changes reconstructs the baseline from three latest snapshots, without loading every historical event."""
    response = await api.get(
        f"/api/v1/users/{user['id']}/resources/history",
        params={
            "from": resource_history_series["end"].isoformat(),
            "to": (resource_history_series["end"] + timedelta(hours=1)).isoformat(),
            "limit": 1,
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["points"] == [] and report["next_after_revision"] is None
    assert {row["code"]: (row["total"], row["occupied"]) for row in report["initial"]} == {
        "cpu": (2012, 0),
        "memory": (2048, 0),
        "gpu": (4, 0),
    }
    assert len(resource_query_observer["history_rows"]) == 3
    assert len(resource_query_observer["catalog_queries"]) == 1


async def test_baseline_preserves_zero_contributions_after_transfer(
    api, user, computer, company, clock
):
    """The last owner-matching snapshot records an exit as zero even if the resource later changes under a different owner."""
    clock.now += timedelta(seconds=1)
    response = await api.patch(
        f"/api/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    clock.now += timedelta(seconds=1)
    response = await api.patch(f"/api/v1/computers/{computer['id']}", json={"cpu_millis": 9000})
    assert response.status_code == 200, response.text
    response = await api.get(
        f"/api/v1/users/{user['id']}/resources/history",
        params={"from": clock.now.isoformat(), "to": (clock.now + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["points"] == []
    assert {row["code"]: row["total"] for row in response.json()["initial"]} == {
        "cpu": 0,
        "memory": 0,
    }


async def test_history_rechecks_owner_scope_after_concurrent_transfer(
    api,
    other_api,
    user,
    computer,
    computer_resources,
    company,
    membership,
    clock,
    paused_resource_history,
):
    """A transfer between authorization and history-scope lookup returns 404, hiding the new owner's private history and the existence of the now-foreign resource."""
    response = await api.patch(
        f"/api/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    path = f"/api/v1/computer-resources/{computer_resources['cpu']['id']}/history"
    params = {
        "from": (clock.now - timedelta(hours=1)).isoformat(),
        "to": (clock.now + timedelta(hours=1)).isoformat(),
    }
    entered, resume = paused_resource_history
    pending = asyncio.create_task(other_api.get(path, params=params))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        response = await api.patch(
            f"/api/v1/computer-ownerships/{computer['ownership']['id']}",
            json={"user_id": user["id"]},
        )
        assert response.status_code == 200, response.text
    finally:
        resume.set()
        response = await asyncio.wait_for(pending, timeout=5)
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Resource not found"}
    assert (await other_api.get(path, params=params)).status_code == 404


async def test_history_baseline_index_upgrades_existing_resource_databases(
    alembic_config, migration_database
):
    """The incremental migration adds a reason/time index to existing resource databases and supports downgrade without losing history tables."""
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0002")
    async with migration_database.engine.connect() as connection:
        indexes = await connection.run_sync(
            lambda conn: inspect(conn).get_indexes("computer_resource_changes")
        )
        assert "ix_resource_history_reason_created_at" not in {row["name"] for row in indexes}
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with migration_database.engine.connect() as connection:
        indexes = await connection.run_sync(
            lambda conn: inspect(conn).get_indexes("computer_resource_changes")
        )
        index = next(
            row for row in indexes if row["name"] == "ix_resource_history_reason_created_at"
        )
        assert index["column_names"] == ["reason", "created_at"]


async def test_inventory_cannot_read_new_private_resources_after_transfer(
    api, other_api, user, computer, company, membership, resource_type, paused_resource_inventory
):
    """A list authorized before transfer cannot disclose a resource created by the new private owner; the same 404 hides foreign and missing resources."""
    response = await api.patch(
        f"/api/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    path = f"/api/v1/computers/{computer['id']}/resources"
    entered, resume = paused_resource_inventory
    pending = asyncio.create_task(other_api.get(path))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        response = await api.patch(
            f"/api/v1/computer-ownerships/{computer['ownership']['id']}",
            json={"user_id": user["id"]},
        )
        assert response.status_code == 200, response.text
        response = await api.post(
            path, json={"resource_type_id": resource_type["id"], "total": 777}
        )
        assert response.status_code == 201, response.text
    finally:
        resume.set()
        response = await asyncio.wait_for(pending, timeout=5)
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Resource not found"}
    assert (await other_api.get(path)).status_code == 404


async def test_inventory_keeps_empty_pages_and_detached_registrant_access(api, other_api, computer):
    """The SQL ownership guard preserves empty pagination and permits only the original registrant to read a detached computer's resources."""
    path = f"/api/v1/computers/{computer['id']}/resources"
    response = await api.get(path, params={"offset": 100})
    assert response.status_code == 200 and response.json() == []
    response = await api.delete(f"/api/v1/computer-ownerships/{computer['ownership']['id']}")
    assert response.status_code == 204, response.text
    response = await api.get(path)
    assert response.status_code == 200, response.text
    assert {row["resource_type"]["code"] for row in response.json()} == {"cpu", "memory"}
    assert (await other_api.get(path)).status_code == 404


@pytest.mark.parametrize("controller_shutdown_probe", [False, True], indirect=True)
async def test_shutdown_finishes_controller_transaction_before_resource_migration(
    http_server, controller_shutdown_probe, migration_database, alembic_config
):
    """Shutdown commits or rolls back the running tick without cancellation, releasing SQLite write locks before an immediate resource-index migration."""
    probe = controller_shutdown_probe
    http_server.server.should_exit = True
    await asyncio.wait_for(probe["shutdown_requested"].wait(), timeout=5)
    probe["resume"].set()
    await asyncio.wait_for(http_server.task, timeout=5)
    assert probe["completed"] is True and probe["cancelled"] is False
    assert http_server.server.lifespan.shutdown_failed is False
    async with migration_database.engine.connect() as connection:
        revision = await connection.scalar(text("SELECT revision FROM coordination"))
        assert revision == probe["revision"] - int(probe["fail"])
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0002")
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)


@pytest.mark.parametrize("settings", [{"controller_interval": 60}], indirect=True)
async def test_shutdown_wakes_idle_controller_without_waiting_for_poll_interval(
    http_server, client
):
    """An idle controller with a long poll interval wakes immediately for shutdown instead of delaying deployment for that interval."""
    # Uvicorn startup does not imply that the background controller's first
    # database transaction has committed, particularly under parallel test load.
    async with asyncio.timeout(5):
        while True:
            response = await client.get("/health/ready")
            if response.status_code == 200:
                break
            assert response.status_code == 503, response.text
            await asyncio.sleep(0.01)
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=3)
    assert http_server.server.lifespan.shutdown_failed is False
