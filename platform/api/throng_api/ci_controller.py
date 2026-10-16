"""Durable CI runner lifecycle. All provider HTTP calls happen outside DB transactions."""

import asyncio
import logging
import secrets
from datetime import timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import or_, select

from throng_api.ci import ACTIVE, creator_authorized, verify_repository
from throng_api.execution import TERMINAL, destroy_isolate
from throng_api.github_client import GitHubError
from throng_api.models import CIReservation, CIRoute, Command, Isolate

logger = logging.getLogger(__name__)
RUNNER_COMMAND = 'exec ./run.sh --jitconfig "$POWERCELL_GITHUB_JIT_CONFIG"'


def close(row, status, reason, now):
    row.status, row.reason, row.finished_at = status, reason, now
    row.credentials = None


async def mark_running(session, row, route, now):
    row.status = "running"
    row.expires_at = now + timedelta(seconds=route.timeout_seconds)
    isolate = await session.get(Isolate, row.isolate_id)
    isolate.expires_at = row.expires_at


async def local_tick(app):
    now, settings = app.state.clock(), app.state.settings
    async with app.state.db.write() as session:
        rows = (
            await session.scalars(select(CIReservation).where(CIReservation.cleaned_at.is_(None)))
        ).all()
        for row in rows:
            route = await session.get(CIRoute, row.route_id)
            isolate = await session.get(Isolate, row.isolate_id) if row.isolate_id else None
            if row.status in ACTIVE:
                if not await creator_authorized(session, route):
                    close(
                        row,
                        "fallback" if row.status in {"preparing", "ready"} else "cancelled",
                        "authorization_revoked",
                        now,
                    )
                elif row.expires_at <= now:
                    close(
                        row,
                        "fallback" if row.status in {"preparing", "ready"} else "timed_out",
                        "runner_timeout",
                        now,
                    )
                elif row.status in {"preparing", "ready"} and row.deadline <= now:
                    close(
                        row,
                        "fallback",
                        "preparation_timeout" if isolate and isolate.computer_id else "no_capacity",
                        now,
                    )
                elif isolate and isolate.status in {"failed", "destroying", "destroyed"}:
                    close(
                        row,
                        "fallback" if row.status in {"preparing", "ready"} else "failed",
                        "isolate_unavailable",
                        now,
                    )
                elif row.command_id:
                    command = await session.get(Command, row.command_id)
                    if command.status in TERMINAL:
                        close(
                            row,
                            "fallback" if row.status in {"preparing", "ready"} else "completed",
                            "runner_exited",
                            now,
                        )
            if row.status not in ACTIVE:
                if isolate:
                    await destroy_isolate(session, isolate, now, settings, "ci_finished")
                if (
                    (not isolate or not isolate.reserved)
                    and row.runner_id is None
                    and row.reason
                    in {"no_capacity", "concurrency_limit", "isolate_quota", "route_disabled"}
                ):
                    row.cleaned_at = now


async def claim_work(app):
    now = app.state.clock()
    async with app.state.db.write() as session:
        rows = (
            await session.scalars(
                select(CIReservation)
                .outerjoin(Isolate, Isolate.id == CIReservation.isolate_id)
                .where(
                    CIReservation.cleaned_at.is_(None),
                    CIReservation.next_check <= now,
                    or_(CIReservation.lease_until.is_(None), CIReservation.lease_until <= now),
                    or_(CIReservation.status != "preparing", Isolate.status == "ready"),
                )
                .order_by(CIReservation.next_check, CIReservation.id)
                .limit(1)
            )
        ).all()
        for row in rows:
            row.lease_nonce = secrets.token_urlsafe(24)
            row.lease_until = now + timedelta(seconds=60)
            row.next_check = now + timedelta(seconds=5)
            route = await session.get(CIRoute, row.route_id)
            # Plain detached values; never hold the coordination lock across HTTP.
            return SimpleNamespace(
                **{
                    column.key: getattr(row, column.key)
                    for column in CIReservation.__table__.columns
                }
            ), SimpleNamespace(
                **{column.key: getattr(route, column.key) for column in CIRoute.__table__.columns}
            )


async def update(app, work, callback):
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, work.id)
        if row and row.lease_nonce == work.lease_nonce:
            await callback(session, row)


