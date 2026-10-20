"""Repository CI bindings and OIDC-authenticated, idempotent runner reservations."""

import asyncio
import hashlib
import io
import re
import tarfile
import time
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPBearer
from pydantic import Field, model_validator
from sqlalchemy import func, or_, select

from throng_api.artifacts import check_storage_quota
from throng_api.execution import allocate_isolate, destroy_isolate
from throng_api.github import external_error, service, user_get, user_token
from throng_api.github_client import GitHubError
from throng_api.models import (
    Artifact,
    CIReservation,
    CIRoute,
    Command,
    Computer,
    ComputerOwnership,
    GitHubAccount,
    Isolate,
    Membership,
    new_id,
)
from throng_api.permissions import effective_permissions
from throng_api.schemas import Input
from throng_api.security import (
    access_token_scheme,
    authenticate,
    bearer,
    company_access,
    owned,
    owned_filter,
    owner_values,
    personal,
)
from throng_api.serialization import public
from throng_api.workload_images import IMAGE_ALIASES

router = APIRouter(
    prefix="/v1/github", tags=["GitHub CI"], dependencies=[Depends(access_token_scheme)]
)
oidc_scheme = HTTPBearer(
    scheme_name="GitHubActionsOIDC",
    auto_error=False,
    description="Short-lived GitHub Actions OIDC token with the platform CI audience; not a platform access token.",
)
public_router = APIRouter(
    prefix="/v1/github/ci", tags=["GitHub CI routing"], dependencies=[Depends(oidc_scheme)]
)
ACTIVE = ("preparing", "ready", "selected", "running")
GiB = 1024**3


class RouteCreate(Input):
    installation_id: int = Field(gt=0, lt=2**63)
    repository_id: int = Field(gt=0, lt=2**63)
    workflow_path: str = Field(
        pattern=r"^\.github/workflows/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,150}\.ya?ml$"
    )
    ref: str = Field(default="refs/heads/main", pattern=r"^refs/heads/[A-Za-z0-9_./-]{1,200}$")
    job_key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,99}$")
    company_id: UUID | None = None
    computer_ids: list[UUID] = Field(default_factory=list, max_length=100)
    cpu_millis: int = Field(default=2000, gt=0, le=1000000)
    memory_bytes: int = Field(default=4 * GiB, ge=16 * 1024**2, le=2**50)
    disk_bytes: int = Field(default=10 * GiB, ge=GiB, le=1024 * GiB)
    preparation_seconds: int = Field(default=90, ge=10, le=300)
    timeout_seconds: int = Field(default=3600, ge=60, le=21600)
    max_parallel: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def workflow_policy(self):
        if (
            ".." in self.workflow_path
            or ".." in self.ref
            or "//" in self.ref
            or self.ref.endswith(("/", "."))
            or any(part.startswith(".") or part.endswith(".lock") for part in self.ref.split("/"))
            or self.job_key == "powercell_route"
        ):
            raise ValueError("Invalid workflow path, branch or job ID")
        return self


class Reserve(Input):
    instance: str = Field(default="default", pattern=r"^[A-Za-z0-9_-]{1,100}$")


async def pool_permission(session, principal, company_id):
    if company_id:
        await company_access(session, principal, company_id, permission="resources.manage")
        await company_access(session, principal, company_id, permission="execution.run")


async def creator_authorized(session, route):
    if not route.enabled or not await session.get(GitHubAccount, route.created_by):
        return False
    if not route.company_id:
        return route.user_id == route.created_by
    member = await session.scalar(
        select(Membership).where(
            Membership.company_id == route.company_id, Membership.user_id == route.created_by
        )
    )
    return bool(
        member
        and {"resources.manage", "execution.run"}
        <= effective_permissions(member.role, member.permissions)
    )


async def runner_isolate(session, reservation, now):
    """Admission uses current local runner/VM/session facts in one transaction."""
    return await session.scalar(
        select(Isolate)
        .join(Command, Command.isolate_id == Isolate.id)
        .join(Computer, Computer.id == Isolate.computer_id)
        .where(
            Isolate.id == reservation.isolate_id,
            Isolate.status == "ready",
            Isolate.expires_at > now,
            Command.id == reservation.command_id,
            Command.status == "running",
            Computer.online.is_(True),
            Computer.session_id == Isolate.session_id,
            Computer.lease_expires_at > now,
        )
    )


