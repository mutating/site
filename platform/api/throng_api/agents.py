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
    fingerprint,
    remember,
    replay,
    task_for_agent,
)
from throng_api.models import AgentTask, Artifact, CIReservation, Command, Isolate, TaskArtifact
from throng_api.schemas import Claim, SessionStart, TaskComplete
from throng_api.security import agent_auth, agent_credential_scheme, renew_agent
from throng_api.serialization import public
from throng_api.transport import ProtocolError

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


async def delivery_payload(session, task, request):
    """Decrypt runner configuration only for its authenticated assigned agent."""
    result = public(task)
    if task.kind != "run":
        return result
    reservation = await session.scalar(
        select(CIReservation).where(CIReservation.command_id == task.command_id)
    )
    if reservation is None:
        return result
    if task.status in TASK_TERMINAL:
        return result
    now = request.app.state.clock()
    from throng_api.ci import creator_authorized
    from throng_api.models import CIRoute

    if (
        request.app.state.github is None
        or not await creator_authorized(session, await session.get(CIRoute, reservation.route_id))
        or not reservation.credentials
        or reservation.status not in {"preparing", "ready", "selected", "running"}
        or reservation.expires_at <= now
        or (reservation.status in {"preparing", "ready"} and reservation.deadline <= now)
    ):
        return None
    credentials = request.app.state.github.unseal(reservation.credentials)
    result["payload"] = {
        **task.payload,
        "env": {**task.payload.get("env", {}), "POWERCELL_GITHUB_JIT_CONFIG": credentials["jit"]},
    }
    return result


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
        # An empty poll doesn't need the global mutation lock. Recheck under the
        # write lock before dispatching; the read never grants execution rights.
        async with request.app.state.db.read() as session:
            computer = await agent_auth(session, request)
            now = request.app.state.clock()
            pending = await session.scalar(
                select(AgentTask.id)
                .where(
                    AgentTask.computer_id == computer.id,
                    AgentTask.session_id == computer.session_id,
                    AgentTask.status.in_(["pending", "dispatched"]),
                    AgentTask.deadline > now,
                )
                .limit(1)
            )
            response = {**session_info(computer, now), "tasks": []}
        if pending is None:
            if time.monotonic() >= until or await request.is_disconnected():
                return response
            await asyncio.sleep(min(settings.long_poll_interval, max(0, until - time.monotonic())))
            continue
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
            delivered = []
            for task in tasks:
                payload = await delivery_payload(session, task, request)
                if payload is None:
                    continue
                task.status = "dispatched"
                task.delivered_at = task.delivered_at or now
                payload.update(status=task.status, delivered_at=task.delivered_at)
                delivered.append(payload)
            response = {**session_info(computer, now), "tasks": delivered}
        if tasks or time.monotonic() >= until:
            return response
        if await request.is_disconnected():
            return response
        await asyncio.sleep(min(settings.long_poll_interval, max(0, until - time.monotonic())))


@router.get("/tasks/{task_id}", response_model=out.AgentTask, openapi_extra=AGENT_SESSION_DOCS)
async def get_task(task_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        computer = await agent_auth(session, request)
        task = await task_for_agent(session, task_id, computer)
        result = await delivery_payload(session, task, request)
        if result is None:
            raise HTTPException(409, "CI task no longer accepts delivery")
        return result


@router.post("/tasks/{task_id}/ack", response_model=out.AgentTask, openapi_extra=AGENT_SESSION_DOCS)
async def acknowledge(task_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request)
        task = await task_for_agent(session, task_id, computer)
        now = request.app.state.clock()
        if task.status in TASK_TERMINAL or task.delivered_at is None or task.deadline <= now:
            raise ProtocolError("task_not_awaiting_ack", "Task is not awaiting acknowledgement")
        task.status = "accepted"
        if task.accepted_at is None:
            if task.kind in {"create", "run", "snapshot"} and not computer.can_start_work:
                raise ProtocolError("agent_not_accepting", "Agent is not accepting work")
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
            raise ProtocolError("task_not_startable", "Task cannot be started")
        command = await session.get(Command, task.command_id)
        if command.started_at is None:
            if not computer.can_start_work:
                raise ProtocolError("agent_not_accepting", "Agent is not accepting work")
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


@router.post("/tasks/{task_id}/receipt", response_model=out.TaskReceipt)
async def receipt(
    task_id: UUID,
    data: TaskComplete,
    request: Request,
    original_session: UUID = Header(alias="X-Agent-Session"),
):
    """Reconcile an exact result without renewing or reviving its original session."""
    async with request.app.state.db.read() as session:
        computer = await agent_auth(session, request, require_session=False)
        task = await session.get(AgentTask, str(task_id))
        if not task or task.computer_id != computer.id or task.session_id != str(original_session):
            raise HTTPException(404, "Task not assigned to this computer and session")
        accepted = task.completion_hash is not None
        if accepted and task.completion_hash != fingerprint(data.model_dump()):
            raise ProtocolError("completion_conflict", "Task already has a different completion")
        now = request.app.state.clock()
        active = bool(
            computer.online
            and computer.session_id == task.session_id
            and computer.lease_expires_at > now
            and task.deadline > now
            and task.status not in TASK_TERMINAL
        )
        return {"accepted": accepted, "active": active, "task_status": task.status}


async def upload_target(session, request, task_id, purpose):
    computer = await agent_auth(session, request)
    task = await task_for_agent(session, task_id, computer)
    permitted = (task.kind == "snapshot" and purpose == "snapshot") or (
        task.kind in {"run", "cancel"} and purpose in {"stdout", "stderr"}
    )
    if not permitted or task.status in TASK_TERMINAL or task.delivered_at is None:
        raise HTTPException(409, "Artifact is not allowed for this task")
    if task.deadline <= request.app.state.clock():
        raise ProtocolError("task_expired", "Task deadline has expired")
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
