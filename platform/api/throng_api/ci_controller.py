"""Durable CI runner lifecycle. All provider HTTP calls happen outside DB transactions."""

import asyncio
import logging
import secrets
from datetime import timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from throng_api.ci import (
    ACTIVE,
    creator_authorized,
    repository_name,
    runner_isolate,
    verify_repository,
)
from throng_api.ci_policy import authorizer, workflow_run_allowed
from throng_api.ci_profiles import effective_profile
from throng_api.execution import TERMINAL, destroy_isolate
from throng_api.github_client import GitHubError
from throng_api.models import CIReservation, CIRoute, Command, Isolate

logger = logging.getLogger(__name__)
RUNNER_COMMAND = 'exec ./run.sh --jitconfig "$POWERCELL_GITHUB_JIT_CONFIG"'


def close(row, status, reason, now):
    row.status, row.reason, row.finished_at = status, reason, now
    row.credentials = None
    row.next_check = min(row.next_check, now)


def close_github(row, conclusion, now):
    close(
        row,
        "cancelled" if conclusion == "cancelled" else "completed",
        "github_" + str(conclusion)[:50],
        now,
    )


def github_outcome(row):
    """Identify provider/provisional outcomes that an exact job result may refine."""
    return row.status in {"completed", "cancelled"} and (
        row.reason == "runner_exited" or (row.reason or "").startswith("github_")
    )


async def job_conclusion(client, base, work, token):
    """Find only the selected runner's completed job within its original attempt."""
    if work.runner_id is None:
        return None
    page = 1
    while True:
        data = await client.api(
            "GET",
            base + f"/actions/runs/{work.run_id}/attempts/{work.run_attempt}/jobs",
            token=token,
            params={"per_page": 100, "page": page},
        )
        for job in data["jobs"]:
            if (
                job.get("run_id") == work.run_id
                and job.get("run_attempt", work.run_attempt) == work.run_attempt
                and job.get("runner_id") == work.runner_id
                and job.get("runner_name") == work.label
                and job.get("status") == "completed"
                and isinstance(job.get("conclusion"), str)
            ):
                return job["conclusion"]
        if page * 100 >= data["total_count"] or len(data["jobs"]) < 100:
            return None
        page += 1


async def mark_running(session, row, route, now):
    if row.expires_at <= now or row.status == "ready" and row.deadline <= now:
        return  # A late event/poll cannot renew an expired or unavailable runner.
    isolate = await runner_isolate(session, row, now)
    if isolate is None:
        return
    row.started_at = row.started_at or now
    row.status = "running"
    row.expires_at = now + timedelta(
        seconds=effective_profile(route, row.workflow_path)["timeout_seconds"]
    )
    isolate.expires_at = row.expires_at


async def local_tick(app):
    async with app.state.db.write() as session:
        now, settings = app.state.clock(), app.state.settings
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
                        "failed" if row.status in {"preparing", "ready"} else "cancelled",
                        "authorization_revoked",
                        now,
                    )
                elif row.expires_at <= now:
                    close(
                        row,
                        "failed" if row.status in {"preparing", "ready"} else "timed_out",
                        "runner_timeout",
                        now,
                    )
                elif row.status in {"preparing", "ready"} and row.deadline <= now:
                    close(
                        row,
                        "failed",
                        "preparation_timeout" if isolate and isolate.computer_id else "no_capacity",
                        now,
                    )
                elif isolate and isolate.status in {"failed", "destroying", "destroyed"}:
                    close(
                        row,
                        "failed",
                        "isolate_unavailable",
                        now,
                    )
                elif row.command_id:
                    command = await session.get(Command, row.command_id)
                    if command.status in TERMINAL:
                        close(
                            row,
                            "failed" if row.status in {"preparing", "ready"} else "completed",
                            "runner_exited",
                            now,
                        )
            if row.status not in ACTIVE:
                row.finished_at = row.finished_at or now
                row.credentials = None
                if isolate:
                    await destroy_isolate(session, isolate, now, settings, "ci_finished")


