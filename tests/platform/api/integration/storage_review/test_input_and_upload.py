"""Real HTTP regressions for text encoding, SQL pagination and upload deadline guards."""

import asyncio
import hashlib
import json
from datetime import timedelta

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "invalid_text", ["\ud800", "\udfff", "\x00"], ids=["high-surrogate", "low-surrogate", "nul"]
)
async def test_company_rejects_unstorable_text_with_a_serializable_validation_error(
    api, invalid_text
):
    """Invalid Unicode and PostgreSQL-incompatible NUL return HTTP 422, including a safely encoded error response."""
    response = await api.post(
        "/v1/companies",
        content=json.dumps({"name": "invalid " + invalid_text}, ensure_ascii=True),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)
    assert (await api.get("/v1/companies")).json() == []


@pytest.mark.parametrize(
    "invalid_text", ["\ud800", "\udfff", "\x00"], ids=["high-surrogate", "low-surrogate", "nul"]
)
@pytest.mark.parametrize("field", ["command", "env-key", "env-value"])
async def test_command_and_nested_environment_reject_unstorable_text(
    api, agent_api, isolate, invalid_text, field
):
    """Command text and nested environment keys or values reject invalid text before any worker task is queued."""
    payload = {"command": "pytest", "env": {"MODE": "test"}}
    if field == "command":
        payload["command"] += invalid_text
    elif field == "env-key":
        payload["env"] = {"MODE" + invalid_text: "test"}
    else:
        payload["env"] = {"MODE": "test" + invalid_text}
    response = await api.post(
        f"/v1/isolates/{isolate['id']}/commands",
        content=json.dumps(payload, ensure_ascii=True),
        headers={"Content-Type": "application/json", "Idempotency-Key": "invalid-text"},
    )
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200, response.text
    assert response.json()["tasks"] == []


@pytest.mark.parametrize(
    "invalid_text", ["\ud800", "\udfff", "\x00"], ids=["high-surrogate", "low-surrogate", "nul"]
)
@pytest.mark.parametrize("field", ["stdout", "stderr", "error"])
async def test_inline_result_text_rejects_invalid_encoding_without_consuming_task(
    api, agent_api, command, run_task, invalid_text, field
):
    """Invalid inline output returns 422 and leaves the task available for a later valid completion."""
    path = f"/agent/v1/tasks/{run_task['id']}/complete"
    response = await agent_api.post(
        path,
        content=json.dumps({"returncode": 0, field: "output " + invalid_text}, ensure_ascii=True),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)
    result = await api.get(f"/v1/commands/{command['id']}/result")
    assert result.status_code == 200, result.text
    assert result.json()["result"] is None
    response = await agent_api.post(path, json={"returncode": 0, "stdout": "Исправлено 🧪"})
    assert response.status_code == 200, response.text
    result = await api.get(f"/v1/commands/{command['id']}/result")
    assert result.json()["result"]["stdout"] == "Исправлено 🧪"


async def test_valid_unicode_survives_company_command_environment_and_result_round_trip(
    api, agent_api, isolate
):
    """Normal Unicode, supplementary characters and command whitespace remain unchanged through persistence and worker delivery."""
    company_name = "Компания 🧪"
    response = await api.post("/v1/companies", json={"name": company_name})
    assert response.status_code == 201, response.text
    response = await api.get(f"/v1/companies/{response.json()['id']}")
    assert response.json()["name"] == company_name
    payload = {
        "command": "  printf 'Привет 🧪'\n",
        "cwd": "./папка",
        "env": {"ПЕРЕМЕННАЯ": "  значение 🧪  "},
    }
    response = await api.post(
        f"/v1/isolates/{isolate['id']}/commands",
        json=payload,
        headers={"Idempotency-Key": "valid-unicode"},
    )
    assert response.status_code == 202, response.text
    command = response.json()
    for field, value in payload.items():
        assert command[field] == value
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert response.status_code == 200, response.text
    task = response.json()["tasks"][0]
    assert task["command_id"] == command["id"]
    for field, value in payload.items():
        assert task["payload"][field] == value
    report = {
        "returncode": 0,
        "stdout": "Вывод 🧪\n",
        "stderr": "Ошибки é\n",
        "error": "Причина 🧪",
    }
    response = await agent_api.post(f"/agent/v1/tasks/{task['id']}/complete", json=report)
    assert response.status_code == 200, response.text
    response = await api.get(f"/v1/commands/{command['id']}/result")
    assert response.status_code == 200, response.text
    for field, value in report.items():
        assert response.json()["result"][field] == value


