"""Behavior boundaries that must survive consolidation of Actions API helpers."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from tests.platform.api.e2e.test_github import connect
from throng_api.models import Artifact, CIReservation, CIRoute, ComputerOwnership
from throng_api.serialization import public

from .test_ci import reserve
from .test_repository_bindings import catalog_peer as catalog_peer
from .test_repository_bindings import ci_peer as ci_peer
from .test_repository_bindings import create, policy
from .test_repository_bindings import github as github
from .test_repository_bindings import powercell as powercell

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]
DEPLOY = ".github/workflows/deploy.yml"


async def test_binding_visibility_and_pagination_share_the_same_permission_boundary(
    api,
    other_api,
    other_user,
    app,
    clock,
    catalog_peer,
    github,
    company,
    membership,
    company_token,
):
    """Filtering must precede pagination, with stable ties and identical catalog/detail visibility after permission changes."""
    bindings = []
    for _ in range(5):
        binding = await create(api)
        assert (await api.delete(f"/v1/github/routes/{binding['id']}")).status_code == 204
        bindings.append(binding)
    # Interleave inaccessible Alice rows with Bob's personal and company rows;
    # timestamps tie deliberately so the ID is the required secondary ordering.
    identifiers = sorted(binding["id"] for binding in bindings)
    async with app.state.db.write() as session:
        for index, identifier in enumerate(identifiers):
            row = await session.get(CIRoute, identifier)
            row.created_at = clock.now
            if index in (1, 4):
                row.user_id = other_user["id"]
            elif index == 2:
                row.user_id, row.company_id = None, company["id"]
    github.peer.user_id += 1
    await connect(other_api)
    for permissions, expected in [
        (["execution.read"], identifiers[1:3] + identifiers[4:]),
        ([], [identifiers[1], identifiers[4]]),
    ]:
        response = await api.patch(
            f"/v1/memberships/{membership['id']}", json={"permissions": permissions}
        )
        assert response.status_code == 200, response.text
        listed = await other_api.get("/v1/github/routes")
        assert [row["id"] for row in listed.json()] == expected
        for offset in range(len(expected) + 1):
            page = await other_api.get("/v1/github/routes", params={"limit": 1, "offset": offset})
            assert [row["id"] for row in page.json()] == expected[offset : offset + 1]
        metadata = await other_api.get(
            "/v1/github/repositories/42/configuration", params={"installation_id": 456}
        )
        assert metadata.status_code == 200, metadata.text
        assert {row["id"] for row in metadata.json()["bindings"]} == set(expected)
        catalog = await other_api.get("/v1/github/repositories", params={"search": "test-org/repo"})
        assert catalog.status_code == 200, catalog.text
        row = catalog.json()["repositories"][0]
        assert row["binding_id"] == expected[-1]
        assert row["state"] == "disabled" and row["active_bindings"] == 0
    corporate = await api.get(
        "/v1/github/routes",
        headers={"Authorization": "Bearer " + company_token["token"]},
    )
    assert corporate.status_code == 200, corporate.text
    assert [row["id"] for row in corporate.json()] == [identifiers[2]]


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("organization", [False, True])
@pytest.mark.parametrize("location", ["default", "profile"])
@pytest.mark.parametrize(
    "kind,expected", [("valid", 200), ("missing", 404), ("foreign", 404), ("unsupported", 422)]
)
async def test_computer_validation_is_identical_for_create_edit_defaults_and_profiles(
    api,
    other_api,
    app,
    catalog_peer,
    powercell,
    computer,
    company,
    editing,
    organization,
    location,
    kind,
    expected,
):
    """Default/profile computers must obey the exact pool and runtime boundary; rejected writes leave no policy, audit, or artifact residue."""
    owner = {"company_id": company["id"]} if organization else {}
    if organization:
        async with app.state.db.write() as session:
            for identifier in (powercell.computer_id, computer["id"]):
                ownership = await session.scalar(
                    select(ComputerOwnership).where(ComputerOwnership.computer_id == identifier)
                )
                ownership.user_id, ownership.company_id = None, company["id"]
    foreign = await other_api.post(
        "/v1/computers",
        json={
            "name": "foreign",
            "os": "linux",
            "architecture": "arm64",
            "runtime": "oci",
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
        },
    )
    assert foreign.status_code == 201, foreign.text
    candidate = {
        "valid": powercell.computer_id,
        "missing": str(uuid4()),
        "foreign": foreign.json()["id"],
        "unsupported": computer["id"],
    }[kind]
    default, override = (
        (candidate, powercell.computer_id)
        if location == "default"
        else (powercell.computer_id, candidate)
    )
    configuration = policy(
        **owner,
        computer_ids=[default.upper(), default],
        workflow_profiles={DEPLOY: {"computer_ids": [override.upper(), override]}},
    )
    binding = await create(api, **owner) if editing else None
    endpoint = f"/v1/github/routes/{binding['id']}" if binding else "/v1/github/routes"
    history = (await api.get(endpoint + "/revisions")).json() if binding else None
    async with app.state.db.read() as session:
        artifacts = set(await session.scalars(select(Artifact.id)))
    files = set(app.state.settings.storage_path.iterdir())
    response = (
        await api.patch(endpoint, json={"revision": 1, "configuration": configuration})
        if binding
        else await api.post(endpoint, json=configuration)
    )
    assert response.status_code == (201 if expected == 200 and not editing else expected), (
        response.text
    )
    if expected == 200:
        assert response.json()["computer_ids"] == [default]
        assert response.json()["workflow_profiles"][DEPLOY]["computer_ids"] == [override]
        assert response.json()["revision"] == (2 if editing else 1)
    else:
        if binding:
            assert (await api.get(endpoint)).json() == binding
            assert (await api.get(endpoint + "/revisions")).json() == history
        else:
            assert (await api.get(endpoint)).json() == []
        async with app.state.db.read() as session:
            assert set(await session.scalars(select(Artifact.id))) == artifacts
        assert set(app.state.settings.storage_path.iterdir()) == files


@pytest.mark.parametrize("operation", ["reserve", "cancel"])
@pytest.mark.parametrize("dimension", ["route", "run", "attempt", "instance"])
async def test_reservation_lookup_never_reuses_or_cancels_a_neighbor(
    api, client, app, catalog_peer, ci_peer, company, operation, dimension
):
    """Route, run, attempt and instance must all isolate reserve/cancel, preserving neighboring state and exact retry idempotency."""
    binding = await create(api)
    original = await reserve(client, binding, ci_peer)
    assert original.status_code == 200 and original.json()["reason"] == "no_capacity"
    async with app.state.db.read() as session:
        before = public(await session.get(CIReservation, original.json()["id"]))
    target = await create(api, company_id=company["id"]) if dimension == "route" else binding
    claims = (
        {"run_id": "4321"}
        if dimension == "run"
        else {"run_attempt": "2"}
        if dimension == "attempt"
        else {}
    )
    instance = "another-instance" if dimension == "instance" else "default"
    endpoint = f"/v1/github/ci/routes/{target['id']}/{operation}"
    options = {
        "headers": {"Authorization": "Bearer " + ci_peer.token(**claims)},
        "json": {"instance": instance},
    }
    response = await client.post(endpoint, **options)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["id"] != original.json()["id"]
    assert result["status"] == "failed"
    assert result["reason"] == ("bootstrap_cancelled" if operation == "cancel" else "no_capacity")
    assert (await client.post(endpoint, **options)).json() == result
    async with app.state.db.read() as session:
        assert (await session.get(CIReservation, result["id"])).status == (
            "cancelled" if operation == "cancel" else "failed"
        )
        assert public(await session.get(CIReservation, original.json()["id"])) == before
        assert len(list(await session.scalars(select(CIReservation)))) == 2
