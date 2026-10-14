"""Capacity changes and history are written inside the caller's serialized transaction."""

from fastapi import HTTPException
from sqlalchemy import func, select

from throng_api.availability import sync_capacity
from throng_api.models import (
    CPU_RESOURCE_ID,
    MEMORY_RESOURCE_ID,
    Computer,
    ComputerOwnership,
    ComputerResource,
    ComputerResourceChange,
    Coordination,
)


async def computer_owner(session, computer_id):
    owner = await session.scalar(
        select(ComputerOwnership).where(ComputerOwnership.computer_id == computer_id)
    )
    return (owner.user_id, owner.company_id) if owner else (None, None)


async def change_resource(
    session,
    row,
    now,
    reason,
    *,
    actor_id=None,
    total=None,
    occupied=None,
    deleted=None,
    before_owner=None,
    after_owner=None,
    new=False,
):
    owner = (
        await computer_owner(session, row.computer_id)
        if before_owner is None or after_owner is None
        else None
    )
    before_owner = before_owner if before_owner is not None else owner
    after_owner = after_owner if after_owner is not None else owner
    was_deleted = row.deleted_at is not None
    before = (
        (0, 0, None, None)
        if new
        else (
            0 if was_deleted else row.total,
            0 if was_deleted else row.occupied,
            *before_owner,
        )
    )
    next_total = row.total if total is None else total
    next_occupied = row.occupied if occupied is None else occupied
    next_deleted = was_deleted if deleted is None else deleted
    if not 0 <= next_occupied <= next_total or (next_deleted and next_occupied):
        raise HTTPException(409, "Resource capacity is already reserved")
    after = (0 if next_deleted else next_total, 0 if next_deleted else next_occupied, *after_owner)
    if not new and before == after and was_deleted == next_deleted:
        return row
    if "resource_time" not in session.info:
        last_time = await session.scalar(select(func.max(ComputerResourceChange.created_at)))
        session.info["resource_time"] = max(now, last_time) if last_time else now
        session.info["resource_revision"] = await session.scalar(
            select(Coordination.revision).where(Coordination.id == 1)
        )
    timestamp = session.info["resource_time"]
    row.total, row.occupied = next_total, next_occupied
    row.deleted_at = timestamp if next_deleted else None
    row.version = 1 if new else row.version + 1
    session.add(row)
    await session.flush()
    session.add(
        ComputerResourceChange(
            computer_resource_id=row.id,
            computer_id=row.computer_id,
            resource_type_id=row.resource_type_id,
            version=row.version,
            revision=session.info["resource_revision"],
            created_at=timestamp,
            before_total=before[0],
            before_occupied=before[1],
            before_user_id=before[2],
            before_company_id=before[3],
            after_total=after[0],
            after_occupied=after[1],
            after_user_id=after[2],
            after_company_id=after[3],
            reason=reason,
            actor_id=actor_id,
        )
    )
    await session.flush()
    if row.resource_type_id in {CPU_RESOURCE_ID, MEMORY_RESOURCE_ID} and (new or before != after):
        await sync_capacity(session, await session.get(Computer, row.computer_id), now)
    return row


async def add_resource(session, computer, type_id, total, now, actor_id=None):
    existing = next((row for row in computer.resources if row.resource_type_id == type_id), None)
    if existing:
        if existing.deleted_at is None:
            raise HTTPException(409, "Computer already has this resource type")
        return await change_resource(
            session, existing, now, "restored", total=total, deleted=False, actor_id=actor_id
        )
    row = ComputerResource(
        computer_id=computer.id,
        resource_type_id=type_id,
        total=total,
        occupied=0,
        version=1,
        created_at=now,
    )
    # Keep compatibility properties consistent with the same identity-mapped rows.
    computer.resources.append(row)
    return await change_resource(session, row, now, "created", new=True, actor_id=actor_id)


async def transfer_resources(session, computer, before, after, now, actor_id):
    for row in computer.resources:
        if row.deleted_at is None:
            await change_resource(
                session,
                row,
                now,
                "ownership_changed",
                before_owner=before,
                after_owner=after,
                actor_id=actor_id,
            )
