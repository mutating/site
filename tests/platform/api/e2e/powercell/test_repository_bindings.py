"""Repository catalog and editable policies through HTTP, SQLite and signed OIDC."""

import asyncio
import base64
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import yaml
from sqlalchemy import select

from tests.platform.api.e2e.test_github import connect
from throng_api.models import Artifact, CIReservation, GitHubAccount

from .test_ci import ci_peer as ci_peer_fixture
from .test_ci import github as github_fixture
from .test_ci import powercell as powercell_fixture
from .test_ci import reserve

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]
GIB = 1024**3
ci_peer = ci_peer_fixture
github = github_fixture
powercell = powercell_fixture


def policy(**values):
    """Supply a repository-wide policy without inventing a workflow or target job."""
    return {"installation_id": 456, "repository_id": 42, "workflow_paths": [], **values}


@pytest.fixture
async def catalog_peer(ci_peer, github):
    """Fake only GitHub responses; discovery, policy decisions and storage stay real."""

    def repository(identifier, owner, name, kind):
        return {
            "id": identifier,
            "full_name": f"{owner}/{name}",
            "owner": {"login": owner, "type": kind},
            "permissions": {"admin": True},
            "private": True,
            "default_branch": "main",
            "pushed_at": "2026-10-08T12:00:00Z",
        }

    peer = SimpleNamespace(
        installations=[
            {
                **github.peer.installation,
                "account": {"login": "test-org", "type": "Organization"},
            },
            {
                **github.peer.installation,
                "id": 457,
                "account": {"login": "github-user", "type": "User"},
            },
        ],
        repositories={
            456: [
                repository(42, "test-org", "repo", "Organization"),
                *[
                    repository(1000 + index, "test-org", f"project-{index:03}", "Organization")
                    for index in range(120)
                ],
            ],
            457: [
                repository(5000 + index, "github-user", f"personal-{index:03}", "User")
                for index in range(101)
            ],
        },
        workflows=[
            {"name": "Deploy", "path": ".github/workflows/deploy.yml"},
            {"name": "Tests", "path": ".github/workflows/tests.yml"},
        ],
        sources={
            ".github/workflows/deploy.yml": "name: Deploy\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n",
            ".github/workflows/tests.yml": "jobs:\n  api: {}\n  web: {}\n  powercell_route_00000000000000000000000000000000: {}\n",
        },
        branches=[{"name": "main"}, {"name": "develop"}],
        calls=[],
        failures={},
        raw={},
        delays={},
        entered={},
    )
    original = github.service.client._transport

    async def handler(request):
        peer.calls.append(request)
        path = request.url.path
        if path in peer.entered:
            peer.entered[path].set()
        if path in peer.delays:
            await asyncio.sleep(peer.delays[path])
        if path in peer.raw:
            return httpx.Response(200, json=peer.raw[path])
        if path in peer.failures:
            return httpx.Response(
                peer.failures[path], json={"message": "redacted provider failure"}
            )
        page = int(request.url.params.get("page", "1"))
        start = (page - 1) * 100
        if path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "total_count": len(peer.installations),
                    "installations": peer.installations[start : start + 100],
                },
            )
        if path.startswith("/user/installations/") and path.endswith("/repositories"):
            rows = peer.repositories[int(path.split("/")[3])]
            return httpx.Response(
                200,
                json={
                    "total_count": len(rows),
                    "repositories": rows[start : start + 100],
                },
            )
        if path == "/repositories/42/branches":
            return httpx.Response(200, json=peer.branches[start : start + 100])
        if path == "/repositories/42/actions/workflows":
            return httpx.Response(
                200,
                json={
                    "total_count": len(peer.workflows),
                    "workflows": peer.workflows[start : start + 100],
                },
            )
        if path.startswith("/repositories/42/contents/"):
            source = peer.sources[path.removeprefix("/repositories/42/contents/")]
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(source.encode()).decode(),
                },
            )
        return await original.handle_async_request(request)

    github.service.client._transport = httpx.MockTransport(handler)
    return peer


async def create(api, **values):
    """Create a policy through the same endpoint as the new repository editor."""
    response = await api.post("/v1/github/routes", json=policy(**values))
    assert response.status_code == 201, response.text
    return response.json()


async def execution(app, clock, binding, *, started=False, reason="no_capacity", active=False):
    """Represent an authoritative execution fact without replacing the catalog API."""
    async with app.state.db.write() as session:
        session.add(
            CIReservation(
                route_id=binding["id"],
                route_revision=binding["revision"],
                workflow_path=".github/workflows/deploy.yml",
                run_id=1234,
                run_attempt=1,
                instance=uuid4().hex,
                status="running" if active else "completed" if started else "failed",
                reason=reason,
                label=uuid4().hex,
                deadline=clock.now,
                expires_at=clock.now + timedelta(hours=1),
                next_check=clock.now,
                created_at=clock.now,
                started_at=clock.now if started else None,
                finished_at=None if active else clock.now,
                cleaned_at=None if active else clock.now,
            )
        )


