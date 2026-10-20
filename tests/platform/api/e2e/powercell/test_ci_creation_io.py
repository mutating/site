"""Failed CI initial-state writes must not leave unaccounted artifact files."""

import errno
from pathlib import Path

import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from throng_api import ci
from throng_api.models import Artifact, CIRoute

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: a failed state write cleans its partial bytes before metadata
# exists. Repeating the failed creation cannot accumulate files outside quota.
@pytest.mark.parametrize("boundary", ["component", "http"])
async def test_failed_initial_state_write_leaves_no_orphan(
    api, ci_peer, app, settings, monkeypatch, boundary
):
    """Compensate a partial initial-state write before the filesystem reports ENOSPC.

    Real account authorization, repository verification and SQLite metadata stay
    in use. Inject only the filesystem fault: writing a short real file before
    raising models Path.write_bytes on a full or size-limited filesystem. Both
    direct creation and HTTP retries must leave storage and metadata unchanged.
    """
    before = set(settings.storage_path.iterdir())
    write_bytes = Path.write_bytes

    def short_write(path, content):
        if path.parent == settings.storage_path:
            write_bytes(path, content[:512])
            raise OSError(errno.ENOSPC, "simulated full artifact filesystem")
        return write_bytes(path, content)

    monkeypatch.setattr(Path, "write_bytes", short_write)
    body = {
        "installation_id": 456,
        "repository_id": 42,
        "workflow_path": ".github/workflows/ci.yml",
        "job_key": "test",
    }
    for _ in range(2):
        if boundary == "component":
            request = Request(
                {
                    "type": "http",
                    "app": app,
                    "method": "POST",
                    "path": "/v1/github/routes",
                    "query_string": b"",
                    "headers": [(b"authorization", api.headers["Authorization"].encode())],
                }
            )
            with pytest.raises(OSError) as error:
                await ci.create_route(ci.RouteCreate(**body), request)
            assert error.value.errno == errno.ENOSPC
        else:
            response = await api.post("/v1/github/routes", json=body)
            assert 500 <= response.status_code < 600, response.text
        async with app.state.db.read() as session:
            assert await session.scalar(select(func.count()).select_from(CIRoute)) == 0
            assert await session.scalar(select(func.count()).select_from(Artifact)) == 0
        assert set(settings.storage_path.iterdir()) == before
