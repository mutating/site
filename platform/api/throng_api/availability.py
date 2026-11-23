"""Available capacity over time, including lease expiry without controller polling."""

from collections import defaultdict
from datetime import UTC, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, select

from throng_api.models import AvailabilityBaseline, CapacityInterval, Computer, ComputerOwnership


def active_computer_session(computer, now):
    return bool(
        computer.online
        and computer.session_id
        and computer.lease_expires_at
        and computer.lease_expires_at > now
    )


def active_computer_session_filter(now):
    return and_(
        Computer.online.is_(True),
        Computer.session_id.is_not(None),
        Computer.lease_expires_at > now,
    )


def available_computer_filter(now):
    return and_(
        Computer.enabled.is_(True),
        Computer.accepting_work.is_(True),
        active_computer_session_filter(now),
    )


async def sync_capacity(session, computer, now):
    """Called inside the same serialized transaction as every availability mutation."""
    now = max(now, session.info.get("resource_time", now))
    await session.flush()
    current = await session.scalar(
        select(CapacityInterval).where(
            CapacityInterval.computer_id == computer.id, CapacityInterval.closed.is_(False)
        )
    )
    owner = await session.scalar(
        select(ComputerOwnership).where(ComputerOwnership.computer_id == computer.id)
    )
    available = bool(
        owner
        and computer.enabled
        and computer.accepting_work
        and active_computer_session(computer, now)
    )
    value = (
        (
            owner.user_id,
            owner.company_id,
            min(computer.cpu_millis, computer.target_cpu)
            if computer.target_cpu is not None
            else computer.cpu_millis,
            min(computer.memory_bytes, computer.target_memory)
            if computer.target_memory is not None
            else computer.memory_bytes,
            computer.reserved_cpu,
            computer.reserved_memory,
        )
        if owner
        else None
    )
    if current:
        now = max(now, current.starts_at)
        unchanged = value == (
            current.user_id,
            current.company_id,
            current.cpu_millis,
            current.memory_bytes,
            current.cpu_occupied_millis,
            current.memory_occupied_bytes,
        )
        if available and unchanged and current.ends_at >= now:
            current.ends_at = max(current.ends_at, computer.lease_expires_at)
            return
        current.ends_at = max(current.starts_at, min(now, current.ends_at))
        current.closed = True
    if current is None:
        last_end = await session.scalar(
            select(func.max(CapacityInterval.ends_at)).where(
                CapacityInterval.computer_id == computer.id
            )
        )
        if last_end:
            now = max(now, last_end)
    if available and computer.lease_expires_at > now:
        session.add(
            CapacityInterval(
                computer_id=computer.id,
                user_id=value[0],
                company_id=value[1],
                cpu_millis=value[2],
                memory_bytes=value[3],
                cpu_occupied_millis=value[4],
                memory_occupied_bytes=value[5],
                starts_at=now,
                ends_at=computer.lease_expires_at,
                created_at=now,
            )
        )
    await session.flush()


async def capacity_history(session, owner, start, end, now, *, include_free=False):
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise HTTPException(422, "from and to must be timezone-aware, with from < to")
    try:
        start, end = start.astimezone(UTC), end.astimezone(UTC)
    except OverflowError as error:
        raise HTTPException(422, "Time range is outside the supported datetime range") from error
    if end - start > timedelta(days=31):
        raise HTTPException(422, "Request at most 31 days of availability history")
    baseline = await session.get(AvailabilityBaseline, 1)
    beginning = max(start, baseline.available_from)
    ending = min(end, now)
    response = {
        "from_time": start,
        "to_time": end,
        "as_of": now,
        "available_from": baseline.available_from,
        "points": [],
    }
    if beginning >= ending:
        return response
    condition = (
        CapacityInterval.user_id == owner[0]
        if owner[0]
        else CapacityInterval.company_id == owner[1]
    )
    stored_fields = ("cpu_millis", "memory_bytes", "cpu_occupied_millis", "memory_occupied_bytes")
    rows = await session.execute(
        select(
            CapacityInterval.starts_at,
            CapacityInterval.ends_at,
            *(getattr(CapacityInterval, field) for field in stored_fields),
        ).where(
            condition, CapacityInterval.starts_at < ending, CapacityInterval.ends_at > beginning
        )
    )
    fields = stored_fields + (("cpu_free_millis", "memory_free_bytes") if include_free else ())
    deltas = defaultdict(lambda: [0] * len(fields))
    deltas[beginning] = [0] * len(fields)
    for starts_at, ends_at, cpu, memory, occupied_cpu, occupied_memory in rows:
        if ends_at <= starts_at:
            continue
        left = max(starts_at, beginning)
        values = [cpu, memory, occupied_cpu, occupied_memory]
        if include_free:
            values.extend(
                (
                    max(0, cpu - occupied_cpu),
                    max(0, memory - occupied_memory),
                )
            )
        for index, value in enumerate(values):
            deltas[left][index] += value
            if ends_at < ending:
                deltas[ends_at][index] -= value
    totals = [0] * len(fields)
    for at, changes in sorted(deltas.items()):
        totals = [value + delta for value, delta in zip(totals, changes, strict=True)]
        point = {"at": at, **dict(zip(fields, totals, strict=True))}
        previous = response["points"][-1] if response["points"] else None
        if previous is None or any(previous[field] != point[field] for field in fields):
            response["points"].append(point)
    response["points"].append({"at": ending, **dict(zip(fields, totals, strict=True))})
    return response