async def test_catalog_aggregates_installations_and_paginates_after_global_sort(api, catalog_peer):
    """A default page must include all owners, rather than just one installation/page."""
    first = (await api.get("/v1/github/repositories")).json()
    assert first["total_count"] == 222 and len(first["repositories"]) == 20
    assert {row["owner_type"] for row in first["repositories"]} == {"personal"}
    assert first["owners"] == ["github-user", "test-org"]
    last = (await api.get("/v1/github/repositories", params={"page": 12})).json()
    assert len(last["repositories"]) == 2
    assert any(
        request.url.params.get("page") == "2"
        for request in catalog_peer.calls
        if request.url.path.endswith("/repositories")
    )
    all_rows = (
        await api.get("/v1/github/repositories", params={"per_page": 100, "sort": "name_desc"})
    ).json()
    assert all_rows["repositories"][0]["full_name"] == "test-org/repo"
    assert (await api.get("/v1/github/repositories", params={"page": 1001})).json()[
        "repositories"
    ] == []


@pytest.mark.parametrize(
    "params,total",
    [
        ({"owner_type": "personal"}, 101),
        ({"owner_type": "organization"}, 121),
        ({"owner": "TEST-ORG"}, 121),
        ({"search": " PROJECT-11 "}, 10),
        ({"state": "connected"}, 0),
        ({"search": "absent"}, 0),
    ],
)
async def test_catalog_filters_apply_before_pagination(api, catalog_peer, params, total):
    """Filters cannot silently search or sort only the first GitHub page."""
    response = await api.get("/v1/github/repositories", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["total_count"] == total


@pytest.mark.parametrize(
    "path", ["/v1/github/installations", "/v1/github/installations/456/repositories"]
)
async def test_provider_page_numbers_are_not_arbitrarily_capped(api, catalog_peer, path):
    """Page 1001 is forwarded to GitHub; zero and negative pages remain invalid."""
    response = await api.get(path, params={"page": 1001})
    assert response.status_code == 200, response.text
    assert any(request.url.params.get("page") == "1001" for request in catalog_peer.calls)
    for value in (0, -1, "not-a-page"):
        assert (await api.get(path, params={"page": value})).status_code == 422


async def test_catalog_cache_is_refreshable_and_does_not_cache_binding_state(api, catalog_peer):
    """Refresh must discover newly granted repositories; CI states must always be fresh."""
    await api.get("/v1/github/repositories")
    calls = len(catalog_peer.calls)
    await api.get("/v1/github/repositories", params={"owner_type": "personal"})
    assert len(catalog_peer.calls) == calls
    catalog_peer.repositories[457].clear()
    assert (await api.get("/v1/github/repositories")).json()["total_count"] == 222
    assert (await api.get("/v1/github/repositories", params={"refresh": True})).json()[
        "total_count"
    ] == 121
    binding = await create(api)
    rows = (await api.get("/v1/github/repositories", params={"search": "test-org/repo"})).json()[
        "repositories"
    ]
    assert rows[0]["state"] == "pending" and rows[0]["binding_id"] == binding["id"]


async def test_setup_status_requires_confirmed_execution_and_updates_after_edit(
    api, catalog_peer, app, clock
):
    """Creating a record or failing preparation never proves workflow integration."""
    binding = await create(api)

    async def state():
        return (
            await api.get("/v1/github/repositories", params={"search": "test-org/repo"})
        ).json()["repositories"][0]

    assert (await state())["state"] == "pending"
    await execution(app, clock, binding)
    assert (await state())["state"] == "pending"
    await execution(app, clock, binding, started=True, reason="github_success")
    assert (await state())["state"] == "connected"
    updated = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={"revision": 1, "configuration": policy(memory_bytes=GIB)},
    )
    assert updated.status_code == 200, updated.text
    assert (await state())["state"] == "pending"
    assert (await state())["last_run"]["url"].endswith("/1234/attempts/1")
    assert (await api.delete(f"/v1/github/routes/{binding['id']}")).status_code == 204
    assert (await state())["state"] == "disabled"


async def test_edits_preserve_id_and_record_before_after_history(api, catalog_peer):
    """Resource edits must update a stable connection rather than accumulate disabled copies."""
    binding = await create(api)
    response = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={
            "revision": 1,
            "configuration": policy(
                cpu_millis=1000,
                memory_bytes=GIB,
                workflow_paths=[".github/workflows/deploy.yml"],
                ref="refs/heads/develop",
            ),
        },
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["id"] == binding["id"] and edited["revision"] == 2
    assert len((await api.get("/v1/github/routes")).json()) == 1
    history = (await api.get(f"/v1/github/routes/{binding['id']}/revisions")).json()
    assert history["total_count"] == 2
    assert [row["action"] for row in history["revisions"]] == ["updated", "created"]
    changes = {row["field"]: row for row in history["revisions"][0]["changes"]}
    assert changes["memory_bytes"] == {
        "field": "memory_bytes",
        "before": 4 * GIB,
        "after": GIB,
    }
    assert history["revisions"][1]["configuration"]["workflow_paths"] == []
    assert (await api.get(f"/v1/github/routes/{binding['id']}")).json() == edited


