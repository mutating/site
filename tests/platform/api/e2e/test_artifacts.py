"""Real file storage, output attachments and sandbox snapshots through both HTTP APIs."""

import hashlib
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_artifact_upload_download_and_delete(api, artifact):
    """Artifact CRUD preserves bytes and checksum while hiding the private storage location."""
    path = f"/v1/artifacts/{artifact['id']}"
    assert "storage_key" not in artifact
    assert artifact["sha256"] == hashlib.sha256(b"initial state").hexdigest()
    assert artifact["size_bytes"] == len(b"initial state")
    assert (await api.get(path)).json() == artifact
    assert (await api.get("/v1/artifacts", params={"limit": 1})).json() == [artifact]
    assert (await api.get("/v1/artifacts", params={"offset": 1})).json() == []
    response = await api.get(path + "/content")
    assert response.status_code == 200 and response.content == b"initial state"
    assert response.headers["x-content-sha256"] == artifact["sha256"]
    assert "state.tar" in response.headers["content-disposition"]
    assert (await api.delete(path)).status_code == 204
    assert (await api.get(path)).status_code == 404
    assert (await api.get(path + "/content")).status_code == 404


async def test_filename_is_sanitized_and_binary_bytes_are_opaque(api):
    """The API stores opaque binary bytes and strips directories from the download filename."""
    response = await api.post(
        "/v1/artifacts",
        params={"filename": "../../archive.bin"},
        content=b"\x00\xffnot an archive",
    )
    assert response.status_code == 201
    artifact = response.json()
    assert artifact["filename"] == "archive.bin"
    assert (
        await api.get(f"/v1/artifacts/{artifact['id']}/content")
    ).content == b"\x00\xffnot an archive"


@pytest.mark.parametrize(
    "settings", [{"max_artifact_bytes": 13, "max_owner_storage_bytes": 13}], indirect=True
)
async def test_artifact_size_and_owner_quota_recover_after_deletion(api, artifact):
    """Oversized files and owner quota overflow are rejected; deleting a file restores usable quota."""
    assert (await api.post("/v1/artifacts", content=b"x" * 14)).status_code == 413
    assert (await api.post("/v1/artifacts", content=b"x")).status_code == 409
    assert (await api.get(f"/v1/artifacts/{artifact['id']}/content")).content == b"initial state"
    assert (await api.delete(f"/v1/artifacts/{artifact['id']}")).status_code == 204
    assert (await api.post("/v1/artifacts", content=b"replacement")).status_code == 201
    assert len((await api.get("/v1/artifacts")).json()) == 1


@pytest.mark.parametrize("purpose", ["stdout", "stderr"])
async def test_output_artifacts_are_idempotent_and_attached_to_results(
    api, agent_api, command, run_task, purpose
):
    """Large output uploads deduplicate by task and purpose and remain downloadable through command results."""
    path = f"/agent/v1/tasks/{run_task['id']}/artifacts/{purpose}"
    content = b"worker output\n" * 8192
    response = await agent_api.put(path, content=content)
    assert response.status_code == 200
    artifact = response.json()
    assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
    assert (await agent_api.put(path, content=content)).json() == artifact
    assert (await agent_api.put(path, content=b"conflicting")).status_code == 409
    assert (
        await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/complete", json={"returncode": 0})
    ).status_code == 200
    result = (await api.get(f"/v1/commands/{command['id']}/result")).json()["result"]
    assert result[purpose + "_artifact_id"] == artifact["id"]
    assert (await api.get(f"/v1/artifacts/{artifact['id']}/content")).content == content
    assert (await agent_api.put(path, content=content)).status_code == 409
    assert (await api.delete(f"/v1/artifacts/{artifact['id']}")).status_code == 409


async def test_snapshot_blocks_dispatch_and_can_seed_another_isolate(
    api, agent_api, isolate, isolate_payload
):
    """Snapshot completion requires uploaded state, excludes concurrent execution and produces reusable state."""
    path = f"/v1/isolates/{isolate['id']}/snapshots"
    response = await api.post(path, headers={"Idempotency-Key": "snapshot"})
    assert response.status_code == 202
    operation = response.json()
    assert (await api.post(path, headers={"Idempotency-Key": "snapshot"})).json()[
        "id"
    ] == operation["id"]
    assert (await api.post(path, headers={"Idempotency-Key": "second-snapshot"})).status_code == 409
    task = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ][0]
    assert task["kind"] == "snapshot"
    assert (await agent_api.post(f"/agent/v1/tasks/{task['id']}/ack")).status_code == 200
    response = await api.post(
        f"/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": "while-snapshot"},
        json={"command": "pytest"},
    )
    assert response.status_code == 202
    queued = response.json()
    assert (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.05})).json()[
        "tasks"
    ] == []
    assert (await api.get(f"/v1/commands/{queued['id']}")).json()["status"] == "queued"
    task_path = f"/agent/v1/tasks/{task['id']}"
    assert (await agent_api.post(task_path + "/complete", json={})).status_code == 422
    response = await agent_api.put(task_path + "/artifacts/snapshot", content=b"saved snapshot")
    assert response.status_code == 200
    snapshot = response.json()
    assert (await agent_api.post(task_path + "/complete", json={})).status_code == 200
    response = await api.get(f"/v1/operations/{operation['id']}")
    assert response.status_code == 200 and response.json()["artifact_id"] == snapshot["id"]
    run = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()["tasks"][
        0
    ]
    assert run["command_id"] == queued["id"]
    assert (
        await agent_api.post(f"/agent/v1/tasks/{run['id']}/complete", json={"returncode": 0})
    ).status_code == 200
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": "restored"},
        json={**isolate_payload, "state_artifact_id": snapshot["id"]},
    )
    assert response.status_code == 202
    create = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ][0]
    assert create["payload"]["state_artifact_id"] == snapshot["id"]
    assert (
        await agent_api.get(f"/agent/v1/artifacts/{snapshot['id']}/content")
    ).content == b"saved snapshot"
    assert (
        await agent_api.post(f"/agent/v1/tasks/{create['id']}/complete", json={})
    ).status_code == 200