async def claim_work(app):
    async with app.state.db.write() as session:
        now = app.state.clock()
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
            route = await session.get(CIRoute, row.route_id)
            interval = 5
            command = (
                await session.get(Command, row.command_id)
                if row.status == "preparing" and row.command_id
                else None
            )
            fast = command is not None and now < command.created_at + timedelta(seconds=10)
            if fast or row.status == "running" or row.status not in ACTIVE:
                peers = await session.scalar(
                    select(func.count())
                    .select_from(CIReservation)
                    .join(CIRoute, CIRoute.id == CIReservation.route_id)
                    .where(
                        CIRoute.installation_id == route.installation_id,
                        CIReservation.cleaned_at.is_(None),
                    )
                )
                # Share a conservative provider budget across long jobs; webhooks
                # and local deadlines stay immediate. No token/cache state needed.
                # A brief startup burst is shared across the installation, then
                # returns to the normal interval without new persistent state.
                interval = min(5, max(1, peers)) if fast else max(30, 12 * peers)
            row.next_check = now + timedelta(seconds=interval)
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
    repository = None
    if work.status in ACTIVE:
        repository = await verify_repository(
            request, authorizer(route), route.installation_id, route.repository_id
        )
    async with client.installation_token(
        route.installation_id,
        [route.repository_id],
        {"administration": "write", "actions": "read", "metadata": "read"},
    ) as token:
        if repository is None:
            # Cleanup survives rename and OAuth disconnect using only App authority.
            repository = await client.api(
                "GET", f"/repositories/{route.repository_id}", token=token
            )
        base = "/repos/" + repository_name(repository, route.repository_id)
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

            # GitHub does not redeliver lost completion webhooks. Recover the
            # exact attempt/job after mandatory cleanup, never by display name.
            outcome_retry, outcome_delay = False, 5
            if github_outcome(work):
                try:
                    conclusion = await job_conclusion(client, base, work, token)
                    exact = conclusion is not None
                    outcome_retry = (
                        conclusion is None
                        and work.started_at is not None
                        and work.reason != "github_outcome_unavailable"
                    )
                    if (
                        conclusion is None
                        and work.reason == "runner_exited"
                        and work.started_at is None
                    ):
                        run = await client.api(
                            "GET",
                            base + f"/actions/runs/{work.run_id}/attempts/{work.run_attempt}",
                            token=token,
                        )
                        if (
                            run.get("status") == "completed"
                            and run.get("run_attempt") == work.run_attempt
                        ):
                            conclusion = run.get("conclusion", "completed")
                    if conclusion is not None:

                        async def settled(session, row):
                            if github_outcome(row) and row.reason == work.reason:
                                if exact:
                                    row.started_at = (
                                        row.started_at or row.finished_at or app.state.clock()
                                    )
                                close_github(row, conclusion, row.finished_at or app.state.clock())

                        await update(app, work, settled)
                except GitHubError as error:
                    outcome_retry = error.status != 404
                    outcome_delay = max(30, error.retry_after or 0)
                    if error.status == 404:

                        async def unavailable(session, row):
                            if row.reason == "runner_exited":
                                row.reason = "github_outcome_unavailable"

                        await update(app, work, unavailable)

            async def cleaned(session, row):
                # Keep historical identity for late exact job results, including
                # those arriving after aggregate completion and confirmed cleanup.
                if row.reason != "runner_exited" and not (
                    row.status in {"completed", "cancelled"}
                    and (row.reason or "").startswith("github_")
                ):
                    row.runner_id = None
                isolate = await session.get(Isolate, row.isolate_id) if row.isolate_id else None
                grace = row.finished_at + timedelta(seconds=60)
                if grace > app.state.clock():
                    # Check once after any in-flight registration has settled,
                    # even if the Mac is offline and steady polling is sparse.
                    row.next_check = min(row.next_check, grace)
                if not isolate or not isolate.reserved:
                    # A cancelled HTTP operation may still have reached GitHub.
                    # Recheck after the maximum claim lifetime before forgetting it.
                    if (
                        grace <= app.state.clock()
                        and row.reason != "runner_exited"
                        and not outcome_retry
                    ):
                        row.cleaned_at = app.state.clock()
                    else:
                        row.next_check = max(
                            grace,
                            row.next_check,
                            app.state.clock() + timedelta(seconds=outcome_delay),
                        )

            await update(app, work, cleaned)
            return
        run = await client.api(
            "GET", base + f"/actions/runs/{work.run_id}/attempts/{work.run_attempt}", token=token
        )
        valid_run = (
            run.get("repository", {}).get("id") == route.repository_id
            and run.get("head_repository", {}).get("id") == route.repository_id
            and run.get("head_branch") == route.ref.removeprefix("refs/heads/")
            and run.get("event") in {"push", "workflow_dispatch"}
            and run.get("run_attempt") == work.run_attempt
            and workflow_run_allowed(route, run.get("path", ""), work.workflow_path)
        )
        if not valid_run or run.get("status") == "completed":

            async def finished(session, row):
                if row.status in ACTIVE:
                    if valid_run:
                        close_github(row, run.get("conclusion", "completed"), app.state.clock())
                    else:
                        close(row, "cancelled", "workflow_identity_changed", app.state.clock())

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
                    timeout_seconds=effective_profile(route, row.workflow_path)["timeout_seconds"]
                    + effective_profile(route, row.workflow_path)["preparation_seconds"]
                    + 300,
                    created_at=app.state.clock(),
                    queued_deadline=row.deadline,
                )
                session.add(command)
                await session.flush()
                row.command_id = command.id

            await update(app, work, registered)
        else:
            try:
                runner = await client.api(
                    "GET", base + f"/actions/runners/{work.runner_id}", token=token
                )
            except GitHubError as error:
                if error.status != 404:
                    raise

                async def disappeared(session, row):
                    if row.status in ACTIVE:
                        before_selection = row.status in {"preparing", "ready"}
                        close(
                            row,
                            "failed" if before_selection else "completed",
                            "runner_unavailable" if before_selection else "runner_exited",
                            app.state.clock(),
                        )

                await update(app, work, disappeared)
                return

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
        remaining = (work.lease_until - app.state.clock()).total_seconds()
        if remaining <= 0:
            return
        async with asyncio.timeout(min(40, remaining)), app.state.github.client.connection():
            await reconcile(app, work, route)
    except (GitHubError, HTTPException, TimeoutError, KeyError, TypeError) as error:
        # A confirmed authorization denial is different from a temporary outage.
        # Remote 403s may be rate limits; explicit local 403 means missing admin.
        denied = (
            isinstance(error, HTTPException)
            and error.__cause__ is None
            and error.status_code in {403, 404}
            or isinstance(error, GitHubError)
            and error.status in {401, 404}
            or isinstance(error.__cause__, GitHubError)
            and error.__cause__.status in {401, 404}
        )
        provider_error = error if isinstance(error, GitHubError) else error.__cause__
        retry_after = getattr(provider_error, "retry_after", None) or 0

        async def failed(session, row):
            if row.status in {"preparing", "ready"} or denied and row.status in ACTIVE:
                close(
                    row,
                    "failed" if row.status in {"preparing", "ready"} else "cancelled",
                    "authorization_revoked" if denied else "github_unavailable",
                    app.state.clock(),
                )
            # Back off cleanup/reconciliation. Error bodies/tokens are never logged.
            row.next_check = max(
                row.next_check, app.state.clock() + timedelta(seconds=max(30, retry_after))
            )

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
