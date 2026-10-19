"""State transitions. Call exclusively inside Database.write() transactions."""

import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from throng_api.availability import sync_capacity
from throng_api.models import (
    CPU_RESOURCE_ID,
    MEMORY_RESOURCE_ID,
    AgentTask,
    Command,
    CommandResult,
    Computer,
    ComputerOwnership,
    ComputerResource,
    Idempotency,
    Isolate,
    TaskArtifact,
    new_id,
)
from throng_api.resource_ledger import change_resource
from throng_api.transport import ProtocolError
from throng_api.workload_images import supports_image

TERMINAL = frozenset({"completed", "cancelled", "timed_out", "isolate_failed", "failed"})
TASK_TERMINAL = frozenset({"completed", "failed", "obsolete"})


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


async def replay(session, namespace, operation, key, payload):
    if not key or len(key) > 128 or not key.strip():
        raise HTTPException(422, "Idempotency-Key of 1 to 128 characters is required")
    row = await session.get(Idempotency, (namespace, operation, key))
    if row and row.request_hash != fingerprint(payload):
        raise HTTPException(409, "Idempotency key was used with a different request")
    return row.resource_id if row else None


def remember(session, namespace, operation, key, payload, resource_id):
    session.add(
        Idempotency(
            principal=namespace,
            operation=operation,
            key=key,
            request_hash=fingerprint(payload),
            resource_id=resource_id,
        )
    )


async def add_task(session, isolate, kind, now, timeout_seconds, command=None, payload=None):
    deadline = now + timedelta(seconds=timeout_seconds)
    if kind == "create":
        deadline = min(deadline, isolate.provisioning_deadline)
    task = AgentTask(
        id=new_id(),
        computer_id=isolate.computer_id,
        session_id=isolate.session_id,
        isolate_id=isolate.id,
        command_id=command.id if command else None,
        kind=kind,
        status="pending",
        payload=payload or {},
        created_at=now,
        deadline=deadline,
    )
    session.add(task)
    await session.flush()
    return task


async def finish(
    session,
    command,
    status,
    now,
    *,
    returncode=None,
    stdout=None,
    stderr=None,
    error=None,
    stdout_artifact_id=None,
    stderr_artifact_id=None,
):
    if command.status in TERMINAL:
        return
    command.status = status
    command.finished_at = now
    session.add(
        CommandResult(
            command_id=command.id,
            success=status == "completed" and returncode == 0,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            error=error,
            stdout_artifact_id=stdout_artifact_id,
            stderr_artifact_id=stderr_artifact_id,
            created_at=now,
        )
    )
    tasks = (
        await session.scalars(
            select(AgentTask).where(
                AgentTask.command_id == command.id, AgentTask.status.not_in(TASK_TERMINAL)
            )
        )
    ).all()
    for task in tasks:
        task.status = "obsolete"
        task.finished_at = now
    await session.flush()


async def request_cancel(session, command, now, settings, reason="user"):
    if command.status in TERMINAL or command.status == "cancel_requested":
        return
    command.cancel_reason = reason
    command.cancel_requested_at = now
    # An undelivered run task can safely be withdrawn in this same transaction.
    run_task = await session.scalar(
        select(AgentTask).where(AgentTask.command_id == command.id, AgentTask.kind == "run")
    )
    if command.status == "queued" or (run_task and run_task.delivered_at is None):
        await finish(
            session, command, "timed_out" if reason == "timeout" else "cancelled", now, error=reason
        )
        return
    command.status = "cancel_requested"
    isolate = await session.get(Isolate, command.isolate_id)
    cancel_task = await add_task(
        session,
        isolate,
        "cancel",
        now,
        settings.cancellation_timeout_seconds,
        command=command,
        payload={"reason": reason, "run_task_id": run_task.id if run_task else None},
    )
    if run_task:
        # The stop confirmation now governs both tasks, including a delivered
        # run whose original start acknowledgement deadline is about to expire.
        run_task.deadline = cancel_task.deadline


