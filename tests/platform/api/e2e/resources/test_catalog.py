"""The global resource catalog is controlled by host-appointed system administrators."""

import asyncio
import sys
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_builtin_types_and_catalog_crud(admin_api, resource_type):
    """CPU and memory are seeded once; custom types support paginated CRUD and soft deletion."""
    response = await admin_api.get("/api/v1/resource-types")
    assert response.status_code == 200
    rows = response.json()
    assert [(row["code"], row["unit"], row["builtin"]) for row in rows] == [
        ("cpu", "millicore", True),
        ("gpu", "device", False),
        ("memory", "byte", True),
    ]
    assert (
        await admin_api.get("/api/v1/resource-types", params={"limit": 1, "offset": 1})
    ).json() == [resource_type]
    path = f"/api/v1/resource-types/{resource_type['id']}"
    assert (await admin_api.get(path)).json() == resource_type
    response = await admin_api.patch(
        path, json={"name": "Accelerators", "description": "GPU slots"}
    )
    assert response.status_code == 200
    assert (
        response.json()["name"] == "Accelerators" and response.json()["description"] == "GPU slots"
    )
    assert (await admin_api.delete(path)).status_code == 204
    for method, kwargs in [("GET", {}), ("PATCH", {"json": {"name": "Missing"}}), ("DELETE", {})]:
        assert (await admin_api.request(method, path, **kwargs)).status_code == 404
    assert len((await admin_api.get("/api/v1/resource-types")).json()) == 2
    # A deleted code remains reserved so old history cannot silently acquire a different unit.
    assert (
        await admin_api.post(
            "/api/v1/resource-types", json={"name": "GPU", "code": "gpu", "unit": "other"}
        )
    ).status_code == 409


async def test_builtin_identity_and_units_cannot_be_changed(admin_api):
    """Built-in types can be described and renamed without deleting or changing their measurement units."""
    rows = (await admin_api.get("/api/v1/resource-types")).json()
    for row in rows:
        path = f"/api/v1/resource-types/{row['id']}"
        assert (await admin_api.delete(path)).status_code == 409
        for payload in [
            {"unit": "core"},
            {"code": "another"},
            {"builtin": False},
            {"name": None},
            {"description": None},
        ]:
            assert (await admin_api.patch(path, json=payload)).status_code == 422
        response = await admin_api.patch(path, json={"name": "Renamed " + row["name"]})
        assert response.status_code == 200 and response.json()["builtin"] is True
        assert response.json()["unit"] == row["unit"] and response.json()["code"] == row["code"]


@pytest.mark.parametrize("membership", ["user", "admin"], indirect=True)
async def test_company_roles_do_not_grant_catalog_administration(
    api, other_api, membership, company_token, client
):
    """Ordinary users, company creators, company admins and company tokens cannot read or mutate the global catalog."""
    unknown = str(uuid4())
    for headers in [
        dict(api.headers),
        dict(other_api.headers),
        {"Authorization": "Bearer " + company_token["token"]},
    ]:
        for method, path, kwargs in [
            ("GET", "/api/v1/resource-types", {}),
            (
                "POST",
                "/api/v1/resource-types",
                {"json": {"code": "gpu", "name": "GPU", "unit": "device"}},
            ),
            ("GET", f"/api/v1/resource-types/{unknown}", {}),
            ("PATCH", f"/api/v1/resource-types/{unknown}", {"json": {"name": "GPU"}}),
            ("DELETE", f"/api/v1/resource-types/{unknown}", {}),
        ]:
            response = await client.request(method, path, headers=headers, **kwargs)
            assert response.status_code == 403, response.text


async def test_system_admin_flag_cannot_be_self_assigned(client, api, user):
    """Signup and profile updates reject the privilege field, so users cannot promote themselves through HTTP."""
    response = await client.post(
        "/api/v1/users",
        json={
            "email": "intruder@example.com",
            "password": "long enough password",
            "first_name": "A",
            "last_name": "B",
            "nickname": "intruder",
            "is_system_admin": True,
        },
    )
    assert response.status_code == 422
    assert (
        await api.patch(f"/api/v1/users/{user['id']}", json={"is_system_admin": True})
    ).status_code == 422
    assert (await api.get("/api/v1/users/me")).json()["is_system_admin"] is False


async def test_cli_revocation_applies_to_already_issued_tokens(
    admin_api, system_admin, cli_environment
):
    """Removing the host-granted administrator role immediately closes catalog access for an existing bearer token."""
    assert (await admin_api.get("/api/v1/resource-types")).status_code == 200
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "throng_api.cli",
        "system-admin",
        system_admin["id"],
        "--revoke",
        env=cli_environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, errors = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 0, errors.decode()
    assert (await admin_api.get("/api/v1/resource-types")).status_code == 403
    assert (await admin_api.get("/api/v1/users/me")).json()["is_system_admin"] is False


async def test_cli_rejects_unknown_user(cli_environment):
    """Host administration fails clearly instead of silently granting a role to a nonexistent user."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "throng_api.cli",
        "system-admin",
        str(uuid4()),
        env=cli_environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, errors = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 2 and "User not found" in errors.decode()


async def test_system_admin_privilege_does_not_cross_company_token_or_inventory_boundaries(
    admin_api, administrator_company_token, computer, computer_resources, history_range, user
):
    """A catalog administrator cannot inspect private inventory, and its company's token gains no global administrator privilege."""
    assert (
        await admin_api.get(
            "/api/v1/resource-types",
            headers={"Authorization": "Bearer " + administrator_company_token["token"]},
        )
    ).status_code == 403
    for path in [
        f"/api/v1/computers/{computer['id']}/resources",
        f"/api/v1/computer-resources/{computer_resources['cpu']['id']}",
        f"/api/v1/users/{user['id']}/resources/summary",
        f"/api/v1/users/{user['id']}/resources/history",
    ]:
        assert (await admin_api.get(path, params=history_range)).status_code == 404


async def test_active_resources_prevent_type_deletion(admin_api, api, resource_type, gpu_resource):
    """Deleting an in-use type fails atomically; after detaching it, old resource history remains readable."""
    path = f"/api/v1/resource-types/{resource_type['id']}"
    assert (await admin_api.delete(path)).status_code == 409
    assert (await admin_api.get(path)).status_code == 200
    assert (await api.delete(f"/api/v1/computer-resources/{gpu_resource['id']}")).status_code == 204
    assert (await admin_api.delete(path)).status_code == 204
