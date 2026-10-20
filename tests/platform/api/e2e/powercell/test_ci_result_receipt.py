"""A runner's accepted result remains reconcilable across a second network failure."""

import httpx
import pytest
from sqlalchemy import func, select

from tests.platform.api.e2e.network_faults import FaultProxy
from throng_api.models import CommandResult, TaskArtifact

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import ready_runner
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


async def test_runner_result_receipt_recovers_after_upload_conflict_and_api_outage(
    client, api, route, ci_peer, powercell, app, clock, server
):
    """Confirm the exact runner result after a lost completion reply and receipt outage.

    The real signed agent uploads binary stdout and commits its completion. A
    retry of the upload then correctly conflicts with the terminal task. One
    receipt request receives HTTP 503 from a disposable TCP proxy; after recovery
    the original result is still accepted, without duplicate artifacts/results.
    A changed completion must remain a conflict rather than a false success.
    """
    _, run = await ready_runner(client, route, ci_peer, powercell, app, clock)
    path = f"/agent/v1/tasks/{run['id']}"
    output = b"runner output\x00\xff"
    upload = await powercell.client.put(path + "/artifacts/stdout", content=output)
    assert upload.status_code == 200, upload.text
    report = {"outcome": "completed", "returncode": 0}
    async with FaultProxy(server, "drop_after") as proxy:
        async with httpx.AsyncClient(
            base_url=proxy.url,
            headers=powercell.client.headers,
            auth=powercell.client.auth,
            trust_env=False,
            timeout=3,
        ) as interrupted:
            with pytest.raises(httpx.RemoteProtocolError):
                await interrupted.post(path + "/complete", json=report)
        assert proxy.attempts == 1
    assert (
        await powercell.client.put(path + "/artifacts/stdout", content=output)
    ).status_code == 409
    async with FaultProxy(server, "unavailable") as proxy:
        async with httpx.AsyncClient(
            base_url=proxy.url,
            headers=powercell.client.headers,
            auth=powercell.client.auth,
            trust_env=False,
            timeout=3,
        ) as recovering:
            unavailable = await recovering.post(path + "/receipt", json=report)
            assert unavailable.status_code == 503
            receipt = await recovering.post(path + "/receipt", json=report)
            assert receipt.status_code == 200, receipt.text
            assert receipt.json() == {"accepted": True, "active": False}
        assert proxy.attempts == 2
    changed = await powercell.client.post(path + "/receipt", json={**report, "returncode": 23})
    assert changed.status_code == 409
    assert changed.json()["code"] == "completion_conflict"
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(CommandResult)) == 1
        attachments = (await session.scalars(select(TaskArtifact))).all()
        assert len(attachments) == 1 and attachments[0].task_id == run["id"]
    downloaded = await api.get(f"/v1/artifacts/{upload.json()['id']}/content")
    assert downloaded.status_code == 200 and downloaded.content == output