async def test_running_commands_prevent_snapshots(api, isolate, running_command):
    """A snapshot cannot start while a command is already active in its isolate."""
    assert (
        await api.post(
            f"/v1/isolates/{isolate['id']}/snapshots", headers={"Idempotency-Key": "busy"}
        )
    ).status_code == 409


async def test_agent_can_fetch_only_assigned_live_initial_state(api, agent_api, isolate, artifact):
    """An agent can download its assigned state but loses access when the isolate is destroyed."""
    path = f"/agent/v1/artifacts/{artifact['id']}/content"
    assert (await agent_api.get(path)).content == b"initial state"
    assert (await agent_api.get(f"/agent/v1/artifacts/{uuid4()}/content")).status_code == 404
    assert (await api.delete(f"/v1/artifacts/{artifact['id']}")).status_code == 409
    assert (await api.delete(f"/v1/isolates/{isolate['id']}")).status_code == 202
    assert (await agent_api.get(path)).status_code == 404


@pytest.mark.parametrize("control_task", ["create", "snapshot", "cancel", "destroy"], indirect=True)
async def test_artifact_purpose_must_match_task_type(agent_api, control_task):
    """Workers cannot attach files under a purpose that does not belong to their task type."""
    purpose = "stdout" if control_task["kind"] != "cancel" else "snapshot"
    response = await agent_api.put(
        f"/agent/v1/tasks/{control_task['id']}/artifacts/{purpose}", content=b"invalid attachment"
    )
    assert response.status_code == 409


async def test_cancel_report_can_attach_partial_output(api, agent_api, command, run_task):
    """A separate stop task may upload partial stdout and include it in the cancellation result."""
    assert (await api.post(f"/v1/commands/{command['id']}/cancel")).status_code == 202
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    cancel = next(task for task in tasks if task["kind"] == "cancel")
    response = await agent_api.put(
        f"/agent/v1/tasks/{cancel['id']}/artifacts/stdout", content=b"partial"
    )
    assert response.status_code == 200
    artifact = response.json()
    assert (
        await agent_api.post(f"/agent/v1/tasks/{cancel['id']}/complete", json={"returncode": -9})
    ).status_code == 200
    result = (await api.get(f"/v1/commands/{command['id']}/result")).json()["result"]
    assert result["stdout_artifact_id"] == artifact["id"]
    assert (await api.get(f"/v1/artifacts/{artifact['id']}/content")).content == b"partial"


async def test_undelivered_tasks_cannot_be_completed_or_receive_artifacts(api, agent_api, isolate):
    """Knowing an operation UUID does not authorize acknowledgement, completion or upload before delivery."""
    response = await api.post(
        f"/v1/isolates/{isolate['id']}/snapshots", headers={"Idempotency-Key": "undelivered"}
    )
    assert response.status_code == 202
    path = f"/agent/v1/tasks/{response.json()['id']}"
    for suffix in ["ack", "started", "complete"]:
        assert (await agent_api.post(path + "/" + suffix, json={})).status_code == 409
    assert (
        await agent_api.put(path + "/artifacts/snapshot", content=b"premature")
    ).status_code == 409
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    assert tasks[0]["status"] == "dispatched"


async def test_interrupted_http_upload_does_not_leave_partial_files(
    api, user, upload_connection, settings
):
    """Disconnecting halfway through an HTTP body removes temporary bytes and leaves no artifact metadata."""
    import asyncio

    _, writer = upload_connection
    request = (
        "POST /v1/artifacts HTTP/1.1\r\nHost: localhost\r\n"
        f"Authorization: {user['headers']['Authorization']}\r\n"
        "Content-Length: 100000\r\nContent-Type: application/octet-stream\r\n\r\npartial"
    )
    writer.write(request.encode())
    await writer.drain()
    # Observe receipt before closing, so this exercises cleanup after an actual partial write.
    for _ in range(100):
        if list(settings.storage_path.iterdir()):
            break
        await asyncio.sleep(0.01)
    assert list(settings.storage_path.iterdir())
    writer.close()
    await writer.wait_closed()
    for _ in range(100):
        if not list(settings.storage_path.iterdir()):
            break
        await asyncio.sleep(0.01)
    assert not list(settings.storage_path.iterdir())
    assert (await api.get("/v1/artifacts")).json() == []
    assert (await api.post("/v1/artifacts", content=b"complete replacement")).status_code == 201
