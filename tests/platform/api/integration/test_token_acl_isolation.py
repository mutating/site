"""Token registries must apply current organization ACLs in their data queries."""

import asyncio

import pytest
from sqlalchemy import null, select, update

from tests.platform.api.token_helpers import move_to_legacy
from throng_api import accounts, api_tokens
from throng_api.models import Membership, TokenOwnership
from throng_api.permissions import PERMISSIONS, effective_permissions
from throng_api.security import SCOPES, Principal, owned_filter

pytestmark = pytest.mark.anyio


async def issue_registry_token(api, app, owner, name, historical=False):
    """Issue through HTTP, optionally preserving the credential in historical storage."""
    response = await api.post(
        "/v1/api-tokens", json={**owner, "name": name, "scopes": ["read"]}
    )
    assert response.status_code == 201, response.text
    token = response.json()
    if historical:
        async with app.state.db.write() as session:
            await move_to_legacy(session, token["id"])
    return token


@pytest.fixture
def paused_token_registry(monkeypatch, endpoint):
    """Pause a real registry request after authentication, before count and data reads."""
    module = api_tokens if endpoint == "api-tokens" else accounts
    authenticate = module.authenticate
    entered, resume = asyncio.Event(), asyncio.Event()

    async def pause_after_authentication(session, request, scope="read"):
        """Keep production authentication and control only the following scheduling gap."""
        principal = await authenticate(session, request, scope)
        if request.method == "GET" and request.url.path == f"/v1/{endpoint}":
            entered.set()
            await resume.wait()
        return principal

    monkeypatch.setattr(module, "authenticate", pause_after_authentication)
    try:
        yield entered, resume
    finally:
        resume.set()


@pytest.mark.parametrize("endpoint", ["api-tokens", "access-tokens"])
@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize(
    "owner,revoked_permission",
    [("personal", "tokens.read"), ("company", "tokens.read"), ("company", "tokens.manage")],
)
async def test_registry_does_not_read_tokens_created_after_permission_revocation(
    api, other_api, client, app, other_user, company, membership,
    endpoint, historical, owner, revoked_permission, paused_token_registry,
):
    """An in-flight personal/API/legacy read cannot combine old ACLs with newly issued metadata.

    Company readers must also retain their issuer's tokens.manage right in the
    data query, even when the issuer still has tokens.read after revocation.
    The new registry's count must use the same authorization as its rows.
    """
    permissions = {"tokens.read", "tokens.manage"}
    path = f"/v1/memberships/{membership['id']}"
    response = await api.patch(path, json={"permissions": sorted(permissions)})
    assert response.status_code == 200, response.text
    reader = await issue_registry_token(
        other_api, app,
        {"user_id": other_user["id"]} if owner == "personal" else {"company_id": company["id"]},
        "Registry reader", historical,
    )
    headers = {"Authorization": "Bearer " + reader["token"]}
    entered, resume = paused_token_registry
    pending = asyncio.create_task(client.get(f"/v1/{endpoint}", headers=headers))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        response = await api.patch(
            path, json={"permissions": sorted(permissions - {revoked_permission})}
        )
        assert response.status_code == 200, response.text
        await issue_registry_token(
            api, app, {"company_id": company["id"]}, "Issued after permission revocation"
        )
    finally:
        resume.set()
        response = await asyncio.wait_for(pending, timeout=5)
    expected = (
        {reader["id"]}
        if owner == "personal" and (endpoint == "access-tokens" or not historical)
        else set()
    )
    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()} == expected, response.text
    if endpoint == "api-tokens":
        assert response.headers["X-Total-Count"] == str(len(expected))
    if owner == "company":
        subsequent = await client.get(f"/v1/{endpoint}", headers=headers)
        assert subsequent.status_code == 401
        assert subsequent.json()["code"] == "token_invalid"


@pytest.mark.parametrize("endpoint", ["api-tokens", "access-tokens"])
@pytest.mark.parametrize("historical", [False, True])
async def test_registry_filters_current_acl_before_pagination_and_count(
    api, other_api, app, user, other_user, company, membership, endpoint, historical,
):
    """Each page contains only personal or readable-company rows, across both stores.

    A default user may read company tokens without tokens.manage. A custom
    empty ACL excludes that company's rows before pagination and total count;
    another user's personal credentials remain private independently of ACLs.
    """
    denied = await api.post("/v1/companies", json={"name": "Unreadable company"})
    assert denied.status_code == 201, denied.text
    response = await api.post(
        "/v1/memberships",
        json={
            "company_id": denied.json()["id"], "user_id": other_user["id"], "permissions": [],
        },
    )
    assert response.status_code == 201, response.text
    visible = []
    for issuer, owner, allowed in (
        (other_api, {"user_id": other_user["id"]}, True),
        (api, {"company_id": company["id"]}, True),
        (api, {"user_id": user["id"]}, False),
        (api, {"company_id": denied.json()["id"]}, False),
    ):
        for legacy in (False, historical):
            token = await issue_registry_token(issuer, app, owner, "Page entry", legacy)
            if allowed and (endpoint == "access-tokens" or not legacy):
                visible.append(token)
    visible.sort(
        key=(lambda token: (token["created_at"], token["id"]))
        if endpoint == "api-tokens" else (lambda token: token["id"]),
        reverse=endpoint == "api-tokens",
    )
    for offset in range(len(visible) + 1):
        response = await other_api.get(f"/v1/{endpoint}", params={"limit": 1, "offset": offset})
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [
            token["id"] for token in visible[offset : offset + 1]
        ]
        if endpoint == "api-tokens":
            assert response.headers["X-Total-Count"] == str(len(visible))


async def test_owned_filter_matches_effective_permissions_for_all_acl_representations(
    api, app, other_user, company, membership,
):
    """SQL authorization matches Python for every permission, role and default/custom ACL.

    SQL NULL and JSON null both mean role defaults; an empty custom list means
    no rights except for creators, whose permissions always remain complete.
    An unclassified internal request still uses ordinary membership visibility.
    """
    response = await api.post("/v1/token-ownerships", json={"company_id": company["id"]})
    assert response.status_code == 201, response.text
    ownership_id = response.json()["id"]
    variants = [
        (None, False), (None, True), ([], False),
        (["tokens.read"], False), (["tokens.manage"], False), (sorted(PERMISSIONS), False),
    ]
    async with app.state.db.write() as session:
        for role in ("creator", "admin", "user"):
            for custom, sql_null in variants:
                await session.execute(
                    update(Membership)
                    .where(Membership.id == membership["id"])
                    .values(role=role, permissions=null() if sql_null else custom)
                )
                for permission in (*sorted(PERMISSIONS), None):
                    principal = Principal(
                        other_user["id"], None, other_user["id"], SCOPES, permission
                    )
                    actual = await session.scalar(
                        select(TokenOwnership.id).where(
                            TokenOwnership.id == ownership_id, owned_filter(TokenOwnership, principal)
                        )
                    )
                    allowed = permission is None or permission in effective_permissions(role, custom)
                    assert (actual is not None) == allowed, (role, custom, sql_null, permission)
