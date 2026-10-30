"""Individual run history, transport retries and conflicting uploads over HTTP/SQL."""

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


def suite(run_id=None, *, version="0.0.2", single=10, multi=20):
    """Build a complete upload with an optional stable run identifier."""
    return {
        "benchmarker_version": version,
        "run_id": run_id,
        "results": [
            {"benchmark_name": "single_core", "number": single},
            {"benchmark_name": "multi_core", "number": multi},
        ],
    }


async def history(api, computer):
    """Read every persisted run through the owner's HTTP endpoint."""
    response = await api.get(
        f"/v1/computers/{computer['id']}/benchmark-results", params={"latest_only": False}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def submit(api, agent_api, computer, channel, run_id, *, version="0.0.2", number=10):
    """Submit one measurement through the owner or agent HTTP contract."""
    identity = {"benchmarker_version": version, "run_id": run_id}
    measurement = {"benchmark_name": "single_core", "number": number}
    if channel == "owner":
        return await api.post(
            "/v1/benchmark-results",
            json={**identity, **measurement, "computer_id": computer["id"]},
        )
    return await agent_api.put(
        "/agent/v1/benchmark-results",
        json={**identity, "results": [measurement]},
    )


@pytest.mark.parametrize("channel", ["owner", "agent"])
async def test_late_retry_preserves_the_original_date_and_newer_latest_result(
    api, agent_api, computer, channel
):
    """Replaying an old run cannot replace a newer run or move the old run's date."""
    old_run_id, new_run_id = str(uuid4()), str(uuid4())
    first = await submit(api, agent_api, computer, channel, old_run_id, number=10)
    second = await submit(api, agent_api, computer, channel, new_run_id, number=20)
    retry = await submit(api, agent_api, computer, channel, old_run_id, number=10)
    assert (
        first.status_code
        == second.status_code
        == retry.status_code
        == (201 if channel == "owner" else 200)
    )
    assert retry.json() == first.json()
    rows = await history(api, computer)
    assert [(row["run_id"], row["number"]) for row in rows] == [(old_run_id, 10), (new_run_id, 20)]
    latest = (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json()
    assert latest == ([second.json()] if channel == "owner" else second.json())


@pytest.mark.parametrize("channel", ["owner", "agent"])
@pytest.mark.parametrize("run_id", ["invalid", "", 42, {}])
async def test_invalid_run_ids_never_create_history(api, agent_api, computer, channel, run_id):
    """Invalid identifiers are rejected by both upload contracts without storing a run."""
    response = await submit(api, agent_api, computer, channel, run_id)
    assert response.status_code == 422, response.text
    assert await history(api, computer) == []


@pytest.mark.parametrize("field", ["number", "version"])
async def test_owner_conflict_preserves_the_original_run(api, agent_api, computer, field):
    """Owner POST cannot silently change a run through a reused identifier."""
    run_id = str(uuid4())
    first = await submit(api, agent_api, computer, "owner", run_id)
    assert first.status_code == 201
    response = await submit(
        api,
        agent_api,
        computer,
        "owner",
        run_id,
        number=99 if field == "number" else 10,
        version="0.0.3" if field == "version" else "0.0.2",
    )
    assert response.status_code == 409, response.text
    assert await history(api, computer) == [first.json()]


async def test_conflicting_owner_and_agent_uploads_commit_only_one_run(api, agent_api, computer):
    """Simultaneous incompatible writes to one run cannot overwrite or partly extend it."""
    run_id = str(uuid4())
    owner, agent = await asyncio.gather(
        submit(api, agent_api, computer, "owner", run_id, number=99),
        agent_api.put("/agent/v1/benchmark-results", json=suite(run_id)),
    )
    assert (owner.status_code, agent.status_code) in {(201, 409), (409, 200)}
    expected = [owner.json()] if owner.status_code == 201 else agent.json()
    assert {row["id"] for row in await history(api, computer)} == {row["id"] for row in expected}
    replay = await (
        submit(api, agent_api, computer, "owner", run_id, number=99)
        if owner.status_code == 201
        else agent_api.put("/agent/v1/benchmark-results", json=suite(run_id))
    )
    assert replay.json() == (owner.json() if owner.status_code == 201 else agent.json())


async def test_identical_legacy_uploads_and_new_run_ids_all_preserve_history(
    api, agent_api, computer
):
    """Even identical scores create separate history unless a run ID is replayed."""
    uploads = []
    payloads = [suite(), suite(), suite(str(uuid4())), suite(str(uuid4()))]
    del payloads[0]["run_id"]
    for payload in payloads:
        response = await agent_api.put("/agent/v1/benchmark-results", json=payload)
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len({row["run_id"] for row in rows}) == 1
        assert len({row["created_at"] for row in rows}) == 1
        assert datetime.fromisoformat(rows[0]["created_at"]).tzinfo is not None
        uploads.append(rows)
    assert len({rows[0]["run_id"] for rows in uploads}) == 4
    assert {row["id"] for row in await history(api, computer)} == {
        row["id"] for rows in uploads for row in rows
    }
    latest = (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json()
    assert {row["id"] for row in latest} == {row["id"] for row in uploads[-1]}


async def test_identical_owner_uploads_without_ids_create_separate_runs(api, computer):
    """Omitting or nulling run_id in owner POST never deduplicates identical scores."""
    payload = {
        "computer_id": computer["id"],
        "benchmark_name": "single_core",
        "benchmarker_version": "0.0.2",
        "number": 10,
    }
    first = await api.post("/v1/benchmark-results", json=payload)
    second = await api.post("/v1/benchmark-results", json={**payload, "run_id": None})
    assert first.status_code == second.status_code == 201
    assert first.json()["run_id"] != second.json()["run_id"]
    rows = await history(api, computer)
    assert {row["id"] for row in rows} == {first.json()["id"], second.json()["id"]}
    assert [row["number"] for row in rows] == [10, 10]


async def test_concurrent_transport_replays_do_not_create_runs(api, agent_api, computer):
    """Concurrent retries of one run converge on its original results."""
    payload = suite(str(uuid4()))
    responses = await asyncio.gather(
        *(agent_api.put("/agent/v1/benchmark-results", json=payload) for _ in range(6))
    )
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json() == responses[0].json() for response in responses)
    assert len(await history(api, computer)) == 2


@pytest.mark.parametrize("conflict", ["number", "version"])
async def test_conflicting_run_replay_is_atomic(api, agent_api, computer, conflict):
    """A conflicting replay rolls back all additions and preserves previous results."""
    run_id = str(uuid4())
    first = await agent_api.put("/agent/v1/benchmark-results", json=suite(run_id))
    assert first.status_code == 200
    payload = suite(run_id, version="0.0.3" if conflict == "version" else "0.0.2")
    payload["results"].insert(0, {"benchmark_name": "new_metric", "number": 99})
    if conflict == "number":
        payload["results"][-1]["number"] = 100
    rejected = await agent_api.put("/agent/v1/benchmark-results", json=payload)
    assert rejected.status_code == 409, rejected.text
    rows = await history(api, computer)
    assert {row["id"] for row in rows} == {row["id"] for row in first.json()}
    assert {row["number"] for row in rows} == {10, 20}


async def test_owner_retries_and_incremental_agent_results_share_run_date(api, agent_api, computer):
    """Owner retries and incremental uploads preserve one run's original timestamp."""
    run_id = str(uuid4())
    payload = {
        "computer_id": computer["id"],
        "benchmark_name": "single_core",
        "benchmarker_version": "0.0.2",
        "number": 10,
        "run_id": run_id,
    }
    first = await api.post("/v1/benchmark-results", json=payload)
    retry = await api.post("/v1/benchmark-results", json=payload)
    assert first.status_code == retry.status_code == 201
    assert first.json() == retry.json()
    uploaded = await agent_api.put("/agent/v1/benchmark-results", json=suite(run_id))
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()[0] == first.json()
    assert uploaded.json()[1]["created_at"] == first.json()["created_at"]
    assert len(await history(api, computer)) == 2


async def test_run_ids_are_scoped_to_computer(api, other_api, computer):
    """Reusing a run UUID on another computer neither exposes nor changes its results."""
    response = await other_api.post(
        "/v1/computers",
        json={
            "name": "another owner",
            "os": "linux",
            "architecture": "arm64",
            "runtime": "oci",
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 201, response.text
    other = response.json()
    run_id = str(uuid4())
    ids = set()
    for owner, worker, number in [(api, computer, 10), (other_api, other, 20)]:
        result = await owner.post(
            "/v1/benchmark-results",
            json={
                "computer_id": worker["id"],
                "benchmark_name": "single_core",
                "benchmarker_version": "0.0.2",
                "number": number,
                "run_id": run_id,
            },
        )
        assert result.status_code == 201, result.text
        ids.add(result.json()["id"])
    assert len(ids) == 2
    assert len(await history(api, computer)) == 1
    assert (
        await other_api.get(f"/v1/computers/{computer['id']}/benchmark-results")
    ).status_code == 404
