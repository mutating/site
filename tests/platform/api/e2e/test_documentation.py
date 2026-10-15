"""Public documentation reflects the actual credentials, headers and file formats."""

import pytest

pytestmark = pytest.mark.anyio


async def test_docs_and_redoc_are_public_and_link_to_the_live_schema(client):
    """Both documentation UIs and their OpenAPI schema are available without authentication."""
    for path in ["/docs", "/redoc"]:
        response = await client.get(path)
        assert response.status_code == 200
        assert "/openapi.json" in response.text
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Throng API"


async def test_public_api_uses_v1_without_an_api_prefix(client):
    """Publish user routes at /v1 and reject the retired /api/v1 prefix."""
    schema = (await client.get("/openapi.json")).json()
    assert "/v1/users" in schema["paths"]
    assert not any(path.startswith("/api/") for path in schema["paths"])
    assert (await client.get("/v1/agent-capabilities")).status_code == 200
    assert (await client.get("/api/v1/agent-capabilities")).status_code == 404


async def test_openapi_security_matches_public_and_protected_operations(client):
    """Public operations require no token, and user and worker operations declare different credentials."""
    schema = (await client.get("/openapi.json")).json()
    public = {
        ("/v1/users", "post"),
        ("/v1/auth/login", "post"),
        ("/v1/auth/session", "post"),
        ("/v1/auth/session", "delete"),
        ("/health/live", "get"),
        ("/v1/agent-capabilities", "get"),
        ("/health/ready", "get"),
    }
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if (path, method) in public or path.startswith("/acme/"):
                assert not operation.get("security"), (path, method)
            else:
                expected = (
                    [{"AgentCredential": []}]
                    if path.startswith("/agent/")
                    else [{"AccessToken": []}, {"BrowserSession": []}]
                )
                assert operation["security"] == expected, (path, method)
            headers = {
                item["name"]: item
                for item in operation.get("parameters", [])
                if item["in"] == "header"
            }
            if path.startswith("/agent/") and path not in {
                "/agent/v1/session",
                "/agent/v1/unbind",
                "/agent/v1/diagnostics/{diagnostic_id}",
                "/agent/v1/benchmark-results",
            }:
                assert headers["X-Agent-Session"]["required"] is True
                assert headers["X-Agent-Session"]["schema"]["format"] == "uuid"
            else:
                assert "X-Agent-Session" not in headers
    for scheme in ["AccessToken", "AgentCredential"]:
        assert schema["components"]["securitySchemes"][scheme]["type"] == "http"
        assert schema["components"]["securitySchemes"][scheme]["scheme"] == "bearer"
    assert schema["components"]["securitySchemes"]["BrowserSession"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "tmc_session",
        "description": "Host-only HttpOnly cookie issued by POST /v1/auth/session.",
    }


async def test_openapi_includes_every_required_crud_and_execution_operation(client):
    """The published contract exposes account/inventory CRUD, the resource catalog and batch execution endpoints."""
    paths = (await client.get("/openapi.json")).json()["paths"]
    resources = {
        "users": "user_id",
        "companies": "company_id",
        "memberships": "membership_id",
        "computers": "computer_id",
        "computer-ownerships": "ownership_id",
        "access-tokens": "token_id",
        "token-ownerships": "ownership_id",
        "resource-types": "type_id",
        "benchmark-results": "result_id",
    }
    for resource, identifier in resources.items():
        assert {"get", "post"} <= paths[f"/v1/{resource}"].keys()
        assert {"get", "patch", "delete"} <= paths[f"/v1/{resource}/{{{identifier}}}"].keys()
    for path in [
        "/v1/isolates",
        "/v1/isolates/{isolate_id}/commands",
        "/v1/commands/statuses",
        "/v1/commands/results",
        "/v1/commands/cancel",
    ]:
        assert "post" in paths[path]


async def test_resource_contract_describes_inventory_reports_and_server_managed_reservations(
    client,
):
    """OpenAPI publishes all resource/report routes and separates writable capacity from server-owned occupancy and privilege fields."""
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]
    assert {"get", "post"} <= paths["/v1/computers/{computer_id}/resources"].keys()
    assert {"get", "patch", "delete"} <= paths["/v1/computer-resources/{resource_id}"].keys()
    for owner, identifier in [("users", "user_id"), ("companies", "company_id")]:
        assert "get" in paths[f"/v1/{owner}/{{{identifier}}}/resources/summary"]
        operation = paths[f"/v1/{owner}/{{{identifier}}}/resources/history"]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}
        assert parameters["from"]["required"] and parameters["to"]["required"]
        assert {
            "snapshot_revision",
            "after_revision",
            "resource_type_id",
            "limit",
        } <= parameters.keys()
    assert "get" in paths["/v1/computer-resources/{resource_id}/history"]
    models = schema["components"]["schemas"]
    assert set(models["ComputerResourceUpdate"]["properties"]) == {"total", "expected_version"}
    assert {"total", "occupied", "free", "resource_type", "version"} <= models["ComputerResource"][
        "properties"
    ].keys()
    assert "is_system_admin" not in models["UserCreate"]["properties"]
    assert "is_system_admin" not in models["UserUpdate"]["properties"]
    assert "system-admin" in schema["info"]["description"]


async def test_documented_binary_uploads_match_real_file_transfers(
    client, api, agent_api, artifact, run_task
):
    """Swagger describes raw binary uploads and downloads, matching the bytes transferred by both APIs."""
    paths = (await client.get("/openapi.json")).json()["paths"]
    for path, method in [
        ("/v1/artifacts", "post"),
        ("/agent/v1/tasks/{task_id}/artifacts/{purpose}", "put"),
    ]:
        content = paths[path][method]["requestBody"]["content"]
        assert set(content) == {"application/octet-stream"}
        assert content["application/octet-stream"]["schema"] == {
            "type": "string",
            "format": "binary",
        }
    for path in [
        "/v1/artifacts/{artifact_id}/content",
        "/agent/v1/artifacts/{artifact_id}/content",
    ]:
        assert set(paths[path]["get"]["responses"]["200"]["content"]) == {
            "application/octet-stream"
        }
    response = await agent_api.put(
        f"/agent/v1/tasks/{run_task['id']}/artifacts/stdout",
        headers={"Content-Type": "application/octet-stream"},
        content=b"binary\x00output",
    )
    assert response.status_code == 200
    response = await api.get(f"/v1/artifacts/{response.json()['id']}/content")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == b"binary\x00output"
    response = await agent_api.get(f"/agent/v1/artifacts/{artifact['id']}/content")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == b"initial state"