async def test_optimistic_edits_reject_lost_updates_and_do_not_record_noops(api, catalog_peer):
    """Two editors saving the same revision must not overwrite each other's settings."""
    binding = await create(api)
    endpoint = f"/v1/github/routes/{binding['id']}"
    responses = await asyncio.gather(
        *[
            api.patch(
                endpoint,
                json={"revision": 1, "configuration": policy(cpu_millis=value)},
            )
            for value in (1000, 3000)
        ]
    )
    assert sorted(row.status_code for row in responses) == [200, 409]
    saved = next(row.json() for row in responses if row.status_code == 200)
    response = await api.patch(
        endpoint,
        json={"revision": 2, "configuration": policy(cpu_millis=saved["cpu_millis"])},
    )
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert (await api.get(endpoint + "/revisions")).json()["total_count"] == 2


async def test_repository_wide_duplicates_are_rejected_atomically(api, catalog_peer, app):
    """Concurrent Connect clicks cannot create two repository-wide bindings or leak artifacts."""
    responses = await asyncio.gather(
        *[api.post("/v1/github/routes", json=policy()) for _ in range(2)]
    )
    assert sorted(row.status_code for row in responses) == [201, 409]
    async with app.state.db.read() as session:
        assert (
            len(
                list(
                    await session.scalars(
                        select(Artifact).where(Artifact.filename == "ci-empty-state.tar")
                    )
                )
            )
            == 1
        )


async def test_disable_reenable_and_disconnect_are_versioned(api, catalog_peer):
    """Disabling preserves audit/execution history and re-enabling reuses the same ID."""
    binding = await create(api)
    endpoint = f"/v1/github/routes/{binding['id']}"
    for version, enabled in [(1, False), (2, True)]:
        response = await api.patch(endpoint, json={"revision": version, "enabled": enabled})
        assert response.status_code == 200 and response.json()["revision"] == version + 1
    assert (await api.delete("/v1/github/connection")).status_code == 204
    history = (await api.get(endpoint + "/revisions", params={"limit": 2})).json()
    assert history["total_count"] == 4
    assert [row["action"] for row in history["revisions"]] == [
        "disconnected",
        "enabled",
    ]
    assert (await api.get(endpoint + "/revisions", params={"offset": 2})).json()["revisions"][0][
        "action"
    ] == "disabled"


async def test_active_reservations_block_policy_changes_but_allow_disable(
    api, catalog_peer, app, clock
):
    """A running VM cannot inherit edited limits or a different pool halfway through a job."""
    binding = await create(api)
    await execution(app, clock, binding, started=True, active=True)
    endpoint = f"/v1/github/routes/{binding['id']}"
    response = await api.patch(
        endpoint, json={"revision": 1, "configuration": policy(cpu_millis=1000)}
    )
    assert response.status_code == 409
    assert (await api.patch(endpoint, json={"revision": 1, "enabled": False})).status_code == 200
    assert (await api.patch(endpoint, json={"revision": 2, "enabled": True})).status_code == 409


async def test_workflow_discovery_returns_real_branch_names_and_job_ids(api, catalog_peer):
    """The editor must offer actual repository workflows/jobs instead of ci.yml/test placeholders."""
    response = await api.get(
        "/v1/github/repositories/42/configuration",
        params={"installation_id": 456, "branch": "develop"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["branches"] == ["main", "develop"]
    assert [row["jobs"] for row in data["workflows"]] == [["deploy"], ["api", "web"]]
    assert all(
        request.url.params["ref"] == "develop"
        for request in catalog_peer.calls
        if "/contents/" in request.url.path
    )


@pytest.mark.parametrize(
    "source", ["jobs: [test]", "not a mapping", "jobs: [", "jobs:\n  3: {}", "jobs: {}"]
)
async def test_invalid_or_empty_workflow_definitions_do_not_invent_job_ids(
    api, catalog_peer, source
):
    """Malformed YAML must remain visible as unavailable instead of manufacturing a test job."""
    catalog_peer.sources[".github/workflows/deploy.yml"] = source
    response = await api.get(
        "/v1/github/repositories/42/configuration", params={"installation_id": 456}
    )
    assert response.status_code == 200, response.text
    assert response.json()["workflows"][0]["jobs"] == []


async def test_all_workflow_scope_and_narrowing_verify_signed_oidc(
    api, catalog_peer, ci_peer, client
):
    """Repository-wide scope admits valid workflows without opening another repo, branch or PR."""
    binding = await create(api)
    for path in ("deploy.yml", "tests.yml"):
        response = await reserve(
            client,
            binding,
            ci_peer,
            run_id=str(3000 + len(path)),
            workflow_ref=f"test-org/repo/.github/workflows/{path}@refs/heads/main",
        )
        assert response.status_code == 200, response.text
        assert response.json()["reason"] == "no_capacity"
    for claims in (
        {"event_name": "pull_request"},
        {"repository": "attacker/repo"},
        {"ref": "refs/heads/develop"},
        {"workflow_ref": "test-org/repo/../../evil.yml@refs/heads/main"},
    ):
        assert (await reserve(client, binding, ci_peer, **claims)).status_code == 403
    response = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={
            "revision": 1,
            "configuration": policy(workflow_paths=[".github/workflows/deploy.yml"]),
        },
    )
    assert response.status_code == 200, response.text
    assert (await reserve(client, binding, ci_peer)).status_code == 403


async def test_generated_examples_use_actual_jobs_and_independent_instances(api, catalog_peer):
    """Two jobs sharing a repository binding need distinct reservations and routing IDs."""
    binding = await create(api)
    examples = []
    for job in ("api", "web"):
        response = await api.get(
            f"/v1/github/routes/{binding['id']}/workflow",
            params={"workflow_path": ".github/workflows/tests.yml", "job_key": job},
        )
        assert response.status_code == 200, response.text
        parsed = yaml.safe_load(response.json()["yaml"])
        router, target = list(parsed["jobs"].values())
        instance = router["steps"][0]["env"]["POWERCELL_INSTANCE"]
        assert target["steps"][0]["env"]["POWERCELL_INSTANCE"] == instance
        assert list(parsed["jobs"])[1] == job and target["strategy"]["fail-fast"] is True
        examples.append((next(iter(parsed["jobs"])), instance))
    assert examples[0][0] != examples[1][0] and examples[0][1] != examples[1][1]


async def test_other_users_cannot_read_edit_or_audit_a_personal_binding(
    api, catalog_peer, other_api
):
    """New detail/edit/audit endpoints must retain the original personal ownership boundary."""
    binding = await create(api)
    endpoint = f"/v1/github/routes/{binding['id']}"
    assert (await other_api.get(endpoint)).status_code == 404
    assert (await other_api.get(endpoint + "/revisions")).status_code == 404
    assert (
        await other_api.patch(endpoint, json={"revision": 1, "enabled": False})
    ).status_code == 404


async def test_pool_change_moves_only_empty_state_and_records_owner(
    api, catalog_peer, company, app
):
    """Moving a binding must not leave its required state artifact owned by the previous pool."""
    binding = await create(api)
    response = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={"revision": 1, "configuration": policy(company_id=company["id"])},
    )
    assert response.status_code == 200, response.text
    async with app.state.db.read() as session:
        artifact = await session.get(Artifact, binding["state_artifact_id"])
        assert artifact.company_id == company["id"] and artifact.user_id is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "/user/installations",
        "/user/installations/456/repositories",
        "/repositories/42/branches",
        "/repositories/42/actions/workflows",
    ],
)
async def test_provider_failures_are_errors_rather_than_empty_catalogs(api, catalog_peer, endpoint):
    """GitHub outages must not be displayed as no repositories/workflows or disconnected CI."""
    catalog_peer.failures[endpoint] = 503
    path = (
        "/v1/github/repositories/42/configuration?installation_id=456"
        if endpoint.startswith("/repositories")
        else "/v1/github/repositories"
    )
    response = await api.get(path)
    assert response.status_code == 502
    assert "redacted provider failure" not in response.text