async def release_reservation(session, isolate, now):
    if isolate.reserved:
        computer = await session.get(Computer, isolate.computer_id)
        for type_id, quantity in (
            (CPU_RESOURCE_ID, isolate.cpu_millis),
            (MEMORY_RESOURCE_ID, isolate.memory_bytes),
        ):
            row = computer.resource(type_id)
            await change_resource(session, row, now, "released", occupied=row.occupied - quantity)
        computer.reserved_disk -= isolate.disk_bytes
        isolate.reserved = False


async def ensure_destroy(session, isolate, now, settings):
    if not isolate.reserved:
        if isolate.status == "destroying":
            isolate.status = "destroyed"
        isolate.destroyed_at = isolate.destroyed_at or now
        return
    computer = await session.get(Computer, isolate.computer_id)
    if (
        not computer.online
        or computer.session_id != isolate.session_id
        or computer.lease_expires_at <= now
    ):
        return  # Retain the reservation until a confirmed clean start.
    existing = await session.scalar(
        select(AgentTask.id).where(
            AgentTask.isolate_id == isolate.id,
            AgentTask.kind == "destroy",
            AgentTask.status.not_in(TASK_TERMINAL),
        )
    )
    if not existing:
        await add_task(session, isolate, "destroy", now, settings.cancellation_timeout_seconds)


async def destroy_isolate(session, isolate, now, settings, reason="isolate_deleted"):
    if isolate.status == "destroyed":
        return
    isolate.status = "destroying"
    commands = (
        await session.scalars(
            select(Command).where(Command.isolate_id == isolate.id, Command.status.not_in(TERMINAL))
        )
    ).all()
    for command in commands:
        await request_cancel(session, command, now, settings, reason)
    # Withdraw undelivered create/snapshot work; destroy supersedes it.
    tasks = (
        await session.scalars(
            select(AgentTask).where(
                AgentTask.isolate_id == isolate.id,
                AgentTask.kind.in_(["create", "snapshot"]),
                AgentTask.status.not_in(TASK_TERMINAL),
            )
        )
    ).all()
    for task in tasks:
        task.status = "obsolete"
        task.finished_at = now
    await ensure_destroy(session, isolate, now, settings)


async def fail_isolate(session, isolate, now, reason):
    if isolate.status == "destroyed":
        return
    isolate.status = "failed"
    isolate.error = reason
    commands = (
        await session.scalars(
            select(Command).where(Command.isolate_id == isolate.id, Command.status.not_in(TERMINAL))
        )
    ).all()
    for command in commands:
        await finish(session, command, "isolate_failed", now, error=reason)
    tasks = (
        await session.scalars(
            select(AgentTask).where(
                AgentTask.isolate_id == isolate.id, AgentTask.status.not_in(TASK_TERMINAL)
            )
        )
    ).all()
    for task in tasks:
        task.status = "obsolete"
        task.finished_at = now
        task.error = reason


async def invalidate_computer(session, computer, now, reason):
    computer.online = False
    await sync_capacity(session, computer, now)
    isolates = (
        await session.scalars(
            select(Isolate).where(Isolate.computer_id == computer.id, Isolate.reserved.is_(True))
        )
    ).all()
    for isolate in isolates:
        await fail_isolate(session, isolate, now, reason)
    return isolates


async def clean_start(session, computer, now, settings):
    for isolate in await invalidate_computer(session, computer, now, "agent_restarted"):
        await release_reservation(session, isolate, now)
        isolate.destroyed_at = now
    computer.capacity_revision = -1
    computer.capacity_report = None
    # An attested agent must publish policy before the first assignment in every session.
    computer.accepting_work = computer.certificate_sha256 is None
    computer.target_cpu = computer.target_memory = computer.target_disk = None
    computer.session_id = new_id()
    computer.online = True
    computer.last_seen_at = now
    computer.lease_expires_at = now + timedelta(seconds=settings.agent_lease_seconds)
    await sync_capacity(session, computer, now)


async def task_for_agent(session, task_id, computer):
    task = await session.get(AgentTask, str(task_id))
    if not task or task.computer_id != computer.id:
        raise HTTPException(404, "Task not found")
    if task.session_id != computer.session_id:
        raise HTTPException(409, "Task belongs to an obsolete session")
    return task


