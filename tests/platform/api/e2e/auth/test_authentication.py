"""Authentication is enforced by live HTTP handlers, beyond their OpenAPI declarations."""

from datetime import timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_authentication_matrix_covers_all_protected_operations(
    client, api_requests, agent_requests
):
    """Adding any route or method requires an explicit public/protected classification and HTTP case."""
    schema = (await client.get("/openapi.json")).json()
    public = {
        ("GET", "/health/live"),
        ("GET", "/api/v1/agent-capabilities"),
        ("GET", "/health/ready"),
        ("POST", "/api/v1/users"),
        ("POST", "/api/v1/auth/login"),
    }
    actual = {
        (method.upper(), path) for path, methods in schema["paths"].items() for method in methods
    }
    cases = api_requests + agent_requests
    covered = {(case["method"], case["template"]) for case in cases}
    assert len(covered) == len(cases), "Duplicate HTTP cases hide gaps in the route inventory"
    assert covered.isdisjoint(public)
    # ACME uses its own JWS/account/nonce authentication, exercised end to end in powercell/.
    acme = {
        (method, "/acme/powercell/{enrollment_id}/{directory_secret}/{operation}")
        for method in ["GET", "HEAD", "POST"]
    }
    assert actual == covered | public | acme


@pytest.mark.parametrize("settings", [{"agent_lease_seconds": 3600}], indirect=True)
@pytest.mark.parametrize("access_token", [{"expires_in_seconds": 60}], indirect=True)
@pytest.mark.parametrize(
    "mode", ["missing", "basic", "empty", "unknown", "expired", "revoked", "agent_credential"]
)
async def test_every_client_operation_rejects_invalid_authentication(
    client, api, api_requests, access_token, agent, clock, mode, running_command, artifact
):
    """Every client handler rejects absent, malformed, unknown, expired, revoked and worker tokens."""
    authorization = {
        "missing": None,
        "basic": "Basic abc",
        "empty": "Bearer",
        "unknown": "Bearer thr_unknown",
        "expired": "Bearer " + access_token["token"],
        "revoked": "Bearer " + access_token["token"],
        "agent_credential": agent["headers"]["Authorization"],
    }[mode]
    if mode == "expired":
        clock.now += timedelta(seconds=60)
    elif mode == "revoked":
        response = await api.patch(
            f"/api/v1/access-tokens/{access_token['id']}", json={"revoke": True}
        )
        assert response.status_code == 200, response.text

    for case in api_requests:
        kwargs = {**case["kwargs"], "headers": {**case["kwargs"]["headers"]}}
        if authorization is not None:
            kwargs["headers"]["Authorization"] = authorization
        response = await client.request(case["method"], case["url"], **kwargs)
        assert response.status_code == 401, (mode, case["method"], case["template"], response.text)
        if mode in {"missing", "basic", "empty"}:
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.json() == {"detail": "Bearer token required"}
        else:
            assert response.json() == {"detail": "Invalid or expired token"}

    response = await api.get(f"/api/v1/commands/{running_command['command']['id']}")
    assert response.status_code == 200 and response.json()["status"] == "running"
    response = await api.get(f"/api/v1/artifacts/{artifact['id']}/content")
    assert response.status_code == 200 and response.content == b"initial state"


@pytest.mark.parametrize(
    "access_token,missing_scope",
    [
        ({"scopes": ["manage", "execute"]}, "read"),
        ({"scopes": ["read", "execute"]}, "manage"),
        ({"scopes": ["read", "manage"]}, "execute"),
    ],
    indirect=["access_token"],
)
async def test_every_client_operation_requires_its_scope(
    client, api, api_requests, access_token, missing_scope, running_command
):
    """Each client operation denies an otherwise valid owner token lacking exactly its required scope."""
    authorization = "Bearer " + access_token["token"]
    cases = [case for case in api_requests if case["scope"] == missing_scope]
    assert cases
    for case in cases:
        kwargs = {
            **case["kwargs"],
            "headers": {**case["kwargs"]["headers"], "Authorization": authorization},
        }
        response = await client.request(case["method"], case["url"], **kwargs)
        assert response.status_code == 403, (
            missing_scope,
            case["method"],
            case["template"],
            response.text,
        )
        assert response.json() == {"detail": "Token lacks required scope"}

    response = await api.get(f"/api/v1/commands/{running_command['command']['id']}")
    assert response.status_code == 200 and response.json()["status"] == "running"


@pytest.mark.parametrize("mode", ["missing", "basic", "empty", "unknown", "user_token", "rotated"])
async def test_every_agent_operation_requires_a_current_agent_credential(
    client, api, agent_requests, agent, user, mode
):
    """All worker operations reject absent, malformed, unknown, rotated and user credentials."""
    authorization = {
        "missing": None,
        "basic": "Basic abc",
        "empty": "Bearer",
        "unknown": "Bearer agt_unknown",
        "user_token": user["headers"]["Authorization"],
        "rotated": agent["headers"]["Authorization"],
    }[mode]
    if mode == "rotated":
        response = await api.post(f"/api/v1/computers/{agent['computer']['id']}/credentials")
        assert response.status_code == 201, response.text

    for case in agent_requests:
        kwargs = {
            **case["kwargs"],
            "headers": {
                **case["kwargs"]["headers"],
                "X-Agent-Session": agent["session"]["session_id"],
            },
        }
        if authorization is not None:
            kwargs["headers"]["Authorization"] = authorization
        response = await client.request(case["method"], case["url"], **kwargs)
        assert response.status_code == 401, (mode, case["method"], case["template"], response.text)
        if mode in {"missing", "basic", "empty"}:
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.json() == {"detail": "Bearer token required"}
        else:
            assert response.json() == {"detail": "Invalid agent credential"}


@pytest.mark.parametrize("mode", ["missing", "unknown", "expired", "superseded"])
async def test_every_session_bound_agent_operation_requires_a_live_session(
    client, agent_api, agent_requests, agent, settings, clock, mode
):
    """A valid worker credential cannot access session-bound operations without the current live session."""
    session_id = agent["session"]["session_id"]
    if mode == "missing":
        session_id = None
    elif mode == "unknown":
        session_id = str(uuid4())
    elif mode == "expired":
        clock.now += timedelta(seconds=settings.agent_lease_seconds)
    elif mode == "superseded":
        response = await agent_api.post(
            "/agent/v1/session",
            headers={"Idempotency-Key": str(uuid4())},
            json={"clean_start": True, "previous_session_id": session_id},
        )
        assert response.status_code == 200, response.text
        assert response.json()["session_id"] != session_id

    for case in agent_requests:
        if case["template"] in {
            "/agent/v1/session",
            "/agent/v1/unbind",
            "/agent/v1/diagnostics/{diagnostic_id}",
        }:
            continue
        kwargs = {
            **case["kwargs"],
            "headers": {
                **case["kwargs"]["headers"],
                "Authorization": agent["headers"]["Authorization"],
            },
        }
        if session_id is not None:
            kwargs["headers"]["X-Agent-Session"] = session_id
        response = await client.request(case["method"], case["url"], **kwargs)
        assert response.status_code == 409, (mode, case["method"], case["template"], response.text)
        assert response.json() == {"detail": "Agent session expired; a clean start is required"}
