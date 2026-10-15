"""Benchmark CRUD, SemVer selection and worker uploads over real HTTP/SQL."""

import asyncio
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


def measurement(computer, *, version="0.0.2", name="single_core", number=1234.5):
    return {
        "computer_id": computer["id"],
        "benchmark_name": name,
        "number": number,
        "benchmarker_version": version,
    }


async def create(api, computer, **kwargs):
    response = await api.post("/v1/benchmark-results", json=measurement(computer, **kwargs))
    assert response.status_code == 201, response.text
    return response.json()


async def test_crud_upsert_retains_identity_and_other_names_and_versions(api, computer):
    first = await create(api, computer)
    assert (await create(api, computer, number=55))["id"] == first["id"]
    second = await create(api, computer, name="multi_core", number=250)
    newer = await create(api, computer, version="0.0.3", number=777)
    path = f"/v1/benchmark-results/{first['id']}"
    assert (await api.get(path)).json()["number"] == 55
    assert (await api.patch(path, json={"number": 0})).json()["number"] == 0
    rows = (
        await api.get(
            "/v1/benchmark-results", params={"computer_id": computer["id"], "latest_only": False}
        )
    ).json()
    assert {row["id"] for row in rows} == {first["id"], second["id"], newer["id"]}
    assert (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json() == [newer]
    assert (await api.delete(f"/v1/benchmark-results/{newer['id']}")).status_code == 204
    assert len((await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json()) == 2
    assert (await api.delete(path)).status_code == 204
    assert (await api.get(path)).status_code == 404
    assert (await api.patch(path, json={"number": 1})).status_code == 404
    assert (await api.delete(path)).status_code == 404


async def test_latest_uses_semver_including_prerelease_without_mixing_suites(api, computer):
    versions = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
        "1.0.9",
        "1.0.10",
        "1.2.0",
        "1.10.0",
        "2.0.0",
        "10.0.0",
    ]
    for version in versions:
        row = await create(api, computer, version=version, name="future_benchmark")
        latest = (
            await api.get("/v1/benchmark-results", params={"computer_id": computer["id"]})
        ).json()
        assert latest == [row]
    await create(api, computer, version="0.1.0", name="another_benchmark")
    assert (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json() == [row]


async def test_build_metadata_tie_is_deterministic(api, computer):
    await create(api, computer, version="1.0.0+build.1")
    winner = await create(api, computer, version="1.0.0+build.2")
    await create(api, computer, version="1.0.0+build.0")
    assert (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json() == [winner]


async def test_access_boundaries_and_computer_deletion_cascade(api, other_api, client, computer):
    row = await create(api, computer)
    path = f"/v1/benchmark-results/{row['id']}"
    listing = f"/v1/computers/{computer['id']}/benchmark-results"
    for peer, status in [(other_api, 404), (client, 401)]:
        for response in [
            await peer.get(path),
            await peer.get(listing),
            await peer.patch(path, json={"number": 1}),
            await peer.delete(path),
            await peer.post("/v1/benchmark-results", json=measurement(computer)),
        ]:
            assert response.status_code == status, response.text
    assert (await api.delete(f"/v1/computers/{computer['id']}")).status_code == 204
    assert (await api.get(path)).status_code == 404


async def test_empty_unknown_and_invalid_computers(api, computer):
    assert (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json() == []
    unknown = {"id": str(uuid4())}
    assert (await api.get(f"/v1/computers/{unknown['id']}/benchmark-results")).status_code == 404
    assert (await api.post("/v1/benchmark-results", json=measurement(unknown))).status_code == 404
    assert (await api.get("/v1/benchmark-results")).status_code == 422
    assert (await api.get("/v1/computers/invalid/benchmark-results")).status_code == 422


@pytest.mark.parametrize(
    "version",
    [
        "1",
        "1.2",
        "v1.2.3",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3-01",
        "1.2.3-alpha..1",
        "1.2.3+",
        "1.2.3-",
        "1.2.3-α",
        "1.2.3\n4",
        "a" * 201,
    ],
)
async def test_reject_invalid_versions_without_writing(api, computer, version):
    assert (
        await api.post("/v1/benchmark-results", json=measurement(computer, version=version))
    ).status_code == 422
    assert (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json() == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("number", -1),
        ("number", True),
        ("number", "12"),
        ("number", None),
        ("benchmark_name", ""),
        ("benchmark_name", "bad/name"),
        ("benchmark_name", "bad\x00name"),
        ("benchmark_name", "a" * 201),
        ("computer_id", "invalid"),
        ("unknown", 1),
    ],
)
async def test_reject_invalid_measurement_fields(api, computer, field, value):
    payload = {**measurement(computer), field: value}
    assert (await api.post("/v1/benchmark-results", json=payload)).status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999"])
async def test_nonfinite_numbers_are_rejected_over_wire(api, computer, value):
    body = (
        '{"computer_id":"'
        + computer["id"]
        + '","benchmark_name":"single_core","benchmarker_version":"0.0.2","number":'
        + value
        + "}"
    )
    assert (
        await api.post(
            "/v1/benchmark-results", content=body, headers={"Content-Type": "application/json"}
        )
    ).status_code == 422


async def test_patch_only_changes_number_and_validates_it(api, computer):
    row = await create(api, computer)
    path = f"/v1/benchmark-results/{row['id']}"
    for payload in [
        {},
        {"number": None},
        {"number": -1},
        {"number": True},
        {"number": "5"},
        {"number": 2, "benchmarker_version": "1.0.0"},
    ]:
        assert (await api.patch(path, json=payload)).status_code == 422
    assert (await api.get(path)).json() == row


async def test_concurrent_upserts_create_one_row_per_name_and_version(api, computer):
    responses = await asyncio.gather(
        *(
            api.post("/v1/benchmark-results", json=measurement(computer, number=index))
            for index in range(8)
        )
    )
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 1
    assert len((await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json()) == 1


async def test_successful_worker_result_upload_before_session_and_retry(api, client, computer):
    credential = (await api.post(f"/v1/computers/{computer['id']}/credentials")).json()
    headers = {"Authorization": "Bearer " + credential["token"]}
    first = {
        "benchmarker_version": "0.0.2",
        "results": [{"benchmark_name": "single_core", "number": 12.5}],
    }
    path = "/agent/v1/benchmark-results"
    response = await client.put(path, headers=headers, json=first)
    assert response.status_code == 200, response.text
    assert (await client.put(path, headers=headers, json=first)).json() == response.json()
    second = {
        "benchmarker_version": "0.0.2",
        "results": [{"benchmark_name": "multi_core", "number": 50}],
    }
    assert (await client.put(path, headers=headers, json=second)).status_code == 200
    rows = (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json()
    assert {row["benchmark_name"] for row in rows} == {"single_core", "multi_core"}
    assert {row["computer_id"] for row in rows} == {computer["id"]}
    assert (await client.put(path, json=first)).status_code == 401
    assert (await api.put(path, json=first)).status_code == 401
    await api.post(f"/v1/computers/{computer['id']}/credentials")
    assert (await client.put(path, headers=headers, json=first)).status_code == 401


async def test_invalid_worker_payloads_do_not_write_partial_results(api, agent_api, computer):
    valid = {"benchmark_name": "single_core", "number": 5}
    for payload in [
        {"benchmarker_version": "0.0.2", "results": []},
        {"benchmarker_version": "bad", "results": [valid]},
        {"benchmarker_version": "0.0.2", "results": [valid, valid]},
        {
            "benchmarker_version": "0.0.2",
            "results": [valid, {"benchmark_name": "multi_core", "number": -1}],
        },
        {"benchmarker_version": "0.0.2", "results": [valid], "computer_id": str(uuid4())},
    ]:
        assert (await agent_api.put("/agent/v1/benchmark-results", json=payload)).status_code == 422
    assert (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json() == []


async def test_company_resource_permissions_apply_to_benchmarks(
    api, other_api, company, membership, computer
):
    await api.patch(
        f"/v1/computer-ownerships/{computer['ownership']['id']}", json={"company_id": company["id"]}
    )
    row = await create(api, computer)
    path = f"/v1/benchmark-results/{row['id']}"
    assert (
        await api.patch(
            f"/v1/memberships/{membership['id']}", json={"permissions": ["resources.read"]}
        )
    ).status_code == 200
    assert (await other_api.get(path)).status_code == 200
    assert (await other_api.patch(path, json={"number": 3})).status_code == 403
    assert (await other_api.delete(path)).status_code == 403
    assert (
        await other_api.post("/v1/benchmark-results", json=measurement(computer))
    ).status_code == 403
    assert (
        await api.patch(
            f"/v1/memberships/{membership['id']}",
            json={"permissions": ["resources.read", "resources.manage"]},
        )
    ).status_code == 200
    assert (await other_api.patch(path, json={"number": 3})).status_code == 200
    await api.patch(f"/v1/memberships/{membership['id']}", json={"permissions": []})
    assert (await other_api.get(path)).status_code == 403
