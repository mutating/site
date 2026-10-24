"""Regression contracts found while reviewing the complete repository Actions flow."""

import asyncio

import pytest
import yaml
from sqlalchemy import select

from throng_api import ci_controller
from throng_api.models import CIReservation, Isolate

from .test_ci import deliver, reserve, schedule
from .test_repository_bindings import catalog_peer as catalog_peer
from .test_repository_bindings import ci_peer as ci_peer
from .test_repository_bindings import create, policy
from .test_repository_bindings import github as github
from .test_repository_bindings import powercell as powercell

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


async def test_workflow_discovery_preserves_scalar_looking_job_identifiers(api, catalog_peer):
    """Legal Actions job IDs must not disappear or collide through YAML boolean/null coercion."""
    identifiers = ["on", "off", "yes", "no", "true", "false", "null", "build"]
    path = ".github/workflows/deploy.yml"
    catalog_peer.workflows = [{"name": "Deploy", "path": path}]
    catalog_peer.sources[path] = "on: workflow_dispatch\njobs:\n" + "".join(
        f"  {job}:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo {job}\n"
        for job in identifiers
    )
    response = await api.get(
        "/v1/github/repositories/42/configuration", params={"installation_id": 456}
    )
    assert response.status_code == 200, response.text
    assert response.json()["workflows"] == [
        {"path": path, "name": "Deploy", "jobs": identifiers, "parse_error": False}
    ]


@pytest.mark.parametrize(
    ("event", "action", "extra"),
    [
        ("installation", "suspend", {}),
        ("installation", "deleted", {}),
        ("installation_repositories", "removed", {"repositories_removed": [{"id": 42}]}),
    ],
)
async def test_provider_disablement_advances_binding_history_once(
    api, client, github, catalog_peer, event, action, extra
):
    """Provider revocation must record the real disabled state without duplicating audit on retries."""
    binding = await create(api)
    endpoint = f"/v1/github/routes/{binding['id']}"
    original = (await api.get(endpoint + "/revisions")).json()["revisions"][0]
    payload = {"action": action, "installation": {"id": 456}, **extra}
    for delivery in ("provider-disable", "provider-disable", "provider-disable-retry"):
        response = await deliver(client, github, payload, event=event, delivery=delivery)
        assert response.status_code == 202, response.text
        actual = (await api.get(endpoint)).json()
        assert actual["enabled"] is False and actual["revision"] == 2
        history = (await api.get(endpoint + "/revisions")).json()
        assert history["total_count"] == 2
        latest, first = history["revisions"]
        assert first == original
        assert latest["action"] == "disabled" and latest["actor"] is None
        assert latest["configuration"]["enabled"] is False
        assert latest["changes"] == [{"field": "enabled", "before": True, "after": False}]


