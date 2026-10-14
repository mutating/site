"""Authenticated collections used to exercise shared SQL pagination boundaries."""

import pytest


@pytest.fixture
def paginated_collections(api, admin_api, company, computer):
    """Provide every offset-based collection with the credentials and parent IDs it requires."""
    return [
        (api, "/api/v1/companies", {}),
        (api, "/api/v1/memberships", {"company_id": company["id"]}),
        (api, "/api/v1/computers", {}),
        (api, "/api/v1/computer-ownerships", {}),
        (api, "/api/v1/token-ownerships", {}),
        (api, "/api/v1/access-tokens", {}),
        (api, "/api/v1/artifacts", {}),
        (api, "/api/v1/isolates", {}),
        (admin_api, "/api/v1/resource-types", {}),
        (api, f"/api/v1/computers/{computer['id']}/resources", {}),
    ]
