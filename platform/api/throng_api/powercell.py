"""Agent policy and confirmed cleanup; all mutations share the scheduler's lock."""

import hashlib
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from throng_api import outputs as out
from throng_api.agents import AGENT_SESSION_DOCS
from throng_api.artifacts import BINARY_UPLOAD_DOCS
from throng_api.availability import sync_capacity
from throng_api.computers import computer_for_user
from throng_api.device_security import agent_device_proof
from throng_api.execution import fail_isolate, fingerprint, invalidate_computer, release_reservation
from throng_api.models import (
    CPU_RESOURCE_ID,
    MEMORY_RESOURCE_ID,
    AgentCredential,
    AgentDiagnostic,
    AgentMutation,
    AgentTask,
    ComputerOwnership,
    DeviceCertificate,
    Isolate,
    ProtocolNonce,
)
from throng_api.powercell_schemas import (
    Capacity,
    CapacityAccepted,
    Cleanup,
    DiagnosticAccepted,
    DiagnosticRecord,
    Disconnect,
    Eviction,
)
from throng_api.resource_ledger import change_resource, transfer_resources
from throng_api.security import (
    access_token_scheme,
    agent_auth,
    agent_credential_scheme,
    authenticate,
    bearer,
    digest,
    owner_access,
)
from throng_api.serialization import public

router = APIRouter(
    prefix="/agent/v1",
    tags=["Power Cell"],
    dependencies=[Depends(agent_credential_scheme), Depends(agent_device_proof)],
)
user_router = APIRouter(
    prefix="/v1", tags=["Power Cell management"], dependencies=[Depends(access_token_scheme)]
)


def mutation_key(request, operation, key):
    if not key.strip() or len(key) > 128:
        raise HTTPException(422, "Idempotency-Key of 1 to 128 characters is required")
    claimed_session = request.headers.get("x-agent-session")
    if claimed_session is not None:
        try:
            UUID(claimed_session)
        except ValueError as error:
            raise HTTPException(422, "X-Agent-Session must be a UUID") from error
    return digest(bearer(request)), operation, key


async def mutation_replay(session, request, operation, key, payload, *, credential_hash=None):
    identity = mutation_key(request, operation, key)
    identity = (credential_hash or identity[0], identity[1], identity[2])
    row = await session.get(AgentMutation, identity)
    if not row or row.expires_at <= request.app.state.clock():
        if row:
            await session.delete(row)
            await session.flush()
        return None
    if row.request_hash != fingerprint(payload) or row.session_id != request.headers.get(
        "x-agent-session"
    ):
        raise HTTPException(409, "Idempotency key belongs to a different request or session")
    return row.response


def mutation_save(
    session, request, computer, operation, key, payload, response, *, credential_hash=None
):
    identity = mutation_key(request, operation, key)
    session.add(
        AgentMutation(
            credential_hash=credential_hash or identity[0],
            operation=identity[1],
            key=identity[2],
            request_hash=fingerprint(payload),
            session_id=request.headers.get("x-agent-session"),
            certificate_sha256=computer.certificate_sha256,
            response=jsonable_encoder(response),
            expires_at=request.app.state.clock()
            + timedelta(seconds=request.app.state.settings.agent_mutation_seconds),
        )
    )


@router.post("/capacity", response_model=CapacityAccepted, openapi_extra=AGENT_SESSION_DOCS)
async def capacity(data: Capacity, request: Request, idempotency_key: str = Header()):
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request)
        payload = data.model_dump(mode="json")
        previous = await mutation_replay(session, request, "capacity", idempotency_key, payload)
        if previous:
            return previous
        if data.revision < computer.capacity_revision or (
            data.revision == computer.capacity_revision and payload != computer.capacity_report
        ):
            raise HTTPException(409, "Capacity revision is stale or has conflicting contents")
        if computer.isolation_modes and data.default_isolation not in computer.isolation_modes:
            raise HTTPException(422, "Isolation mode was not advertised during enrollment")
        now = request.app.state.clock()
        # Preserve occupied <= inventory. Falling targets never manufacture a release.
        for type_id, amount in (
            (CPU_RESOURCE_ID, data.cpu_millis),
            (MEMORY_RESOURCE_ID, data.memory_bytes),
        ):
            row = computer.resource(type_id)
            if row is None:
                raise HTTPException(409, "Computer resource inventory is incomplete")
            if amount > row.total:
                await change_resource(session, row, now, "capacity_changed", total=amount)
        computer.disk_bytes = max(computer.disk_bytes, data.disk_bytes)
        computer.target_cpu, computer.target_memory, computer.target_disk = (
            data.cpu_millis,
            data.memory_bytes,
            data.disk_bytes,
        )
        computer.capacity_revision, computer.capacity_report = data.revision, payload
        computer.accepting_work = data.accepting_work
        computer.default_isolation = data.default_isolation
        computer.network_enabled, computer.internet_only = data.network_enabled, data.internet_only
        await sync_capacity(session, computer, now)
        response = {"revision": data.revision, "accepted": True}
        mutation_save(session, request, computer, "capacity", idempotency_key, payload, response)
        return response


