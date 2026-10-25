"""CI history stays within the binding revision authorized before an ownership transfer."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.platform.api.e2e.test_github import connect
from tests.platform.api.token_helpers import move_to_legacy
from throng_api import ci, ci_bindings, github_catalog
from throng_api.models import CIReservation, CIRoute

from .test_ci import reserve
from .test_repository_bindings import catalog_peer as catalog_peer
from .test_repository_bindings import ci_peer as ci_peer
from .test_repository_bindings import create, execution, policy
from .test_repository_bindings import github as github

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.fixture
async def company_binding_history(api, client, company, catalog_peer, ci_peer, clock, request):
    """Keep two authorized policy revisions and reservations, including an original revision-one run."""
    response = await api.post("/v1/companies", json={"name": "Second pool"})
    assert response.status_code == 201, response.text
    other_company = response.json()
    workflow_policy = {
        "workflow_paths": None if getattr(request, "param", False) else [],
        "workflow_path": ".github/workflows/ci.yml",
    }
    binding = await create(api, company_id=company["id"], **workflow_policy)
    runs = []
    for index in range(2):
        if index:
            response = await api.patch(
                f"/v1/github/routes/{binding['id']}",
                json={
                    "revision": 1,
                    "configuration": policy(
                        company_id=company["id"], **workflow_policy, cpu_millis=1500
                    ),
                },
            )
            assert response.status_code == 200, response.text
            binding = response.json()
        clock.now += timedelta(seconds=1)
        response = await reserve(client, binding, ci_peer, instance=f"old-{index}")
        assert response.status_code == 200 and response.json()["status"] == "failed", response.text
        runs.append(response.json()["id"])
    return {"binding": binding, "other_company": other_company, "runs": runs}


@pytest.mark.parametrize("offset", [0, 1])
async def test_revision_page_and_count_exclude_new_owner_policy(
    api, client, company_token, company_binding_history, monkeypatch, offset
):
    """A's pending history page and total use its authorized revision snapshot after the binding moves to B."""
    binding = company_binding_history["binding"]
    authorized, resume = asyncio.Event(), asyncio.Event()
    original = ci_bindings.read_binding

    async def pause_after_binding_check(session, request, route_id):
        """Pause after the real ownership and execution permission checks."""
        row = await original(session, request, route_id)
        if request.headers.get("authorization") == "Bearer " + company_token["token"]:
            authorized.set()
            await resume.wait()
        return row

    monkeypatch.setattr(ci_bindings, "read_binding", pause_after_binding_check)
    pending = asyncio.create_task(
        client.get(
            f"/v1/github/routes/{binding['id']}/revisions",
            params={"offset": offset, "limit": 1},
            headers={"Authorization": "Bearer " + company_token["token"]},
        )
    )
    try:
        await asyncio.wait_for(authorized.wait(), 3)
        response = await api.patch(
            f"/v1/github/routes/{binding['id']}",
            json={
                "revision": binding["revision"],
                "configuration": policy(
                    company_id=company_binding_history["other_company"]["id"], cpu_millis=777
                ),
            },
        )
        assert response.status_code == 200, response.text
    finally:
        resume.set()
    response = await pending
    assert response.status_code == 200, response.text
    history = response.json()
    assert history["total_count"] == 2
    assert [row["revision"] for row in history["revisions"]] == [2 - offset]
    assert all(
        row["configuration"]["company_id"] == binding["company_id"] for row in history["revisions"]
    )


