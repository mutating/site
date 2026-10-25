"""Repository binding editing with optimistic concurrency and an immutable audit log."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import func, or_, select

from throng_api.artifacts import check_storage_quota
from throng_api.ci import (
    ACTIVE,
    RouteCreate,
    pool_permission,
    validate_computers,
    verify_repository,
)
from throng_api.ci_policy import record_revision, snapshot
from throng_api.ci_profiles import profile_values
from throng_api.github import external_error, user_token
from throng_api.github_client import GitHubError
from throng_api.models import (
    Artifact,
    CIReservation,
    CIRoute,
    CIRouteRevision,
    GitHubAccount,
    User,
)
from throng_api.schemas import Input, PaginationOffset
from throng_api.security import (
    access_token_scheme,
    authenticate,
    company_access,
    owned,
    owner_values,
    personal,
)
from throng_api.serialization import public

router = APIRouter(
    prefix="/v1/github",
    tags=["Repository CI bindings"],
    dependencies=[Depends(access_token_scheme)],
)


class RoutePatch(Input):
    revision: int = Field(ge=1)
    configuration: RouteCreate | None = None
    enabled: bool | None = None


async def read_binding(session, request, route_id):
    principal = await authenticate(session, request)
    route = await owned(session, CIRoute, route_id, principal)
    if route.company_id:
        await company_access(session, principal, route.company_id, permission="execution.read")
    return route


@router.get("/routes/{route_id}")
async def get_binding(route_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        return public(await read_binding(session, request, route_id))


@router.patch("/routes/{route_id}")
async def edit_binding(route_id: UUID, data: RoutePatch, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request, "manage")
        await authenticate(session, request, "execute")
        actor = personal(principal)
        route = await owned(session, CIRoute, route_id, principal)
        await pool_permission(session, principal, route.company_id)
        installation_id, repository_id = route.installation_id, route.repository_id
    configuration = data.configuration
    if configuration and (
        configuration.installation_id != installation_id
        or configuration.repository_id != repository_id
    ):
        raise HTTPException(422, "The repository of an existing binding cannot change")
    repository = None
    grant = None
    if configuration or data.enabled is True:
        try:
            await user_token(request, actor)
        except GitHubError as error:
            raise external_error(error) from error
        async with request.app.state.db.read() as session:
            account = await session.get(GitHubAccount, actor)
            if not account:
                raise HTTPException(409, "Connect GitHub before editing this binding")
            grant = account.tokens
        repository = await verify_repository(request, actor, installation_id, repository_id)
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        await authenticate(session, request, "execute")
        route = await owned(session, CIRoute, route_id, principal)
        await pool_permission(session, principal, route.company_id)
        if route.revision != data.revision:
            raise HTTPException(409, "Binding changed; reload before saving")
        before = snapshot(route)
        values = (
            configuration.model_dump(
                exclude={
                    "installation_id",
                    "repository_id",
                    "company_id",
                    "computer_ids",
                    "workflow_profiles",
                }
            )
            if configuration
            else {}
        )
        if configuration:
            await pool_permission(session, principal, configuration.company_id)
            owner = await owner_values(session, principal, configuration.company_id)
            await validate_computers(session, configuration, owner)
            values.update(
                owner,
                computer_ids=sorted(set(map(str, configuration.computer_ids))),
                workflow_profiles=profile_values(configuration),
                repository_binding=configuration.workflow_paths is not None,
            )
        if data.enabled is not None:
            values["enabled"] = data.enabled
        changed = any(getattr(route, name) != value for name, value in values.items()) or (
            repository is not None
            and (
                route.authorized_by != actor
                or route.repository != repository["full_name"]
                or route.repository_owner_id != repository["owner"]["id"]
            )
        )
        if not changed:
            return public(route)
        # Disabling is always allowed. All other edits wait for runner/VM cleanup,
        # so a running reservation can never silently inherit another resource policy.
        disabling_only = values.get("enabled") is False and all(
            name == "enabled" or getattr(route, name) == value for name, value in values.items()
        )
        if not disabling_only and await session.scalar(
            select(CIReservation.id)
            .where(
                CIReservation.route_id == route.id,
                or_(CIReservation.status.in_(ACTIVE), CIReservation.cleaned_at.is_(None)),
            )
            .limit(1)
        ):
            raise HTTPException(409, "Wait for active runs and runner cleanup before editing")
        account = await session.get(GitHubAccount, actor)
        if not disabling_only and (not account or account.tokens != grant):
            raise HTTPException(409, "GitHub connection changed; retry editing the binding")
        owner_user, owner_company = (
            values.get("user_id", route.user_id),
            values.get("company_id", route.company_id),
        )
        if values.get("enabled", route.enabled) and values.get(
            "repository_binding", route.repository_binding
        ):
            duplicate = await session.scalar(
                select(CIRoute.id)
                .where(
                    CIRoute.id != route.id,
                    CIRoute.repository_id == route.repository_id,
                    CIRoute.user_id == owner_user,
                    CIRoute.company_id == owner_company,
                    CIRoute.enabled.is_(True),
                )
                .limit(1)
            )
            if duplicate:
                raise HTTPException(
                    409, "An active binding already exists for this repository and pool owner"
                )
        if (owner_user, owner_company) != (route.user_id, route.company_id):
            owner = {"user_id": owner_user, "company_id": owner_company}
            count = await session.scalar(
                select(func.count())
                .select_from(CIRoute)
                .where(CIRoute.user_id == owner_user, CIRoute.company_id == owner_company)
            )
            if count >= 100:
                raise HTTPException(409, "CI route quota exceeded")
            artifact = await session.get(Artifact, route.state_artifact_id)
            await check_storage_quota(
                session, owner, artifact.size_bytes, request.app.state.settings
            )
            artifact.user_id, artifact.company_id = owner_user, owner_company
        for name, value in values.items():
            setattr(route, name, value)
        if repository is not None:
            route.authorized_by = actor
            route.repository = repository["full_name"]
            route.repository_owner_id = repository["owner"]["id"]
        action = (
            "enabled"
            if not before["enabled"] and route.enabled
            else "disabled"
            if before["enabled"] and not route.enabled
            else "reauthorized"
            if before == snapshot(route)
            else "updated"
        )
        record_revision(session, route, actor, action, request.app.state.clock())
        await session.flush()
        return public(route)


@router.get("/routes/{route_id}/revisions")
async def binding_revisions(
    route_id: UUID,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: PaginationOffset = 0,
):
    async with request.app.state.db.read() as session:
        route = await read_binding(session, request, route_id)
        # The binding may move to another owner after authorization. Its audit
        # rows are immutable, so retain only the authorized revision snapshot.
        conditions = (
            CIRouteRevision.route_id == route.id,
            CIRouteRevision.revision <= route.revision,
        )
        total = await session.scalar(
            select(func.count()).select_from(CIRouteRevision).where(*conditions)
        )
        rows = await session.scalars(
            select(CIRouteRevision)
            .where(*conditions)
            .order_by(CIRouteRevision.revision.desc())
            .offset(offset)
            .limit(limit)
        )
        result = []
        for row in rows:
            actor = await session.get(User, row.actor_id) if row.actor_id else None
            previous = await session.scalar(
                select(CIRouteRevision).where(
                    CIRouteRevision.route_id == route.id,
                    CIRouteRevision.revision == row.revision - 1,
                )
            )
            before = previous.configuration if previous else {}
            result.append(
                {
                    **public(row),
                    "changes": [
                        {"field": name, "before": before.get(name), "after": value}
                        for name, value in row.configuration.items()
                        if name not in before or before[name] != value
                    ],
                    "actor": {
                        "id": actor.id,
                        "avatar_version": actor.avatar_version,
                        "first_name": actor.first_name,
                        "last_name": actor.last_name,
                        "nickname": actor.nickname,
                    }
                    if actor
                    else None,
                }
            )
        return {"total_count": total, "revisions": result}
