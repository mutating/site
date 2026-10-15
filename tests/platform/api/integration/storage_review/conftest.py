"""Authenticated collections used to exercise shared SQL pagination boundaries."""

import pytest


@pytest.fixture
def paginated_collections(api, admin_api, company, computer):
    """Provide every offset-based collection with the credentials and parent IDs it requires."""
    return [
        (api, "/v1/companies", {}),
        (api, "/v1/memberships", {"company_id": company["id"]}),
        (api, "/v1/computers", {}),
        (api, "/v1/computer-ownerships", {}),
        (api, "/v1/token-ownerships", {}),
        (api, "/v1/access-tokens", {}),
        (api, "/v1/artifacts", {}),
        (api, "/v1/isolates", {}),
        (admin_api, "/v1/resource-types", {}),
        (api, f"/v1/computers/{computer['id']}/resources", {}),
    ]