async def test_binary_output_artifact_preserves_nul_and_arbitrary_non_utf8_bytes(
    api, agent_api, run_task, command
):
    """Binary artifacts remain the lossless output path for NUL and bytes that cannot appear in validated text fields."""
    content = b"\x00\xff\xed\xa0\x80binary output\x00"
    path = f"/agent/v1/tasks/{run_task['id']}"
    response = await agent_api.put(path + "/artifacts/stdout", content=content)
    assert response.status_code == 200, response.text
    artifact = response.json()
    assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
    response = await agent_api.post(path + "/complete", json={"returncode": 0})
    assert response.status_code == 200, response.text
    result = (await api.get(f"/v1/commands/{command['id']}/result")).json()["result"]
    assert result["stdout_artifact_id"] == artifact["id"]
    response = await api.get(f"/v1/artifacts/{artifact['id']}/content")
    assert response.status_code == 200, response.text
    assert response.content == content


async def test_artifact_filename_rejects_nul_before_creating_storage(api, settings):
    """The filename query parameter obeys the same portable text rules and rejects NUL without leaving file bytes."""
    response = await api.post(
        "/v1/artifacts", params={"filename": "bad\x00name.tar"}, content=b"unused bytes"
    )
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)
    assert list(settings.storage_path.iterdir()) == []
    assert (await api.get("/v1/artifacts")).json() == []


@pytest.mark.parametrize("offset", [2**63 - 1, 2**63, 10**100])
async def test_all_collection_offsets_respect_the_database_integer_boundary(
    paginated_collections, offset
):
    """Every collection accepts the largest SQL offset and rejects larger integers with 422 instead of a database overflow."""
    for client, path, parameters in paginated_collections:
        response = await client.get(path, params={**parameters, "offset": offset})
        if offset == 2**63 - 1:
            assert response.status_code == 200, (path, response.text)
            assert response.json() == [], path
        else:
            assert response.status_code == 422, (path, response.text)
            assert isinstance(response.json()["detail"], list), path


@pytest.mark.parametrize("settings", [{"task_ack_timeout_seconds": 5}], indirect=True)
async def test_expired_task_rejects_upload_before_waiting_for_request_body(
    api, agent, run_task, artifact, clock, paused_controller, upload_connection, settings
):
    """A task whose completion deadline has passed rejects an upload before receiving bytes, even before the controller observes expiry."""
    clock.now += timedelta(seconds=6)
    reader, writer = upload_connection
    initial_files = set(settings.storage_path.iterdir())
    request = (
        f"PUT /agent/v1/tasks/{run_task['id']}/artifacts/stdout HTTP/1.1\r\n"
        "Host: localhost\r\n"
        f"Authorization: {agent['headers']['Authorization']}\r\n"
        f"X-Agent-Session: {agent['headers']['X-Agent-Session']}\r\n"
        "Content-Length: 100000\r\nContent-Type: application/octet-stream\r\n\r\n"
    )
    writer.write(request.encode())
    await writer.drain()
    headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
    assert headers.startswith(b"HTTP/1.1 409"), headers
    assert set(settings.storage_path.iterdir()) == initial_files
    assert (await api.get("/v1/artifacts")).json() == [artifact]


@pytest.mark.parametrize("settings", [{"task_ack_timeout_seconds": 5}], indirect=True)
async def test_upload_rechecks_deadline_after_streaming_and_removes_uncommitted_bytes(
    api, agent, run_task, artifact, clock, paused_controller, upload_connection, settings
):
    """An upload that starts in time but crosses the task deadline returns 409 and leaves neither artifact metadata nor orphaned bytes."""
    reader, writer = upload_connection
    initial_files = set(settings.storage_path.iterdir())
    request = (
        f"PUT /agent/v1/tasks/{run_task['id']}/artifacts/stdout HTTP/1.1\r\n"
        "Host: localhost\r\n"
        f"Authorization: {agent['headers']['Authorization']}\r\n"
        f"X-Agent-Session: {agent['headers']['X-Agent-Session']}\r\n"
        "Content-Length: 12\r\nContent-Type: application/octet-stream\r\n\r\nearly"
    )
    writer.write(request.encode())
    await writer.drain()
    for _ in range(200):
        if set(settings.storage_path.iterdir()) != initial_files:
            break
        await asyncio.sleep(0.01)
    assert set(settings.storage_path.iterdir()) != initial_files
    clock.now += timedelta(seconds=6)
    writer.write(b"payload")
    await writer.drain()
    headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
    assert headers.startswith(b"HTTP/1.1 409"), headers
    assert set(settings.storage_path.iterdir()) == initial_files
    assert (await api.get("/v1/artifacts")).json() == [artifact]
