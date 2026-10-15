"""Resource CRUD, ownership authorization and compatibility with existing computer endpoints."""

import asyncio
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_custom_resource_crud_restoration_and_builtin_compatibility(
    api, computer, computer_resources, resource_type, gpu_resource
):
    """Resources have independent CRUD, stable identities on restoration and one authoritative CPU/memory capacity."""
    collection = f"/v1/computers/{computer['id']}/resources"
    path = f"/v1/computer-resources/{gpu_resource['id']}"
    assert (await api.get(path)).json() == gpu_resource
    rows = (await api.get(collection)).json()
    assert len(rows) == 3
    assert (await api.get(collection, params={"limit": 1, "offset": 1})).json() == rows[1:2]
    response = await api.patch(path, json={"total": 8, "expected_version": 1})
    assert response.status_code == 200
    assert (
        response.json()["total"],
        response.json()["occupied"],
        response.json()["free"],
        response.json()["version"],
    ) == (8, 0, 8, 2)
    assert (await api.patch(path, json={"total": 16, "expected_version": 1})).status_code == 409
    assert (await api.delete(path)).status_code == 204
    assert (await api.get(path)).status_code == 404
    response = await api.post(
        collection, json={"resource_type_id": resource_type["id"], "total": 3}
    )
    assert response.status_code == 201 and response.json()["id"] == gpu_resource["id"]
    assert response.json()["version"] == 4 and response.json()["total"] == 3
    assert (
        await api.post(collection, json={"resource_type_id": resource_type["id"], "total": 9})
    ).status_code == 409
    for code, field, value in [("cpu", "cpu_millis", 3500), ("memory", "memory_bytes", 4096)]:
        resource_path = f"/v1/computer-resources/{computer_resources[code]['id']}"
        assert (await api.patch(resource_path, json={"total": value})).status_code == 200
        assert (await api.get(f"/v1/computers/{computer['id']}")).json()[field] == value
        assert (
            await api.patch(f"/v1/computers/{computer['id']}", json={field: value + 1})
        ).status_code == 200
        assert (await api.get(resource_path)).json()["total"] == value + 1


@pytest.mark.parametrize("total", [-1, 2**60 + 1, 1.5, "10", True, None])
async def test_capacity_requires_bounded_nonnegative_integers(
    api, computer, computer_resources, total
):
    """Capacity validation rejects negatives, overflow, fractions and coerced input without changing inventory."""
    response = await api.patch(
        f"/v1/computer-resources/{computer_resources['cpu']['id']}", json={"total": total}
    )
    assert response.status_code == 422
    response = await api.post(
        f"/v1/computers/{computer['id']}/resources",
        json={"resource_type_id": computer_resources["cpu"]["resource_type_id"], "total": total},
    )
    assert response.status_code == 422
    assert (await api.get(f"/v1/computers/{computer['id']}")).json()["cpu_millis"] == 2000


async def test_users_cannot_write_occupied_or_reassign_resource_identity(api, computer_resources):
    """Only the scheduler changes reservations; clients cannot replace a resource's type, computer or occupied count."""
    path = f"/v1/computer-resources/{computer_resources['cpu']['id']}"
    for extra in [
        {"occupied": 1},
        {"computer_id": str(uuid4())},
        {"resource_type_id": str(uuid4())},
        {"version": 100},
        {"deleted_at": None},
    ]:
        assert (await api.patch(path, json={"total": 2000, **extra})).status_code == 422


async def test_foreign_and_absent_resources_are_indistinguishable(
    api, other_api, user, computer, computer_resources, history_range
):
    """Foreign and nonexistent IDs both return the same 404, concealing the existence of another owner's resources and reports."""
    resource = computer_resources["cpu"]
    for method, suffix, kwargs in [
        ("GET", "", {}),
        ("PATCH", "", {"json": {"total": 9000}}),
        ("DELETE", "", {}),
        ("GET", "/history", {"params": history_range}),
    ]:
        foreign = await other_api.request(
            method, f"/v1/computer-resources/{resource['id']}{suffix}", **kwargs
        )
        absent = await other_api.request(
            method, f"/v1/computer-resources/{uuid4()}{suffix}", **kwargs
        )
        assert foreign.status_code == absent.status_code == 404
        assert foreign.json() == absent.json() == {"detail": "Resource not found"}
    for suffix in ["summary", "history"]:
        response = await other_api.get(
            f"/v1/users/{user['id']}/resources/{suffix}", params=history_range
        )
        assert response.status_code == 404 and response.json() == {"detail": "Resource not found"}
    for method, kwargs in [
        ("GET", {}),
        ("POST", {"json": {"resource_type_id": resource["resource_type_id"], "total": 1000}}),
    ]:
        assert (
            await other_api.request(method, f"/v1/computers/{computer['id']}/resources", **kwargs)
        ).status_code == 404
    assert (await api.get(f"/v1/computer-resources/{resource['id']}")).json() == resource


