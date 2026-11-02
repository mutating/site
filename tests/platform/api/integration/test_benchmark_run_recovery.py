"""Recover run uploads after a committed write loses its HTTP acknowledgement."""

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import event

from throng_api.models import BenchmarkResult

pytestmark = pytest.mark.anyio


async def test_retry_after_commit_acknowledgement_loss_preserves_one_run(
    api, agent_api, app, computer, monkeypatch
):
    """A failed HTTP response after COMMIT can be retried without duplicate history."""
    write = app.state.db.write
    fail_once = True
    run_id = str(uuid4())

    @asynccontextmanager
    async def lose_acknowledgement():
        """Commit the real SQL transaction before losing its first acknowledgement."""
        nonlocal fail_once
        saved_run = False

        def remember_run(session, flush_context, instances):
            """Target this upload's inserts without faulting background controller writes."""
            nonlocal saved_run
            saved_run = saved_run or any(
                isinstance(row, BenchmarkResult) and row.run_id == run_id for row in session.new
            )

        async with write() as session:
            event.listen(session.sync_session, "before_flush", remember_run)
            try:
                yield session
            finally:
                event.remove(session.sync_session, "before_flush", remember_run)
        if saved_run and fail_once:
            fail_once = False
            raise RuntimeError("benchmark commit acknowledgement lost")

    monkeypatch.setattr(app.state.db, "write", lose_acknowledgement)
    payload = {
        "run_id": run_id,
        "benchmarker_version": "0.0.2",
        "results": [
            {"benchmark_name": "single_core", "number": 10},
            {"benchmark_name": "multi_core", "number": 20},
        ],
    }
    failed = await agent_api.put("/agent/v1/benchmark-results", json=payload)
    assert failed.status_code == 500
    path = f"/v1/computers/{computer['id']}/benchmark-results"
    before = (await api.get(path, params={"latest_only": False})).json()
    assert len(before) == 2
    retry = await agent_api.put("/agent/v1/benchmark-results", json=payload)
    assert retry.status_code == 200, retry.text
    after = (await api.get(path, params={"latest_only": False})).json()
    assert after == before
    assert {row["id"] for row in retry.json()} == {row["id"] for row in before}
    assert len({row["created_at"] for row in after}) == 1
