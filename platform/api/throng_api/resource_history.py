"""Owner-scoped inventory and step histories with stable revision pagination."""

from collections import defaultdict
from datetime import UTC, timedelta
from itertools import chain, groupby

from fastapi import HTTPException
from sqlalchemy import and_, case, func, literal, or_, select, tuple_, union_all

from throng_api.availability import available_computer_filter, capacity_history
from throng_api.database import company_query_id
from throng_api.models import (
    COMPANY_OWNERSHIP_CHANGED,
    CPU_RESOURCE_ID,
    INVENTORY_CHANGED,
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


def ownership_condition(owner):
    return (
        ComputerOwnership.user_id == owner[0]
        if owner[0]
        else ComputerOwnership.company_id == owner[1]
    )


def offered_resources():
    target = case(
        (ComputerResource.resource_type_id == CPU_RESOURCE_ID, Computer.target_cpu),
        (ComputerResource.resource_type_id == MEMORY_RESOURCE_ID, Computer.target_memory),
        else_=None,
    )
    return case((target < ComputerResource.total, target), else_=ComputerResource.total)


async def summary(session, owner, now, only_available):
    condition = ownership_condition(owner)
    statement = (
        select(
            ComputerResource.resource_type_id,
            offered_resources() if only_available else ComputerResource.total,
            ComputerResource.occupied,
        )
        .join(Computer, Computer.id == ComputerResource.computer_id)
        .join(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
        .where(condition, ComputerResource.deleted_at.is_(None))
    )
    if only_available:
        statement = statement.where(available_computer_filter(now))
    totals = defaultdict(lambda: [0, 0, 0])
    # Python integers keep large multi-computer totals exact on both database backends.
    computer_query = (
        select(func.count()).select_from(Computer).join(ComputerOwnership).where(condition)
    )
    if only_available:
        computer_query = computer_query.where(available_computer_filter(now))
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


async def pool_resource_totals(session, condition, available, *, include_types=True):
    types = []
    inventory, schedulable = {}, {}
    resources = (
        select(
            ComputerResource.resource_type_id.label("type_id"),
            ComputerResource.total,
            ComputerResource.occupied,
            case((available, offered_resources())).label("offered"),
        )
        .join(Computer, Computer.id == ComputerResource.computer_id)
        .join(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
        .where(condition, ComputerResource.deleted_at.is_(None))
    )
    if session.bind.dialect.name == "postgresql":
        # PostgreSQL SUM(bigint) widens to numeric. Return just the totals per type.
        rows = resources.subquery()
        totals = (
            select(
                rows.c.type_id,
                func.sum(rows.c.total).label("total"),
                func.sum(rows.c.occupied).label("occupied"),
                func.sum(rows.c.offered).label("offered"),
                func.sum(case((rows.c.offered.is_not(None), rows.c.occupied))).label(
                    "offered_occupied"
                ),
                func.sum(
                    case(
                        (rows.c.offered > rows.c.occupied, rows.c.offered - rows.c.occupied),
                        else_=0,
                    )
                ).label("free"),
            )
            .group_by(rows.c.type_id)
            .subquery()
        )
        statement = select(totals)
        if include_types:
            statement = (
                statement.add_columns(ResourceType)
                .join(ResourceType, ResourceType.id == totals.c.type_id)
                .order_by(ResourceType.code)
            )
        for (
            type_id,
            total,
            occupied,
            offered,
            offered_occupied,
            free,
            *kind,
        ) in await session.execute(statement):
            types.extend(kind)
            inventory[type_id] = (int(total), int(occupied))
            if offered is not None:
                schedulable[type_id] = (int(offered), int(offered_occupied), int(free))
    else:
        # SQLite SUM overflows signed 64 bits, but each resource can hold 2**60.
        # Read the narrow projection once and still sum exactly with Python integers.
        for type_id, total, occupied, offered in await session.execute(resources):
            full = inventory.setdefault(type_id, [0, 0])
            full[0] += total
            full[1] += occupied
            if offered is not None:
                free = schedulable.setdefault(type_id, [0, 0, 0])
                free[0] += offered
                free[1] += occupied
                free[2] += max(0, offered - occupied)
        if include_types and inventory:
            types = (
                await session.scalars(
                    select(ResourceType)
                    .where(ResourceType.id.in_(inventory))
                    .order_by(ResourceType.code)
                )
            ).all()
    return types, inventory, schedulable


async def pool_summary(session, company_id, now):
    condition = ComputerOwnership.company_id == company_query_id(session, company_id)
    available = available_computer_filter(now)
    types, inventory, schedulable = await pool_resource_totals(session, condition, available)
    computer_counts = await session.execute(
        select(func.count(), func.count(case((available, 1))))
        .select_from(ComputerOwnership)
        .join(Computer, Computer.id == ComputerOwnership.computer_id)
        .where(condition)
    )
    all_computers, available_computers = computer_counts.one()
    return {
        "inventory": {
            "as_of": now,
            "only_available": False,
            "resources": amounts(types, inventory),
            "computer_count": all_computers,
        },
        "available": {
            "as_of": now,
            "only_available": True,
            "resources": amounts(types, schedulable),
            "computer_count": available_computers,
        },
    }


def history_owner_filters(owner):
    return (
        (Change.before_user_id == owner[0], Change.after_user_id == owner[0])
        if owner[0]
        else (Change.before_company_id == owner[1], Change.after_company_id == owner[1])
    )


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
    before_match, after_match = history_owner_filters(owner)
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


POOL_FIELDS = (
    "cpu_total_millis",
    "memory_total_bytes",
    "cpu_free_millis",
    "memory_free_bytes",
)


def chart_points(points, start, end):
    # Keep both edges, each series' extremes and their transitions back. A
    # brief reservation must not be drawn as an outage across a whole bucket.
    buckets = 46
    if len(points) <= 18 * buckets:
        return points
    width = (end - start) / buckets

    def bucket(index):
        point = points[index]
        return min(buckets - 1, (point["at"] - start) // width)

    selected = set()
    extremes = {field: set() for field in POOL_FIELDS}
    for _, group in groupby(range(len(points)), key=bucket):
        interval = list(group)
        selected.update((interval[0], interval[-1]))
        for field, indices in extremes.items():

            def point_value(index, field=field):
                return points[index][field]

            indices.update((min(interval, key=point_value), max(interval, key=point_value)))

    for field, indices in extremes.items():
        selected.update(indices)
        change = len(points)
        next_value = points[-1][field]
        for index in range(len(points) - 2, -1, -1):
            value = points[index][field]
            if value != next_value:
                change = index + 1
            if index in indices and change < len(points):
                selected.add(change)
            next_value = value
    return [points[index] for index in sorted(selected)]


async def pool_inventory(session, company_id, start, end):
    """Read the baseline and each relevant inventory revision once for a chart window."""
    # The chart runs in a database snapshot, so only complete committed ledger
    # revisions are visible. Unlike the paginated report, it has no older cursor.
    available_from = await session.scalar(
        select(func.max(Change.created_at)).where(Change.reason == "migration")
    )
    beginning = max(start, available_from) if available_from else start
    if beginning >= end:
        return {}
    before_owner, after_owner = Change.before_company_id, Change.after_company_id
    owner_key = literal(company_id, type_=before_owner.type)
    before_match, after_match = before_owner == owner_key, after_owner == owner_key
    composite_range = session.bind.dialect.name == "postgresql"
    lower = literal(beginning, type_=Change.created_at.type)
    upper = literal(end, type_=Change.created_at.type)
    if composite_range:
        lower = tuple_(owner_key, lower)
        upper = tuple_(owner_key, upper)

    def scoped_changes(columns, *, baseline):
        changes = select(*columns).where(
            Change.resource_type_id.in_((CPU_RESOURCE_ID, MEMORY_RESOURCE_ID))
        )

        def edge(owner_column, change_filter):
            # PostgreSQL's generic plans can choose a global date index for a
            # skewed owner distribution. Compare the composite key there.
            # SQLite can use both indexed columns with a plain date range.
            key = tuple_(owner_column, Change.created_at) if composite_range else Change.created_at
            period = key <= lower if baseline else and_(key > lower, key < upper)
            return changes.where(owner_column == owner_key, change_filter, period)

        # Each branch can use its owner/time index. An in-place change belongs
        # to the first branch only, while a transfer is visible to both owners.
        return union_all(
            edge(before_owner, INVENTORY_CHANGED), edge(after_owner, COMPANY_OWNERSHIP_CHANGED)
        ).subquery()

    # Rank only this owner's matching snapshots, including exits with zero contribution.
    baseline = scoped_changes(
        (
            Change.computer_resource_id,
            Change.version,
            Change.resource_type_id,
            case((after_match, Change.after_total), else_=0).label("total"),
        ),
        baseline=True,
    )
    ranked = select(
        baseline.c.resource_type_id,
        baseline.c.total,
        func.row_number()
        .over(partition_by=baseline.c.computer_resource_id, order_by=baseline.c.version.desc())
        .label("rank"),
    ).subquery()
    totals = {CPU_RESOURCE_ID: 0, MEMORY_RESOURCE_ID: 0}
    for type_id, total in await session.execute(
        select(ranked.c.resource_type_id, ranked.c.total).where(ranked.c.rank == 1)
    ):
        totals[type_id] += total
    inventory = {beginning: (totals[CPU_RESOURCE_ID], totals[MEMORY_RESOURCE_ID])}
    delta = case((after_match, Change.after_total), else_=0) - case(
        (before_match, Change.before_total), else_=0
    )
    window = scoped_changes(
        (
            Change.id,
            Change.version,
            Change.revision,
            Change.created_at,
            Change.resource_type_id,
            delta.label("delta"),
        ),
        baseline=False,
    )
    rows = await session.execute(
        select(
            window.c.revision, window.c.created_at, window.c.resource_type_id, window.c.delta
        ).order_by(window.c.revision, window.c.version, window.c.id)
    )
    for _, events in groupby(rows, key=lambda row: row.revision):
        first = next(events)
        for event in chain((first,), events):
            totals[event.resource_type_id] += event.delta
        inventory[first.created_at] = (totals[CPU_RESOURCE_ID], totals[MEMORY_RESOURCE_ID])
    return inventory


async def pool_history(session, company_id, now):
    """Pair the full inventory with currently schedulable free resources over seven days."""
    start = now - timedelta(days=7)
    response = {"from_time": start, "to_time": now, "points": [], "sampled": False}
    available = await capacity_history(
        session, (None, company_id), start, now, now, include_free=True
    )
    if not available["points"]:
        return response

    inventory = await pool_inventory(session, company_id, available["points"][0]["at"], now)
    if not inventory:
        return response
    known_since = next(iter(inventory))

    free = {
        point["at"]: (point["cpu_free_millis"], point["memory_free_bytes"])
        for point in available["points"]
    }
    # The source reports end on a left-hand limit. The final marker represents
    # current inventory and leases, including changes at exactly this instant.
    chart_resources = and_(
        ComputerOwnership.company_id == company_query_id(session, company_id),
        ComputerResource.resource_type_id.in_((CPU_RESOURCE_ID, MEMORY_RESOURCE_ID)),
    )
    _, current, available_now = await pool_resource_totals(
        session, chart_resources, available_computer_filter(now), include_types=False
    )
    inventory[now] = (
        current.get(CPU_RESOURCE_ID, (0, 0))[0],
        current.get(MEMORY_RESOURCE_ID, (0, 0))[0],
    )
    free[now] = (
        available_now.get(CPU_RESOURCE_ID, (0, 0, 0))[2],
        available_now.get(MEMORY_RESOURCE_ID, (0, 0, 0))[2],
    )
    totals = free_now = (0, 0)
    previous = None
    for at in sorted(inventory.keys() | free.keys()):
        totals = inventory.get(at, totals)
        free_now = free.get(at, free_now)
        # Intervals written before the two clocks were aligned can start before
        # the corresponding inventory change after a wall-clock correction.
        values = (*totals, min(free_now[0], totals[0]), min(free_now[1], totals[1]))
        if at >= known_since and (values != previous or at == now):
            response["points"].append({"at": at, **dict(zip(POOL_FIELDS, values, strict=True))})
            previous = values
    selected = chart_points(response["points"], start, now)
    response["sampled"] = len(selected) < len(response["points"])
    response["points"] = selected
    return response
