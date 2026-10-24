"""User-scoped repository discovery, global filtering and CI setup metadata."""

import asyncio
import base64
import time
from collections import defaultdict
from typing import Literal
from urllib.parse import quote
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from throng_api.ci import readable_routes, verify_repository
from throng_api.ci_policy import WORKFLOW
from throng_api.github import external_error, principal_user, service, user_get, user_token
from throng_api.github_client import GitHubError
from throng_api.models import CIReservation, GitHubAccount
from throng_api.security import access_token_scheme, authenticate
from throng_api.serialization import public

DISCOVERY_TIMEOUT = 60

router = APIRouter(
    prefix="/v1/github",
    tags=["GitHub repository catalog"],
    dependencies=[Depends(access_token_scheme)],
)


async def provider_pages(request, actor, token, path, key, **params):
    """Read complete provider pages before applying global sorting or pagination."""
    rows, page = [], 1
    while True:
        data = await user_get(
            request, actor, path, token=token, params={**params, "per_page": 100, "page": page}
        )
        if key and (not isinstance(data, dict) or key not in data):
            raise GitHubError()
        batch = data[key] if key else data
        if not isinstance(batch, list):
            raise GitHubError()
        rows.extend(batch)
        if len(batch) < 100 or (key and page * 100 >= data.get("total_count", float("inf"))):
            return rows
        page += 1


def repository_metadata(row, installation, login):
    owner = row.get("owner", {})
    owner_login = owner.get("login") or row["full_name"].split("/")[0]
    owner_type = owner.get("type") or installation.get("account", {}).get("type")
    return {
        "id": row["id"],
        "installation_id": installation["id"],
        "full_name": row["full_name"],
        "owner_login": owner_login,
        "owner_type": "personal"
        if owner_type == "User" or (not owner_type and owner_login == login)
        else "organization",
        "private": row.get("private", False),
        "default_branch": row.get("default_branch", "main"),
        "can_administer": row.get("permissions", {}).get("admin", False),
        "updated_at": row.get("pushed_at") or row.get("updated_at"),
    }


async def discover(request, actor, refresh):
    integration = service(request)
    try:
        token = await user_token(request, actor)
    except GitHubError as error:
        raise external_error(error) from error
    async with request.app.state.db.read() as session:
        account = await session.get(GitHubAccount, actor)
        if not account:
            raise HTTPException(409, "Connect GitHub first")
        grant, login = account.tokens, account.login
    cache = integration.catalog_cache
    now = time.monotonic()
    cached = cache.get(actor)
    if not refresh and cached and cached[0] == grant and cached[1] > now:
        return cached[2]
    try:
        async with asyncio.timeout(DISCOVERY_TIMEOUT), integration.client.connection():
            installations = await provider_pages(
                request, actor, token, "/user/installations", "installations"
            )
            repositories = {}
            for installation in installations:
                if installation.get("app_id") != integration.app_id or installation.get(
                    "suspended_at"
                ):
                    continue
                rows = await provider_pages(
                    request,
                    actor,
                    token,
                    f"/user/installations/{installation['id']}/repositories",
                    "repositories",
                )
                for row in rows:
                    repositories[row["id"]] = repository_metadata(row, installation, login)
    except GitHubError as error:
        raise external_error(error) from error
    except TimeoutError as error:
        raise HTTPException(503, "GitHub repository discovery timed out; retry") from error
    cache.clear() if len(cache) >= 128 else None
    cache[actor] = (grant, time.monotonic() + 30, list(repositories.values()))
    return cache[actor][2]


def run_summary(binding, run):
    """Link a result to the binding that actually produced it, including archived runs."""
    if run is None:
        return None
    return {
        "id": run.id,
        "run_id": run.run_id,
        "run_attempt": run.run_attempt,
        "status": run.status,
        "reason": run.reason,
        "created_at": run.created_at.isoformat(),
        "workflow_path": run.workflow_path or binding.workflow_path,
        "url": f"https://github.com/{binding.repository}/actions/runs/{run.run_id}/attempts/{run.run_attempt}",
    }


