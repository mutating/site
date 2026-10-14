import asyncio
import time
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import and_, case, or_, select

from throng_api import outputs as out
from throng_api.artifacts import (
    BINARY_DOWNLOAD_RESPONSES,
    BINARY_UPLOAD_DOCS,
    artifact_file,
    check_storage_quota,
    receive_file,
)
from throng_api.availability import sync_capacity
from throng_api.device_security import agent_device_proof
from throng_api.execution import (
    TASK_TERMINAL,
    check_new_work,
    clean_start,
    complete_task,
    remember,
    replay,
    task_for_agent,
)
from throng_api.models import AgentTask, Artifact, Command, Isolate, TaskArtifact
from throng_api.schemas import Claim, SessionStart, TaskComplete
from throng_api.security import agent_auth, agent_credential_scheme, renew_agent
from throng_api.serialization import public

router = APIRouter(
    prefix="/agent/v1",
    tags=["agent protocol v1"],
    dependencies=[Depends(agent_credential_scheme), Depends(agent_device_proof)],
)

# The runtime guard stays in agent_auth(), preserving 401 for invalid credentials
# and 409 for absent/expired sessions instead of introducing a validation-only 422.
AGENT_SESSION_DOCS = {
    "parameters": [
        {
            "name": "X-Agent-Session",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "format": "uuid"},
            "description": "Session ID returned by POST /agent/v1/session. Missing, expired or superseded sessions return 409.",
        }
    ]
}


def session_info(computer, now):
    return {
        "protocol_version": 1,
        "computer_id": computer.id,
        "session_id": computer.session_id,
        "server_time": now,
        "lease_expires_at": computer.lease_expires_at,
    }


@router.post("/session", response_model=out.AgentSession)
async def start_session(data: SessionStart, request: Request, idempotency_key: str = Header()):
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request, require_session=False)
        now = request.app.state.clock()
        namespace = f"agent:{computer.id}"
        payload = data.model_dump(mode="json")
        previous = await replay(session, namespace, "start_session", idempotency_key, payload)
        if previous:
            if (
                previous != computer.session_id
                or not computer.online
                or computer.lease_expires_at <= now
            ):
                raise HTTPException(409, "Handshake belongs to an expired or superseded session")
            return session_info(computer, now)
        expected = str(data.previous_session_id) if data.previous_session_id else None
        if expected != computer.session_id:
            raise HTTPException(
                409, "Session changed; reconcile and clean up before starting again"
            )
        await clean_start(session, computer, now, request.app.state.settings)
        remember(session, namespace, "start_session", idempotency_key, payload, computer.session_id)
        return session_info(computer, now)


@router.get("/session", response_model=out.CurrentAgentSession)
async def current_session(request: Request):
    async with request.app.state.db.read() as session:
        computer = await agent_auth(session, request, require_session=False)
        now = request.app.state.clock()
        return {
            "computer_id": computer.id,
            "session_id": computer.session_id,
            "server_time": now,
            "lease_expires_at": computer.lease_expires_at,
            "active": bool(
                computer.online and computer.lease_expires_at and computer.lease_expires_at > now
            ),
        }


@router.post("/heartbeat", response_model=out.AgentSession, openapi_extra=AGENT_SESSION_DOCS)
async def heartbeat(request: Request):
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request)
        now = request.app.state.clock()
        renew_agent(computer, now, request.app.state.settings)
        await sync_capacity(session, computer, now)
        return session_info(computer, now)


@router.post("/tasks/claim", response_model=out.ClaimedTasks, openapi_extra=AGENT_SESSION_DOCS)
async def claim(data: Claim, request: Request):
    settings = request.app.state.settings
    until = time.monotonic() + min(data.wait_seconds, settings.long_poll_max_seconds)
    while True:
        # Release the transaction before waiting; works across processes/restarts.
        async with request.app.state.db.write() as session:
            computer = await agent_auth(session, request)
            now = request.app.state.clock()
            tasks = (
                await session.scalars(
                    select(AgentTask)
                    .join(Isolate, Isolate.id == AgentTask.isolate_id)
                    .where(
                        AgentTask.computer_id == computer.id,
                        AgentTask.session_id == computer.session_id,
                        AgentTask.status.in_(["pending", "dispatched"]),
                        AgentTask.deadline > now,
                        or_(
                            AgentTask.kind.in_(["cancel", "destroy"]),
                            and_(
                                computer.can_start_work,
                                Isolate.expires_at > now,
                                or_(
                                    and_(
                                        AgentTask.kind == "create",
                                        Isolate.status == "provisioning",
                                        Isolate.provisioning_deadline > now,
                                    ),
                                    and_(
                                        AgentTask.kind.in_(["run", "snapshot"]),
                                        Isolate.status == "ready",
                                    ),
                                ),
                            ),
                        ),
                    )
                    .order_by(
                        case(
                            (AgentTask.kind == "destroy", 0),
                            (AgentTask.kind == "cancel", 1),
                            else_=2,
                        ),
                        AgentTask.created_at,
                        AgentTask.id,
                    )
                    .limit(data.limit)
                )
            ).all()
            for task in tasks:
                task.status = "dispatched"
                task.delivered_at = task.delivered_at or now
            response = {**session_info(computer, now), "tasks": [public(task) for task in tasks]}
        if tasks or time.monotonic() >= until:
            return response
        if await request.is_disconnected():
            return response
        await asyncio.sleep(min(settings.long_poll_interval, max(0, until - time.monotonic())))