@router.post(
    "/isolates/{isolate_id}/evict", response_model=out.Isolate, openapi_extra=AGENT_SESSION_DOCS
)
async def evict(
    isolate_id: UUID, data: Eviction, request: Request, idempotency_key: str = Header()
):
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request)
        payload = data.model_dump(mode="json")
        operation = f"evict:{isolate_id}"
        previous = await mutation_replay(session, request, operation, idempotency_key, payload)
        if previous:
            return previous
        isolate = await session.get(Isolate, str(isolate_id))
        if not isolate or isolate.computer_id != computer.id:
            raise HTTPException(404, "Isolate not assigned to this agent")
        if isolate.session_id != computer.session_id:
            raise HTTPException(409, "Isolate belongs to an obsolete session")
        if isolate.destroyed_at is None:
            now = request.app.state.clock()
            # Agent can further restrict retries, never override a plugin prohibition.
            isolate.retry_allowed = isolate.retry_allowed and data.retry_allowed
            await fail_isolate(session, isolate, now, data.reason)
            await release_reservation(session, isolate, now)
            isolate.destroyed_at = now
            await sync_capacity(session, computer, now)
        response = public(isolate)
        mutation_save(session, request, computer, operation, idempotency_key, payload, response)
        return response


async def settle(session, computer, now, reason):
    computer.accepting_work = False
    for isolate in await invalidate_computer(session, computer, now, reason):
        await release_reservation(session, isolate, now)
        isolate.destroyed_at = now
    computer.lease_expires_at = now
    await sync_capacity(session, computer, now)


async def detach_computer(session, computer, now, actor_id=None):
    await settle(session, computer, now, "logout")
    owner = await session.scalar(
        select(ComputerOwnership).where(ComputerOwnership.computer_id == computer.id)
    )
    if owner:
        await transfer_resources(
            session, computer, (owner.user_id, owner.company_id), (None, None), now, actor_id
        )
        await session.delete(owner)
    if computer.certificate_sha256:
        certificate = await session.get(DeviceCertificate, computer.certificate_sha256)
        certificate.revoked_at = now
    computer.session_id = None
    await session.execute(delete(AgentCredential).where(AgentCredential.computer_id == computer.id))


@user_router.post("/computers/{computer_id}/unbind")
async def recover_unbind(
    computer_id: UUID, data: Cleanup, request: Request, idempotency_key: str = Header()
):
    """Recover from a lost device key after the owner confirms local sandbox cleanup."""
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        payload = data.model_dump(mode="json")
        operation = f"owner_unbind:{computer_id}"
        # A response lost during recovery must remain recoverable after a fresh login.
        identity = digest("powercell.owner-recovery.v1:" + principal.key)
        previous = await mutation_replay(
            session, request, operation, idempotency_key, payload, credential_hash=identity
        )
        if previous:
            return previous
        computer = await computer_for_user(session, principal, computer_id, admin=True)
        await detach_computer(session, computer, request.app.state.clock(), principal.actor_id)
        response = {"unbound": True, "computer_id": computer.id}
        mutation_save(
            session,
            request,
            computer,
            operation,
            idempotency_key,
            payload,
            response,
            credential_hash=identity,
        )
        return response


@router.post("/disconnect", openapi_extra=AGENT_SESSION_DOCS)
async def disconnect(data: Disconnect, request: Request, idempotency_key: str = Header()):
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request, require_session=False)
        payload = data.model_dump(mode="json")
        previous = await mutation_replay(session, request, "disconnect", idempotency_key, payload)
        if previous:
            return previous
        # Exact retries work after disconnect, but a stale session cannot disconnect a new one.
        await agent_auth(session, request)
        await settle(session, computer, request.app.state.clock(), data.reason)
        response = {"disconnected": True, "computer_id": computer.id}
        mutation_save(session, request, computer, "disconnect", idempotency_key, payload, response)
        return response