def repository_name(repository, repository_id):
    name = repository.get("full_name") if isinstance(repository, dict) else None
    if (
        not isinstance(name, str)
        or repository.get("id") != repository_id
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name)
    ):
        raise GitHubError()
    return name


async def verify_repository(request, user_id, installation_id, repository_id):
    integration = service(request)
    try:
        token = await user_token(request, user_id)
        installation = await integration.client.api("GET", f"/app/installations/{installation_id}")
        if installation.get("app_id") != integration.app_id or installation.get("suspended_at"):
            raise HTTPException(404, "GitHub installation unavailable")
        repository = await user_get(request, user_id, f"/repositories/{repository_id}", token=token)
        if repository.get("id") != repository_id or not repository.get("permissions", {}).get(
            "admin"
        ):
            raise HTTPException(403, "GitHub repository administrator required")
        repository_name(repository, repository_id)
        # A user token can see several installations: also prove access through THIS installation.
        async with integration.client.installation_token(
            installation_id, [repository_id], {"metadata": "read"}
        ) as app_token:
            scoped = await integration.client.api(
                "GET", f"/repositories/{repository_id}", token=app_token
            )
            if scoped.get("id") != repository_id:
                raise GitHubError()
        return repository
    except GitHubError as error:
        raise external_error(error) from error


