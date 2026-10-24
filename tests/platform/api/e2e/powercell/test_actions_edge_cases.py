"""Edge cases of Actions configuration, identity and lifecycle transitions."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from throng_api import ci
from throng_api.models import CIReservation, GitHubAccount, Isolate

from .test_ci_github_outcome_edges import outcome_peer as outcome_peer
from .test_repository_bindings import catalog_peer as catalog_peer
from .test_repository_bindings import ci_peer as ci_peer
from .test_repository_bindings import create, policy
from .test_repository_bindings import github as github
from .test_repository_bindings import powercell as powercell

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.mark.parametrize("operation", ["catalog", "edit", "enable", "metadata"])
@pytest.mark.parametrize(("fault", "expected"), [(503, 502), (429, 503)])
async def test_actions_oauth_refresh_failures_keep_provider_error_and_binding_state(
    api, app, user, clock, catalog_peer, github, operation, fault, expected
):
    """OAuth refresh failures must retain retry headers and leave policy/history unchanged, including a token expiring during metadata discovery."""
    binding = await create(api)
    if operation == "enable":
        assert (await api.delete(f"/v1/github/routes/{binding['id']}")).status_code == 204
    route_url = f"/v1/github/routes/{binding['id']}"
    before = (await api.get(route_url)).json()
    history = (await api.get(f"{route_url}/revisions")).json()
    if operation == "metadata":
        async with app.state.db.write() as session:
            account = await session.get(GitHubAccount, user["id"])
            account.expires_at = clock.now + timedelta(seconds=61)
        original = github.service.client._transport

        async def handler(request):
            response = await original.handle_async_request(request)
            if request.method == "DELETE" and request.url.path == "/installation/token":
                clock.now += timedelta(seconds=2)
            return response

        github.service.client._transport = httpx.MockTransport(handler)
    else:
        clock.now += timedelta(hours=8)
    github.peer.failures[("POST", "/login/oauth/access_token")] = fault

    async def perform():
        if operation == "catalog":
            return await api.get("/v1/github/repositories")
        if operation == "metadata":
            return await api.get(
                "/v1/github/repositories/42/configuration", params={"installation_id": 456}
            )
        return await api.patch(
            route_url,
            json={"revision": before["revision"], "configuration": policy(cpu_millis=1000)}
            if operation == "edit"
            else {"revision": before["revision"], "enabled": True},
        )

    response = await perform()
    assert response.status_code == expected, response.text
    assert response.headers.get("retry-after") == ("60" if fault == 429 else None)
    assert (await api.get(route_url)).json() == before
    assert (await api.get(f"{route_url}/revisions")).json() == history
    github.peer.failures.clear()
    clock.now += timedelta(seconds=61)
    recovered = await perform()
    assert recovered.status_code == 200, recovered.text
    if operation in {"edit", "enable"}:
        assert recovered.json()["revision"] == before["revision"] + 1


@pytest.mark.parametrize(
    ("change", "expected_status"),
    [
        ({"ref": "refs/heads/develop"}, 403),
        ({"workflow_paths": [".github/workflows/deploy.yml"]}, 403),
        ({"cpu_millis": 1000}, 200),
    ],
)
async def test_cancel_revalidates_signed_scope_inside_the_current_policy_transaction(
    api, client, catalog_peer, ci_peer, app, monkeypatch, change, expected_status
):
    """A cancel crossing a branch/workflow edit must not write an unauthorized tombstone; resource-only edits remain valid."""
    binding = await create(api)
    entered, release = asyncio.Event(), asyncio.Event()
    original = ci.routing_identity

    async def checked_identity(request, route_id):
        claims = await original(request, route_id)
        entered.set()
        await release.wait()
        return claims

    monkeypatch.setattr(ci, "routing_identity", checked_identity)
    pending = asyncio.create_task(
        client.post(
            f"/v1/github/ci/routes/{binding['id']}/cancel",
            headers={"Authorization": "Bearer " + ci_peer.token()},
            json={"instance": "cancel-crossing-edit"},
        )
    )
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        edited = await api.patch(
            f"/v1/github/routes/{binding['id']}",
            json={"revision": 1, "configuration": policy(**change)},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["revision"] == 2
    finally:
        release.set()
    response = await pending
    assert response.status_code == expected_status, response.text
    async with app.state.db.read() as session:
        rows = list(await session.scalars(select(CIReservation)))
        assert (await session.scalars(select(Isolate))).all() == []
    if expected_status == 403:
        assert rows == []
    else:
        assert len(rows) == 1
        assert rows[0].route_revision == 2
        assert rows[0].workflow_path == ".github/workflows/ci.yml"
        assert rows[0].status == "cancelled"


@pytest.mark.parametrize("length", [100, 101, 512])
async def test_discovered_job_ids_are_not_limited_by_legacy_binding_storage(
    api, catalog_peer, length
):
    """A real job longer than the old stored job_key budget must remain discoverable and selectable in modern YAML."""
    import yaml

    path = ".github/workflows/deploy.yml"
    job = "build_" + "x" * (length - 6)
    catalog_peer.sources[path] = (
        f"name: Deploy\non: push\njobs:\n  {job}:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
    )
    metadata = await api.get(
        "/v1/github/repositories/42/configuration", params={"installation_id": 456}
    )
    assert metadata.status_code == 200, metadata.text
    definition = next(row for row in metadata.json()["workflows"] if row["path"] == path)
    assert definition["jobs"] == [job]
    assert definition["parse_error"] is False
    binding = await create(api)
    example = await api.get(
        f"/v1/github/routes/{binding['id']}/workflow",
        params={"workflow_path": path, "job_key": definition["jobs"][0]},
    )
    assert example.status_code == 200, example.text
    jobs = yaml.safe_load(example.json()["yaml"])["jobs"]
    assert job in jobs
    assert jobs[job]["needs"] in jobs
    unchanged = (await api.get(f"/v1/github/routes/{binding['id']}")).json()
    assert unchanged["revision"] == 1
    assert unchanged["job_key"] == "test"
    if length > 100:
        legacy = await api.post(
            "/v1/github/routes", json=policy(workflow_path=path, workflow_paths=None, job_key=job)
        )
        assert legacy.status_code == 422


@pytest.mark.parametrize("legacy", [False, True])
async def test_independent_job_snippets_keep_distinct_routing_jobs_and_yaml_anchors(
    api, catalog_peer, legacy
):
    """Explicit jobs must not collide through a 32-bit hash prefix or a legacy binding's default identity; old no-argument YAML stays compatible."""
    import yaml

    binding = await create(
        api, workflow_path=".github/workflows/ci.yml", workflow_paths=None if legacy else []
    )
    original = await api.get(f"/v1/github/routes/{binding['id']}/workflow")
    assert original.status_code == 200
    if legacy:
        jobs = yaml.safe_load(original.json()["yaml"])["jobs"]
        router = "powercell_route_" + binding["id"].replace("-", "")
        assert jobs["test"]["needs"] == router
        assert "POWERCELL_INSTANCE" not in jobs[router]["steps"][0]["env"]
    targets = ("build_112857", "build_148201")
    snippets, routers, instances = [], [], []
    for target in targets:
        response = await api.get(
            f"/v1/github/routes/{binding['id']}/workflow",
            params={"workflow_path": ".github/workflows/ci.yml", "job_key": target},
        )
        assert response.status_code == 200, response.text
        raw = response.json()["yaml"]
        jobs = yaml.safe_load(raw)["jobs"]
        router = jobs[target]["needs"]
        routers.append(router)
        instances.append(jobs[router]["steps"][0]["env"]["POWERCELL_INSTANCE"])
        snippets.append(raw)
    assert len(set(instances)) == 2
    assert len(set(routers)) == 2
    merged = (
        snippets[0].split("\njobs:\n", 1)[0]
        + "\njobs:\n"
        + "\n".join(snippet.split("\njobs:\n", 1)[1] for snippet in snippets)
    )
    jobs = yaml.safe_load(merged)["jobs"]
    assert len(jobs) == 4
    for target, router, instance in zip(targets, routers, instances, strict=True):
        assert jobs[target]["needs"] == router
        assert jobs[router]["steps"][0]["env"]["POWERCELL_INSTANCE"] == instance
        assert jobs[target]["steps"][0]["env"]["POWERCELL_INSTANCE"] == instance
    assert (await api.get(f"/v1/github/routes/{binding['id']}/workflow")).json() == original.json()
    unchanged = (await api.get(f"/v1/github/routes/{binding['id']}")).json()
    assert unchanged["revision"] == 1
    assert unchanged["workflow_paths"] == (None if legacy else [])