def check_new_work(isolate, kind, now):
    """Check renewable isolate lifetime independently of controller scheduling."""
    if kind not in {"create", "run", "snapshot"}:
        return
    expected_status = "provisioning" if kind == "create" else "ready"
    if isolate.status != expected_status or isolate.expires_at <= now:
        raise HTTPException(409, "Isolate no longer accepts new work")
    if kind == "create" and isolate.provisioning_deadline <= now:
        raise HTTPException(409, "Isolate provisioning deadline has expired")


async def complete_task(session, task, report, now, settings):
    if task.kind != "run" and report.outcome not in {"completed", "failed"}:
        raise HTTPException(422, "Only run tasks can report cancellation or a local timeout")
    report_hash = fingerprint(report.model_dump())
    if task.completion_hash:
        if task.completion_hash != report_hash:
            raise ProtocolError("completion_conflict", "Task already has a different completion")
        return
    if task.status in TASK_TERMINAL:
        raise ProtocolError("task_terminal", "Task is no longer active")
    if task.deadline <= now:
        raise ProtocolError("task_expired", "Task deadline has expired")
    if task.delivered_at is None:
        raise HTTPException(409, "Task has not been delivered")
    isolate = await session.get(Isolate, task.isolate_id)
    command = await session.get(Command, task.command_id) if task.command_id else None
    attachments = {
        row.purpose: row.artifact_id
        for row in (
            await session.scalars(select(TaskArtifact).where(TaskArtifact.task_id == task.id))
        ).all()
    }
    if task.kind == "run":
        if report.outcome == "completed" and report.returncode is None:
            raise HTTPException(422, "A completed command requires returncode")
        if report.outcome == "cancelled" and command.status != "cancel_requested":
            raise HTTPException(409, "Cancellation was not requested")
        status = report.outcome
        if status == "timed_out":
            command.cancel_reason = "timeout"
        if status == "cancelled" and command.cancel_reason == "timeout":
            status = "timed_out"
        await finish(
            session,
            command,
            status,
            now,
            returncode=report.returncode,
            stdout=report.stdout,
            stderr=report.stderr,
            error=report.error,
            stdout_artifact_id=attachments.get("stdout"),
            stderr_artifact_id=attachments.get("stderr"),
        )
    elif task.kind == "cancel":
        if report.outcome != "completed":
            await fail_isolate(session, isolate, now, report.error or "cancellation_failed")
        else:
            # finish() owns terminal-result idempotency, including stale callbacks.
            await finish(
                session,
                command,
                "timed_out" if command.cancel_reason == "timeout" else "cancelled",
                now,
                returncode=report.returncode,
                stdout=report.stdout,
                stderr=report.stderr,
                error=command.cancel_reason,
                stdout_artifact_id=attachments.get("stdout"),
                stderr_artifact_id=attachments.get("stderr"),
            )
    elif task.kind == "create":
        if report.outcome == "completed" and isolate.status == "provisioning":
            check_new_work(isolate, "create", now)
            isolate.status = "ready"
        else:
            await fail_isolate(session, isolate, now, report.error or "provisioning_failed")
    elif task.kind == "snapshot":
        if report.outcome == "completed":
            if "snapshot" not in attachments:
                raise HTTPException(422, "Upload the snapshot before completing its task")
            task.artifact_id = attachments["snapshot"]
    elif task.kind == "destroy":
        if report.outcome == "completed":
            commands = (
                await session.scalars(
                    select(Command).where(
                        Command.isolate_id == isolate.id, Command.status.not_in(TERMINAL)
                    )
                )
            ).all()
            for pending in commands:
                await finish(
                    session,
                    pending,
                    "timed_out" if pending.cancel_reason == "timeout" else "cancelled",
                    now,
                    error=pending.cancel_reason or "isolate_deleted",
                )
            await release_reservation(session, isolate, now)
            isolate.destroyed_at = now
            if isolate.status == "destroying":
                isolate.status = "destroyed"
            others = (
                await session.scalars(
                    select(AgentTask).where(
                        AgentTask.isolate_id == isolate.id, AgentTask.status.not_in(TASK_TERMINAL)
                    )
                )
            ).all()
            for other in others:
                other.status = "obsolete"
                other.finished_at = now
        else:
            # A worker unable to destroy a sandbox cannot safely receive more work.
            computer = await session.get(Computer, task.computer_id)
            await invalidate_computer(session, computer, now, report.error or "cleanup_failed")
    else:
        raise HTTPException(409, "Unknown task kind")
    task.status = "completed" if report.outcome != "failed" else "failed"
    task.completion_hash = report_hash
    task.finished_at = now
    task.error = report.error
    if isolate.status == "failed":
        await ensure_destroy(session, isolate, now, settings)


