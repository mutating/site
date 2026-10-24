"""Workflow profiles through real binding APIs, signed OIDC and the runner protocol."""

from datetime import timedelta
from uuid import uuid4

import pytest
import yaml

from throng_api import ci_controller
from throng_api.models import CIReservation, Command, Computer, Isolate

from .test_ci import ready_runner, reserve
from .test_repository_bindings import catalog_peer as catalog_peer
from .test_repository_bindings import ci_peer as ci_peer
from .test_repository_bindings import create, policy
from .test_repository_bindings import github as github
from .test_repository_bindings import powercell as powercell

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]
GIB = 1024**3
DEPLOY = ".github/workflows/deploy.yml"
CI = ".github/workflows/ci.yml"


@pytest.mark.parametrize(
    "profiles",
    [
        {"../deploy.yml": {}},
        {".github/workflows/../a.yml": {}},
        {DEPLOY: {"cpu_millis": 0}},
        {DEPLOY: {"memory_bytes": 1}},
        {DEPLOY: {"disk_bytes": 0}},
        {DEPLOY: {"preparation_seconds": 301}},
        {DEPLOY: {"timeout_seconds": 59}},
        {DEPLOY: {"cpu_millis": None}},
        {DEPLOY: {"computer_ids": ["invalid-uuid"]}},
        {DEPLOY: {"company_id": str(uuid4())}},
        {f".github/workflows/test{i}.yml": {} for i in range(201)},
    ],
)
async def test_invalid_profiles_are_rejected_before_creating_a_binding(api, catalog_peer, profiles):
    """Overrides must enforce the same resource bounds and cannot change the binding owner."""
    response = await api.post("/v1/github/routes", json=policy(workflow_profiles=profiles))
    assert response.status_code == 422, response.text
    assert (await api.get("/v1/github/routes")).json() == []


async def test_sparse_profiles_persist_and_audit_without_copying_inherited_fields(
    api, catalog_peer
):
    """Changing a default must update inherited values without overwriting explicit workflow values."""
    profiles = {DEPLOY: {"cpu_millis": 500, "computer_ids": []}, CI: {}}
    binding = await create(api, workflow_profiles=profiles)
    assert binding["workflow_profiles"] == profiles
    assert (await api.get(f"/v1/github/routes/{binding['id']}")).json()[
        "workflow_profiles"
    ] == profiles
    response = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={
            "revision": 1,
            "configuration": policy(cpu_millis=1500, workflow_profiles=profiles),
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["workflow_profiles"] == profiles and updated["cpu_millis"] == 1500
    assert updated["id"] == binding["id"] and updated["revision"] == 2
    history = (await api.get(f"/v1/github/routes/{binding['id']}/revisions")).json()["revisions"]
    assert history[-1]["configuration"]["workflow_profiles"] == profiles
    # An omitted field inherits; an explicitly empty computer list chooses the entire pool.
    reduced = {DEPLOY: {"computer_ids": []}}
    response = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={
            "revision": 2,
            "configuration": policy(cpu_millis=1500, workflow_profiles=reduced),
        },
    )
    assert response.status_code == 200
    history = (await api.get(f"/v1/github/routes/{binding['id']}/revisions")).json()["revisions"]
    change = next(item for item in history[0]["changes"] if item["field"] == "workflow_profiles")
    assert change == {
        "field": "workflow_profiles",
        "before": profiles,
        "after": reduced,
    }
    response = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={"revision": 3, "configuration": policy(cpu_millis=1500)},
    )
    assert response.status_code == 200 and response.json()["workflow_profiles"] == {}


@pytest.mark.parametrize(
    "path,expected_cpu,expected_memory,expected_disk,wait,duration",
    [
        (DEPLOY, 500, GIB, 4 * GIB, 120, 180),
        (CI, 1500, 2 * GIB, 8 * GIB, 90, 3600),
        (".github/workflows/another.yml", 1500, 2 * GIB, 8 * GIB, 90, 3600),
    ],
)
async def test_signed_workflow_selects_actual_vm_resources_and_time_budgets(
    api,
    client,
    catalog_peer,
    ci_peer,
    powercell,
    app,
    clock,
    path,
    expected_cpu,
    expected_memory,
    expected_disk,
    wait,
    duration,
):
    """A workflow override must affect allocation and deadlines, while unlisted workflows inherit defaults."""
    binding = await create(
        api,
        cpu_millis=1500,
        memory_bytes=2 * GIB,
        disk_bytes=8 * GIB,
        workflow_profiles={
            DEPLOY: {
                "cpu_millis": 500,
                "memory_bytes": GIB,
                "disk_bytes": 4 * GIB,
                "preparation_seconds": 120,
                "timeout_seconds": 180,
                "computer_ids": [powercell.computer_id],
            }
        },
    )
    response = await reserve(
        client, binding, ci_peer, workflow_ref=f"test-org/repo/{path}@refs/heads/main"
    )
    assert response.status_code == 200 and response.json()["status"] == "preparing", response.text
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, response.json()["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        assert (isolate.cpu_millis, isolate.memory_bytes, isolate.disk_bytes) == (
            expected_cpu,
            expected_memory,
            expected_disk,
        )
        assert isolate.computer_id == powercell.computer_id
        assert row.workflow_path == path and row.route_revision == 1
        assert abs((row.deadline - clock.now).total_seconds() - wait) < 2
        assert row.expires_at == clock.now + timedelta(seconds=wait + duration + 300)
    snippet = await api.get(
        f"/v1/github/routes/{binding['id']}/workflow",
        params={"workflow_path": path, "job_key": "build"},
    )
    jobs = yaml.safe_load(snippet.json()["yaml"])["jobs"]
    router = next(job for key, job in jobs.items() if key.startswith("powercell_route"))
    assert router["steps"][0]["env"]["POWERCELL_WAIT_SECONDS"] == str(wait)
    assert (
        jobs["build"]["strategy"]["matrix"]["include"][0]["timeout_minutes"]
        == (duration + 59) // 60
    )


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("computer_kind,code", [("foreign", 404), ("unsupported", 422)])
async def test_profile_computers_require_pool_ownership_and_supported_runtime(
    api,
    catalog_peer,
    powercell,
    app,
    editing,
    computer_kind,
    code,
):
    """A workflow must not bypass computer ownership/runtime checks by hiding IDs inside an override."""
    binding = await create(api) if editing else None
    computer_id = str(uuid4()) if computer_kind == "foreign" else powercell.computer_id
    if computer_kind == "unsupported":
        async with app.state.db.write() as session:
            (await session.get(Computer, computer_id)).runtime = "unsupported"
    configuration = policy(workflow_profiles={DEPLOY: {"computer_ids": [computer_id]}})
    response = (
        await api.patch(
            f"/v1/github/routes/{binding['id']}",
            json={"revision": 1, "configuration": configuration},
        )
        if binding
        else await api.post("/v1/github/routes", json=configuration)
    )
    assert response.status_code == code, response.text