@router.post("/unbind")
async def unbind(data: Cleanup, request: Request, idempotency_key: str = Header()):
    async with request.app.state.db.write() as session:
        payload = data.model_dump(mode="json")
        previous = await mutation_replay(session, request, "unbind", idempotency_key, payload)
        if previous:
            # Only this operation accepts its revoked credential, for this exact request.
            return previous
        computer = await agent_auth(session, request, require_session=False)
        now = request.app.state.clock()
        await detach_computer(session, computer, now)
        response = {"unbound": True, "computer_id": computer.id}
        mutation_save(session, request, computer, "unbind", idempotency_key, payload, response)
        return response


async def bounded_body(request, limit):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "Request exceeds the configured size limit")
        body.extend(chunk)
    # Proof verification and the handler must see exactly the same bytes.
    request._body = bytes(body)
    return request._body


@router.put(
    "/diagnostics/{diagnostic_id}",
    response_model=DiagnosticAccepted,
    openapi_extra=BINARY_UPLOAD_DOCS,
)
async def diagnostic(diagnostic_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        await agent_auth(session, request, require_session=False)
    settings = request.app.state.settings
    content = await bounded_body(request, settings.max_diagnostic_bytes)
    try:
        data = DiagnosticRecord.model_validate_json(content)
    except ValidationError as error:
        raise HTTPException(422, "Expected a UTF-8 Power Cell diagnostic record") from error
    if data.id != diagnostic_id:
        raise HTTPException(422, "Diagnostic ID does not match its URL")
    checksum = hashlib.sha256(content).hexdigest()
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request, require_session=False)
        now = request.app.state.clock()
        await session.execute(delete(AgentDiagnostic).where(AgentDiagnostic.expires_at <= now))
        previous = await session.get(AgentDiagnostic, (computer.id, str(diagnostic_id)))
        if previous:
            if previous.sha256 != checksum:
                raise HTTPException(409, "Diagnostic ID already has different contents")
            return {"id": previous.id, "sha256": previous.sha256}
        owner = await session.scalar(
            select(ComputerOwnership).where(ComputerOwnership.computer_id == computer.id)
        )
        if data.task_id:
            task = await session.get(AgentTask, str(data.task_id))
            if (
                not task
                or task.computer_id != computer.id
                or (data.session_id and task.session_id != str(data.session_id))
            ):
                raise HTTPException(404, "Task not assigned to this computer and session")
            owner = await session.get(Isolate, task.isolate_id)
        if not owner:
            raise HTTPException(409, "Computer needs an owner for diagnostics")
        # Session-only diagnostic metadata is an untrusted historical label, not authorization.
        if data.created > now + timedelta(minutes=5) or data.created < now - timedelta(
            days=settings.diagnostic_retention_days
        ):
            raise HTTPException(422, "Diagnostic timestamp is outside the retention window")
        used = await session.scalar(
            select(func.coalesce(func.sum(AgentDiagnostic.size_bytes), 0)).where(
                AgentDiagnostic.computer_id == computer.id
            )
        )
        if used + len(content) > settings.max_computer_diagnostics_bytes:
            raise HTTPException(409, "Computer diagnostic quota exceeded")
        session.add(
            AgentDiagnostic(
                computer_id=computer.id,
                user_id=owner.user_id,
                company_id=owner.company_id,
                id=str(diagnostic_id),
                content=content,
                size_bytes=len(content),
                sha256=checksum,
                session_id=str(data.session_id) if data.session_id else None,
                task_id=str(data.task_id) if data.task_id else None,
                created_at=data.created,
                expires_at=data.created + timedelta(days=settings.diagnostic_retention_days),
            )
        )
        return {"id": str(diagnostic_id), "sha256": checksum}


@user_router.get("/computers/{computer_id}/diagnostics/{diagnostic_id}", response_class=Response)
async def read_diagnostic(computer_id: UUID, diagnostic_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        await computer_for_user(session, principal, computer_id, admin=True)
        row = await session.get(AgentDiagnostic, (str(computer_id), str(diagnostic_id)))
        if not row or row.expires_at <= request.app.state.clock():
            raise HTTPException(404, "Diagnostic not found")
        await owner_access(session, principal, row, admin=True)
        await session.refresh(row, ["content"])
        return Response(
            row.content,
            media_type="application/octet-stream",
            headers={"X-Content-SHA256": row.sha256},
        )


async def prune_protocol(session, now):
    for model in (ProtocolNonce, AgentMutation, AgentDiagnostic):
        await session.execute(delete(model).where(model.expires_at <= now))
