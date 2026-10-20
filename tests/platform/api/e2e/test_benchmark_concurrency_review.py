"""Concurrent suite uploads remain atomic and isolated from other writers and workers."""

import asyncio

import pytest

pytestmark = pytest.mark.anyio


def suite(version, single, multi):
    """Build a complete single-core and multi-core benchmark upload."""
    return {
        "benchmarker_version": version,
        "results": [
            {"benchmark_name": "single_core", "number": single},
            {"benchmark_name": "multi_core", "number": multi},
        ],
    }


async def owner_result(api, computer, *, name="single_core", version="2.3.0", number=0):
    """Create an owner-managed benchmark measurement for concurrency checks."""
    response = await api.post(
        "/v1/benchmark-results",
        json={
            "computer_id": computer["id"],
            "benchmark_name": name,
            "benchmarker_version": version,
            "number": number,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def listed(api, computer, *, latest_only=True):
    """Read the computer's benchmark results with the requested version filter."""
    response = await api.get(
        f"/v1/computers/{computer['id']}/benchmark-results",
        params={"latest_only": latest_only},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_competing_complete_suites_commit_one_coherent_pair(api, agent_api, computer):
    """A final suite cannot combine measurements from separate concurrent uploads."""
    historical = await owner_result(api, computer, version="2.2.0", number=999)
    manual = await owner_result(api, computer, name="manual_calibration", number=77)
    payloads = [suite("2.3.0", index, index + 100) for index in range(4)]
    responses = await asyncio.gather(
        *(agent_api.put("/agent/v1/benchmark-results", json=payload) for payload in payloads)
    )
    for response, payload in zip(responses, payloads, strict=True):
        assert response.status_code == 200, response.text
        assert [
            {"benchmark_name": row["benchmark_name"], "number": row["number"]}
            for row in response.json()
        ] == payload["results"]
    for name in ("single_core", "multi_core"):
        assert (
            len(
                {
                    row["id"]
                    for response in responses
                    for row in response.json()
                    if row["benchmark_name"] == name
                }
            )
            == 1
        )

    rows = {row["benchmark_name"]: row for row in await listed(api, computer)}
    assert set(rows) == {"single_core", "multi_core", "manual_calibration"}
    assert (rows["single_core"]["number"], rows["multi_core"]["number"]) in {
        (index, index + 100) for index in range(4)
    }
    assert rows["manual_calibration"] == manual
    all_rows = await listed(api, computer, latest_only=False)
    assert len(all_rows) == 4
    assert historical in all_rows


@pytest.mark.parametrize("owner_method", ["post", "patch"])
async def test_agent_and_owner_updates_share_identity_without_losing_other_results(
    api, agent_api, computer, owner_method
):
    """Concurrent worker and owner updates retain the existing row UUID and preserve unrelated measurements in the same version."""
    original = await owner_result(api, computer)
    retained = await owner_result(api, computer, name="manual_calibration", number=77)
    if owner_method == "post":
        owner_write = api.post(
            "/v1/benchmark-results",
            json={
                "computer_id": computer["id"],
                "benchmark_name": "single_core",
                "benchmarker_version": "2.3.0",
                "number": 40,
            },
        )
    else:
        owner_write = api.patch(f"/v1/benchmark-results/{original['id']}", json={"number": 40})
    uploaded, updated = await asyncio.gather(
        agent_api.put("/agent/v1/benchmark-results", json=suite("2.3.0", 20, 30)),
        owner_write,
    )
    assert uploaded.status_code == 200, uploaded.text
    assert updated.status_code == (201 if owner_method == "post" else 200), updated.text
    assert uploaded.json()[0]["id"] == updated.json()["id"] == original["id"]
    assert updated.json()["number"] == 40

    rows = {row["benchmark_name"]: row for row in await listed(api, computer)}
    assert set(rows) == {"single_core", "multi_core", "manual_calibration"}
    assert rows["single_core"]["id"] == original["id"]
    assert rows["single_core"]["number"] in {20, 40}
    assert rows["multi_core"] == uploaded.json()[1]
    assert rows["manual_calibration"] == retained


async def test_delete_racing_upload_is_serializable_and_rerun_restores_missing_measurement(
    api, agent_api, computer
):
    """A delete racing an upload has a valid serial outcome; a later replay restores a deleted measurement without changing unaffected row identities."""
    original = await owner_result(api, computer)
    path = f"/v1/benchmark-results/{original['id']}"
    payload = suite("2.3.0", 10, 20)
    deleted, uploaded = await asyncio.gather(
        api.delete(path), agent_api.put("/agent/v1/benchmark-results", json=payload)
    )
    assert deleted.status_code == 204, deleted.text
    assert uploaded.status_code == 200, uploaded.text
    single, multi = uploaded.json()
    assert (await api.get(path)).status_code == 404
    current = {row["benchmark_name"]: row for row in await listed(api, computer)}
    assert current["multi_core"] == multi
    if single["id"] == original["id"]:
        assert set(current) == {"multi_core"}
    else:
        assert current == {"single_core": single, "multi_core": multi}

    retry = await agent_api.put("/agent/v1/benchmark-results", json=payload)
    assert retry.status_code == 200, retry.text
    restored = {row["benchmark_name"]: row for row in retry.json()}
    assert restored["single_core"]["id"] != original["id"]
    assert restored["multi_core"] == multi
    assert {row["benchmark_name"]: row for row in await listed(api, computer)} == restored
    assert (await agent_api.put("/agent/v1/benchmark-results", json=payload)).json() == retry.json()


async def test_concurrent_suites_on_two_computers_keep_versions_scores_and_ids_isolated(
    api, client, computer
):
    """Concurrent uploads preserve separate histories and identities for each computer, with latest listings selecting only its highest SemVer."""
    response = await api.post(
        "/v1/computers",
        json={
            "name": "second worker",
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
        },
    )
    assert response.status_code == 201, response.text
    computers = [computer, response.json()]
    credentials = await asyncio.gather(
        *(api.post(f"/v1/computers/{worker['id']}/credentials") for worker in computers)
    )
    for credential in credentials:
        assert credential.status_code == 201, credential.text

    # Highest SemVer is submitted first, followed by lower versions whose names overlap.
    versions = ["10.0.0", "2.0.0", "1.10.0", "1.9.0", "2.0.0-rc.10"]
    expected = {}
    uploads = []
    for worker_index, (worker, credential) in enumerate(zip(computers, credentials, strict=True)):
        headers = {"Authorization": "Bearer " + credential.json()["token"]}
        for version_index, version in enumerate(versions):
            score = worker_index * 1000 + version_index * 10
            expected[worker["id"], version] = (score, score + 1)
            uploads.append(
                client.put(
                    "/agent/v1/benchmark-results",
                    headers=headers,
                    json=suite(version, score, score + 1),
                )
            )
    responses = await asyncio.gather(*uploads)
    for response in responses:
        assert response.status_code == 200, response.text
    all_ids = [row["id"] for response in responses for row in response.json()]
    assert len(set(all_ids)) == len(computers) * len(versions) * 2

    ordered_versions = ["1.9.0", "1.10.0", "2.0.0-rc.10", "2.0.0", "10.0.0"]
    for worker in computers:
        rows = await listed(api, worker, latest_only=False)
        assert len(rows) == len(versions) * 2
        assert {row["computer_id"] for row in rows} == {worker["id"]}
        assert [(row["benchmarker_version"], row["benchmark_name"]) for row in rows] == [
            (version, name)
            for version in ordered_versions
            for name in ("multi_core", "single_core")
        ]
        for version in versions:
            scores = {
                row["benchmark_name"]: row["number"]
                for row in rows
                if row["benchmarker_version"] == version
            }
            assert (scores["single_core"], scores["multi_core"]) == expected[worker["id"], version]
        assert await listed(api, worker) == [
            row for row in rows if row["benchmarker_version"] == "10.0.0"
        ]