@pytest.mark.parametrize("offset", [0, 1])
async def test_run_page_excludes_reservations_created_for_new_owner(
    api,
    client,
    company,
    company_token,
    company_binding_history,
    ci_peer,
    clock,
    monkeypatch,
    offset,
):
    """A's pending run page retains its old runs without including a reservation admitted under B's later policy."""
    binding = company_binding_history["binding"]
    authorized, resume = asyncio.Event(), asyncio.Event()
    original = ci.company_access

    async def pause_after_execution_permission(session, principal, company_id, *args, **kwargs):
        """Hold A's read after its live permission check while B creates a real OIDC reservation."""
        result = await original(session, principal, company_id, *args, **kwargs)
        if principal.company_id == company["id"]:
            authorized.set()
            await resume.wait()
        return result

    monkeypatch.setattr(ci, "company_access", pause_after_execution_permission)
    pending = asyncio.create_task(
        client.get(
            f"/v1/github/routes/{binding['id']}/runs",
            params={"offset": offset, "limit": 1},
            headers={"Authorization": "Bearer " + company_token["token"]},
        )
    )
    try:
        await asyncio.wait_for(authorized.wait(), 3)
        response = await api.patch(
            f"/v1/github/routes/{binding['id']}",
            json={
                "revision": binding["revision"],
                "configuration": policy(company_id=company_binding_history["other_company"]["id"]),
            },
        )
        assert response.status_code == 200, response.text
        clock.now += timedelta(seconds=1)
        response = await reserve(client, response.json(), ci_peer, instance="new-owner")
        assert response.status_code == 200 and response.json()["status"] == "failed", response.text
    finally:
        resume.set()
    response = await pending
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [company_binding_history["runs"][1 - offset]]