async def binding_summaries(request):
    async with request.app.state.db.read() as session:
        routes = await readable_routes(session, await authenticate(session, request))
        identifiers = [row.id for row in routes]
        latest = (
            select(
                CIReservation.id,
                func.row_number()
                .over(
                    partition_by=CIReservation.route_id,
                    order_by=(CIReservation.created_at.desc(), CIReservation.id.desc()),
                )
                .label("position"),
            )
            .where(CIReservation.route_id.in_(identifiers))
            .subquery()
        )
        runs = list(
            await session.scalars(
                select(CIReservation)
                .join(latest, latest.c.id == CIReservation.id)
                .where(latest.c.position == 1)
            )
        )
        confirmed = set(
            (
                await session.execute(
                    select(CIReservation.route_id, CIReservation.route_revision)
                    .where(
                        CIReservation.route_id.in_(identifiers),
                        CIReservation.started_at.is_not(None),
                    )
                    .distinct()
                )
            ).all()
        )
        by_repository = defaultdict(list)
        by_route = {row.route_id: row for row in runs}
        occupied = set(
            await session.scalars(
                select(CIReservation.route_id)
                .where(
                    CIReservation.route_id.in_(identifiers),
                    (
                        CIReservation.status.in_(("preparing", "ready", "selected", "running"))
                        | CIReservation.cleaned_at.is_(None)
                    ),
                )
                .distinct()
            )
        )
        for route in routes:
            by_repository[route.repository_id].append(route)
        result = {}
        for repository_id, bindings in by_repository.items():
            bindings.sort(key=lambda row: (row.enabled, row.created_at, row.id), reverse=True)
            active = [row for row in bindings if row.enabled]
            connected = any((row.id, row.revision) in confirmed for row in active)
            last = max(
                (by_route[row.id] for row in bindings if row.id in by_route),
                key=lambda row: (row.created_at, row.id),
                default=None,
            )
            summary = {
                "state": "connected" if connected else "pending" if active else "disabled",
                "binding_id": bindings[0].id,
                "active_bindings": len(active),
                "bindings": [
                    {
                        **public(row),
                        "active_runs": row.id in occupied,
                        "state": "disabled"
                        if not row.enabled
                        else "connected"
                        if (row.id, row.revision) in confirmed
                        else "pending",
                        "last_run": run_summary(row, by_route.get(row.id)),
                    }
                    for row in bindings
                ],
                "last_run": None,
            }
            if last:
                binding = next(row for row in bindings if row.id == last.route_id)
                summary["last_run"] = run_summary(binding, last)
            result[repository_id] = summary
        return result


@router.get("/repositories")
async def repository_catalog(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    owner_type: Literal["all", "personal", "organization"] = "all",
    owner: str = Query("", max_length=100),
    search: str = Query("", max_length=200),
    state: Literal["all", "unconfigured", "pending", "connected", "disabled"] = "all",
    sort: Literal["name", "name_desc", "last_run", "updated"] = "name",
    refresh: bool = False,
):
    actor = await principal_user(request)
    rows = await discover(request, actor, refresh)
    summaries = await binding_summaries(request)
    owners = sorted({row["owner_login"] for row in rows}, key=str.casefold)
    items = []
    for repository in rows:
        summary = summaries.get(
            repository["id"],
            {"state": "unconfigured", "binding_id": None, "active_bindings": 0, "last_run": None},
        )
        row = {
            **repository,
            **{name: value for name, value in summary.items() if name != "bindings"},
        }
        if owner_type != "all" and row["owner_type"] != owner_type:
            continue
        if owner and row["owner_login"].casefold() != owner.casefold():
            continue
        if search.casefold().strip() not in row["full_name"].casefold() or (
            state != "all" and row["state"] != state
        ):
            continue
        items.append(row)
    items.sort(key=lambda row: (row["full_name"].casefold(), row["id"]))
    if sort == "name_desc":
        items.reverse()
    elif sort in {"last_run", "updated"}:
        items.sort(
            key=lambda row: (
                (row["last_run"] or {}).get("created_at", "")
                if sort == "last_run"
                else row["updated_at"] or ""
            ),
            reverse=True,
        )
    start = (page - 1) * per_page
    return {
        "repositories": items[start : start + per_page],
        "total_count": len(items),
        "owners": owners,
        "page": page,
        "per_page": per_page,
    }


