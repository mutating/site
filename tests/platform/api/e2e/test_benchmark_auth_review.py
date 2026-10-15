"""Additional benchmark authorization regressions over real HTTP and SQL."""

from datetime import timedelta

import pytest

pytestmark = pytest.mark.anyio


def measurement(computer, number=12):
    return {
        "computer_id": computer["id"],
        "benchmark_name": "single_core",
        "benchmarker_version": "0.0.2",
        "number": number,
    }


async def create_result(api, computer):
    response = await api.post("/v1/benchmark-results", json=measurement(computer))
    assert response.status_code == 201, response.text
    return response.json()


async def read_responses(client, computer, row, *, headers):
    return [
        await client.get(f"/v1/benchmark-results/{row['id']}", headers=headers),
        await client.get(
            "/v1/benchmark-results",
            params={"computer_id": computer["id"], "latest_only": False},
            headers=headers,
        ),
        await client.get(f"/v1/computers/{computer['id']}/benchmark-results", headers=headers),
    ]


async def write_responses(client, computer, row, *, headers):
    return [
        await client.post("/v1/benchmark-results", json=measurement(computer, 20), headers=headers),
        await client.patch(
            f"/v1/benchmark-results/{row['id']}", json={"number": 30}, headers=headers
        ),
        await client.delete(f"/v1/benchmark-results/{row['id']}", headers=headers),
    ]


@pytest.mark.parametrize(
    "access_token",
    [
        {"scopes": ["read"]},
        {"scopes": ["manage"]},
        {"scopes": ["execute"]},
        {"scopes": ["read", "execute"]},
        {"scopes": ["manage", "execute"]},
    ],
    indirect=True,
)
async def test_token_scopes_independently_gate_every_benchmark_operation(
    api, client, computer, access_token
):
    """Each result read requires the read scope and each mutation requires manage, independently of the user's ownership."""
    row = await create_result(api, computer)
    headers = {"Authorization": "Bearer " + access_token["token"]}
    for response in await read_responses(client, computer, row, headers=headers):
        assert response.status_code == (200 if "read" in access_token["scopes"] else 403)
        if response.status_code == 403:
            assert response.json() == {"detail": "Token lacks required scope"}
    responses = await write_responses(client, computer, row, headers=headers)
    expected = [201, 200, 204] if "manage" in access_token["scopes"] else [403] * 3
    assert [response.status_code for response in responses] == expected
    if "manage" not in access_token["scopes"]:
        assert (await api.get(f"/v1/benchmark-results/{row['id']}")).json() == row


@pytest.mark.parametrize("access_token", [{"expires_in_seconds": 60}], indirect=True)
@pytest.mark.parametrize("invalidity", ["expired", "revoked"])
async def test_invalidated_personal_token_cannot_read_or_modify_benchmarks(
    api, client, computer, access_token, clock, invalidity
):
    """Expired or revoked personal tokens lose all benchmark access immediately without changing existing scores."""
    row = await create_result(api, computer)
    if invalidity == "expired":
        clock.now += timedelta(seconds=60)
    else:
        response = await api.patch(f"/v1/access-tokens/{access_token['id']}", json={"revoke": True})
        assert response.status_code == 200, response.text
    headers = {"Authorization": "Bearer " + access_token["token"]}
    responses = await read_responses(client, computer, row, headers=headers)
    responses += await write_responses(client, computer, row, headers=headers)
    assert [response.status_code for response in responses] == [401] * 6
    assert (await api.get(f"/v1/benchmark-results/{row['id']}")).json() == row