@pytest.mark.parametrize("company_binding_history", [False, True], indirect=True)
async def test_archived_backtransfer_keeps_authorized_policy_and_run_history(
    api, client, company, company_token, company_binding_history, ci_peer, clock
):
    """Modern and legacy bindings retain earlier policy and revision-one runs after A→B→A and archival."""
    binding = company_binding_history["binding"]
    endpoint = f"/v1/github/routes/{binding['id']}"
    for company_id in (company_binding_history["other_company"]["id"], company["id"]):
        response = await api.patch(
            endpoint,
            json={
                "revision": binding["revision"],
                "configuration": policy(
                    company_id=company_id,
                    workflow_paths=binding["workflow_paths"],
                    workflow_path=binding["workflow_path"],
                ),
            },
        )
        assert response.status_code == 200, response.text
        binding = response.json()
        clock.now += timedelta(seconds=1)
        response = await reserve(
            client, binding, ci_peer, instance=f"revision-{binding['revision']}"
        )
        assert response.status_code == 200 and response.json()["status"] == "failed", response.text
    assert (await api.delete(endpoint)).status_code == 204
    headers = {"Authorization": "Bearer " + company_token["token"]}
    response = await client.get(endpoint + "/revisions", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["total_count"] == 5
    assert [row["revision"] for row in response.json()["revisions"]] == [5, 4, 3, 2, 1]
    response = await client.get(endpoint + "/runs", headers=headers)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 4
    assert set(company_binding_history["runs"]) <= {row["id"] for row in response.json()}


@pytest.mark.parametrize("offset", [0, 1])
@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("company_reader", [False, True])
async def test_route_pages_use_current_execution_permission(
    api,
    other_api,
    client,
    app,
    company,
    membership,
    other_user,
    catalog_peer,
    github,
    monkeypatch,
    offset,
    historical,
    company_reader,
):
    """Personal and company readers cannot use a captured ACL to read new organization bindings after execution.read is revoked; allowed personal rows survive filtering before pagination."""
    response = await api.post("/v1/companies", json={"name": "Unreadable pool"})
    assert response.status_code == 201, response.text
    denied_company = response.json()
    response = await api.post(
        "/v1/memberships",
        json={
            "company_id": denied_company["id"],
            "user_id": other_user["id"],
            "permissions": [],
        },
    )
    assert response.status_code == 201, response.text
    legacy_policy = {"workflow_paths": None, "workflow_path": ".github/workflows/ci.yml"}
    await create(api, company_id=denied_company["id"], **legacy_policy)
    github.peer.user_id += 1
    await connect(other_api)
    personal = await create(other_api, **legacy_policy)
    membership_path = f"/v1/memberships/{membership['id']}"
    response = await api.patch(
        membership_path, json={"permissions": ["tokens.manage", "execution.read"]}
    )
    assert response.status_code == 200, response.text
    response = await other_api.post(
        "/v1/api-tokens",
        json={
            **({"company_id": company["id"]} if company_reader else {"user_id": other_user["id"]}),
            "name": "Route reader",
            "scopes": ["read"],
        },
    )
    assert response.status_code == 201, response.text
    reader = response.json()
    if historical:
        async with app.state.db.write() as session:
            await move_to_legacy(session, reader["id"])
    entered, resume = asyncio.Event(), asyncio.Event()
    original = AsyncSession.scalars

    async def pause_before_route_data(session, statement, *args, **kwargs):
        """Pause only the route SELECT after any earlier Python membership snapshot."""
        if statement.column_descriptions[0].get("entity") is CIRoute and not entered.is_set():
            entered.set()
            await resume.wait()
        return await original(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalars", pause_before_route_data)
    pending = asyncio.create_task(
        client.get(
            "/v1/github/routes",
            params={"limit": 1, "offset": offset},
            headers={"Authorization": "Bearer " + reader["token"]},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        response = await api.patch(membership_path, json={"permissions": ["tokens.manage"]})
        assert response.status_code == 200, response.text
        for cpu in (777, 778):
            await create(api, company_id=company["id"], **legacy_policy, cpu_millis=cpu)
        new_personal = await create(other_api, **legacy_policy)
    finally:
        resume.set()
    response = await pending
    assert response.status_code == 200, response.text
    expected = [] if company_reader else [personal["id"], new_personal["id"]][offset : offset + 1]
    assert [row["id"] for row in response.json()] == expected, response.text


@pytest.mark.parametrize("endpoint", ["catalog", "configuration"])
@pytest.mark.parametrize("personal_state", ["active", "disabled"])
async def test_catalog_summary_keeps_authorized_binding_and_run_snapshots(
    api,
    other_api,
    client,
    app,
    membership,
    company_binding_history,
    github,
    ci_peer,
    clock,
    monkeypatch,
    endpoint,
    personal_state,
):
    """Both catalog APIs keep A's prior last_run and independent personal binding state when A's route moves to B and B starts a private run."""
    binding = company_binding_history["binding"]
    github.peer.user_id += 1
    await connect(other_api)
    personal = await create(other_api)
    clock.now += timedelta(seconds=1)
    await execution(app, clock, personal, started=True, active=personal_state == "active")
    if personal_state == "disabled":
        assert (await other_api.delete(f"/v1/github/routes/{personal['id']}")).status_code == 204
    path, params = (
        ("/v1/github/repositories", {"search": "test-org/repo"})
        if endpoint == "catalog"
        else (
            "/v1/github/repositories/42/configuration",
            {"installation_id": 456, "binding_id": binding["id"]},
        )
    )
    response = await other_api.get(path, params=params)
    assert response.status_code == 200, response.text
    baseline = response.json()["repositories"][0] if endpoint == "catalog" else response.json()
    entered, resume = asyncio.Event(), asyncio.Event()
    original = github_catalog.readable_routes

    async def pause_after_readable_routes(session, principal, **kwargs):
        """Retain actual authorized routes while a transfer admits a run under another revision."""
        rows = await original(session, principal, **kwargs)
        if principal.user_id == membership["user_id"]:
            entered.set()
            await resume.wait()
        return rows

    monkeypatch.setattr(github_catalog, "readable_routes", pause_after_readable_routes)
    pending = asyncio.create_task(other_api.get(path, params=params))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        response = await api.patch(
            f"/v1/github/routes/{binding['id']}",
            json={
                "revision": binding["revision"],
                "configuration": policy(company_id=company_binding_history["other_company"]["id"]),
            },
        )
        assert response.status_code == 200, response.text
        clock.now += timedelta(seconds=1)
        response = await reserve(client, response.json(), ci_peer, run_id="777777")
        assert response.status_code == 200, response.text
        new_run = response.json()
        async with app.state.db.write() as session:
            # A started reservation also exercises the occupied/confirmed queries.
            row = await session.get(CIReservation, new_run["id"])
            row.status, row.started_at, row.cleaned_at = "running", clock.now, None
    finally:
        resume.set()
    response = await pending
    assert response.status_code == 200, response.text
    summary = response.json()["repositories"][0] if endpoint == "catalog" else response.json()
    for field in ("state", "binding_id", "active_bindings", "last_run"):
        assert summary[field] == baseline[field], (field, summary)
    if endpoint == "configuration":
        assert summary["bindings"] == baseline["bindings"]
    assert new_run["id"] not in response.text
    assert "777777" not in response.text
