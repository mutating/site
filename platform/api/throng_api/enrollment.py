"""User-authorized computer registration with a client-generated device id.

There is no hardware attestation, ACME profile or possession proof here. The
installation announces its opaque id at login and reuses it when registering a
computer. The server only has to keep that association and issue one durable
agent credential per registration.
"""

import base64
import hashlib
from datetime import timedelta
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select

from throng_api.execution import remember, replay
from throng_api.models import (
    CPU_RESOURCE_ID,
    MEMORY_RESOURCE_ID,
    AgentCredential,
    AgentEnrollment,
    Computer,
    ComputerOwnership,
    Device,
    DeviceAccount,
    new_id,
)
from throng_api.powercell import bounded_body
from throng_api.powercell_schemas import Binding, EnrollmentCreate, EnrollmentStatus
from throng_api.resource_ledger import add_resource
from throng_api.security import (
    access_token_scheme,
    authenticate,
    digest,
    owner_values,
    personal,
    secret,
)
from throng_api.workload_images import IMAGE_ALIASES

router = APIRouter(
    prefix="/v1", tags=["Power Cell enrollment"], dependencies=[Depends(access_token_scheme)]
)
public_router = APIRouter(tags=["Power Cell capabilities"])
EXTENSIONS = [
    "powercell.enrollment.v1",
    "powercell.device.v1",
    "powercell.capacity.v1",
    "powercell.eviction.v1",
    "powercell.diagnostics.v1",
    "powercell.recovery.v1",
    "powercell.isolation.v1",
]