async def test_workflows_missing_on_selected_branch_do_not_hide_existing_jobs(api, catalog_peer):
    """GitHub's cross-branch workflow list can contain deleted files; a 404 must not break the editor."""
    catalog_peer.failures["/repositories/42/contents/.github/workflows/deploy.yml"] = 404
    response = await api.get(
        "/v1/github/repositories/42/configuration",
        params={"installation_id": 456, "branch": "develop"},
    )
    assert response.status_code == 200, response.text
    assert [row["path"] for row in response.json()["workflows"]] == [".github/workflows/tests.yml"]
    catalog_peer.failures["/repositories/42/contents/.github/workflows/tests.yml"] = 503
    assert (
        await api.get("/v1/github/repositories/42/configuration", params={"installation_id": 456})
    ).status_code == 502


async def test_run_history_pagination_never_repeats_the_first_hundred_rows(
    api, catalog_peer, app, clock
):
    """The browser reads all pages: ignoring offset would loop forever once a route had 100 runs."""
    binding = await create(api)
    for _ in range(101):
        await execution(app, clock, binding)
    endpoint = f"/v1/github/routes/{binding['id']}/runs"
    first = (await api.get(endpoint, params={"limit": 100})).json()
    second = (await api.get(endpoint, params={"limit": 100, "offset": 100})).json()
    assert len(first) == 100 and len(second) == 1
    assert {row["id"] for row in first}.isdisjoint(row["id"] for row in second)
    assert (await api.get(endpoint, params={"offset": 101})).json() == []
    assert (await api.get(endpoint, params={"offset": -1})).status_code == 422


@pytest.mark.parametrize(
    "configuration",
    [
        {"repository_id": 43},
        {"installation_id": 457},
        {"workflow_paths": [".github/workflows/../../evil.yml"]},
        {"workflow_paths": [".github/workflows/test.txt"]},
        {"ref": "refs/heads/topic..hidden"},
        {"ref": "refs/heads/topic@{work}"},
        {"ref": "refs/heads/main.lock"},
    ],
)
async def test_invalid_edits_leave_the_binding_and_its_history_untouched(
    api, catalog_peer, configuration
):
    """Policy validation and immutable repository identity must precede every database mutation."""
    binding = await create(api)
    endpoint = f"/v1/github/routes/{binding['id']}"
    response = await api.patch(
        endpoint, json={"revision": 1, "configuration": policy(**configuration)}
    )
    assert response.status_code == 422, response.text
    assert (await api.get(endpoint)).json() == binding
    assert (await api.get(endpoint + "/revisions")).json()["total_count"] == 1


