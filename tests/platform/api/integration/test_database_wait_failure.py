"""A failed write lock must retain the wait that explains request latency."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event, insert, update
from sqlalchemy.exc import OperationalError

from throng_api import database as database_module
from throng_api.models import Coordination
from throng_api.transport import RequestLog


@pytest.mark.anyio
async def test_failed_sqlite_write_lock_is_included_in_correlated_request_log(tmp_path, caplog):
    """A competing request can hold the sole SQLite writer until busy_timeout.
    The losing request is exactly the one whose latency must remain attributable
    to database contention. Use only this test's database and a 40ms busy budget;
    no production settings, external process, or network endpoint is involved.
    """
    database = database_module.Database("sqlite+aiosqlite:///" + str(tmp_path / "wait.db"))

    @event.listens_for(database.engine.sync_engine, "connect")
    def short_test_budget(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA busy_timeout=40")
        cursor.close()

    caplog.set_level("INFO", logger="throng.transport")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Coordination.__table__.create)
            await connection.execute(insert(Coordination).values(id=1, revision=0))
        async with database.engine.connect() as holder:
            await holder.execute(update(Coordination).values(revision=1))

            async def application(scope, receive, send):
                async with database.write():
                    pytest.fail("A competing uncommitted writer must exclude this request")

            with pytest.raises(OperationalError, match="locked"):
                await RequestLog(application)(
                    {
                        "type": "http",
                        "headers": [(b"x-request-id", b"b287a95e-e0a1-40a3-9773-8ff6c30e1000")],
                        "method": "POST",
                        "path": "/agent/v1/heartbeat",
                    },
                    None,
                    None,
                )
            await holder.rollback()
        row = json.loads(
            next(
                record.message
                for record in reversed(caplog.records)
                if record.name == "throng.transport"
            )
        )
        assert row["status"] == 500
        assert row["db_writes"] == 0, "A failed lock must not count as an acquired writer"
        assert row["request_id"] == "b287a95e-e0a1-40a3-9773-8ff6c30e1000"
        assert row["duration_ms"] >= 30
        assert row["db_wait_ms"] >= 30, "The failed lock wait was lost from transport timing"
        assert row["db_wait_ms"] <= row["duration_ms"]
    finally:
        await database.close()


@pytest.mark.anyio
async def test_cancelled_write_wait_is_logged_and_cancellation_propagates(
    tmp_path, caplog, monkeypatch
):
    """A disconnected client can cancel the write while it awaits its lock.
    Control the awaited driver boundary and its own monotonic clock only; no
    signal, global event-loop clock, or actual database timeout is changed.
    Cancellation must remain cancellation, and log its elapsed lock wait.
    """
    module = database_module
    now = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    waiting = asyncio.Event()

    class WaitingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def begin(self):
            return self

        async def execute(self, statement):
            waiting.set()
            await asyncio.Future()

    database = module.Database("sqlite+aiosqlite:///" + str(tmp_path / "unused.db"))
    database.sessions = WaitingSession
    caplog.set_level("INFO", logger="throng.transport")

    async def application(scope, receive, send):
        async with database.write():
            pytest.fail("A cancelled lock cannot enter the transaction body")

    request = asyncio.create_task(
        RequestLog(application)(
            {"type": "http", "headers": [], "method": "POST", "path": "/agent/v1/heartbeat"},
            None,
            None,
        )
    )
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        now[0] = 0.125
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        row = json.loads(caplog.records[-1].message)
        assert row["status"] == 500
        assert row["db_writes"] == 0
        assert row["db_wait_ms"] == 125.0, "Cancellation lost the interrupted lock wait"
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        await database.close()
