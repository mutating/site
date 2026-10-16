"""Repository CI bindings and OIDC-authenticated, idempotent runner reservations."""

import hashlib
import io
import re
import tarfile
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPBearer
from pydantic import Field, model_validator
from sqlalchemy import func, select

from throng_api.artifacts import check_storage_quota
from throng_api.execution import destroy_isolate
from throng_api.github import external_error, service, user_token
from throng_api.github_client import GitHubError
from throng_api.models import (
    Artifact,
    CIReservation,
    CIRoute,
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
    memory_bytes: int = Field(default=4 * GiB, gt=0, le=2**50)
    disk_bytes: int = Field(default=10 * GiB, ge=GiB, le=2**50)
    preparation_seconds: int = Field(default=90, ge=10, le=180)
    timeout_seconds: int = Field(default=3600, ge=60, le=21600)
    max_parallel: int = Field(default=1, ge=1, le=100)
    fallback: Literal["ubuntu-24.04-arm", "none"] = "ubuntu-24.04-arm"

    @model_validator(mode="after")
    def fallback_resources(self):
        # Conservative limits also cover private repositories on standard runners.
        if self.fallback != "none" and (
            self.cpu_millis > 2000 or self.memory_bytes > 8 * GiB or self.disk_bytes > 14 * GiB
        ):
            raise ValueError(
                "Standard fallback requires at most 2 CPU, 8 GiB RAM and 14 GiB disk; select none for larger jobs"
            )
        if (
            ".." in self.workflow_path
            or ".." in self.ref
            or "//" in self.ref
            or self.job_key == "powercell_route"
        ):
            raise ValueError("Invalid workflow path or branch")
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


async def verify_repository(request, user_id, installation_id, repository_id):
    integration = service(request)
    token = await user_token(request, user_id)
    try:
        installation = await integration.client.api("GET", f"/app/installations/{installation_id}")
        if installation.get("app_id") != integration.app_id or installation.get("suspended_at"):
            raise HTTPException(404, "GitHub installation unavailable")
        repository = await integration.client.api(
            "GET", f"/repositories/{repository_id}", token=token
        )
        if repository.get("id") != repository_id or not repository.get("permissions", {}).get(
            "admin"
        ):
            raise HTTPException(403, "GitHub repository administrator required")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository["full_name"]):
            raise GitHubError()
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
    repository = await verify_repository(request, user_id, data.installation_id, data.repository_id)
    # A deterministic empty tar initializes a fresh isolate; never a registered runner snapshot.
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w"):
        pass
    content = content.getvalue()
    key = new_id()
    path = request.app.state.settings.storage_path / key
    path.write_bytes(content)
    keep = False
    try:
        async with request.app.state.db.write() as session:
            principal = await authenticate(session, request, "manage")
            await authenticate(session, request, "execute")
            await pool_permission(session, principal, data.company_id)
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
        rows = await session.scalars(
            select(CIRoute)
            .where(owned_filter(CIRoute, principal))
            .order_by(CIRoute.created_at, CIRoute.id)
            .limit(limit)
            .offset(offset)
        )
        result = []
        for route in rows:
            if route.company_id:
                try:
                    await company_access(
                        session, principal, route.company_id, permission="execution.read"
                    )
                except HTTPException:
                    continue
            result.append(public(route))
        return result


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
                "url": f"https://github.com/{route.repository}/actions/runs/{row.run_id}",
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
            "backend": "powercell",
            "runs_on": ["self-hosted", row.label],
        }
    if row.status == "preparing":
        return {"id": row.id, "status": "preparing"}
    if row.status == "fallback" and row.fallback != "none":
        return {
            "id": row.id,
            "status": "ready",
            "backend": "github",
            "reason": row.reason,
            "runs_on": row.fallback,
        }
    return {"id": row.id, "status": "failed", "reason": row.reason or row.status}


@public_router.post("/routes/{route_id}/reserve")
async def reserve(route_id: UUID, data: Reserve, request: Request):
    started_at = request.app.state.clock()
    claims = await routing_identity(request, route_id)
    async with request.app.state.db.read() as session:
        route = await session.get(CIRoute, str(route_id))
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
    github_available = True
    if check_github:
        try:
            await verify_repository(request, creator_id, installation_id, repository_id)
        except (GitHubError, HTTPException):
            github_available = False
    now = request.app.state.clock()
    async with request.app.state.db.write() as session:
        route = await session.get(CIRoute, str(route_id))
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
            fallback=route.fallback,
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
            row.status = "fallback"
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
            row.isolate_id = isolate.id
        await session.flush()
        return decision(row)


@public_router.post("/routes/{route_id}/commit")
async def commit_selection(route_id: UUID, data: Reserve, request: Request):
    claims = await routing_identity(request, route_id)
    now = request.app.state.clock()
    async with request.app.state.db.write() as session:
        row = await session.scalar(
            select(CIReservation).where(
                CIReservation.route_id == str(route_id),
                CIReservation.run_id == int(claims["run_id"]),
                CIReservation.run_attempt == int(claims["run_attempt"]),
                CIReservation.instance == data.instance,
            )
        )
        if row is None:
            raise HTTPException(409, "CI reservation does not exist")
        route = await session.get(CIRoute, row.route_id)
        if row.expires_at <= now or not await creator_authorized(session, route):
            raise HTTPException(409, "CI selection has expired or authorization was revoked")
        if row.status == "ready" and row.deadline > now:
            row.status = "selected"
            row.expires_at = min(row.expires_at, now + timedelta(seconds=300))
            isolate = await session.get(Isolate, row.isolate_id)
            isolate.expires_at = row.expires_at
        elif row.status not in {"selected", "running"}:
            raise HTTPException(409, "CI runner is no longer ready")
        return decision(row)


@public_router.post("/routes/{route_id}/cancel")
async def cancel_selection(route_id: UUID, data: Reserve, request: Request):
    claims = await routing_identity(request, route_id)
    now = request.app.state.clock()
    async with request.app.state.db.write() as session:
        route = await session.get(CIRoute, str(route_id))
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
                status="fallback",
                reason="bootstrap_cancelled",
                deadline=now,
                expires_at=now,
                next_check=now,
                finished_at=now,
                cleaned_at=now,
                fallback=route.fallback,
            )
            session.add(row)
        elif row.status in ACTIVE:
            row.status, row.reason, row.finished_at = "fallback", "bootstrap_cancelled", now
            row.credentials = None
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