@router.get("/repositories/{repository_id}/configuration")
async def repository_configuration(
    repository_id: int,
    request: Request,
    installation_id: int = Query(gt=0),
    branch: str | None = Query(None, max_length=239),
    binding_id: UUID | None = None,
):
    import re

    actor = await principal_user(request)
    repository = await verify_repository(
        request, actor, installation_id, repository_id, require_admin=False
    )
    summaries = await binding_summaries(request)
    summary = summaries.get(
        repository_id, {"state": "unconfigured", "bindings": [], "last_run": None}
    )
    primary = next(
        (
            row
            for row in summary["bindings"]
            if (
                row["id"] == str(binding_id)
                if binding_id
                else row["installation_id"] == installation_id
            )
        ),
        None,
    )
    if binding_id and primary is None:
        raise HTTPException(404, "Binding unavailable in this repository")
    if primary:
        summary = {
            **summary,
            "binding_id": primary["id"],
            "state": primary["state"],
            "last_run": primary["last_run"],
        }
    else:
        summary = {**summary, "binding_id": None}
    integration = service(request)
    ref = branch or (
        primary["ref"].removeprefix("refs/heads/")
        if primary
        else repository.get("default_branch", "main")
    )
    try:
        token = await user_token(request, actor)
        async with asyncio.timeout(DISCOVERY_TIMEOUT), integration.client.connection():
            branches = await provider_pages(
                request, actor, token, f"/repositories/{repository_id}/branches", None
            )
            workflows = await provider_pages(
                request,
                actor,
                token,
                f"/repositories/{repository_id}/actions/workflows",
                "workflows",
            )
            definitions = []
            for workflow in workflows:
                path = workflow["path"]
                if not re.fullmatch(WORKFLOW, path):
                    continue
                try:
                    content = await user_get(
                        request,
                        actor,
                        f"/repositories/{repository_id}/contents/{quote(path, safe='/')}",
                        token=token,
                        params={"ref": ref},
                    )
                except GitHubError as error:
                    # Actions also lists workflows from other branches and deleted files.
                    if error.status == 404:
                        continue
                    raise
                jobs, parse_error = [], False
                try:
                    raw = base64.b64decode(content["content"].replace("\n", ""), validate=True)
                    if len(raw) > 1024 * 1024:
                        raise ValueError()
                    # Job IDs remain strings even when YAML 1.1 treats them as bool/null.
                    parsed = yaml.load(raw, Loader=yaml.BaseLoader)
                    if not isinstance(parsed, dict) or not isinstance(parsed.get("jobs"), dict):
                        raise ValueError()
                    jobs = [
                        key
                        for key in parsed.get("jobs", {})
                        if isinstance(key, str)
                        and isinstance(parsed["jobs"][key], dict)
                        and "uses" not in parsed["jobs"][key]
                        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key)
                        and not re.fullmatch(
                            r"powercell_route_[0-9a-f]{32}(?:_(?:[0-9a-f]{8}|[0-9a-f]{32}))?",
                            key,
                        )
                    ]
                except (
                    ValueError,
                    KeyError,
                    AttributeError,
                    TypeError,
                    RecursionError,
                    yaml.YAMLError,
                ):
                    parse_error = True
                definitions.append(
                    {
                        "path": path,
                        "name": workflow.get("name", path),
                        "jobs": jobs,
                        "parse_error": parse_error,
                    }
                )
    except GitHubError as error:
        raise external_error(error) from error
    except TimeoutError as error:
        raise HTTPException(503, "GitHub workflow discovery timed out; retry") from error
    return {
        "repository": repository_metadata(repository, {"id": installation_id}, ""),
        "branches": [row["name"] for row in branches],
        "workflows": definitions,
        **summary,
    }