async def test_reinstalled_app_selects_current_installation_and_preserves_old_history(
    api, client, github, catalog_peer
):
    """Reinstallation must offer a new connection instead of editing an immutable, unusable installation ID."""
    binding = await create(api, ref="refs/heads/develop")
    response = await deliver(
        client,
        github,
        {"action": "deleted", "installation": {"id": 456}},
        event="installation",
        delivery="reinstall-old-deleted",
    )
    assert response.status_code == 202
    endpoint = f"/v1/github/routes/{binding['id']}"
    archived = (await api.get(endpoint + "/revisions")).json()
    reinstalled = {**github.peer.installation, "id": 457}
    catalog_peer.installations = [reinstalled]
    catalog_peer.repositories = {457: [catalog_peer.repositories[456][0]]}
    catalog_peer.failures["/app/installations/456"] = 404
    catalog_peer.raw["/app/installations/457"] = reinstalled
    catalog_peer.raw["/app/installations/457/access_tokens"] = {"token": "scoped-token"}
    catalog = (await api.get("/v1/github/repositories", params={"refresh": True})).json()
    assert catalog["repositories"][0]["installation_id"] == 457
    catalog_peer.calls.clear()
    params = {"installation_id": 457}
    response = await api.get("/v1/github/repositories/42/configuration", params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["binding_id"] is None
    assert data["bindings"][0]["id"] == binding["id"]
    assert data["bindings"][0]["installation_id"] == 456
    assert {
        request.url.params["ref"]
        for request in catalog_peer.calls
        if "/contents/" in request.url.path
    } == {"main"}
    explicit = await api.get(
        "/v1/github/repositories/42/configuration", params={**params, "binding_id": binding["id"]}
    )
    assert explicit.json()["binding_id"] == binding["id"]
    fresh = await create(api, installation_id=457)
    assert fresh["id"] != binding["id"]
    data = (await api.get("/v1/github/repositories/42/configuration", params=params)).json()
    assert data["binding_id"] == fresh["id"]
    assert {row["id"] for row in data["bindings"]} == {binding["id"], fresh["id"]}
    assert (await api.get(endpoint + "/revisions")).json() == archived


@pytest.mark.parametrize("organization", [False, True])
async def test_pool_owner_can_revoke_connection_after_losing_github_admin(
    api, catalog_peer, ci_peer, company, organization
):
    """GitHub administration gates configuration/enable, while independent pool authority still permits local revoke."""
    values = {"company_id": company["id"]} if organization else {}
    binding = await create(api, **values)
    endpoint = f"/v1/github/routes/{binding['id']}"
    ci_peer.admin = False
    metadata = await api.get(
        "/v1/github/repositories/42/configuration", params={"installation_id": 456}
    )
    assert metadata.status_code == 200
    assert metadata.json()["repository"]["can_administer"] is False
    assert metadata.json()["bindings"][0]["enabled"] is True
    assert (
        await api.patch(endpoint, json={"revision": 1, "configuration": policy(**values)})
    ).status_code == 403
    before = len(catalog_peer.calls)
    response = await api.patch(endpoint, json={"revision": 1, "enabled": False})
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is False and response.json()["revision"] == 2
    assert len(catalog_peer.calls) == before
    assert (await api.patch(endpoint, json={"revision": 2, "enabled": True})).status_code == 403
    assert (await api.get(endpoint + "/revisions")).json()["revisions"][0]["action"] == "disabled"


@pytest.mark.parametrize("suffix", [None, "/runs", "/revisions"])
async def test_actions_offsets_respect_existing_database_integer_budget(api, catalog_peer, suffix):
    """Offsets beyond int64 must fail validation rather than overflow SQL and return a server error."""
    binding = await create(api)
    endpoint = "/v1/github/routes" + (f"/{binding['id']}{suffix}" if suffix else "")
    response = await api.get(endpoint, params={"offset": 2**63})
    assert response.status_code == 422, response.text
    response = await api.get(endpoint, params={"offset": 2**63 - 1})
    assert response.status_code == 200, response.text
    assert response.json() == (
        {"total_count": 1, "revisions": []} if suffix == "/revisions" else []
    )
    assert (await api.get(endpoint, params={"offset": -1})).status_code == 422


@pytest.mark.parametrize(
    "change", [{"workflow_paths": [".github/workflows/deploy.yml"]}, {"ref": "refs/heads/develop"}]
)
async def test_editing_policy_during_github_verification_rejects_stale_reserve(
    api, client, catalog_peer, ci_peer, powercell, app, change
):
    """A saved workflow/branch restriction must apply before an in-flight request can allocate its VM."""
    binding = await create(api)
    barrier = ci_peer.barrier = asyncio.Event()
    entered = catalog_peer.entered["/repositories/42"] = asyncio.Event()
    pending = asyncio.create_task(reserve(client, binding, ci_peer))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        ci_peer.barrier = None
        edited = await api.patch(
            f"/v1/github/routes/{binding['id']}",
            json={"revision": 1, "configuration": policy(**change)},
        )
        assert edited.status_code == 200, edited.text
    finally:
        barrier.set()
    response = await pending
    assert response.status_code == 403, response.text
    async with app.state.db.read() as session:
        assert (await session.scalars(select(CIReservation))).all() == []
        assert (await session.scalars(select(Isolate))).all() == []


@pytest.mark.parametrize(
    ("branch", "literal_filter"),
    [
        ("релиз", "релиз"),
        ("feature+ci", r"feature\+ci"),
        ('feature"ci', 'feature"ci'),
        ("!release", r"\!release"),
        ("fix!hot++", r"fix\!hot\+\+"),
        ("release\u00a0", "release\u00a0"),
        ("release\u2003", "release\u2003"),
        ("a" * 201, "a" * 201),
        ("a" * 239, "a" * 239),
    ],
)
async def test_provider_branches_can_be_saved_and_safely_rendered(
    api, catalog_peer, branch, literal_filter
):
    """Real branches must roundtrip exactly, including Unicode spaces and the existing 250-char ref budget."""
    catalog_peer.branches.append({"name": branch})
    metadata = await api.get(
        "/v1/github/repositories/42/configuration",
        params={"installation_id": 456, "branch": branch},
    )
    assert metadata.status_code == 200 and branch in metadata.json()["branches"]
    binding = await create(api, ref="refs/heads/" + branch)
    assert binding["ref"] == "refs/heads/" + branch
    response = await api.get(f"/v1/github/routes/{binding['id']}/workflow")
    parsed = yaml.safe_load(response.json()["yaml"])
    assert parsed[True]["push"]["branches"] == [literal_filter]


async def test_exact_refs_still_reject_ascii_whitespace_and_oversized_names(api, catalog_peer):
    """Preserving legal Unicode whitespace must not normalize invalid refs or exceed stored ref length."""
    response = await api.get(
        "/v1/github/repositories/42/configuration",
        params={"installation_id": 456, "branch": "a" * 240},
    )
    assert response.status_code == 422
    for ref in ("refs/heads/" + "a" * 240, " refs/heads/main", "refs/heads/main "):
        response = await api.post("/v1/github/routes", json=policy(ref=ref))
        assert response.status_code == 422, response.text
    assert (await api.get("/v1/github/routes")).json() == []


async def test_signed_identity_matches_branch_with_unicode_space(
    api, client, catalog_peer, ci_peer, powercell
):
    """A signed run for the selected legal branch must not be rejected because binding input trimmed its ref."""
    ref = "refs/heads/release\u00a0"
    binding = await create(api, ref=ref)
    response = await reserve(
        client,
        binding,
        ci_peer,
        ref=ref,
        workflow_ref=f"test-org/repo/.github/workflows/ci.yml@{ref}",
    )
    assert response.status_code == 200 and response.json()["status"] == "preparing", response.text


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/build+test.yml",
        ".github/workflows/релиз.yml",
        ".github/workflows/build test.yml",
        ".github/workflows/build..test.yml",
        ".github/workflows/" + "a" * 228 + ".yml",
        ".github/workflows/" + "a" * 227 + ".yaml",
    ],
)
async def test_provider_workflow_names_are_discoverable_and_apply_profiles(
    api, client, catalog_peer, ci_peer, powercell, app, path
):
    """Valid filenames up to the stored 250-character path budget must stay selectable and apply profiles."""
    catalog_peer.workflows.append({"name": "Build", "path": path})
    catalog_peer.sources[path] = "jobs:\n  build: {}\n"
    metadata = await api.get(
        "/v1/github/repositories/42/configuration", params={"installation_id": 456}
    )
    assert metadata.status_code == 200
    assert path in [row["path"] for row in metadata.json()["workflows"]]
    binding = await create(
        api, workflow_paths=[path], workflow_profiles={path: {"cpu_millis": 500}}
    )
    response = await reserve(
        client, binding, ci_peer, workflow_ref=f"test-org/repo/{path}@refs/heads/main"
    )
    assert response.status_code == 200 and response.json()["status"] == "preparing", response.text
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, response.json()["id"])
        assert (await session.get(Isolate, row.isolate_id)).cpu_millis == 500


