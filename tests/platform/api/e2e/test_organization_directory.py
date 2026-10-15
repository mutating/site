"""Directory totals are real ownership/member counts, including invitation previews."""

import pytest

pytestmark = pytest.mark.anyio


async def add_computer(api, company_id=None):
    payload = {
        "name": "Directory node",
        "os": "linux",
        "architecture": "amd64",
        "runtime": "oci",
        "cpu_millis": 1000,
        "memory_bytes": 1024**3,
        "disk_bytes": 1024**3,
    }
    if company_id:
        payload["company_id"] = company_id
    response = await api.post("/v1/computers", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_new_organization_totals_are_consistent_across_create_list_read_and_update(
    api,
):
    created = await api.post("/v1/companies", json={"name": "Fresh space"})
    assert created.status_code == 201
    company = created.json()
    assert (company["computer_count"], company["member_count"]) == (0, 1)
    for response in [
        await api.get(f"/v1/companies/{company['id']}"),
        await api.patch(f"/v1/companies/{company['id']}", json={"name": "Renamed"}),
    ]:
        assert response.status_code == 200
        assert (response.json()["computer_count"], response.json()["member_count"]) == (
            0,
            1,
        )
    assert (await api.get("/v1/companies")).json()[0]["member_count"] == 1


async def test_totals_include_offline_and_disabled_computers_without_join_multiplication(
    api, company, membership
):
    first = await add_computer(api, company["id"])
    await add_computer(api, company["id"])
    await add_computer(
        api
    )  # A personal computer must not enter the organization total.
    response = await api.patch(f"/v1/computers/{first['id']}", json={"enabled": False})
    assert response.status_code == 200
    company = (await api.get(f"/v1/companies/{company['id']}")).json()
    assert (company["computer_count"], company["member_count"]) == (2, 2)
    listing = (await api.get("/v1/companies")).json()
    assert (listing[0]["computer_count"], listing[0]["member_count"]) == (2, 2)


async def test_counts_follow_membership_changes_and_preserve_permission_boundaries(
    api, other_api, company, membership
):
    await add_computer(api, company["id"])
    changed = await api.patch(
        f"/v1/memberships/{membership['id']}", json={"permissions": []}
    )
    assert changed.status_code == 200
    preview = (await other_api.get("/v1/companies")).json()[0]
    assert preview["my_permissions"] == []
    assert (preview["computer_count"], preview["member_count"]) == (1, 2)
    assert (
        await other_api.get(f"/v1/companies/{company['id']}/resources/summary")
    ).status_code == 403
    assert (await api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
    assert (await other_api.get("/v1/companies")).json() == []
    assert (await api.get(f"/v1/companies/{company['id']}")).json()["member_count"] == 1


async def test_invitation_previews_return_current_totals_without_granting_membership(
    api, other_api, company, other_user
):
    created = await api.post(
        "/v1/invitations",
        json={"company_id": company["id"], "identifier": other_user["email"]},
    )
    assert created.status_code == 201
    invitation = created.json()
    assert (invitation["computer_count"], invitation["member_count"]) == (0, 1)
    await add_computer(api, company["id"])
    await add_computer(api, company["id"])
    for response in [
        await other_api.get("/v1/invitations"),
        await api.get("/v1/invitations", params={"company_id": company["id"]}),
    ]:
        assert response.status_code == 200
        row = response.json()[0]
        assert (row["computer_count"], row["member_count"]) == (2, 1)
        assert "token" not in row
    link = await other_api.get(f"/v1/invitations/link/{invitation['token']}")
    assert (link.json()["computer_count"], link.json()["member_count"]) == (2, 1)
    assert (await other_api.get(f"/v1/companies/{company['id']}")).status_code == 404
    assert (await other_api.get("/v1/companies")).json() == []
    accepted = await other_api.post(f"/v1/invitations/{invitation['id']}/accept")
    assert accepted.status_code == 200
    assert (await other_api.get("/v1/invitations")).json() == []
    listing = (await other_api.get("/v1/companies")).json()
    assert (listing[0]["computer_count"], listing[0]["member_count"]) == (2, 2)


async def test_paginated_totals_belong_to_the_correct_visible_organizations(
    api, other_api
):
    created = []
    for index in range(3):
        response = await api.post("/v1/companies", json={"name": f"Space {index}"})
        row = response.json()
        for _ in range(index):
            await add_computer(api, row["id"])
        created.append(row)
    await other_api.post("/v1/companies", json={"name": "Private outsider"})
    actual = []
    for offset in range(4):
        response = await api.get("/v1/companies", params={"limit": 1, "offset": offset})
        assert response.status_code == 200
        actual.extend(response.json())
    assert [row["id"] for row in actual] == [row["id"] for row in created]
    assert [row["computer_count"] for row in actual] == [0, 1, 2]
    assert [row["member_count"] for row in actual] == [1, 1, 1]