async def reconcile(app, work, route):
    integration = app.state.github
    client = integration.client
    request = SimpleNamespace(app=app)
    if work.status in ACTIVE:
        await verify_repository(
            request, route.created_by, route.installation_id, route.repository_id
        )
    async with client.installation_token(
        route.installation_id,
        [route.repository_id],
        {"administration": "write", "actions": "read", "metadata": "read"},
    ) as token:
        base = f"/repos/{route.repository}"
        if work.status not in ACTIVE:
            # Discover by our unique name too: recover a crash after GitHub created
            # the runner but before its ID was committed to the database.
            runners = await client.api(
                "GET",
                base + "/actions/runners",
                token=token,
                params={"name": work.label, "per_page": 100},
            )
            ids = {row["id"] for row in runners["runners"] if row["name"] == work.label}
            if work.runner_id:
                ids.add(work.runner_id)
            for runner_id in ids:
                try:
                    await client.api(
                        "DELETE", base + f"/actions/runners/{int(runner_id)}", token=token
                    )
                except GitHubError as error:
                    if error.status != 404:
                        raise

            async def cleaned(session, row):
                row.runner_id = None
                isolate = await session.get(Isolate, row.isolate_id) if row.isolate_id else None
                if not isolate or not isolate.reserved:
                    # A cancelled HTTP operation may still have reached GitHub.
                    # Recheck after the maximum claim lifetime before forgetting it.
                    grace = row.finished_at + timedelta(seconds=60)
                    if grace <= app.state.clock():
                        row.cleaned_at = app.state.clock()
                    else:
                        row.next_check = grace

            await update(app, work, cleaned)
            return
        run = await client.api("GET", base + f"/actions/runs/{work.run_id}", token=token)
        valid_run = (
            run.get("repository", {}).get("id") == route.repository_id
            and run.get("head_repository", {}).get("id") == route.repository_id
            and run.get("head_branch") == route.ref.removeprefix("refs/heads/")
            and run.get("event") in {"push", "workflow_dispatch"}
            and run.get("run_attempt") == work.run_attempt
            and run.get("path", "").split("@")[0] == route.workflow_path
        )
        if not valid_run or run.get("status") == "completed":

            async def finished(session, row):
                if row.status in ACTIVE:
                    close(
                        row,
                        "cancelled",
                        "workflow_finished" if valid_run else "workflow_identity_changed",
                        app.state.clock(),
                    )

            await update(app, work, finished)
            return
        if work.runner_id is None:
            # An expired lease/crash may leave an unused registration with this name.
            existing = await client.api(
                "GET",
                base + "/actions/runners",
                token=token,
                params={"name": work.label, "per_page": 100},
            )
            for runner in existing["runners"]:
                if runner["name"] == work.label:
                    await client.api(
                        "DELETE", base + f"/actions/runners/{int(runner['id'])}", token=token
                    )
            result = await client.api(
                "POST",
                base + "/actions/runners/generate-jitconfig",
                token=token,
                payload={
                    "name": work.label,
                    "runner_group_id": 1,
                    "labels": [work.label],
                    "work_folder": "_work",
                },
            )
            runner_id, credential = result["runner"]["id"], result["encoded_jit_config"]
            if (
                type(runner_id) is not int
                or runner_id <= 0
                or not isinstance(credential, str)
                or not 0 < len(credential) <= 100000
            ):
                raise GitHubError()

            async def registered(session, row):
                row.runner_id = runner_id
                if row.status != "preparing" or row.deadline <= app.state.clock():
                    return  # Keep the ID for cleanup, never deliver late credentials.
                isolate = await session.get(Isolate, row.isolate_id)
                if isolate.status != "ready":
                    return
                row.credentials = integration.seal({"jit": credential})
                isolate.next_sequence += 1
                command = Command(
                    isolate_id=isolate.id,
                    sequence=isolate.next_sequence,
                    command=RUNNER_COMMAND,
                    shell=True,
                    cwd=".",
                    env={},
                    timeout_seconds=route.timeout_seconds + route.preparation_seconds + 300,
                    created_at=app.state.clock(),
                    queued_deadline=row.deadline,
                )
                session.add(command)
                await session.flush()
                row.command_id = command.id

            await update(app, work, registered)
        else:
            runner = await client.api(
                "GET", base + f"/actions/runners/{work.runner_id}", token=token
            )

            async def readiness(session, row):
                if row.status == "selected" and runner.get("busy"):
                    await mark_running(session, row, route, app.state.clock())
                if row.status != "preparing" or row.deadline <= app.state.clock():
                    return
                command = await session.get(Command, row.command_id) if row.command_id else None
                if (
                    runner.get("status") == "online"
                    and not runner.get("busy")
                    and command
                    and command.status == "running"
                ):
                    row.status = "ready"

            await update(app, work, readiness)


async def run_once(app):
    if app.state.github is None:
        return
    await local_tick(app)
    claimed = await claim_work(app)
    if not claimed:
        return
    work, route = claimed
    try:
        async with asyncio.timeout(40):
            await reconcile(app, work, route)
    except (GitHubError, HTTPException, TimeoutError, KeyError, TypeError) as error:

        async def failed(session, row):
            if row.status == "preparing":
                close(row, "fallback", "github_unavailable", app.state.clock())
            # Back off cleanup/reconciliation. Error bodies/tokens are never logged.
            row.next_check = app.state.clock() + timedelta(seconds=30)

        await update(app, work, failed)
        logger.warning("CI provider reconciliation failed (%s)", type(error).__name__)
    finally:

        async def released(session, row):
            row.lease_nonce = row.lease_until = None

        await update(app, work, released)


async def run(app, stop):
    while not stop.is_set():
        try:
            await run_once(app)
        except Exception:
            # Do not serialize provider objects or request headers in diagnostics.
            logger.error("CI controller tick failed; durable work will be retried")
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
