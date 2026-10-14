"""HTTP-created inventory and report intervals for resource scenarios."""

from datetime import timedelta

import pytest


@pytest.fixture
def history_range(clock):
    """Include initial inventory and subsequent changes in a bounded UTC interval."""
    return {
        "from": (clock.now - timedelta(hours=1)).isoformat(),
        "to": (clock.now + timedelta(hours=1)).isoformat(),
    }


@pytest.fixture
def history_report_paths(user, company, computer_resources):
    """Expose the three authorized history scopes for shared interval-validation checks."""
    return [
        f"/api/v1/users/{user['id']}/resources/history",
        f"/api/v1/companies/{company['id']}/resources/history",
        f"/api/v1/computer-resources/{computer_resources['cpu']['id']}/history",
    ]


@pytest.fixture
async def gpu_resource(api, computer, resource_type):
    """Attach a custom accelerator resource to Alice's personal computer."""
    response = await api.post(
        f"/api/v1/computers/{computer['id']}/resources",
        json={"resource_type_id": resource_type["id"], "total": 4},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def company_computer(api, company):
    """Register a company-owned computer independently of personal inventory."""
    response = await api.post(
        "/api/v1/computers",
        json={
            "company_id": company["id"],
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 3000,
            "memory_bytes": 4096,
            "disk_bytes": 8192,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def company_resources(api, company_computer):
    """Expose the company computer's automatic CPU and memory records."""
    response = await api.get(f"/api/v1/computers/{company_computer['id']}/resources")
    assert response.status_code == 200, response.text
    return {row["resource_type"]["code"]: row for row in response.json()}


@pytest.fixture
async def administrator_company_token(admin_api):
    """Issue a corporate token whose issuer also holds system administrator privileges."""
    response = await admin_api.post("/api/v1/companies", json={"name": "Operator company"})
    assert response.status_code == 201, response.text
    company = response.json()
    response = await admin_api.post("/api/v1/token-ownerships", json={"company_id": company["id"]})
    assert response.status_code == 201, response.text
    response = await admin_api.post(
        "/api/v1/access-tokens",
        json={
            "ownership_id": response.json()["id"],
            "name": "Operator company",
            "scopes": ["read", "manage"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def disabled_pending_isolate(api, computer, agent_api, isolate_payload):
    """Queue an isolate while its enrolled worker is disabled, leaving capacity safely unreserved."""
    response = await api.patch(f"/api/v1/computers/{computer['id']}", json={"enabled": False})
    assert response.status_code == 200, response.text
    response = await api.post(
        "/api/v1/isolates", headers={"Idempotency-Key": "disabled-resource"}, json=isolate_payload
    )
    assert response.status_code == 202, response.text
    return response.json()
