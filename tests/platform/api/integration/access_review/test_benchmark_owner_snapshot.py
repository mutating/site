"""Benchmark reads retain their owner boundary across concurrent computer transfers."""

import asyncio

import pytest

from tests.platform.api.token_helpers import move_to_legacy
from throng_api import benchmarks

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("endpoint", ["collection", "computer"])
@pytest.mark.parametrize("latest_only", [False, True])
@pytest.mark.parametrize("historical", [False, True])
async def test_company_benchmark_read_hides_new_owner_measurements(
    api,
    client,
    app,
    company,
    company_token,
    computer,
    monkeypatch,
    endpoint,
    latest_only,
    historical,
):
    """Both list APIs hide B's new measurements when A's computer moves to B, including legacy tokens and complete history."""
    response = await api.post("/v1/companies", json={"name": "Second pool"})
    assert response.status_code == 201, response.text
    other_company = response.json()
    owner_path = f"/v1/computer-ownerships/{computer['ownership']['id']}"
    response = await api.patch(owner_path, json={"company_id": company["id"]})
    assert response.status_code == 200, response.text
    if historical:
        async with app.state.db.write() as session:
            await move_to_legacy(session, company_token["id"])

    authorized, resume = asyncio.Event(), asyncio.Event()
    original = benchmarks.computer_for_user

    async def pause_after_owner_check(session, principal, computer_id, admin=False):
        """Let a real transfer and new benchmark commit after the original owner guard."""
        row = await original(session, principal, computer_id, admin=admin)
        if principal.company_id == company["id"] and not authorized.is_set():
            authorized.set()
            await resume.wait()
        return row

    monkeypatch.setattr(benchmarks, "computer_for_user", pause_after_owner_check)
    path = (
        "/v1/benchmark-results"
        if endpoint == "collection"
        else f"/v1/computers/{computer['id']}/benchmark-results"
    )
    pending = asyncio.create_task(
        client.get(
            path,
            params={"computer_id": computer["id"], "latest_only": latest_only},
            headers={"Authorization": "Bearer " + company_token["token"]},
        )
    )
    try:
        await asyncio.wait_for(authorized.wait(), 3)
        response = await api.patch(owner_path, json={"company_id": other_company["id"]})
        assert response.status_code == 200, response.text
        for version, number in (("1.0.0", 991), ("2.0.0", 992)):
            response = await api.post(
                "/v1/benchmark-results",
                json={
                    "computer_id": computer["id"],
                    "benchmark_name": "single_core",
                    "benchmarker_version": version,
                    "number": number,
                },
            )
            assert response.status_code == 201, response.text
    finally:
        resume.set()
    response = await pending
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("endpoint", ["collection", "computer"])
async def test_registered_owner_keeps_unowned_benchmark_inventory(api, computer, endpoint):
    """The registrant retains empty, latest and complete benchmark inventory after detaching ownership."""
    owner_path = f"/v1/computer-ownerships/{computer['ownership']['id']}"
    response = await api.delete(owner_path)
    assert response.status_code == 204, response.text
    path = (
        "/v1/benchmark-results"
        if endpoint == "collection"
        else f"/v1/computers/{computer['id']}/benchmark-results"
    )
    response = await api.get(path, params={"computer_id": computer["id"]})
    assert response.status_code == 200 and response.json() == []
    for version, number in (("1.0.0", 100), ("2.0.0", 200)):
        response = await api.post(
            "/v1/benchmark-results",
            json={
                "computer_id": computer["id"],
                "benchmark_name": "single_core",
                "benchmarker_version": version,
                "number": number,
            },
        )
        assert response.status_code == 201, response.text
    response = await api.get(path, params={"computer_id": computer["id"]})
    assert response.status_code == 200
    assert [row["number"] for row in response.json()] == [200]
    response = await api.get(path, params={"computer_id": computer["id"], "latest_only": False})
    assert response.status_code == 200
    assert [row["number"] for row in response.json()] == [100, 200]