@pytest.mark.parametrize("target", ["powercell_route_healthcheck", "powercell_route_abc"])
async def test_user_jobs_with_router_prefix_are_not_confused_with_generated_helpers(
    api, catalog_peer, target
):
    """A broad reserved-prefix filter hid ordinary executable jobs that the YAML endpoint itself accepted."""
    import yaml

    path = ".github/workflows/deploy.yml"
    generated = [
        "powercell_route_" + "a" * 32,
        "powercell_route_" + "a" * 32 + "_" + "b" * 8,
        "powercell_route_" + "a" * 32 + "_" + "c" * 32,
    ]
    job = {"runs-on": "ubuntu-latest", "steps": [{"run": "echo ok"}]}
    catalog_peer.sources[path] = yaml.safe_dump(
        {"jobs": {name: job.copy() for name in [target, *generated]}}
    )
    response = await api.get(
        "/v1/github/repositories/42/configuration", params={"installation_id": 456}
    )
    assert response.status_code == 200, response.text
    definition = next(row for row in response.json()["workflows"] if row["path"] == path)
    assert definition["parse_error"] is False
    assert definition["jobs"] == [target]
    binding = await create(api)
    example = await api.get(
        f"/v1/github/routes/{binding['id']}/workflow",
        params={"workflow_path": path, "job_key": definition["jobs"][0]},
    )
    assert example.status_code == 200, example.text
    assert target in yaml.safe_load(example.json()["yaml"])["jobs"]