@router.post("/routes", status_code=201)
async def create_route(data: RouteCreate, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request, "manage")
        await authenticate(session, request, "execute")
        user_id = personal(principal)
        await pool_permission(session, principal, data.company_id)
    # Refresh first so ordinary token rotation does not invalidate this snapshot.
    try:
        await user_token(request, user_id)
    except GitHubError as error:
        raise external_error(error) from error
    async with request.app.state.db.read() as session:
        account = await session.get(GitHubAccount, user_id)
        if account is None:
            raise HTTPException(409, "GitHub connection changed; retry creating the binding")
        grant = account.tokens
    repository = await verify_repository(request, user_id, data.installation_id, data.repository_id)
    # A deterministic empty tar initializes a fresh isolate; never a registered runner snapshot.
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w"):
        pass
    content = content.getvalue()
    key = new_id()
    path = request.app.state.settings.storage_path / key
    keep = False
    try:
        path.write_bytes(content)
        async with request.app.state.db.write() as session:
            principal = await authenticate(session, request, "manage")
            await authenticate(session, request, "execute")
            await pool_permission(session, principal, data.company_id)
            account = await session.get(GitHubAccount, user_id)
            if not account or account.tokens != grant:
                raise HTTPException(409, "GitHub connection changed; retry creating the binding")
            owner = await owner_values(session, principal, data.company_id)
            for computer_id in set(map(str, data.computer_ids)):
                computer = await session.get(Computer, computer_id)
                ownership = await session.scalar(
                    select(ComputerOwnership).where(
                        ComputerOwnership.computer_id == computer_id,
                        ComputerOwnership.company_id == owner["company_id"],
                        ComputerOwnership.user_id == owner["user_id"],
                    )
                )
                if not computer or not ownership:
                    raise HTTPException(404, "Computer unavailable in the selected pool")
                if computer.runtime != "powercell-ubuntu-arm64-v1":
                    raise HTTPException(422, "CI requires a Linux ARM64 Power Cell")
            condition = (
                CIRoute.user_id == owner["user_id"]
                if owner["user_id"]
                else CIRoute.company_id == owner["company_id"]
            )
            count = await session.scalar(select(func.count()).select_from(CIRoute).where(condition))
            if count >= 100:
                raise HTTPException(409, "CI route quota exceeded")
            await check_storage_quota(session, owner, len(content), request.app.state.settings)
            artifact = Artifact(
                **owner,
                id=key,
                storage_key=key,
                filename="ci-empty-state.tar",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
            session.add(artifact)
            await session.flush()
            route = CIRoute(
                **data.model_dump(exclude={"company_id", "computer_ids"}),
                **owner,
                created_by=user_id,
                repository=repository["full_name"],
                repository_owner_id=repository["owner"]["id"],
                computer_ids=sorted(set(map(str, data.computer_ids))),
                state_artifact_id=artifact.id,
            )
            session.add(route)
            await session.flush()
            result = public(route)
            keep = True  # Preserve the file if metadata committed but connection cleanup fails.
        return result
    finally:
        if not keep:
            path.unlink(missing_ok=True)


@router.get("/routes")
async def list_routes(
    request: Request, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        memberships = await session.scalars(
            select(Membership).where(
                Membership.user_id == (principal.user_id or principal.actor_id)
            )
        )
        readable = [
            member.company_id
            for member in memberships
            if "execution.read" in effective_permissions(member.role, member.permissions)
        ]
        rows = await session.scalars(
            select(CIRoute)
            .where(
                owned_filter(CIRoute, principal),
                or_(CIRoute.company_id.is_(None), CIRoute.company_id.in_(readable)),
            )
            .order_by(CIRoute.created_at, CIRoute.id)
            .limit(limit)
            .offset(offset)
        )
        return [public(route) for route in rows]


@router.delete("/routes/{route_id}", status_code=204)
async def disable_route(route_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        route = await owned(session, CIRoute, route_id, principal)
        await pool_permission(session, principal, route.company_id)
        route.enabled = False


@router.get("/routes/{route_id}/runs")
async def route_runs(route_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100)):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        route = await owned(session, CIRoute, route_id, principal)
        if route.company_id:
            await company_access(session, principal, route.company_id, permission="execution.read")
        rows = await session.scalars(
            select(CIReservation)
            .where(CIReservation.route_id == route.id)
            .order_by(CIReservation.created_at.desc())
            .limit(limit)
        )
        return [
            {
                "id": row.id,
                "run_id": row.run_id,
                "run_attempt": row.run_attempt,
                "instance": row.instance,
                "status": row.status,
                "reason": row.reason,
                "isolate_id": row.isolate_id,
                "created_at": row.created_at,
                "finished_at": row.finished_at,
                "url": f"https://github.com/{route.repository}/actions/runs/{row.run_id}/attempts/{row.run_attempt}",
            }
            for row in rows
        ]


async def routing_identity(request, route_id):
    integration = service(request)
    claims = await integration.oidc.verify(bearer(request), request.app.state.clock())
    async with request.app.state.db.read() as session:
        route = await session.get(CIRoute, str(route_id))
        if route is None:
            raise HTTPException(404, "CI route unavailable")
        if (
            int(claims["repository_id"]) != route.repository_id
            or int(claims["repository_owner_id"]) != route.repository_owner_id
            or claims["repository"] != route.repository
            or claims["ref"] != route.ref
            or claims["workflow_ref"] != f"{route.repository}/{route.workflow_path}@{route.ref}"
            or claims["event_name"] not in {"push", "workflow_dispatch"}
            or claims.get("job_workflow_ref") not in {None, claims["workflow_ref"]}
        ):
            raise HTTPException(403, "Workflow is not authorized for this CI route")
        return claims


def decision(row):
    if row.status in {"ready", "selected", "running"}:
        return {
            "id": row.id,
            "status": "ready",
            "runs_on": [row.label],
        }
    if row.status == "preparing":
        return {"id": row.id, "status": "preparing"}
    return {"id": row.id, "status": "failed", "reason": row.reason or row.status}


def watch_decision(row, now):
    """Distinguish confirmed execution from an admitted or provisionally exited runner."""
    if row is None:
        return {"status": "failed", "reason": "reservation_unavailable"}
    if row.started_at is not None:
        return {"status": "started"}
    if row.status not in ACTIVE and not (
        row.status == "completed" and row.reason == "runner_exited"
    ):
        if (
            row.status in {"completed", "cancelled"}
            and (row.reason or "").startswith("github_")
            and row.reason != "github_outcome_unavailable"
        ):
            return {"status": "started"}
        return {"status": "failed", "reason": row.reason or row.status}
    if row.expires_at <= now:
        return {"status": "failed", "reason": "runner_timeout"}
    if row.status in {"preparing", "ready"} and row.deadline <= now:
        return {"status": "failed", "reason": "preparation_timeout"}
    return {"status": "started" if row.status == "running" else "waiting"}


@public_router.post("/routes/{route_id}/watch")
async def watch_selection(route_id: UUID, data: Reserve, request: Request):
    claims = await routing_identity(request, route_id)
    async with request.app.state.db.read() as session:
        # A concurrent start can renew the lease after this snapshot is read.
        now = request.app.state.clock()
        route = await session.get(CIRoute, str(route_id))
        if route is None:
            raise HTTPException(404, "CI route unavailable")
        row = await session.scalar(
            select(CIReservation).where(
                CIReservation.route_id == route.id,
                CIReservation.run_id == int(claims["run_id"]),
                CIReservation.run_attempt == int(claims["run_attempt"]),
                CIReservation.instance == data.instance,
            )
        )
        if not await creator_authorized(session, route):
            return {"status": "failed", "reason": "authorization_revoked"}
        return watch_decision(row, now)


@public_router.post("/routes/{route_id}/reserve")
async def reserve(
    route_id: UUID,
    data: Reserve,
    request: Request,
    wait_seconds: float = Query(default=0, ge=0, le=20),
):
    until = time.monotonic() + wait_seconds
    result = await reserve_once(route_id, data, request)
    while result["status"] == "preparing" and time.monotonic() < until:
        if await request.is_disconnected():
            break
        await asyncio.sleep(
            min(request.app.state.settings.long_poll_interval, max(0, until - time.monotonic()))
        )
        async with request.app.state.db.read() as session:
            row = await session.get(CIReservation, result["id"])
            if row is None:
                raise HTTPException(404, "CI reservation unavailable")
            result = decision(row)
    if result["status"] != "ready":
        return result
    async with request.app.state.db.write() as session:
        now = request.app.state.clock()
        row = await session.get(CIReservation, result["id"])
        if row is None:
            raise HTTPException(404, "CI reservation unavailable")
        route = await session.get(CIRoute, row.route_id)
        if row.expires_at <= now or not await creator_authorized(session, route):
            raise HTTPException(409, "CI selection has expired or authorization was revoked")
        if row.status not in {"ready", "selected", "running"} or (
            row.status == "ready" and row.deadline <= now
        ):
            raise HTTPException(409, "CI runner is no longer ready")
        isolate = await runner_isolate(session, row, now)
        if isolate is None:
            raise HTTPException(409, "CI runner is no longer available")
        if row.status == "ready":
            row.status = "selected"
            row.expires_at = min(row.expires_at, now + timedelta(seconds=300))
            isolate.expires_at = row.expires_at
        return decision(row)


async def reserve_once(route_id, data, request):
    started_at = request.app.state.clock()
    claims = await routing_identity(request, route_id)
    async with request.app.state.db.read() as session:
        route = await session.get(CIRoute, str(route_id))
        if route is None:
            raise HTTPException(404, "CI route unavailable")
        previous = await session.scalar(
            select(CIReservation).where(
                CIReservation.route_id == route.id,
                CIReservation.run_id == int(claims["run_id"]),
                CIReservation.run_attempt == int(claims["run_attempt"]),
                CIReservation.instance == data.instance,
            )
        )
        if previous:
            return decision(previous)
        check_github = await creator_authorized(session, route)
        installation_id, repository_id, creator_id = (
            route.installation_id,
            route.repository_id,
            route.created_by,
        )
    # Local rights can change before locking; a skipped provider check grants no approval.
    github_available = check_github
    if check_github:
        try:
            await verify_repository(request, creator_id, installation_id, repository_id)
        except (GitHubError, HTTPException):
            github_available = False
    async with request.app.state.db.write() as session:
        now = request.app.state.clock()
        route = await session.get(CIRoute, str(route_id))
        if route is None:
            raise HTTPException(404, "CI route unavailable")
        previous = await session.scalar(
            select(CIReservation).where(
                CIReservation.route_id == route.id,
                CIReservation.run_id == int(claims["run_id"]),
                CIReservation.run_attempt == int(claims["run_attempt"]),
                CIReservation.instance == data.instance,
            )
        )
        if previous:
            return decision(previous)
        authorized = await creator_authorized(session, route)
        active = await session.scalar(
            select(func.count())
            .select_from(CIReservation)
            .where(CIReservation.route_id == route.id, CIReservation.status.in_(ACTIVE))
        )
        total = await session.scalar(
            select(func.count())
            .select_from(CIReservation)
            .where(
                CIReservation.route_id == route.id, CIReservation.run_id == int(claims["run_id"])
            )
        )
        if total >= 1000:
            raise HTTPException(409, "Workflow reservation quota exceeded")
        row = CIReservation(
            id=new_id(),
            route_id=route.id,
            run_id=int(claims["run_id"]),
            run_attempt=int(claims["run_attempt"]),
            instance=data.instance,
            label="powercell-" + new_id(),
            deadline=started_at + timedelta(seconds=route.preparation_seconds),
            expires_at=now
            + timedelta(seconds=route.timeout_seconds + route.preparation_seconds + 300),
            next_check=now,
            created_at=now,
        )
        session.add(row)
        owner_condition = (
            Isolate.user_id == route.user_id
            if route.user_id
            else Isolate.company_id == route.company_id
        )
        isolates = await session.scalar(
            select(func.count())
            .select_from(Isolate)
            .where(owner_condition, Isolate.status.in_(["provisioning", "ready", "destroying"]))
        )
        if (
            not authorized
            or not github_available
            or row.deadline <= now
            or active >= route.max_parallel
            or isolates >= request.app.state.settings.max_owner_isolates
        ):
            row.status = "failed"
            row.reason = (
                "route_disabled"
                if not authorized
                else "github_unavailable"
                if not github_available
                else "preparation_timeout"
                if row.deadline <= now
                else "concurrency_limit"
                if active >= route.max_parallel
                else "isolate_quota"
            )
            row.finished_at = row.cleaned_at = now
        else:
            isolate = Isolate(
                user_id=route.user_id,
                company_id=route.company_id,
                state_artifact_id=route.state_artifact_id,
                image=IMAGE_ALIASES["actions"],
                os="linux",
                architecture="arm64",
                runtime="powercell-ubuntu-arm64-v1",
                cpu_millis=route.cpu_millis,
                memory_bytes=route.memory_bytes,
                disk_bytes=route.disk_bytes,
                created_at=now,
                expires_at=row.deadline,
                provisioning_deadline=row.deadline,
                isolation_mode="vm",
                retry_allowed=False,
            )
            session.add(isolate)
            await session.flush()
            if await allocate_isolate(
                session, isolate, now, request.app.state.settings, computer_ids=route.computer_ids
            ):
                row.isolate_id = isolate.id
            else:
                await session.delete(isolate)
                row.status, row.reason = "failed", "no_capacity"
                row.finished_at = row.cleaned_at = now
        await session.flush()
        return decision(row)


@public_router.post("/routes/{route_id}/cancel")
async def cancel_selection(route_id: UUID, data: Reserve, request: Request):
    claims = await routing_identity(request, route_id)
    async with request.app.state.db.write() as session:
        now = request.app.state.clock()
        route = await session.get(CIRoute, str(route_id))
        if route is None:
            raise HTTPException(404, "CI route unavailable")
        row = await session.scalar(
            select(CIReservation).where(
                CIReservation.route_id == route.id,
                CIReservation.run_id == int(claims["run_id"]),
                CIReservation.run_attempt == int(claims["run_attempt"]),
                CIReservation.instance == data.instance,
            )
        )
        if row and row.status == "running":
            raise HTTPException(409, "CI job has already started")
        if row is None:
            # A tombstone wins against a slow reserve request still checking GitHub.
            total = await session.scalar(
                select(func.count())
                .select_from(CIReservation)
                .where(
                    CIReservation.route_id == route.id,
                    CIReservation.run_id == int(claims["run_id"]),
                )
            )
            if total >= 1000:
                raise HTTPException(409, "Workflow reservation quota exceeded")
            row = CIReservation(
                route_id=route.id,
                run_id=int(claims["run_id"]),
                run_attempt=int(claims["run_attempt"]),
                instance=data.instance,
                label="powercell-" + new_id(),
                status="cancelled",
                reason="bootstrap_cancelled",
                deadline=now,
                expires_at=now,
                next_check=now,
                finished_at=now,
                cleaned_at=now,
            )
            session.add(row)
        elif row.status in ACTIVE:
            row.status, row.reason, row.finished_at = "cancelled", "bootstrap_cancelled", now
            row.credentials = None
            row.next_check = min(row.next_check, now)
            if row.isolate_id:
                await destroy_isolate(
                    session,
                    await session.get(Isolate, row.isolate_id),
                    now,
                    request.app.state.settings,
                    "ci_bootstrap_cancelled",
                )
        await session.flush()
        return decision(row)


@router.get("/routes/{route_id}/workflow")
async def workflow_example(route_id: UUID, request: Request):
    from throng_api.ci_workflow import render

    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        route = await owned(session, CIRoute, route_id, principal)
        if route.company_id:
            await company_access(session, principal, route.company_id, permission="execution.read")
        return {"path": route.workflow_path, "yaml": render(route, service(request).api_origin)}