async def test_company_token_cannot_use_issuers_personal_or_other_company_benchmarks(
    api, client, company, company_token, computer
):
    """A company token reaches only that company's computer results, even when its issuer personally owns other computers."""
    personal_row = await create_result(api, computer)
    response = await api.post("/v1/companies", json={"name": "Separate issuer company"})
    assert response.status_code == 201, response.text
    second_company = response.json()
    response = await api.post(
        "/v1/computers",
        json={
            "company_id": second_company["id"],
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
        },
    )
    assert response.status_code == 201, response.text
    second_computer = response.json()
    second_row = await create_result(api, second_computer)
    headers = {"Authorization": "Bearer " + company_token["token"]}
    for target_computer, row in [(computer, personal_row), (second_computer, second_row)]:
        responses = await read_responses(client, target_computer, row, headers=headers)
        responses += await write_responses(client, target_computer, row, headers=headers)
        assert [response.status_code for response in responses] == [404] * 6
        assert (await api.get(f"/v1/benchmark-results/{row['id']}")).json() == row

    response = await api.patch(
        f"/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    responses = await read_responses(client, computer, personal_row, headers=headers)
    assert [response.status_code for response in responses] == [200] * 3
    responses = await write_responses(client, computer, personal_row, headers=headers)
    assert [response.status_code for response in responses] == [201, 200, 204]


async def test_removed_member_immediately_loses_all_existing_benchmark_access(
    api, other_api, computer, company, membership
):
    """Removing organization membership revokes access to existing benchmark rows and prevents every mutation."""
    response = await api.patch(
        f"/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    response = await api.patch(
        f"/v1/memberships/{membership['id']}",
        json={"permissions": ["resources.read", "resources.manage"]},
    )
    assert response.status_code == 200, response.text
    row = await create_result(other_api, computer)
    assert (await other_api.get(f"/v1/benchmark-results/{row['id']}")).status_code == 200
    assert (await api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
    responses = await read_responses(other_api, computer, row, headers={})
    responses += await write_responses(other_api, computer, row, headers={})
    assert [response.status_code for response in responses] == [404] * 6
    assert (await api.get(f"/v1/benchmark-results/{row['id']}")).json() == row


async def test_benchmark_access_follows_computer_transfer_away_from_original_registrant(
    api, other_api, client, computer, company, company_token, membership, other_user
):
    """Transferring a computer moves benchmark authority to its new owner and invalidates the previous owner's device credential."""
    response = await api.patch(
        f"/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    response = await api.patch(
        f"/v1/memberships/{membership['id']}",
        json={"permissions": ["resources.read", "resources.manage"]},
    )
    assert response.status_code == 200, response.text
    row = await create_result(api, computer)
    response = await api.post(f"/v1/computers/{computer['id']}/credentials")
    assert response.status_code == 201, response.text
    old_credential = response.json()["token"]
    response = await other_api.patch(
        f"/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"user_id": other_user["id"]},
    )
    assert response.status_code == 200, response.text
    for headers in [{}, {"Authorization": "Bearer " + company_token["token"]}]:
        responses = await read_responses(api, computer, row, headers=headers)
        responses += await write_responses(api, computer, row, headers=headers)
        assert [response.status_code for response in responses] == [404] * 6
    assert (await other_api.get(f"/v1/benchmark-results/{row['id']}")).json() == row
    response = await client.put(
        "/agent/v1/benchmark-results",
        headers={"Authorization": "Bearer " + old_credential},
        json={
            "benchmarker_version": "0.0.2",
            "results": [{"benchmark_name": "single_core", "number": 999}],
        },
    )
    assert response.status_code == 401, response.text
    assert (await other_api.get(f"/v1/benchmark-results/{row['id']}")).json() == row
    responses = await write_responses(other_api, computer, row, headers={})
    assert [response.status_code for response in responses] == [201, 200, 204]


async def test_company_token_is_revoked_when_issuer_loses_token_management(
    api, other_api, client, computer, company, membership
):
    """A company token stops authorizing benchmark access once its issuer loses permission to manage company tokens."""
    response = await api.patch(
        f"/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    response = await api.patch(
        f"/v1/memberships/{membership['id']}",
        json={"permissions": ["resources.read", "resources.manage", "tokens.manage"]},
    )
    assert response.status_code == 200, response.text
    response = await other_api.post("/v1/token-ownerships", json={"company_id": company["id"]})
    assert response.status_code == 201, response.text
    response = await other_api.post(
        "/v1/access-tokens",
        json={
            "ownership_id": response.json()["id"],
            "name": "Member benchmark token",
            "scopes": ["read", "manage"],
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    row = await create_result(api, computer)
    assert (
        await client.get(f"/v1/benchmark-results/{row['id']}", headers=headers)
    ).status_code == 200
    response = await api.patch(
        f"/v1/memberships/{membership['id']}",
        json={"permissions": ["resources.read", "resources.manage"]},
    )
    assert response.status_code == 200, response.text
    responses = await read_responses(client, computer, row, headers=headers)
    responses += await write_responses(client, computer, row, headers=headers)
    assert [response.status_code for response in responses] == [401] * 6
    assert (await other_api.get(f"/v1/benchmark-results/{row['id']}")).json() == row


async def test_resource_manage_permission_does_not_imply_resource_read(
    api, other_api, computer, company, membership
):
    """Resource management permission permits benchmark writes but does not grant the separately required permission to read results."""
    response = await api.patch(
        f"/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    response = await api.patch(
        f"/v1/memberships/{membership['id']}", json={"permissions": ["resources.manage"]}
    )
    assert response.status_code == 200, response.text
    row = await create_result(other_api, computer)
    for response in await read_responses(other_api, computer, row, headers={}):
        assert response.status_code == 403, response.text
        assert response.json() == {"detail": "Organization permission required"}
    assert (await api.get(f"/v1/benchmark-results/{row['id']}")).json() == row
    responses = await write_responses(other_api, computer, row, headers={})
    assert [response.status_code for response in responses] == [201, 200, 204]
