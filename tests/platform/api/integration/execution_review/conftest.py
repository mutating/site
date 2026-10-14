"""Persisted failure history and observations of the running production controller."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from throng_api import controller
from throng_api.execution import fail_isolate
from throng_api.models import Isolate


@pytest.fixture
async def controller_failure_history(
    app, create_task, user, isolate_payload, clock, monkeypatch, request
):
    """Pause real ticks and persist cleaned failures alongside reserved and unallocated failures awaiting cleanup."""
    original = controller.run_once
    entered, resume, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    state = {
        "observe": True,
        "resume": resume,
        "completed": completed,
        "materialized": [],
        "ticks": [],
    }

    async def observed_tick(application):
        """Gate the production controller outside its transaction and record each completed tick separately."""
        entered.set()
        await resume.wait()
        await original(application)
        if state["observe"]:
            state["ticks"].append(tuple(state["materialized"]))
            state["materialized"].clear()
            resume.clear()
            completed.set()

    def materialized(session, row):
        """Count isolate ORM loads only in the background controller, excluding HTTP and fixture queries."""
        task = asyncio.current_task()
        if state["observe"] and isinstance(row, Isolate) and task.get_name() == "throng-controller":
            state["materialized"].append(row.id)

    monkeypatch.setattr(controller, "run_once", observed_tick)
    await asyncio.wait_for(entered.wait(), timeout=5)
    event.listen(Session, "loaded_as_persistent", materialized)
    try:
        async with app.state.db.write() as session:
            reserved = await session.get(Isolate, create_task["isolate_id"])
            await fail_isolate(session, reserved, clock.now, "provisioning_failed")
            state["reserved_id"] = reserved.id
            state["computer_id"] = reserved.computer_id
            history = [
                Isolate(
                    **isolate_payload,
                    user_id=user["id"],
                    created_at=clock.now - timedelta(days=1),
                    expires_at=clock.now - timedelta(hours=1),
                    provisioning_deadline=clock.now - timedelta(hours=1),
                    status="failed",
                    error="provisioning_timeout",
                    reserved=False,
                    destroyed_at=clock.now - timedelta(hours=1),
                )
                for _ in range(request.param)
            ]
            unfinished = Isolate(
                **isolate_payload,
                user_id=user["id"],
                created_at=clock.now,
                expires_at=clock.now + timedelta(hours=1),
                provisioning_deadline=clock.now,
                status="failed",
                error="provisioning_timeout",
                reserved=False,
            )
            session.add_all([*history, unfinished])
            await session.flush()
            state["historical_ids"] = {row.id for row in history}
            state["unfinished_id"] = unfinished.id
        yield state
    finally:
        state["observe"] = False
        resume.set()
        event.remove(Session, "loaded_as_persistent", materialized)
