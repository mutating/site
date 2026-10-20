"""Image-aware routing stays compatible with old Power Cell capacity reports."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from throng_api.controller import run_once
from throng_api.execution import clean_start
from throng_api.models import AgentTask, Computer
from throng_api.workload_images import RUNNER_IMAGE_FAMILY, supports_image

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "image,accepted",
    [
        ("ubuntu:24.04", True),
        (RUNNER_IMAGE_FAMILY + ":latest", True),
        (RUNNER_IMAGE_FAMILY + ":2.338.0", True),
        (RUNNER_IMAGE_FAMILY + "@sha256:" + "a" * 64, True),
        (RUNNER_IMAGE_FAMILY, False),
        (RUNNER_IMAGE_FAMILY + ":latest\n", False),
        (RUNNER_IMAGE_FAMILY + "@sha256:" + "A" * 64, False),
        ("ghcr.io/other/actions-runner:latest", False),
        ("ghcr.io.evil/actions/actions-runner:latest", False),
        (RUNNER_IMAGE_FAMILY + ":../private", False),
    ],
)
def test_powercell_image_references_are_explicit_and_allowlisted(image, accepted):
    """Accept only explicit supported Power Cell image references."""
    computer = SimpleNamespace(
        runtime="powercell-ubuntu-arm64-v1",
        network_enabled=True,
        capacity_report={"image_families": [RUNNER_IMAGE_FAMILY]},
    )
    assert supports_image(computer, image) is accepted
    computer.runtime = "oci"
    assert supports_image(computer, image) is True


@pytest.mark.parametrize("report", [None, {}, {"image_families": []}])
def test_old_agents_only_accept_the_bundled_image(report):
    """Restrict legacy agents without image capabilities to their bundled image."""
    computer = SimpleNamespace(
        runtime="powercell-ubuntu-arm64-v1", network_enabled=True, capacity_report=report
    )
    assert supports_image(computer, "ubuntu:24.04")
    assert not supports_image(computer, RUNNER_IMAGE_FAMILY + ":latest")


async def test_image_capabilities_preserve_old_capacity_replay(api, agent_api, computer):
    """Preserve capacity-report replay semantics when image capabilities are present."""
    report = {
        "revision": 0,
        "accepting_work": True,
        "mode": "idle",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
        "running_commands": 0,
        "cpu_used_millis": 0,
        "memory_used_bytes": 0,
    }
    key = str(uuid4())
    for fields in [{}, {"image_families": []}]:
        response = await agent_api.post(
            "/agent/v1/capacity",
            headers={"Idempotency-Key": key},
            json={**report, **fields},
        )
        assert response.status_code == 200, response.text
    row = (await api.get(f"/v1/computers/{computer['id']}")).json()
    assert "image_families" not in row["capacity_report"]
    response = await agent_api.post(
        "/agent/v1/capacity",
        headers={"Idempotency-Key": str(uuid4())},
        json={**report, "revision": 1, "image_families": [RUNNER_IMAGE_FAMILY]},
    )
    assert response.status_code == 200
    row = (await api.get(f"/v1/computers/{computer['id']}")).json()
    assert row["capacity_report"]["image_families"] == [RUNNER_IMAGE_FAMILY]
    for families in [["docker.io/untrusted"], [RUNNER_IMAGE_FAMILY] * 2, "any"]:
        response = await agent_api.post(
            "/agent/v1/capacity",
            headers={"Idempotency-Key": str(uuid4())},
            json={**report, "revision": 2, "image_families": families},
        )
        assert response.status_code == 422


@pytest.mark.parametrize(
    "image_fields,reference",
    [
        ({}, "ubuntu:24.04"),
        ({"image": "ubuntu"}, "ubuntu:24.04"),
        ({"image": "actions"}, RUNNER_IMAGE_FAMILY + ":latest"),
    ],
)
@pytest.mark.parametrize("alias_first", [True, False])
async def test_powercell_image_selection_resolves_before_storage_replay_and_agent_task_creation(
    api, agent_api, app, computer, isolate_payload, image_fields, reference, alias_first
):
    """Resolve the Power Cell image before storage replay and agent task creation."""
    runner = reference.startswith(RUNNER_IMAGE_FAMILY)
    async with app.state.db.write() as session:
        row = await session.get(Computer, computer["id"])
        row.runtime = "powercell-ubuntu-arm64-v1"
        row.architecture = "arm64"
        # Ubuntu must keep working on agents predating OCI support.
        row.capacity_report = {"image_families": [RUNNER_IMAGE_FAMILY]} if runner else None
        row.network_enabled = runner
    payload = {
        **isolate_payload,
        "runtime": "powercell-ubuntu-arm64-v1",
        "architecture": "arm64",
    }
    payload.pop("image")
    headers = {"Idempotency-Key": str(uuid4())}
    first, second = image_fields, {"image": reference}
    if not alias_first:
        first, second = second, first
    response = await api.post("/v1/isolates", headers=headers, json={**payload, **first})
    assert response.status_code == 202, response.text
    isolate = response.json()
    assert isolate["image"] == reference
    response = await api.post("/v1/isolates", headers=headers, json={**payload, **second})
    assert response.status_code == 202, response.text
    assert response.json()["id"] == isolate["id"]
    assert response.json()["image"] == reference
    assert (await api.get(f"/v1/isolates/{isolate['id']}")).json()["image"] == reference
    assert [row["image"] for row in (await api.get("/v1/isolates")).json()] == [reference]
    # Enrollment/proofs have their own HTTP tests. Check the real scheduler's
    # queued payload without bypassing attestation on the synthetic worker.
    await run_once(app)
    async with app.state.db.read() as session:
        task = await session.scalar(
            select(AgentTask).where(
                AgentTask.isolate_id == isolate["id"], AgentTask.kind == "create"
            )
        )
        assert task is not None and task.payload["image"] == reference
    other_image = "ubuntu" if runner else "actions"
    response = await api.post(
        "/v1/isolates", headers=headers, json={**payload, "image": other_image}
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "image_fields,image",
    [({}, "ubuntu"), ({"image": "ubuntu"}, "ubuntu"), ({"image": "actions"}, "actions")],
)
async def test_other_runtimes_keep_literal_image_names(
    api, agent_api, isolate_payload, image_fields, image
):
    """Preserve literal image names for runtimes outside Power Cells."""
    payload = {**isolate_payload}
    payload.pop("image")
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={**payload, **image_fields},
    )
    assert response.status_code == 202, response.text
    assert response.json()["image"] == image
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert response.status_code == 200, response.text
    assert response.json()["tasks"][0]["payload"]["image"] == image


@pytest.mark.parametrize("image", [None, "", " "])
async def test_explicit_invalid_image_does_not_use_the_default(api, isolate_payload, image):
    """Reject an explicit unsupported image instead of silently applying the default."""
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={**isolate_payload, "runtime": "powercell-ubuntu-arm64-v1", "image": image},
    )
    assert response.status_code == 422, response.text


async def test_openapi_advertises_the_optional_image_and_ubuntu_default(client):
    """Document the optional image field and Ubuntu default in OpenAPI."""
    schema = (await client.get("/openapi.json")).json()["components"]["schemas"]["IsolateCreate"]
    assert "image" not in schema["required"]
    assert schema["properties"]["image"]["default"] == "ubuntu"
    assert schema["properties"]["image"]["type"] == "string"


@pytest.mark.parametrize("image", ["actions", RUNNER_IMAGE_FAMILY + ":latest"])
async def test_scheduler_requires_image_support_and_network_then_forgets_it_on_restart(
    api, agent_api, app, computer, isolate_payload, image
):
    # Authentication/enrollment is tested separately. This models an already
    # enrolled physical worker while exercising the real database/controller.
    """Require current image and network capabilities and discard them after restart."""
    async with app.state.db.write() as session:
        row = await session.get(Computer, computer["id"])
        row.runtime = "powercell-ubuntu-arm64-v1"
        row.architecture = "arm64"
        row.capacity_report = {}
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            **isolate_payload,
            "runtime": "powercell-ubuntu-arm64-v1",
            "architecture": "arm64",
            "image": image,
        },
    )
    assert response.status_code == 202, response.text
    isolate_id = response.json()["id"]
    for report, network in [({}, True), ({"image_families": [RUNNER_IMAGE_FAMILY]}, False)]:
        async with app.state.db.write() as session:
            row = await session.get(Computer, computer["id"])
            row.capacity_report = report
            row.network_enabled = network
        await run_once(app)
        isolate = (await api.get(f"/v1/isolates/{isolate_id}")).json()
        assert isolate["computer_id"] is None
    async with app.state.db.write() as session:
        row = await session.get(Computer, computer["id"])
        row.network_enabled = True
    await run_once(app)
    isolate = (await api.get(f"/v1/isolates/{isolate_id}")).json()
    assert isolate["computer_id"] == computer["id"]
    async with app.state.db.write() as session:
        row = await session.get(Computer, computer["id"])
        await clean_start(session, row, app.state.clock(), app.state.settings)
        assert row.capacity_report is None
        assert not supports_image(row, RUNNER_IMAGE_FAMILY + ":latest")


async def test_discovery_advertises_the_optional_extension(client):
    """Advertise the optional image-selection extension in capability discovery."""
    data = (await client.get("/v1/agent-capabilities")).json()
    assert "powercell.images.v1" in data["extensions"]
    assert data["images"] == ["ubuntu:24.04"]
    assert data["image_families"] == [RUNNER_IMAGE_FAMILY]
    assert data["image_aliases"] == {
        "ubuntu": "ubuntu:24.04",
        "actions": "ghcr.io/actions/actions-runner:latest",
    }


async def test_unsupported_powercell_image_is_rejected_before_queueing(api, isolate_payload):
    """Reject unsupported Power Cell images before placing work in the queue."""
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            **isolate_payload,
            "runtime": "powercell-ubuntu-arm64-v1",
            "image": "untrusted:latest",
        },
    )
    assert response.status_code == 422
    assert "official GitHub runner images" in response.text