async def test_reenable_cannot_duplicate_another_active_repository_binding(api, catalog_peer):
    """An archived binding cannot bypass the one-active-connection rule by being re-enabled."""
    first = await create(api)
    endpoint = f"/v1/github/routes/{first['id']}"
    await api.patch(endpoint, json={"revision": 1, "enabled": False})
    second = await create(api)
    response = await api.patch(endpoint, json={"revision": 2, "enabled": True})
    assert response.status_code == 409
    assert (await api.get(endpoint)).json()["enabled"] is False
    assert (await api.get(f"/v1/github/routes/{second['id']}")).json()["enabled"] is True
    await api.delete(endpoint)
    assert (await api.get(endpoint + "/revisions")).json()["total_count"] == 2


async def test_constrained_policy_rejects_snippets_outside_its_workflow_scope(api, catalog_peer):
    """A narrow policy must never produce an example that its own OIDC authorization rejects."""
    binding = await create(
        api, workflow_paths=[".github/workflows/deploy.yml", ".github/workflows/deploy.yml"]
    )
    endpoint = f"/v1/github/routes/{binding['id']}/workflow"
    assert binding["workflow_paths"] == [".github/workflows/deploy.yml"]
    assert (await api.get(endpoint)).json()["path"] == ".github/workflows/deploy.yml"
    for params in (
        {"workflow_path": ".github/workflows/tests.yml"},
        {"workflow_path": ".github/workflows/../evil.yml"},
        {"job_key": "not a job"},
    ):
        assert (await api.get(endpoint, params=params)).status_code == 422


async def test_cache_excludes_suspended_and_foreign_app_installations(api, catalog_peer):
    """Default all-owners discovery must include every page while excluding other apps and suspensions."""
    catalog_peer.installations = [
        dict(catalog_peer.installations[0], id=9000 + i, app_id=-1) for i in range(100)
    ] + catalog_peer.installations
    catalog_peer.installations.insert(
        0, dict(catalog_peer.installations[-1], id=9999, suspended_at="2026-10-01T12:00:00Z")
    )
    response = await api.get("/v1/github/repositories")
    assert response.status_code == 200 and response.json()["total_count"] == 222
    assert not any("9999/repositories" in str(request.url) for request in catalog_peer.calls)
    assert any(
        request.url.path == "/user/installations" and request.url.params["page"] == "2"
        for request in catalog_peer.calls
    )


async def test_default_workflow_metadata_uses_the_existing_binding_branch(api, catalog_peer):
    """Opening settings must read the saved branch, even when the repository default is different."""
    binding = await create(api, ref="refs/heads/develop")
    response = await api.get(
        "/v1/github/repositories/42/configuration", params={"installation_id": 456}
    )
    assert response.status_code == 200, response.text
    assert all(
        request.url.params["ref"] == "develop"
        for request in catalog_peer.calls
        if "/contents/" in request.url.path
    )
    assert (
        await api.get(
            "/v1/github/repositories/42/configuration",
            params={"installation_id": 456, "binding_id": uuid4()},
        )
    ).status_code == 404
    assert (
        await api.get(
            "/v1/github/repositories/42/configuration",
            params={"installation_id": 456, "binding_id": binding["id"]},
        )
    ).status_code == 200


async def test_edit_revalidates_github_grant_after_an_authorization_race(
    api, catalog_peer, ci_peer, github
):
    """An OAuth reconnect during the provider check invalidates the in-flight policy edit."""
    binding = await create(api)
    ci_peer.barrier = asyncio.Event()
    observed = catalog_peer.entered["/repositories/42"] = asyncio.Event()
    editing = asyncio.create_task(
        api.patch(
            f"/v1/github/routes/{binding['id']}",
            json={"revision": 1, "configuration": policy(cpu_millis=1000)},
        )
    )
    try:
        async with asyncio.timeout(5):
            await observed.wait()
        await connect(api)
    finally:
        ci_peer.barrier.set()
    response = await editing
    assert response.status_code == 409, response.text
    saved = (await api.get(f"/v1/github/routes/{binding['id']}")).json()
    assert saved["revision"] == 1 and saved["cpu_millis"] == 2000


async def test_computer_edits_require_matching_pool_and_powercell_runtime(
    api, catalog_peer, computer
):
    """Manual API edits cannot select missing computers or an ordinary OCI runner."""
    binding = await create(api)
    endpoint = f"/v1/github/routes/{binding['id']}"
    for identifier, code in [(str(uuid4()), 404), (computer["id"], 422)]:
        response = await api.patch(
            endpoint, json={"revision": 1, "configuration": policy(computer_ids=[identifier])}
        )
        assert response.status_code == code, response.text
    assert (await api.get(endpoint + "/revisions")).json()["total_count"] == 1