def cipher(settings):
    """Build the at-rest cipher used only to recover an idempotent finish reply."""
    if not settings.enrollment_secret:
        return None
    key = hashlib.sha256(settings.enrollment_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def credential_cipher(request):
    value = request.app.state.enrollment_cipher
    if value is None:
        raise HTTPException(503, "Computer enrollment is not configured")
    return value


@public_router.get("/v1/agent-capabilities")
async def capabilities(request: Request):
    return {
        "protocol_version": 1,
        "extensions": EXTENSIONS + ["powercell.images.v1"],
        "runtime": "powercell-ubuntu-arm64-v1",
        "images": ["ubuntu:24.04"],
        "image_families": ["ghcr.io/actions/actions-runner"],
        "image_aliases": IMAGE_ALIASES,
        "device_registration": "client-uuid-v1",
    }


def enrollment_status(row, now):
    status = "verified"
    if row.expires_at <= now and not row.computer_id:
        status = "expired"
    return {"id": row.id, "status": status, "expires_at": row.expires_at}


async def enrollment_for_user(session, request, enrollment_id, scope="read"):
    principal = await authenticate(session, request, scope)
    user_id = personal(principal)
    row = await session.get(AgentEnrollment, str(enrollment_id))
    if not row or row.actor_id != user_id:
        raise HTTPException(404, "Enrollment not found")
    return row, principal


async def registered_device(session, user_id, client_id):
    """Resolve a device announced by this account, rejecting unregistered claims."""
    device = await session.scalar(select(Device).where(Device.client_key == str(client_id).lower()))
    if device is None or await session.get(DeviceAccount, (device.id, user_id)) is None:
        raise HTTPException(409, "Sign in with this device before registering it")
    return device


@router.post("/agent-enrollments", status_code=201, response_model=EnrollmentStatus)
async def create_enrollment(
    data: EnrollmentCreate, request: Request, idempotency_key: str = Header()
):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        actor_id = personal(principal)
        payload = data.model_dump(mode="json")
        previous = await replay(session, principal.key, "enrollment", idempotency_key, payload)
        now = request.app.state.clock()
        if previous:
            return enrollment_status(await session.get(AgentEnrollment, previous), now)
        device = await registered_device(session, actor_id, data.client_device_id)
        if row := await session.scalar(
            select(AgentEnrollment).where(
                AgentEnrollment.device_id == device.id,
                AgentEnrollment.actor_id == actor_id,
                AgentEnrollment.computer_id.is_(None),
                AgentEnrollment.expires_at > now,
            )
        ):
            # A lost reply can be recovered with a fresh key only when the
            # immutable request is identical. An explicit restart replaces the
            # unfinished attempt instead of stranding this device for an hour.
            if row.request == payload:
                return enrollment_status(row, now)
            await session.delete(row)
            await session.flush()
        count = await session.scalars(
            select(AgentEnrollment).where(
                AgentEnrollment.actor_id == actor_id, AgentEnrollment.expires_at > now
            )
        )
        if len(count.all()) >= 20:
            raise HTTPException(429, "Too many pending enrollments")
        row = AgentEnrollment(
            id=new_id(),
            actor_id=actor_id,
            device_id=device.id,
            request=payload,
            created_at=now,
            expires_at=now + timedelta(seconds=request.app.state.settings.enrollment_seconds),
        )
        session.add(row)
        await session.flush()
        remember(session, principal.key, "enrollment", idempotency_key, payload, row.id)
        return enrollment_status(row, now)


@router.get("/agent-enrollments/{enrollment_id}", response_model=EnrollmentStatus)
async def get_enrollment(enrollment_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        row, _ = await enrollment_for_user(session, request, enrollment_id)
        return enrollment_status(row, request.app.state.clock())


@router.post("/agent-enrollments/{enrollment_id}/finish", response_model=Binding)
async def finish_enrollment(enrollment_id: UUID, request: Request, idempotency_key: str = Header()):
    content = await bounded_body(request, 1024)
    async with request.app.state.db.write() as session:
        row, principal = await enrollment_for_user(session, request, enrollment_id, "manage")
        if content not in (b"", b"{}"):
            raise HTTPException(422, "Enrollment finish accepts only an empty object")
        now = request.app.state.clock()
        if not row.computer_id and row.expires_at <= now:
            raise HTTPException(409, "Enrollment has expired")
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise HTTPException(422, "Idempotency-Key of 1 to 128 characters is required")
        issuer = credential_cipher(request)
        device = await session.get(Device, row.device_id)
        if row.computer_id:
            if row.finish_key != idempotency_key:
                raise HTTPException(409, "Enrollment has already been bound")
            credential = issuer.decrypt(row.credential_encrypted).decode()
            current = await session.scalar(
                select(AgentCredential).where(AgentCredential.computer_id == row.computer_id)
            )
            if not current or current.token_hash != digest(credential):
                raise HTTPException(409, "Enrollment credential has been replaced or revoked")
        else:
            request_values = row.request
            owner = None
            if request_values.get("assign_pool"):
                owner = await owner_values(
                    session, principal, request_values.get("company_id"), admin=True
                )
            values = {
                key: request_values[key]
                for key in ("name", "os", "architecture", "runtime", "disk_bytes", "isolation_modes")
            }
            computer = Computer(
                **values,
                registered_by_user_id=principal.actor_id,
                connected_user_id=principal.user_id if principal.password_login else None,
                resources=[],
                accepting_work=False,
                device_id=device.id,
            )
            session.add(computer)
            await session.flush()
            if owner is not None:
                session.add(ComputerOwnership(computer_id=computer.id, **owner))
                await session.flush()
            for type_id, amount in (
                (CPU_RESOURCE_ID, request_values["cpu_millis"]),
                (MEMORY_RESOURCE_ID, request_values["memory_bytes"]),
            ):
                await add_resource(session, computer, type_id, amount, now, principal.actor_id)
            credential = secret("agt_")
            session.add(AgentCredential(computer_id=computer.id, token_hash=digest(credential)))
            row.computer_id, row.finish_key = computer.id, idempotency_key
            row.credential_encrypted = issuer.encrypt(credential.encode())
        return {
            "computer_id": row.computer_id,
            "credential": credential,
            "device_id": device.id,
        }
