"""Owner-scoped inventory and step histories with stable revision pagination."""

from collections import defaultdict
from datetime import UTC

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select

from throng_api.models import (
    CPU_RESOURCE_ID,
    MEMORY_RESOURCE_ID,
    Computer,
    ComputerOwnership,
    ComputerResource,
    Coordination,
    ResourceType,
)
from throng_api.models import (
    ComputerResourceChange as Change,
)
from throng_api.security import company_access, personal


async def report_owner(session, principal, *, user_id=None, company_id=None):
    if company_id is not None:
        await company_access(session, principal, company_id)
        return (None, str(company_id))
    if principal.user_id != str(user_id):
        raise HTTPException(404, "Resource not found")
    return (personal(principal), None)


def amounts(types, totals):
    return [
        {
            "resource_type_id": item.id,
            "name": item.name,
            "code": item.code,
            "unit": item.unit,
            "total": totals[item.id][0],
            "occupied": totals[item.id][1],
            "free": totals[item.id][2]
            if len(totals[item.id]) == 3
            else totals[item.id][0] - totals[item.id][1],
        }
        for item in types
        if item.id in totals
    ]


async def summary(session, owner, now, only_available):
    condition = (
        ComputerOwnership.user_id == owner[0]
        if owner[0]
        else ComputerOwnership.company_id == owner[1]
    )
    target = case(
        (ComputerResource.resource_type_id == CPU_RESOURCE_ID, Computer.target_cpu),
        (ComputerResource.resource_type_id == MEMORY_RESOURCE_ID, Computer.target_memory),
        else_=None,
    )
    offered = case((target < ComputerResource.total, target), else_=ComputerResource.total)
    statement = (
        select(
            ComputerResource.resource_type_id,
            offered if only_available else ComputerResource.total,
            ComputerResource.occupied,
        )
        .join(Computer, Computer.id == ComputerResource.computer_id)
        .join(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
        .where(condition, ComputerResource.deleted_at.is_(None))
    )
    if only_available:
        statement = statement.where(
            Computer.enabled.is_(True),
            Computer.accepting_work.is_(True),
            Computer.online.is_(True),
            Computer.session_id.is_not(None),
            Computer.lease_expires_at > now,
        )
    totals = defaultdict(lambda: [0, 0, 0])
    # Python integers keep large multi-computer totals exact on both database backends.
    computer_query = (
        select(func.count()).select_from(Computer).join(ComputerOwnership).where(condition)
    )
    if only_available:
        computer_query = computer_query.where(
            Computer.enabled.is_(True),
            Computer.accepting_work.is_(True),
            Computer.online.is_(True),
            Computer.session_id.is_not(None),
            Computer.lease_expires_at > now,
        )
    rows = await session.execute(statement)
    for type_id, total, occupied in rows:
        totals[type_id][0] += total
        totals[type_id][1] += occupied
        totals[type_id][2] += max(0, total - occupied)
    types = (
        await session.scalars(
            select(ResourceType).where(ResourceType.id.in_(totals)).order_by(ResourceType.code)
        )
    ).all()
    return {
        "as_of": now,
        "only_available": only_available,
        "resources": amounts(types, totals),
        "computer_count": await session.scalar(computer_query),
    }


async def history(
    session,
    owner,
    start,
    end,
    *,
    resource_type_id=None,
    resource_id=None,
    limit=500,
    after_revision=0,
    snapshot_revision=None,
):
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise HTTPException(422, "from and to must be timezone-aware, with from < to")
    try:
        start, end = start.astimezone(UTC), end.astimezone(UTC)
    except OverflowError as error:
        raise HTTPException(
            422, "from and to must remain within the datetime range in UTC"
        ) from error
    current_revision = await session.scalar(
        select(Coordination.revision).where(Coordination.id == 1)
    )
    snapshot = current_revision if snapshot_revision is None else snapshot_revision
    if (
        snapshot > current_revision
        or after_revision > snapshot
        or (after_revision and snapshot_revision is None)
    ):
        raise HTTPException(422, "Pagination requires a valid snapshot_revision and after_revision")
    before_match = (
        Change.before_user_id == owner[0] if owner[0] else Change.before_company_id == owner[1]
    )
    after_match = (
        Change.after_user_id == owner[0] if owner[0] else Change.after_company_id == owner[1]
    )
    filters = [or_(before_match, after_match), Change.revision <= snapshot, Change.created_at < end]
    if resource_type_id:
        filters.append(Change.resource_type_id == str(resource_type_id))
    if resource_id:
        filters.append(Change.computer_resource_id == str(resource_id))
    # Before the migration's baseline the old database contains no trustworthy history.
    available_from = await session.scalar(
        select(func.max(Change.created_at)).where(
            Change.reason == "migration", Change.revision <= snapshot
        )
    )
    beginning = max(start, available_from) if available_from else start
    response = {
        "from_time": start,
        "to_time": end,
        "available_from": available_from,
        "initial_at": beginning if beginning < end else None,
        "initial": [],
        "points": [],
        "snapshot_revision": snapshot,
        "next_after_revision": None,
    }
    if beginning >= end:
        return response
    baseline = or_(Change.created_at <= beginning, Change.revision <= after_revision)
    # Each event contains a complete snapshot. Load only the last matching snapshot
    # per resource, including exits from this owner (whose contribution is zero).
    # Joining today's ownership would disclose or rewrite another owner's past.
    totals = defaultdict(lambda: [0, 0])
    latest = (
        select(Change.computer_resource_id, func.max(Change.version).label("version"))
        .where(*filters, baseline)
        .group_by(Change.computer_resource_id)
        .subquery()
    )
    initial_statement = select(Change).join(
        latest,
        and_(
            Change.computer_resource_id == latest.c.computer_resource_id,
            Change.version == latest.c.version,
        ),
    )
    initial_rows = await session.scalars(initial_statement)
    for change in initial_rows:
        total = totals[change.resource_type_id]
        if (change.after_user_id, change.after_company_id) == owner:
            total[0] += change.after_total
            total[1] += change.after_occupied
        if after_revision:
            response["initial_at"] = max(response["initial_at"], change.created_at)
    window = and_(Change.created_at > beginning, Change.revision > after_revision)
    revisions = (
        await session.scalars(
            select(Change.revision)
            .where(*filters, window)
            .distinct()
            .order_by(Change.revision)
            .limit(limit + 1)
        )
    ).all()
    if len(revisions) > limit:
        response["next_after_revision"] = revisions[limit - 1]
        revisions = revisions[:limit]
    changes = (
        (
            await session.scalars(
                select(Change)
                .where(*filters, Change.revision.in_(revisions))
                .order_by(Change.revision, Change.version, Change.id)
            )
        ).all()
        if revisions
        else []
    )
    type_ids = set(totals) | {change.resource_type_id for change in changes}
    types = (
        await session.scalars(
            select(ResourceType).where(ResourceType.id.in_(type_ids)).order_by(ResourceType.code)
        )
    ).all()
    response["initial"] = amounts(types, totals)
    grouped = defaultdict(list)
    for change in changes:
        grouped[change.revision].append(change)
    for revision, entries in grouped.items():
        for change in entries:
            for side, sign in (("before", -1), ("after", 1)):
                if (
                    getattr(change, f"{side}_user_id"),
                    getattr(change, f"{side}_company_id"),
                ) == owner:
                    totals[change.resource_type_id][0] += sign * getattr(change, f"{side}_total")
                    totals[change.resource_type_id][1] += sign * getattr(change, f"{side}_occupied")
        response["points"].append(
            {
                "at": entries[0].created_at,
                "revision": revision,
                "resources": amounts(types, totals),
            }
        )
    return response