async def allocate_isolate(session, isolate, now, settings, *, computer_ids=()):
    """Select a worker and reserve capacity inside the caller's write transaction."""
    cpu, memory = aliased(ComputerResource), aliased(ComputerResource)
    owner_match = (
        ComputerOwnership.user_id == isolate.user_id
        if isolate.user_id
        else ComputerOwnership.company_id == isolate.company_id
    )
    candidates = await session.scalars(
        select(Computer)
        .join(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
        .join(
            cpu,
            (cpu.computer_id == Computer.id)
            & (cpu.resource_type_id == CPU_RESOURCE_ID)
            & cpu.deleted_at.is_(None),
        )
        .join(
            memory,
            (memory.computer_id == Computer.id)
            & (memory.resource_type_id == MEMORY_RESOURCE_ID)
            & memory.deleted_at.is_(None),
        )
        .where(
            owner_match,
            Computer.enabled.is_(True),
            Computer.accepting_work.is_(True),
            Computer.online.is_(True),
            Computer.lease_expires_at > now,
            Computer.os == isolate.os,
            Computer.architecture == isolate.architecture,
            Computer.runtime == isolate.runtime,
            Computer.id.in_(computer_ids) if computer_ids else True,
            func.coalesce(Computer.target_cpu, cpu.total) - cpu.occupied >= isolate.cpu_millis,
            func.coalesce(Computer.target_memory, memory.total) - memory.occupied
            >= isolate.memory_bytes,
            func.coalesce(Computer.target_disk, Computer.disk_bytes) - Computer.reserved_disk
            >= isolate.disk_bytes,
            cpu.total - cpu.occupied >= isolate.cpu_millis,
            memory.total - memory.occupied >= isolate.memory_bytes,
            Computer.disk_bytes - Computer.reserved_disk >= isolate.disk_bytes,
        )
        .order_by(cpu.occupied, Computer.id)
    )
    computer = next(
        (
            candidate
            for candidate in candidates
            if supports_image(candidate, isolate.image)
            and (
                not candidate.isolation_modes
                or (isolate.isolation_mode or candidate.default_isolation)
                in candidate.isolation_modes
            )
        ),
        None,
    )
    if not computer:
        return False
    for type_id, quantity in (
        (CPU_RESOURCE_ID, isolate.cpu_millis),
        (MEMORY_RESOURCE_ID, isolate.memory_bytes),
    ):
        row = computer.resource(type_id)
        await change_resource(session, row, now, "reserved", occupied=row.occupied + quantity)
    computer.reserved_disk += isolate.disk_bytes
    isolate.computer_id = computer.id
    isolate.session_id = computer.session_id
    isolate.reserved = True
    isolate.effective_isolation = isolate.isolation_mode or computer.default_isolation
    isolate.network_enabled = computer.network_enabled
    isolate.internet_only = computer.internet_only
    await add_task(
        session,
        isolate,
        "create",
        now,
        settings.provisioning_timeout_seconds,
        payload={
            "state_artifact_id": isolate.state_artifact_id,
            "image": isolate.image,
            "isolation_mode": isolate.effective_isolation,
            "retry_allowed": isolate.retry_allowed,
            "network_enabled": isolate.network_enabled,
            "internet_only": isolate.internet_only,
            "os": isolate.os,
            "architecture": isolate.architecture,
            "runtime": isolate.runtime,
            "cpu_millis": isolate.cpu_millis,
            "memory_bytes": isolate.memory_bytes,
            "disk_bytes": isolate.disk_bytes,
        },
    )
    return True


async def tick(session, now, settings):
    expired_computers = (
        await session.scalars(
            select(Computer).where(Computer.online.is_(True), Computer.lease_expires_at <= now)
        )
    ).all()
    for computer in expired_computers:
        await invalidate_computer(session, computer, now, "agent_lease_expired")

    isolates = (
        await session.scalars(
            select(Isolate).where(
                or_(
                    Isolate.status.in_(["provisioning", "ready", "destroying"]),
                    and_(
                        Isolate.status == "failed",
                        or_(Isolate.reserved.is_(True), Isolate.destroyed_at.is_(None)),
                    ),
                )
            )
        )
    ).all()
    for isolate in isolates:
        if isolate.status in {"provisioning", "ready"} and isolate.expires_at <= now:
            await destroy_isolate(session, isolate, now, settings, "isolate_expired")
        if isolate.status == "provisioning" and isolate.provisioning_deadline <= now:
            await fail_isolate(session, isolate, now, "provisioning_timeout")
        if isolate.status in {"destroying", "failed"}:
            await ensure_destroy(session, isolate, now, settings)

    commands = (await session.scalars(select(Command).where(Command.status.not_in(TERMINAL)))).all()
    for command in commands:
        if command.status == "queued" and command.queued_deadline <= now:
            await finish(session, command, "timed_out", now, error="queue_timeout")
        elif (
            command.status == "running"
            and command.started_at + timedelta(seconds=command.timeout_seconds) <= now
        ):
            await request_cancel(session, command, now, settings, "timeout")
        elif (
            command.status == "cancel_requested"
            and command.cancel_requested_at
            + timedelta(seconds=settings.cancellation_timeout_seconds)
            <= now
        ):
            isolate = await session.get(Isolate, command.isolate_id)
            await fail_isolate(session, isolate, now, "cancellation_unconfirmed")
            await ensure_destroy(session, isolate, now, settings)

    tasks = (
        await session.scalars(
            select(AgentTask).where(
                AgentTask.status.not_in(TASK_TERMINAL), AgentTask.deadline <= now
            )
        )
    ).all()
    for task in tasks:
        # Its isolate may have failed earlier in this tick.
        if task.status in TASK_TERMINAL:
            continue
        isolate = await session.get(Isolate, task.isolate_id)
        if task.kind == "destroy":
            computer = await session.get(Computer, task.computer_id)
            await invalidate_computer(session, computer, now, "cleanup_unconfirmed")
        else:
            await fail_isolate(session, isolate, now, f"{task.kind}_timeout")
            await ensure_destroy(session, isolate, now, settings)

    # One serialized transaction selects a worker and reserves its capacity.
    waiting = (
        await session.scalars(
            select(Isolate)
            .where(Isolate.status == "provisioning", Isolate.computer_id.is_(None))
            .order_by(Isolate.created_at, Isolate.id)
        )
    ).all()
    for isolate in waiting:
        await allocate_isolate(session, isolate, now, settings)

    ready = (await session.scalars(select(Isolate).where(Isolate.status == "ready"))).all()
    for isolate in ready:
        computer = await session.get(Computer, isolate.computer_id)
        if not computer.can_start_work or not computer.online or computer.lease_expires_at <= now:
            continue
        busy = await session.scalar(
            select(AgentTask.id)
            .where(
                AgentTask.isolate_id == isolate.id,
                AgentTask.kind.in_(["create", "run", "snapshot", "destroy"]),
                AgentTask.status.not_in(TASK_TERMINAL),
            )
            .limit(1)
        )
        if busy:
            continue
        command = await session.scalar(
            select(Command)
            .where(Command.isolate_id == isolate.id, Command.status == "queued")
            .order_by(Command.sequence)
            .limit(1)
        )
        if command:
            command.status = "dispatched"
            command.dispatched_at = now
            await add_task(
                session,
                isolate,
                "run",
                now,
                settings.task_ack_timeout_seconds,
                command=command,
                payload={
                    "command": command.command,
                    "shell": command.shell,
                    "cwd": command.cwd,
                    "env": command.env,
                    "timeout_seconds": command.timeout_seconds,
                },
            )