async def test_oversized_workflow_paths_cannot_exceed_existing_storage_budget(api, catalog_peer):
    """Accepting long legal filenames must still reject paths that do not fit the existing DB columns."""
    path = ".github/workflows/" + "a" * 228 + ".yaml"
    assert len(path) == 251
    for values in (
        {"workflow_path": path},
        {"workflow_paths": [path]},
        {"workflow_profiles": {path: {"cpu_millis": 500}}},
    ):
        response = await api.post("/v1/github/routes", json=policy(**values))
        assert response.status_code == 422, response.text
    assert (await api.get("/v1/github/routes")).json() == []


@pytest.mark.parametrize(
    "path,suffix,branch,legacy",
    [
        (".github/workflows/build@nightly.yml", "", "main", False),
        (".github/workflows/build@nightly.yml", "@refs/heads/main", "main", False),
        (".github/workflows/build@nightly.yml", "@main", "main", False),
        (".github/workflows/build@nightly.yml", "@refs/heads/feature@ci", "feature@ci", False),
        (".github/workflows/ci.yml", "@refs/heads/main", "main", True),
    ],
)
async def test_workflow_filename_at_survives_runner_registration(
    api, client, catalog_peer, ci_peer, powercell, app, clock, path, suffix, branch, legacy
):
    """An @ inside a real workflow name or branch must not be confused with GitHub's optional path/ref separator."""
    binding = await create(
        api,
        workflow_path=path,
        workflow_paths=None if legacy else [path],
        ref="refs/heads/" + branch,
    )
    catalog_peer.raw["/repos/test-org/repo/actions/runs/1234/attempts/1"] = {
        "id": 1234,
        "repository": {"id": 42},
        "head_repository": {"id": 42},
        "head_branch": branch,
        "event": "workflow_dispatch",
        "run_attempt": 1,
        "path": path + suffix,
        "status": "in_progress",
    }
    response = await reserve(
        client,
        binding,
        ci_peer,
        ref="refs/heads/" + branch,
        workflow_ref=f"test-org/repo/{path}@refs/heads/{branch}",
    )
    assert response.status_code == 200 and response.json()["status"] == "preparing", response.text
    identifier = response.json()["id"]
    await schedule(app, clock)
    response = await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    task = response.json()["tasks"][0]
    assert task["kind"] == "create"
    response = await powercell.client.post(f"/agent/v1/tasks/{task['id']}/complete", json={})
    assert response.status_code == 200
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, identifier)
        assert row.status == "preparing", (row.status, row.reason)
        assert row.runner_id is not None