async def test_profile_canonicalization_is_a_noop_and_explicit_empty_computers_survive(
    api,
    catalog_peer,
    powercell,
):
    """Repeated computer IDs and UUID casing must not create spurious audit revisions."""
    profiles = {DEPLOY: {"computer_ids": [powercell.computer_id.upper(), powercell.computer_id]}}
    binding = await create(api, workflow_profiles=profiles)
    assert binding["workflow_profiles"][DEPLOY]["computer_ids"] == [powercell.computer_id]
    response = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={"revision": 1, "configuration": policy(workflow_profiles=profiles)},
    )
    assert response.status_code == 200 and response.json()["revision"] == 1


async def test_profile_timeout_reaches_runner_command_and_actual_job_expiry(
    api,
    client,
    catalog_peer,
    ci_peer,
    powercell,
    app,
    clock,
):
    """Runner registration and verified job start must retain the workflow timeout instead of the default."""
    binding = await create(
        api,
        workflow_profiles={
            CI: {
                "cpu_millis": 500,
                "memory_bytes": GIB,
                "disk_bytes": 4 * GIB,
                "preparation_seconds": 120,
                "timeout_seconds": 180,
            }
        },
    )
    result, _ = await ready_runner(client, binding, ci_peer, powercell, app, clock)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        command = await session.get(Command, row.command_id)
        assert command.timeout_seconds == 600
    changed = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={"revision": 1, "configuration": policy()},
    )
    assert changed.status_code == 409
    ci_peer.busy = True
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        assert row.started_at == clock.now and row.expires_at == clock.now + timedelta(seconds=180)
        assert isolate.expires_at == row.expires_at
    clock.now += timedelta(seconds=181)
    await ci_controller.local_tick(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "timed_out" and row.reason == "runner_timeout"


async def test_profiles_share_concurrency_and_cannot_expand_oidc_scope(
    api,
    client,
    catalog_peer,
    ci_peer,
    powercell,
):
    """Profiles change resources, while binding-wide admission and signed repository/ref restrictions remain authoritative."""
    binding = await create(
        api, workflow_paths=[CI], workflow_profiles={DEPLOY: {"cpu_millis": 500}}
    )
    response = await reserve(
        client, binding, ci_peer, workflow_ref=f"test-org/repo/{DEPLOY}@refs/heads/main"
    )
    assert response.status_code == 403
    response = await reserve(client, binding, ci_peer)
    assert response.json()["status"] == "preparing"
    response = await reserve(client, binding, ci_peer, instance="second")
    assert response.json()["reason"] == "concurrency_limit"
    assert (await reserve(client, binding, ci_peer, repository_id="99")).status_code == 403


@pytest.mark.parametrize("keep_override", [True, False])
async def test_updated_defaults_and_profile_removal_apply_to_the_next_allocation(
    api, client, catalog_peer, ci_peer, powercell, app, keep_override
):
    """Inherited resources must follow edited defaults; removing a profile must stop applying its former limits."""
    profiles = {CI: {"cpu_millis": 500}}
    binding = await create(api, workflow_profiles=profiles)
    response = await api.patch(
        f"/v1/github/routes/{binding['id']}",
        json={
            "revision": 1,
            "configuration": policy(
                cpu_millis=1500,
                memory_bytes=3 * GIB,
                workflow_profiles=profiles if keep_override else {},
            ),
        },
    )
    assert response.status_code == 200, response.text
    response = await reserve(client, response.json(), ci_peer)
    assert response.json()["status"] == "preparing", response.text
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, response.json()["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        assert isolate.cpu_millis == (500 if keep_override else 1500)
        assert isolate.memory_bytes == 3 * GIB and row.route_revision == 2