@pytest.mark.parametrize("source", ["webhook", "rest", "aggregate"])
async def test_fast_exact_job_completion_confirms_binding_without_a_start_notification(
    api, client, catalog_peer, ci_peer, github, powercell, outcome_peer, app, clock, source
):
    """A missed start notification must not leave a verified completed job pending; an aggregate workflow result is not execution proof."""
    from datetime import timedelta

    from .test_ci import deliver, ready_runner
    from .test_ci_github_outcome_edges import exact_job, prepare_outcome, reconcile_due

    binding = await create(api)
    selected, task = await ready_runner(client, binding, ci_peer, powercell, app, clock)
    async with app.state.db.read() as session:
        assert (await session.get(CIReservation, selected["id"])).started_at is None
    if source == "webhook":
        response = await deliver(
            client,
            github,
            {
                "action": "completed",
                "installation": {"id": 456},
                "repository": {"id": 42},
                "workflow_job": exact_job(selected["runs_on"][0], started_at=clock.now.isoformat()),
            },
        )
        assert response.status_code == 202
    else:
        await prepare_outcome(client, github, ci_peer, powercell, task, "workflow_run", "success")
        if source == "rest":
            outcome_peer.jobs = [
                exact_job(selected["runs_on"][0], started_at=clock.now.isoformat())
            ]
        clock.now += timedelta(seconds=5)
        await reconcile_due(app, clock, selected["id"])
    response = await api.get("/v1/github/repositories", params={"search": "test-org/repo"})
    assert response.status_code == 200, response.text
    actual = next(row for row in response.json()["repositories"] if row["id"] == 42)
    assert actual["state"] == ("pending" if source == "aggregate" else "connected")
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, selected["id"])
        assert row.reason == "github_success"
        assert (row.started_at is not None) is (source != "aggregate")
        if row.started_at:
            assert row.started_at <= row.finished_at
        assert row.route_revision == binding["revision"]