@pytest.mark.parametrize("membership", ["user", "admin"], indirect=True)
async def test_company_members_read_inventory_and_only_managers_can_mutate(
    other_api, membership, company_computer, company_resources, company, history_range
):
    """Company membership permits resource reports; only creator/admin roles may edit company capacity."""
    resource = company_resources["cpu"]
    assert (
        await other_api.get(f"/v1/computers/{company_computer['id']}/resources")
    ).status_code == 200
    assert (
        await other_api.get(
            f"/v1/computer-resources/{resource['id']}/history", params=history_range
        )
    ).status_code == 200
    for suffix in ["summary", "history"]:
        assert (
            await other_api.get(
                f"/v1/companies/{company['id']}/resources/{suffix}", params=history_range
            )
        ).status_code == 200
    expected = 200 if membership["role"] == "admin" else 403
    assert (
        await other_api.patch(f"/v1/computer-resources/{resource['id']}", json={"total": 5000})
    ).status_code == expected
    expected = 204 if membership["role"] == "admin" else 403
    assert (
        await other_api.delete(f"/v1/computer-resources/{resource['id']}")
    ).status_code == expected
    response = await other_api.post(
        f"/v1/computers/{company_computer['id']}/resources",
        json={"resource_type_id": resource["resource_type_id"], "total": 3000},
    )
    assert response.status_code == (201 if membership["role"] == "admin" else 403)


async def test_membership_revocation_closes_existing_resource_access(
    api, other_api, membership, company_resources, company, history_range
):
    """Revoking membership removes access to current inventory and old company graphs on the next request."""
    path = f"/v1/computer-resources/{company_resources['cpu']['id']}"
    assert (await other_api.get(path)).status_code == 200
    assert (await api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
    for url in [
        path,
        path + "/history",
        f"/v1/companies/{company['id']}/resources/summary",
        f"/v1/companies/{company['id']}/resources/history",
    ]:
        assert (await other_api.get(url, params=history_range)).status_code == 404


async def test_company_token_is_scoped_to_company_inventory(
    client, company_token, company_resources, computer_resources, user, company, history_range
):
    """Corporate tokens can edit their company's inventory but cannot use the issuer's personal resources or statistics."""
    headers = {"Authorization": "Bearer " + company_token["token"]}
    response = await client.get(
        f"/v1/computers/{company_resources['cpu']['computer_id']}/resources", headers=headers
    )
    assert response.status_code == 200
    assert {row["id"] for row in response.json()} == {
        row["id"] for row in company_resources.values()
    }
    response = await client.patch(
        f"/v1/computer-resources/{company_resources['cpu']['id']}",
        headers=headers,
        json={"total": 6000},
    )
    assert response.status_code == 200
    for suffix in ["summary", "history"]:
        assert (
            await client.get(
                f"/v1/companies/{company['id']}/resources/{suffix}",
                headers=headers,
                params=history_range,
            )
        ).status_code == 200
        assert (
            await client.get(
                f"/v1/users/{user['id']}/resources/{suffix}",
                headers=headers,
                params=history_range,
            )
        ).status_code == 404
    assert (
        await client.get(
            f"/v1/computer-resources/{computer_resources['cpu']['id']}", headers=headers
        )
    ).status_code == 404


async def test_unknown_and_archived_types_cannot_be_attached(
    api, admin_api, computer, resource_type
):
    """Creating inventory requires an active type and never accepts unknown or archived catalog identities."""
    assert (await admin_api.delete(f"/v1/resource-types/{resource_type['id']}")).status_code == 204
    for type_id in [str(uuid4()), resource_type["id"]]:
        response = await api.post(
            f"/v1/computers/{computer['id']}/resources",
            json={"resource_type_id": type_id, "total": 0},
        )
        assert response.status_code == 404


async def test_competing_versioned_updates_and_duplicate_creates_are_serialized(
    api, secondary_client, user, computer_resources, computer, resource_type
):
    """Two API/controller instances cannot both consume a resource version or create duplicate computer/type rows."""
    path = f"/v1/computer-resources/{computer_resources['cpu']['id']}"
    responses = await asyncio.gather(
        api.patch(path, json={"total": 3000, "expected_version": 1}),
        secondary_client.patch(
            path, headers=user["headers"], json={"total": 4000, "expected_version": 1}
        ),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (await api.get(path)).json()["version"] == 2
    path = f"/v1/computers/{computer['id']}/resources"
    payload = {"resource_type_id": resource_type["id"], "total": 4}
    responses = await asyncio.gather(
        api.post(path, json=payload),
        secondary_client.post(path, headers=user["headers"], json=payload),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len((await api.get(path)).json()) == 3