@pytest.mark.parametrize("membership", ["admin"], indirect=True)
async def test_an_authorized_company_editor_can_adopt_the_grant_and_preserve_original_author(
    api, other_api, catalog_peer, company, membership, other_user, github
):
    """A shared binding uses its current editor's OAuth grant; disconnecting its original author must not revoke it."""
    binding = await create(api, company_id=company["id"])
    github.peer.user_id = 790
    await connect(other_api)
    endpoint = f"/v1/github/routes/{binding['id']}"
    assert (await other_api.get(endpoint)).status_code == 200
    response = await other_api.patch(
        endpoint,
        json={"revision": 1, "configuration": policy(company_id=company["id"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created_by"] == binding["created_by"]
    assert response.json()["authorized_by"] == other_user["id"]
    await api.delete("/v1/github/connection")
    assert (await other_api.get(endpoint)).json()["enabled"] is True
    history = (await other_api.get(endpoint + "/revisions")).json()
    assert history["revisions"][0]["actor"]["id"] == other_user["id"]
    assert history["revisions"][0]["actor"].keys() == {
        "id",
        "avatar_version",
        "first_name",
        "last_name",
        "nickname",
    }


async def test_company_read_permissions_are_required_for_detail_and_history(
    api, other_api, catalog_peer, company, membership
):
    """Membership without execution.read cannot expose a company binding, revisions, or repository status."""
    binding = await create(api, company_id=company["id"])
    assert (
        await api.patch(f"/v1/memberships/{membership['id']}", json={"permissions": []})
    ).status_code == 200
    for suffix in ("", "/revisions"):
        assert (
            await other_api.get(f"/v1/github/routes/{binding['id']}" + suffix)
        ).status_code == 403


@pytest.mark.parametrize(
    "path,body",
    [
        ("/user/installations", []),
        ("/user/installations", {"installations": {}}),
        ("/repositories/42/branches", {}),
    ],
)
async def test_malformed_provider_pages_are_recoverable_errors(api, catalog_peer, path, body):
    """Malformed provider pages must not create empty or partially trusted discovery results."""
    catalog_peer.raw[path] = body
    endpoint = (
        "/v1/github/repositories/42/configuration?installation_id=456"
        if path.endswith("branches")
        else "/v1/github/repositories"
    )
    assert (await api.get(endpoint)).status_code == 502


@pytest.mark.parametrize("path", ["/user/installations", "/repositories/42/branches"])
async def test_discovery_is_bounded_when_github_stalls(api, catalog_peer, monkeypatch, path):
    """An unresponsive provider must release the page with a retryable error within a total discovery deadline."""
    from throng_api import github_catalog

    monkeypatch.setattr(github_catalog, "DISCOVERY_TIMEOUT", 0.01)
    catalog_peer.delays[path] = 0.1
    endpoint = (
        "/v1/github/repositories/42/configuration?installation_id=456"
        if path.endswith("branches")
        else "/v1/github/repositories"
    )
    response = await api.get(endpoint)
    assert response.status_code == 503 and "timed out" in response.text


async def test_catalog_cache_expiry_eviction_and_personal_owner_fallback(
    api, catalog_peer, github, app, user
):
    """Expired or evicted discovery must refresh all owners without leaking another account's catalog."""
    catalog_peer.repositories[457][0].pop("owner")
    await api.get("/v1/github/repositories")
    grant, _, rows = github.service.catalog_cache[user["id"]]
    github.service.catalog_cache[user["id"]] = (grant, 0, rows)
    catalog_peer.repositories[456].clear()
    response = await api.get("/v1/github/repositories", params={"sort": "updated"})
    assert response.json()["total_count"] == 101
    assert all(row["owner_type"] == "personal" for row in response.json()["repositories"])
    github.service.catalog_cache = {str(uuid4()): ("foreign-grant", 0, []) for _ in range(128)}
    await api.get("/v1/github/repositories")
    assert list(github.service.catalog_cache) == [user["id"]]
    async with app.state.db.write() as session:
        route_actor = await session.get(GitHubAccount, user["id"])
        await session.delete(route_actor)
    assert (await api.get("/v1/github/repositories")).status_code == 409


async def test_deleted_actor_and_older_revision_pages_preserve_the_audit_diff(
    api, catalog_peer, app
):
    """A missing audit actor must not break history; page offsets compare against the actual preceding revision."""
    binding = await create(api)
    endpoint = f"/v1/github/routes/{binding['id']}"
    await api.patch(endpoint, json={"revision": 1, "enabled": False})
    from throng_api.models import CIRouteRevision

    async with app.state.db.write() as session:
        revisions = await session.scalars(select(CIRouteRevision))
        for row in revisions:
            row.actor_id = None
    response = await api.get(endpoint + "/revisions", params={"offset": 1, "limit": 1})
    assert response.status_code == 200 and response.json()["revisions"][0]["actor"] is None
    assert response.json()["revisions"][0]["action"] == "created"


async def test_archived_binding_metadata_uses_its_own_branch(api, catalog_peer):
    """Selecting an archived connection must not silently replace its branch with the active binding's branch."""
    archived = await create(api, ref="refs/heads/develop")
    await api.delete(f"/v1/github/routes/{archived['id']}")
    await create(api)
    catalog_peer.calls.clear()
    response = await api.get(
        "/v1/github/repositories/42/configuration",
        params={"installation_id": 456, "binding_id": archived["id"]},
    )
    assert response.status_code == 200, response.text
    assert {
        request.url.params["ref"]
        for request in catalog_peer.calls
        if "/contents/" in request.url.path
    } == {"develop"}


async def test_modern_audit_snapshots_do_not_show_invented_legacy_workflow_or_job(
    api, catalog_peer
):
    """All-workflow policies must not present placeholder ci.yml/test as if the user had selected them."""
    binding = await create(api)
    history = (await api.get(f"/v1/github/routes/{binding['id']}/revisions")).json()
    assert "workflow_path" not in history["revisions"][0]["configuration"]
    assert "job_key" not in history["revisions"][0]["configuration"]
    assert history["revisions"][0]["configuration"]["workflow_paths"] == []


async def test_repository_wide_policy_completes_a_real_runner_protocol_cycle(
    api, catalog_peer, client, ci_peer, powercell, app, clock
):
    """The new default policy must provision, dispatch, select and clean a runner through the existing agent protocol."""
    from .test_ci import test_runner_cycle_credentials_and_cleanup as runner_cycle

    binding = await create(api, preparation_seconds=10)
    await runner_cycle(client, api, binding, ci_peer, powercell, app, clock)
    async with app.state.db.read() as session:
        reservation = (await session.scalars(select(CIReservation))).one()
        assert reservation.route_revision == 1
        assert reservation.workflow_path == ".github/workflows/ci.yml"


async def test_two_jobs_in_one_workflow_reserve_independent_power_cells(
    api, catalog_peer, client, ci_peer, powercell, app
):
    """Different job instances in one GitHub run cannot share or overwrite a reservation, isolate or runner label."""
    binding = await create(
        api, max_parallel=2, cpu_millis=500, memory_bytes=512 * 1024**2, disk_bytes=GIB
    )
    replies = await asyncio.gather(
        *[reserve(client, binding, ci_peer, instance=job) for job in ("api", "web")]
    )
    assert all(
        response.status_code == 200 and response.json()["status"] == "preparing"
        for response in replies
    ), [response.text for response in replies]
    assert replies[0].json()["id"] != replies[1].json()["id"]
    async with app.state.db.read() as session:
        reservations = list(await session.scalars(select(CIReservation)))
        assert {row.instance for row in reservations} == {"api", "web"}
        assert len({row.isolate_id for row in reservations}) == 2
        assert len({row.label for row in reservations}) == 2


async def test_disconnecting_between_token_lookup_and_edit_cannot_recreate_authority(
    api, catalog_peer, monkeypatch
):
    """A disconnect at the first async boundary must abort editing instead of reviving its revoked grant."""
    from throng_api import ci_bindings

    binding = await create(api)
    original = ci_bindings.user_token

    async def disconnect_after_lookup(request, actor):
        """Schedule a real disconnect exactly after the token helper releases its transaction."""
        token = await original(request, actor)
        assert (await api.delete("/v1/github/connection")).status_code == 204
        return token

    monkeypatch.setattr(ci_bindings, "user_token", disconnect_after_lookup)
    endpoint = f"/v1/github/routes/{binding['id']}"
    response = await api.patch(
        endpoint, json={"revision": 1, "configuration": policy(cpu_millis=1000)}
    )
    assert response.status_code == 409
    saved = (await api.get(endpoint)).json()
    assert saved["enabled"] is False and saved["cpu_millis"] == 2000
    assert (await api.get(endpoint + "/revisions")).json()["total_count"] == 2


async def test_catalog_disconnect_at_token_boundary_cannot_return_cached_repositories(
    api, catalog_peer, monkeypatch
):
    """A catalog request crossing a disconnect cannot return an earlier account's cached repositories."""
    from throng_api import github_catalog

    await api.get("/v1/github/repositories")
    original = github_catalog.user_token

    async def disconnect_after_lookup(request, actor):
        """Inject a real account revocation between the two independent read transactions."""
        token = await original(request, actor)
        await api.delete("/v1/github/connection")
        return token

    monkeypatch.setattr(github_catalog, "user_token", disconnect_after_lookup)
    assert (await api.get("/v1/github/repositories")).status_code == 409


async def test_moving_to_a_full_owner_pool_preserves_route_and_state_ownership(
    api, catalog_peer, company, app
):
    """Moving a disabled binding cannot evade the destination owner's route quota or partially move its state artifact."""
    from throng_api.models import CIRoute

    source = await create(api)
    seed = await create(
        api, company_id=company["id"], workflow_paths=None, workflow_path=".github/workflows/ci.yml"
    )
    async with app.state.db.write() as session:
        route = await session.get(CIRoute, seed["id"])
        values = {
            column.key: getattr(route, column.key)
            for column in CIRoute.__table__.columns
            if column.key not in {"id", "created_at"}
        }
        session.add_all(CIRoute(**values) for _ in range(99))
    response = await api.patch(
        f"/v1/github/routes/{source['id']}",
        json={"revision": 1, "enabled": False, "configuration": policy(company_id=company["id"])},
    )
    assert response.status_code == 409 and "quota" in response.text
    async with app.state.db.read() as session:
        route = await session.get(CIRoute, source["id"])
        artifact = await session.get(Artifact, source["state_artifact_id"])
        assert route.company_id is None and route.enabled is True and route.revision == 1
        assert artifact.company_id is None and artifact.user_id == source["user_id"]


async def test_workflow_discovery_rejects_oversized_and_nonworkflow_files(api, catalog_peer):
    """Only bounded YAML workflow files may provide job choices; unsafe paths must not be fetched at all."""
    catalog_peer.sources[".github/workflows/deploy.yml"] = "#" + "x" * (1024**2) + "\njobs: {}"
    catalog_peer.workflows.append({"name": "Not a workflow", "path": "../../secrets.txt"})
    response = await api.get(
        "/v1/github/repositories/42/configuration", params={"installation_id": 456}
    )
    assert response.status_code == 200
    assert len(response.json()["workflows"]) == 2
    assert response.json()["workflows"][0]["parse_error"] is True
    assert not any("secrets" in str(request.url) for request in catalog_peer.calls)


async def test_owner_filter_infers_provider_types_when_optional_owner_metadata_is_missing(
    api, catalog_peer
):
    """Older or partial GitHub owner metadata must still classify personal and organization repositories correctly."""
    for installation in catalog_peer.installations:
        installation["account"].pop("type")
    for rows in catalog_peer.repositories.values():
        for row in rows:
            row["owner"].pop("type")
    response = await api.get("/v1/github/repositories", params={"owner_type": "personal"})
    assert response.status_code == 200 and response.json()["total_count"] == 101


async def test_saving_after_repository_rename_refreshes_identity_without_a_new_binding(
    api, catalog_peer, ci_peer
):
    """After a GitHub rename, saving unchanged resources must update the OIDC repository identity on the same route."""
    binding = await create(api)
    ci_peer.full_name = "test-org/renamed"
    endpoint = f"/v1/github/routes/{binding['id']}"
    response = await api.patch(endpoint, json={"revision": 1, "configuration": policy()})
    assert response.status_code == 200
    assert response.json()["id"] == binding["id"] and response.json()["revision"] == 2
    assert response.json()["repository"] == "test-org/renamed"
    history = (await api.get(endpoint + "/revisions")).json()
    assert {change["field"] for change in history["revisions"][0]["changes"]} == {"repository"}


async def test_workflow_discovery_excludes_reusable_jobs_that_cannot_run_on_a_powercell(
    api, catalog_peer
):
    """Reusable-workflow calls and malformed job definitions cannot be offered as directly configurable runner jobs."""
    catalog_peer.sources[".github/workflows/deploy.yml"] = (
        "jobs:\n  release:\n    uses: org/repo/.github/workflows/release.yml@main\n  invalid: text\n  deploy:\n    runs-on: ubuntu-latest\n"
    )
    response = await api.get(
        "/v1/github/repositories/42/configuration", params={"installation_id": 456}
    )
    assert response.status_code == 200
    assert response.json()["workflows"][0]["jobs"] == ["deploy"]


async def test_editing_can_select_a_real_computer_in_the_authorized_pool(
    api, catalog_peer, powercell
):
    """An explicit supported computer must be saved on an existing binding rather than forcing creation of a replacement."""
    binding = await create(api)
    response = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={"revision": 1, "configuration": policy(computer_ids=[powercell.computer_id])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["computer_ids"] == [powercell.computer_id]
    assert response.json()["id"] == binding["id"]


async def test_cancel_before_reserve_keeps_the_actual_workflow_and_current_policy_revision(
    api, catalog_peer, ci_peer, client, app
):
    """Early cancellation of a non-default workflow must not be recorded against ci.yml or an obsolete policy revision."""
    binding = await create(api)
    binding = (
        await api.patch(
            f"/v1/github/routes/{binding['id']}",
            json={"revision": 1, "configuration": policy(cpu_millis=1000)},
        )
    ).json()
    response = await client.post(
        f"/v1/github/ci/routes/{binding['id']}/cancel",
        headers={
            "Authorization": "Bearer "
            + ci_peer.token(
                workflow_ref="test-org/repo/.github/workflows/deploy.yml@refs/heads/main"
            )
        },
        json={"instance": "deploy"},
    )
    assert response.status_code == 200, response.text
    async with app.state.db.read() as session:
        reservation = (await session.scalars(select(CIReservation))).one()
        assert reservation.workflow_path == ".github/workflows/deploy.yml"
        assert reservation.route_revision == binding["revision"] == 2


async def test_omitting_workflow_fields_defaults_to_the_repository_wide_policy(api, catalog_peer):
    """A new API caller must get all workflows by default instead of an invented ci.yml/test scope."""
    response = await api.post(
        "/v1/github/routes", json={"installation_id": 456, "repository_id": 42}
    )
    assert response.status_code == 201, response.text
    assert response.json()["workflow_paths"] == []
    assert response.json()["repository_binding"] is True


async def test_archived_settings_do_not_borrow_an_active_bindings_state_or_last_run(
    api, catalog_peer, app, clock
):
    """The archive must show its own disabled state and execution result while the repository's active binding stays connected."""
    archived = await create(api)
    await execution(app, clock, archived, reason="no_capacity")
    archived_run = (await api.get(f"/v1/github/routes/{archived['id']}/runs")).json()[0]
    await api.delete(f"/v1/github/routes/{archived['id']}")
    active = await create(api)
    clock.now += timedelta(seconds=1)
    await execution(app, clock, active, started=True, reason="github_success")
    response = await api.get(
        "/v1/github/repositories/42/configuration",
        params={"installation_id": 456, "binding_id": archived["id"]},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "disabled"
    assert response.json()["binding_id"] == archived["id"]
    assert response.json()["last_run"]["id"] == archived_run["id"]
    catalog = (await api.get("/v1/github/repositories", params={"search": "test-org/repo"})).json()[
        "repositories"
    ][0]
    assert catalog["state"] == "connected" and catalog["binding_id"] == active["id"]