@router.get("/tasks/{task_id}", response_model=out.AgentTask, openapi_extra=AGENT_SESSION_DOCS)
async def get_task(task_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        computer = await agent_auth(session, request)
        return public(await task_for_agent(session, task_id, computer))


@router.post("/tasks/{task_id}/ack", response_model=out.AgentTask, openapi_extra=AGENT_SESSION_DOCS)
async def acknowledge(task_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request)
        task = await task_for_agent(session, task_id, computer)
        now = request.app.state.clock()
        if task.status in TASK_TERMINAL or task.delivered_at is None or task.deadline <= now:
            raise HTTPException(409, "Task is not awaiting acknowledgement")
        task.status = "accepted"
        if task.accepted_at is None:
            if task.kind in {"create", "run", "snapshot"} and not computer.can_start_work:
                raise HTTPException(409, "Agent is not accepting work")
            check_new_work(await session.get(Isolate, task.isolate_id), task.kind, now)
        task.accepted_at = task.accepted_at or now
        return public(task)


@router.post(
    "/tasks/{task_id}/started", response_model=out.AgentTask, openapi_extra=AGENT_SESSION_DOCS
)
async def started(task_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request)
        task = await task_for_agent(session, task_id, computer)
        now = request.app.state.clock()
        if (
            task.kind != "run"
            or task.status in TASK_TERMINAL
            or task.delivered_at is None
            or task.deadline <= now
        ):
            raise HTTPException(409, "Task cannot be started")
        command = await session.get(Command, task.command_id)
        if command.started_at is None:
            if not computer.can_start_work:
                raise HTTPException(409, "Agent is not accepting work")
            check_new_work(await session.get(Isolate, task.isolate_id), task.kind, now)
            command.started_at = now
            if command.status != "cancel_requested":
                command.status = "running"
                task.deadline = now + timedelta(
                    seconds=command.timeout_seconds
                    + request.app.state.settings.cancellation_timeout_seconds
                )
        task.status = "accepted"
        task.accepted_at = task.accepted_at or now
        return public(task)


@router.post(
    "/tasks/{task_id}/complete", response_model=out.AgentTask, openapi_extra=AGENT_SESSION_DOCS
)
async def complete(task_id: UUID, data: TaskComplete, request: Request):
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request)
        task = await task_for_agent(session, task_id, computer)
        await complete_task(
            session, task, data, request.app.state.clock(), request.app.state.settings
        )
        return public(task)


async def upload_target(session, request, task_id, purpose):
    computer = await agent_auth(session, request)
    task = await task_for_agent(session, task_id, computer)
    permitted = (task.kind == "snapshot" and purpose == "snapshot") or (
        task.kind in {"run", "cancel"} and purpose in {"stdout", "stderr"}
    )
    if not permitted or task.status in TASK_TERMINAL or task.delivered_at is None:
        raise HTTPException(409, "Artifact is not allowed for this task")
    if task.deadline <= request.app.state.clock():
        raise HTTPException(409, "Task deadline has expired")
    return task


@router.put(
    "/tasks/{task_id}/artifacts/{purpose}",
    response_model=out.Artifact,
    openapi_extra={**AGENT_SESSION_DOCS, **BINARY_UPLOAD_DOCS},
)
async def upload(task_id: UUID, purpose: Literal["stdout", "stderr", "snapshot"], request: Request):
    async with request.app.state.db.read() as session:
        await upload_target(session, request, task_id, purpose)
    path, size, checksum = await receive_file(request)
    keep = False
    try:
        async with request.app.state.db.write() as session:
            task = await upload_target(session, request, task_id, purpose)
            attachment = await session.get(TaskArtifact, (task.id, purpose))
            if attachment:
                previous = await session.get(Artifact, attachment.artifact_id)
                if previous.sha256 != checksum or previous.size_bytes != size:
                    raise HTTPException(409, "A different artifact already exists for this purpose")
                return public(previous)
            isolate = await session.get(Isolate, task.isolate_id)
            owner = {"user_id": isolate.user_id, "company_id": isolate.company_id}
            await check_storage_quota(session, owner, size, request.app.state.settings)
            artifact = Artifact(
                **owner,
                filename=f"{purpose}.{'tar' if purpose == 'snapshot' else 'log'}",
                storage_key=path.name,
                size_bytes=size,
                sha256=checksum,
            )
            session.add(artifact)
            await session.flush()
            session.add(TaskArtifact(task_id=task.id, purpose=purpose, artifact_id=artifact.id))
            await session.flush()
            response = public(artifact)
            # COMMIT/connection cleanup may fail after the metadata is durable.
            keep = True
        return response
    finally:
        if not keep:
            path.unlink(missing_ok=True)


@router.get(
    "/artifacts/{artifact_id}/content",
    response_class=FileResponse,
    responses=BINARY_DOWNLOAD_RESPONSES,
    openapi_extra=AGENT_SESSION_DOCS,
)
async def download(artifact_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        computer = await agent_auth(session, request)
        assigned = await session.scalar(
            select(Isolate.id)
            .where(
                Isolate.computer_id == computer.id,
                Isolate.session_id == computer.session_id,
                Isolate.state_artifact_id == str(artifact_id),
                Isolate.status.in_(["provisioning", "ready"]),
            )
            .limit(1)
        )
        if not assigned:
            raise HTTPException(404, "Artifact not assigned to this agent")
        artifact = await session.get(Artifact, str(artifact_id))
        return artifact_file(artifact, request.app.state.settings)
