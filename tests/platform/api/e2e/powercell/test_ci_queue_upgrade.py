"""Retire the old CI capacity queue without disturbing normal pending work."""

from datetime import timedelta

import anyio
import pytest
from alembic import command
from sqlalchemy import select

from throng_api.cli import migration_config
from throng_api.models import CIReservation, CIRoute, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import reserve, schedule
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: upgrade stops all legacy CI-only capacity waiting, including
# unassigned isolates of already-terminal reservations; ordinary pending work
# still allocates, assigned CI stays intact, and history/cleanup are retained.
async def test_upgrade_retires_only_unassigned_ci_work(
    client, route, ci_peer, powercell, app, clock
):
    """Preserve allocated CI and history while permanently closing its former queue."""
    async with app.state.db.write() as session:
        policy = await session.get(CIRoute, route["id"])
        policy.cpu_millis, policy.memory_bytes, policy.disk_bytes = 500, 512 * 1024**2, 4 * 1024**3
    allocated = (await reserve(client, route, ci_peer)).json()
    async with app.state.db.write() as session:
        policy = await session.get(CIRoute, route["id"])
        base = dict(
            user_id=policy.user_id,
            company_id=policy.company_id,
            state_artifact_id=policy.state_artifact_id,
            image="ubuntu:24.04",
            os="linux",
            architecture="arm64",
            runtime="powercell-ubuntu-arm64-v1",
            cpu_millis=500,
            memory_bytes=512 * 1024**2,
            disk_bytes=4 * 1024**3,
            created_at=clock.now,
            expires_at=clock.now + timedelta(hours=1),
            provisioning_deadline=clock.now + timedelta(minutes=5),
            isolation_mode="vm",
            retry_allowed=False,
        )
        pending, terminal, ordinary = (Isolate(**base) for _ in range(3))
        session.add_all([pending, terminal, ordinary])
        await session.flush()
        rows = []
        for index, isolate in enumerate([pending, terminal]):
            row = CIReservation(
                route_id=policy.id,
                run_id=2000 + index,
                run_attempt=1,
                instance="default",
                label="powercell-legacy-" + str(index),
                isolate_id=isolate.id,
                status="preparing" if index == 0 else "failed",
                reason=None if index == 0 else "github_unavailable",
                deadline=isolate.provisioning_deadline,
                expires_at=isolate.expires_at,
                next_check=clock.now,
                credentials="encrypted-cleanup-state" if index == 0 else None,
            )
            rows.append(row)
            session.add(row)
        await session.flush()
        ids = pending.id, terminal.id, ordinary.id, rows[0].id, rows[1].id
        missing = CIReservation(
            route_id=policy.id,
            run_id=2002,
            run_attempt=1,
            instance="default",
            label="powercell-legacy-missing",
            status="preparing",
            deadline=clock.now,
            expires_at=clock.now,
            next_check=clock.now,
        )
        session.add(missing)
        await session.flush()
        missing_id = missing.id
        assigned = await session.get(CIReservation, allocated["id"])
        assigned.credentials = "allocated-encrypted-jit"
        before = (
            assigned.status,
            assigned.isolate_id,
            assigned.expires_at,
            assigned.deadline,
            assigned.credentials,
        )
    await app.state.db.close()
    configuration = migration_config(app.state.settings)
    await anyio.to_thread.run_sync(command.downgrade, configuration, "0011")
    await anyio.to_thread.run_sync(command.upgrade, configuration, "head")
    async with app.state.db.read() as session:
        stopped = await session.get(CIReservation, ids[3])
        assert (
            stopped.status == "failed" and stopped.reason == "no_capacity" and stopped.finished_at
        )
        assert stopped.credentials is None and stopped.cleaned_at is None
        assert (await session.get(CIReservation, missing_id)).status == "failed"
        historical = await session.get(CIReservation, ids[4])
        assert historical.reason == "github_unavailable" and historical.status == "failed"
        for isolate_id in ids[:2]:
            isolate = await session.get(Isolate, isolate_id)
            assert isolate.status == "destroyed" and isolate.destroyed_at and not isolate.reserved
        assigned = await session.get(CIReservation, allocated["id"])
        assert (
            assigned.status,
            assigned.isolate_id,
            assigned.expires_at,
            assigned.deadline,
            assigned.credentials,
        ) == before
    await schedule(app, clock)
    async with app.state.db.read() as session:
        assert (await session.get(Isolate, ids[2])).computer_id == powercell.computer_id
        assert all(
            [(await session.get(Isolate, isolate_id)).computer_id is None for isolate_id in ids[:2]]
        )
    # A rollback may restore schema compatibility, but cannot resurrect old work.
    await app.state.db.close()
    await anyio.to_thread.run_sync(command.downgrade, configuration, "0011")
    async with app.state.db.read() as session:
        assert (
            await session.scalar(select(CIReservation.status).where(CIReservation.id == ids[3]))
            == "failed"
        )


# Assertions: legacy refusals use normal cleanup even without a known runner ID
# or finish timestamp. Find an orphan by label, discard JIT, retain the grace
# window once, then finish cleanup without resurrecting work or renewing grace.
async def test_legacy_refusal_uses_normal_runner_cleanup(client, route, ci_peer, app, clock):
    """Clean unknown registrations instead of forgetting old declined reservations."""
    from throng_api import ci_controller

    result = (await reserve(client, route, ci_peer)).json()
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        row.cleaned_at = row.finished_at = None
        row.credentials = app.state.github.seal({"jit": "old-private-jit"})
        ci_peer.label = row.label
        ci_peer.registered = True
    started = clock.now
    await ci_controller.run_once(app)
    assert ci_peer.deletes == 1 and not ci_peer.registered
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.credentials is None and row.finished_at == started and row.cleaned_at is None
    clock.now += timedelta(seconds=61)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.cleaned_at and row.finished_at == started and row.isolate_id is None
