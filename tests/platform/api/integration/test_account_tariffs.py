"""Tariff publication and assignment on the schema used by payment accounts."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import anyio
import pytest
from alembic import command as alembic
from alembic.util import CommandError
from sqlalchemy import (
    DateTime,
    String,
    bindparam,
    cast,
    column,
    event,
    func,
    insert,
    inspect,
    literal,
    select,
    table,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from throng_api import account_tariff_schema
from throng_api.account_tariff_schema import (
    configure_postgres_timestamp_codec,
    decode_postgres_timestamp,
    encode_postgres_timestamp,
)
from throng_api.models import (
    Base,
    Company,
    PaymentAccount,
    PaymentAccountTariffPlan,
    TariffPlan,
    TariffPlanType,
    User,
    UserTariffPlan,
    utcnow,
)

pytestmark = pytest.mark.anyio
AT = datetime(2026, 4, 5, 12, 0, 0, 245678, tzinfo=UTC)
TICK = timedelta(microseconds=1)
INDIA = timezone(timedelta(hours=5, minutes=30))


def plan(name, plan_type, published_at):
    """Construct a tariff for a type with an empty field schema."""
    return TariffPlan(name=name, plan_type=plan_type, field_values={}, published_at=published_at)


async def assignments(session, account_id):
    """Read the actual plan ids assigned to an account."""
    rows = await session.execute(
        select(
            PaymentAccountTariffPlan.tariff_plan_type_id, PaymentAccountTariffPlan.tariff_plan_id
        ).where(PaymentAccountTariffPlan.payment_account_id == account_id)
    )
    return dict(rows.all())


def legacy_plans():
    """Refer to the tariff table as it exists at revision 0019."""
    return table(
        "tariff_plans",
        *(
            column(name, TariffPlan.__table__.c[name].type)
            for name in ("id", "created_at", "tariff_plan_type_id", "name", "field_values")
        ),
    )


@pytest.fixture
async def owners(migration_database):
    """Create a user and an organization through the normal ORM."""
    user = User(
        email="account-tariffs@example.com",
        first_name="Account",
        last_name="Tariffs",
        password_hash="unused",
    )
    async with migration_database.write() as session:
        session.add(user)
        await session.flush()
        company = Company(name="Account tariffs", created_by_user_id=user.id)
        session.add(company)
    return user, company


async def test_publication_default_and_utc_round_trip(migration_database):
    """Default to the current instant and round-trip an aware timestamp without rounding."""
    kind = TariffPlanType(name="Subscription", field_types={})
    scheduled = plan("Scheduled", kind, AT.astimezone(INDIA))
    current = TariffPlan(name="Current", plan_type=kind, field_values={})
    before = utcnow()
    async with migration_database.write() as session:
        session.add_all([scheduled, current])
    after = utcnow()
    async with migration_database.read() as session:
        planned = await session.get(TariffPlan, scheduled.id)
        defaulted = await session.get(TariffPlan, current.id)
        assert planned.published_at == AT
        assert planned.published_at.tzinfo is UTC
        assert before <= defaulted.published_at <= after


class NoUTCOffset(tzinfo):
    def utcoffset(self, _value):
        return None


def test_postgres_publication_codec_without_a_connection():
    """Preserve finite publication bounds and legacy infinity with the native codec."""
    dialect = asyncpg.dialect()
    timestamp_type = TariffPlan.published_at.type
    latest = datetime.max.replace(tzinfo=UTC)
    statement = select(literal(latest, type_=timestamp_type)).compile(dialect=dialect)
    (bind,) = statement._bind_processors.values()
    assert bind(None) is None
    decoded = timestamp_type.dialect_impl(dialect).result_processor(dialect, None)
    assert decoded(decode_postgres_timestamp(encode_postgres_timestamp(bind(latest)))) == latest
    assert decode_postgres_timestamp(encode_postgres_timestamp(latest)) == datetime.max
    assert decode_postgres_timestamp(encode_postgres_timestamp(datetime.min)) == datetime.min
    day = date(2026, 4, 5)
    midnight = datetime.combine(day, time.min, tzinfo=datetime.now(UTC).astimezone().tzinfo)
    assert decode_postgres_timestamp(encode_postgres_timestamp(day)) == midnight.astimezone(UTC)
    with pytest.raises(TypeError, match="datetime or date"):
        encode_postgres_timestamp("2026-04-05")


async def test_postgres_timestamp_codec_installs_the_native_binary_adapters(monkeypatch):
    """Check engine registration on SQLite CI without opening a PostgreSQL connection."""
    driver = SimpleNamespace(set_type_codec=AsyncMock())
    monkeypatch.setattr(account_tariff_schema, "AsyncpgConnection", SimpleNamespace)
    pending = []
    dbapi = SimpleNamespace(
        driver_connection=driver, run_async=lambda call: pending.append(call(driver))
    )
    configure_postgres_timestamp_codec(dbapi, None)
    await pending.pop()
    driver.set_type_codec.assert_awaited_once_with(
        "timestamptz",
        schema="pg_catalog",
        format="tuple",
        encoder=encode_postgres_timestamp,
        decoder=decode_postgres_timestamp,
    )


@pytest.mark.parametrize("timezone_info", [None, NoUTCOffset()])
async def test_publication_requires_an_unambiguous_timezone(migration_database, timezone_info):
    """Reject naive timestamps and tzinfo objects that supply no offset."""
    kind = TariffPlanType(name="Subscription", field_types={})
    with pytest.raises((StatementError, ValueError), match="Timezone-aware datetime required"):
        async with migration_database.write() as session:
            session.add(plan("Invalid", kind, AT.replace(tzinfo=timezone_info)))


async def test_editing_publication_during_a_repeated_dst_hour(migration_database):
    """Persist a new instant when a local clock repeats an hour."""
    first = datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"))
    second = first.replace(fold=1)
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Publication", kind, first)
    async with migration_database.write() as session:
        session.add(tariff)
        await session.flush()
        tariff.published_at = second
    async with migration_database.read() as session:
        stored = await session.get(TariffPlan, tariff.id)
        assert stored.published_at == second.astimezone(UTC)


@pytest.mark.parametrize(
    "when", [datetime.min.replace(tzinfo=UTC), datetime.max.replace(tzinfo=UTC)]
)
async def test_boundary_publications_and_account_cutoffs_remain_finite(
    migration_database, owners, when
):
    """Use the exact Python instants on both databases, including the asyncpg boundaries."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Boundary", kind, when)
    async with migration_database.write() as session:
        session.add(tariff)
        await session.flush()
        account = PaymentAccount(user_id=user.id, created_at=when)
        session.add(account)
        await session.flush()
        assert await assignments(session, account.id) == {kind.id: tariff.id}
        if migration_database.engine.dialect.name == "postgresql":
            await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        raw = await session.scalar(select(cast(TariffPlan.published_at, String)))
        assert raw.startswith(when.date().isoformat())
    async with migration_database.read() as session:
        assert (await session.get(TariffPlan, tariff.id)).published_at == when


@pytest.mark.parametrize("raw", ["infinity", "-infinity", "10000-01-01 00:00:00+00"])
async def test_postgres_rejects_unrepresentable_publication_times(migration_database, raw):
    """Keep raw SQL publications in the datetime range the application reads."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports extended and infinite timestamps")
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    with pytest.raises(IntegrityError, match="published_at_range"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                insert(TariffPlan.__table__).values(
                    name="Invalid",
                    tariff_plan_type_id=kind.id,
                    field_values={},
                    published_at=cast(literal(raw), DateTime(timezone=True)),
                )
            )


async def test_postgres_legacy_infinite_account_time_uses_equivalent_cutoff(
    migration_database, owners
):
    """Interpret the old asyncpg encoding of datetime.min consistently when assigning plans."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports an infinite timestamp representation")
    user, _ = owners
    earliest = datetime.min.replace(tzinfo=UTC)
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Earliest publication", kind, earliest)
    async with migration_database.write() as session:
        session.add(tariff)
        await session.flush()
        account_id = await session.scalar(
            insert(PaymentAccount)
            .values(user_id=user.id, created_at=cast(literal("-infinity"), DateTime(timezone=True)))
            .returning(PaymentAccount.id)
        )
        assert await assignments(session, account_id) == {kind.id: tariff.id}
        loaded = await session.get(PaymentAccount, account_id)
        assert (
            await session.scalar(
                select(PaymentAccount.id).where(PaymentAccount.created_at == loaded.created_at)
            )
            == account_id
        )


async def test_database_requires_a_publication_time(migration_database):
    """Reject an explicit SQL NULL for a plan's publication time."""
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    with pytest.raises(IntegrityError, match="NOT NULL|not-null"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                insert(TariffPlan.__table__).values(
                    name="Missing", tariff_plan_type_id=kind.id, field_values={}, published_at=None
                )
            )


async def test_publication_uniqueness_uses_type_and_exact_instant(migration_database):
    """Reject different representations of one instant within a type on insert and update."""
    subscription = TariffPlanType(name="Subscription", field_types={})
    cloud = TariffPlanType(name="Cloud", field_types={})
    original = plan("Original", subscription, AT)
    neighbor = plan("Next microsecond", subscription, AT + TICK)
    other_type = plan("Same instant, different type", cloud, AT.astimezone(INDIA))
    async with migration_database.write() as session:
        session.add_all([original, neighbor, other_type])

    with pytest.raises(IntegrityError, match="type_published_at|UNIQUE constraint"):
        async with migration_database.write() as session:
            kind = await session.get(TariffPlanType, subscription.id)
            session.add(plan("Duplicate", kind, AT.astimezone(INDIA)))
    with pytest.raises(IntegrityError, match="type_published_at|UNIQUE constraint"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                update(TariffPlan.__table__)
                .where(TariffPlan.id == neighbor.id)
                .values(published_at=AT.astimezone(INDIA))
            )
    async with migration_database.read() as session:
        assert (await session.get(TariffPlan, neighbor.id)).published_at == AT + TICK
        assert (await session.scalars(select(TariffPlan.id))).all().count(original.id) == 1


@pytest.mark.parametrize(
    "value",
    [
        "2026-04-05 12:00:00",
        "2026-04-05 17:30:00.245678+05:30",
        "2026-02-30 12:00:00.245678",
        "2026-04-05 24:00:00.245678",
        "2026-04-05 12:00:00.245678\0trailing text",
    ],
)
async def test_sqlite_rejects_noncanonical_publication_storage(memory_database, value):
    """Prevent SQL text from evading publication uniqueness through alternate spellings."""
    kind = TariffPlanType(name="Subscription", field_types={})
    async with memory_database.write() as session:
        session.add(plan("Original", kind, AT))
    with pytest.raises(IntegrityError, match="published_at_utc"):
        async with memory_database.engine.begin() as connection:
            await connection.execute(
                insert(TariffPlan.__table__).values(
                    name="Invalid",
                    tariff_plan_type_id=kind.id,
                    field_values={},
                    published_at=literal(value, type_=String()),
                )
            )


async def test_new_accounts_get_latest_published_plan_of_each_type(migration_database, owners):
    """Read a stable assignment snapshot for both users and organizations."""
    user, company = owners
    subscription = TariffPlanType(name="Subscription", field_types={})
    cloud = TariffPlanType(name="Cloud", field_types={})
    upcoming = TariffPlanType(name="Upcoming", field_types={})
    empty = TariffPlanType(name="Empty", field_types={})
    older = plan("Older", subscription, AT - TICK)
    older.created_at = AT + timedelta(days=1)
    latest = plan("Latest", subscription, AT.astimezone(INDIA))
    latest.created_at = AT - timedelta(days=1)
    scheduled = plan("Scheduled", subscription, AT + TICK)
    compute = plan("Compute", cloud, AT)
    future_only = plan("Future only", upcoming, AT + TICK)
    personal = PaymentAccount(user_id=user.id, created_at=AT)
    corporate = PaymentAccount(company_id=company.id, created_at=AT + TICK)
    earlier = PaymentAccount(user_id=user.id, created_at=AT - TICK)

    async with migration_database.write() as session:
        session.add_all([scheduled, older, latest, compute, future_only, empty])
        await session.flush()
        session.add_all([personal, corporate, earlier])
        await session.flush()
        assert await assignments(session, personal.id) == {
            subscription.id: latest.id,
            cloud.id: compute.id,
        }
        assert await assignments(session, earlier.id) == {subscription.id: older.id}
        assert await assignments(session, corporate.id) == {
            subscription.id: scheduled.id,
            cloud.id: compute.id,
            upcoming.id: future_only.id,
        }

    async with migration_database.write() as session:
        new_type = TariffPlanType(name="Added afterwards", field_types={})
        session.add(plan("Backdated publication", new_type, AT - 2 * TICK))

    async with migration_database.read() as session:
        lookup = select(PaymentAccount).options(selectinload(PaymentAccount.tariff_assignments))
        results = (await session.scalars(lookup.where(PaymentAccount.user_id == user.id))).all()
        assert {
            account.id: {link.tariff_plan.name for link in account.tariff_assignments}
            for account in results
        } == {
            personal.id: {"Latest", "Compute", "Backdated publication"},
            earlier.id: {"Older", "Backdated publication"},
        }
        org = (await session.scalars(lookup.where(PaymentAccount.company_id == company.id))).one()
        assert {link.tariff_plan.name for link in org.tariff_assignments} == {
            "Scheduled",
            "Compute",
            "Future only",
            "Backdated publication",
        }


async def test_typed_sql_account_insert_and_empty_catalog(migration_database, owners):
    """Initialize defaults for typed account inserts before and after the first tariff."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        account_id = await session.scalar(
            insert(PaymentAccount)
            .values(user_id=user.id, created_at=AT)
            .returning(PaymentAccount.id)
        )
        assert await assignments(session, account_id) == {}
        current = plan("Current", kind, AT)
        session.add(current)
        await session.flush()
        later_account = await session.scalar(
            insert(PaymentAccount)
            .values(user_id=user.id, created_at=AT)
            .returning(PaymentAccount.id)
        )
        assert await assignments(session, account_id) == {kind.id: current.id}
        assert await assignments(session, later_account) == {kind.id: current.id}


async def test_account_requery_reads_default_assignment_after_empty_collection(
    migration_database, owners
):
    """Refresh an initialized relationship after the database fills it during INSERT."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Published", kind, AT)
    async with migration_database.write() as session:
        session.add(tariff)
        await session.flush()
        account = PaymentAccount(user_id=user.id, created_at=AT, tariff_assignments=[])
        session.add(account)
        await session.flush()
        reloaded = (
            await session.scalars(
                select(PaymentAccount)
                .where(PaymentAccount.user_id == user.id)
                .options(selectinload(PaymentAccount.tariff_assignments))
            )
        ).one()
        assert reloaded is account
        assert [link.tariff_plan_id for link in reloaded.tariff_assignments] == [tariff.id]


@pytest.mark.parametrize(
    ("created_at", "microsecond"),
    [
        ("2026-04-05 12:00:00", 0),
        ("2026-04-05T17:30:00.999999+05:30", 999999),
        ("2026-04-05T12:00:00.999999Z", 999999),
        ("2026-04-05T17:30:00.999999+0530", 999999),
        ("20260405T120000.999999Z", 999999),
        ("2026-04-05T17:30+05:30", 0),
        ("2026-04-05T12:00:00.999999+00:00:00.000001", 999999),
    ],
)
async def test_sqlite_raw_account_timestamp_can_match_a_publication_exactly(
    memory_database, created_at, microsecond
):
    """Accept ordinary SQL and ISO timestamps without rounding a boundary to the next second."""
    instant = AT.replace(microsecond=microsecond)
    user = User(
        email="raw-account@example.com",
        first_name="Raw",
        last_name="Account",
        password_hash="unused",
    )
    kind = TariffPlanType(name="Subscription", field_types={})
    eligible = plan("Eligible", kind, instant)
    future = plan("Future", kind, instant + TICK)
    async with memory_database.write() as session:
        session.add_all([user, eligible, future])
    async with memory_database.engine.begin() as connection:
        account_id = await connection.scalar(
            insert(PaymentAccount.__table__)
            .values(user_id=user.id, created_at=literal(created_at, type_=String()))
            .returning(PaymentAccount.id)
        )
    async with memory_database.read() as session:
        assert await assignments(session, account_id) == {kind.id: eligible.id}


@pytest.mark.parametrize(
    "raw",
    [
        "not a date",
        " 2026-04-05 12:00:00.245678 ",
        "2026-04-05T12:00:00ZZ",
        "20260405",
        "2026-04-05T",
        "2026-04-05T12:00:00.",
        "2026-04-05T1200:00",
        "2026-04-05T12:00+00:00.",
        "0000-12-31T23:30:00-02:00",
    ],
)
async def test_sqlite_rejects_unreadable_account_cutoff_atomically(memory_database, raw):
    """Abort the account insert when its timestamp cannot define an assignment snapshot."""
    owner = User(
        email="invalid-cutoff@example.com",
        first_name="Invalid",
        last_name="Cutoff",
        password_hash="unused",
    )
    async with memory_database.write() as session:
        session.add(owner)
    with pytest.raises(IntegrityError, match="Invalid creation timestamp"):
        async with memory_database.engine.begin() as connection:
            await connection.execute(
                insert(PaymentAccount.__table__).values(
                    user_id=owner.id,
                    created_at=literal(raw, type_=String()),
                )
            )
    async with memory_database.read() as session:
        assert await session.scalar(select(func.count(PaymentAccount.id))) == 0


@pytest.fixture
async def account_with_plans(migration_database, owners):
    """Give later-created accounts a cutoff before the catalogue's first publication."""
    user, company = owners
    first = PaymentAccount(user_id=user.id, created_at=AT - TICK)
    second = PaymentAccount(company_id=company.id, created_at=AT - TICK)
    primary = TariffPlanType(name="Subscription", field_types={})
    secondary = TariffPlanType(name="Cloud", field_types={})
    original = plan("Original", primary, AT)
    alternative = plan("Alternative", primary, AT + TICK)
    compute = plan("Compute", secondary, AT)
    async with migration_database.write() as session:
        session.add_all([original, alternative, compute])
        await session.flush()
        session.add_all([first, second])
    return first, second, original, alternative, compute


def link_values(account, tariff):
    """Build explicit foreign-key values for an account tariff link."""
    return {
        "payment_account_id": account.id,
        "tariff_plan_type_id": tariff.tariff_plan_type_id,
        "tariff_plan_id": tariff.id,
    }


async def test_account_scoped_uniqueness_and_reassignment(migration_database, account_with_plans):
    """Allow one plan per type and account, including reuse of a plan across owners."""
    first, second, original, alternative, compute = account_with_plans
    links = PaymentAccountTariffPlan.__table__
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            insert(links),
            [
                link_values(first, original),
                link_values(first, compute),
                link_values(second, original),
            ],
        )
    with pytest.raises(IntegrityError, match="pk_payment_account_tariff_plans|UNIQUE constraint"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(insert(links).values(**link_values(first, alternative)))
    async with migration_database.write() as session:
        record = await session.get(
            PaymentAccountTariffPlan, (first.id, original.tariff_plan_type_id)
        )
        record.tariff_plan = await session.get(TariffPlan, alternative.id)
    async with migration_database.read() as session:
        assert await assignments(session, first.id) == {
            alternative.tariff_plan_type_id: alternative.id,
            compute.tariff_plan_type_id: compute.id,
        }
        assert await assignments(session, second.id) == {original.tariff_plan_type_id: original.id}


@pytest.mark.parametrize(
    "problem", ["missing_account", "missing_plan", "forged_type", "null_type", "null_plan"]
)
async def test_account_link_constraints_hold_in_sql(
    migration_database, account_with_plans, problem
):
    """Enforce both foreign keys and mandatory link keys independently of the ORM."""
    account, _, original, _, compute = account_with_plans
    values = link_values(account, original)
    values.update(
        {
            "missing_account": {"payment_account_id": str(uuid4())},
            "missing_plan": {"tariff_plan_id": str(uuid4())},
            "forged_type": {"tariff_plan_type_id": compute.tariff_plan_type_id},
            "null_type": {"tariff_plan_type_id": None},
            "null_plan": {"tariff_plan_id": None},
        }[problem]
    )
    with pytest.raises(IntegrityError):
        async with migration_database.engine.begin() as connection:
            await connection.execute(insert(PaymentAccountTariffPlan.__table__).values(values))


async def test_concurrent_account_links_cannot_claim_the_same_type(
    migration_database, account_with_plans
):
    """Let the database arbitrate competing assignments in independent transactions."""
    account, _, original, alternative, _ = account_with_plans

    async def assign(tariff):
        """Write without the application's coordinating lock."""
        async with migration_database.sessions.begin() as session:
            session.add(PaymentAccountTariffPlan(**link_values(account, tariff)))

    results = await asyncio.gather(assign(original), assign(alternative), return_exceptions=True)
    assert results.count(None) == 1
    assert sum(isinstance(result, IntegrityError) for result in results) == 1
    async with migration_database.read() as session:
        assert set((await assignments(session, account.id)).values()) in (
            {original.id},
            {alternative.id},
        )


async def test_concurrent_publications_cannot_share_an_instant(migration_database):
    """Commit at most one plan per type and publication time across concurrent sessions."""
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)

    async def publish(name):
        """Publish without serializing through Database.write()."""
        async with migration_database.sessions.begin() as session:
            session.add(
                TariffPlan(name=name, tariff_plan_type_id=kind.id, field_values={}, published_at=AT)
            )

    results = await asyncio.gather(publish("One"), publish("Two"), return_exceptions=True)
    assert results.count(None) == 1
    assert sum(isinstance(result, IntegrityError) for result in results) == 1
    async with migration_database.read() as session:
        assert await session.scalar(select(func.count(TariffPlan.id))) == 1


async def test_account_creation_rollback_discards_loaded_default_assignments(
    migration_database, owners
):
    """Roll back the account and its trigger-generated rows in the same savepoint."""
    owner, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(plan("Original", kind, AT))
    async with migration_database.write() as session:
        scope = await session.begin_nested()
        account = PaymentAccount(user_id=owner.id, created_at=AT)
        session.add(account)
        await session.flush()
        account_id = account.id
        key = (account_id, kind.id)
        await session.refresh(account, ["tariff_assignments"])
        assignment = account.tariff_assignments[0]
        assert await session.get(PaymentAccountTariffPlan, key) is assignment
        await scope.rollback()
        assert await session.get(PaymentAccountTariffPlan, key) is None
        assert await session.get(PaymentAccount, account_id) is None


async def test_account_deletion_cascades_and_savepoint_rollback_restores_links(
    migration_database, owners
):
    """Delete only the account's links, and retain a loaded link after an undone deletion."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Current", kind, AT)
    account = PaymentAccount(user_id=user.id, created_at=AT)
    async with migration_database.write() as session:
        session.add(tariff)
        await session.flush()
        session.add(account)
    key = (account.id, kind.id)
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint|foreign key constraint"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                TariffPlan.__table__.delete().where(TariffPlan.id == tariff.id)
            )
    async with migration_database.write() as session:
        parent = await session.get(PaymentAccount, account.id)
        assignment = await session.get(PaymentAccountTariffPlan, key)
        scope = await session.begin_nested()
        await session.delete(parent)
        await session.flush()
        assert inspect(assignment).deleted
        assert await session.get(PaymentAccountTariffPlan, key) is None
        await scope.rollback()
        assert await session.get(PaymentAccountTariffPlan, key) is assignment
        await session.delete(parent)
    async with migration_database.read() as session:
        assert await session.get(PaymentAccountTariffPlan, key) is None
        assert await session.get(TariffPlan, tariff.id) is not None


async def test_upgrade_backfills_existing_catalog_and_account_snapshots(
    migration_database, alembic_config, owners
):
    """Preserve legacy assignments and disambiguate equal creation times within a type."""
    user, company = owners
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    subscription, cloud = str(uuid4()), str(uuid4())
    plan_ids = [str(uuid4()) for _ in range(5)]
    plan_ids[1:3] = sorted(plan_ids[1:3])
    created_times = [AT - TICK, AT, AT, AT + TICK, AT]
    older, personal, organization = str(uuid4()), str(uuid4()), str(uuid4())
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            insert(TariffPlanType.__table__),
            [
                {"id": subscription, "name": "Subscription", "field_types": {}},
                {"id": cloud, "name": "Cloud", "field_types": {}},
            ],
        )
        await connection.execute(
            insert(legacy_plans()),
            [
                {
                    "id": uid,
                    "created_at": created,
                    "tariff_plan_type_id": cloud if index == 4 else subscription,
                    "name": f"Legacy {index}",
                    "field_values": {},
                }
                for index, (uid, created) in enumerate(zip(plan_ids, created_times, strict=True))
            ],
        )
        await connection.execute(
            insert(UserTariffPlan.__table__).values(
                user_id=user.id,
                tariff_plan_type_id=subscription,
                tariff_plan_id=plan_ids[0],
            )
        )
        await connection.execute(
            insert(PaymentAccount.__table__),
            [
                {"id": older, "created_at": AT - 2 * TICK, "user_id": user.id, "company_id": None},
                {"id": personal, "created_at": AT + TICK, "user_id": user.id, "company_id": None},
                {
                    "id": organization,
                    "created_at": AT + 2 * TICK,
                    "user_id": None,
                    "company_id": company.id,
                },
            ],
        )
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0020")
    async with migration_database.read() as session:
        actual = {tariff.id: tariff for tariff in await session.scalars(select(TariffPlan))}
        assert [actual[uid].created_at for uid in plan_ids] == created_times
        assert [actual[uid].published_at for uid in plan_ids] == [
            AT - TICK,
            AT,
            AT + TICK,
            AT + 2 * TICK,
            AT,
        ]
        legacy_link = await session.get(UserTariffPlan, (user.id, subscription))
        assert legacy_link.tariff_plan_id == plan_ids[0]
        assert await assignments(session, older) == {}
        assert await assignments(session, personal) == {
            subscription: plan_ids[2],
            cloud: plan_ids[4],
        }
        assert await assignments(session, organization) == {
            subscription: plan_ids[3],
            cloud: plan_ids[4],
        }
    async with migration_database.write() as session:
        added = PaymentAccount(company_id=company.id, created_at=AT)
        session.add(added)
        await session.flush()
        assert await assignments(session, added.id) == {
            subscription: plan_ids[1],
            cloud: plan_ids[4],
        }


@pytest.mark.parametrize("raw_date", ["2026-04-05T17:30+05:30", "2026-W14-7T17:30+05:30"])
async def test_upgrade_preserves_legacy_sqlite_cutoff_and_publication(
    migration_database, alembic_config, owners, raw_date
):
    """Read an existing account's real instant, and protect a subsequent publication edit."""
    if migration_database.engine.dialect.name != "sqlite":
        pytest.skip("SQLite's old schema could contain noncanonical ISO timestamps")
    user, _ = owners
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    kind, plan_id, account_id = (str(uuid4()) for _ in range(3))
    instant = AT.replace(microsecond=0)
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            insert(TariffPlanType.__table__).values(id=kind, name="Subscription", field_types={})
        )
        await connection.execute(
            insert(legacy_plans()).values(
                id=plan_id,
                tariff_plan_type_id=kind,
                name="Legacy",
                field_values={},
                created_at=literal(raw_date, type_=String()),
            )
        )
        await connection.execute(
            insert(PaymentAccount.__table__).values(
                id=account_id,
                user_id=user.id,
                created_at=literal(raw_date, type_=String()),
            )
        )
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0020")
    async with migration_database.write() as session:
        assert (await session.get(PaymentAccount, account_id)).created_at == instant
        assert await assignments(session, account_id) == {kind: plan_id}
        tariff = await session.get(TariffPlan, plan_id)
        assert tariff.published_at == instant
        tariff.published_at = instant + TICK
        await session.delete(await session.get(PaymentAccountTariffPlan, (account_id, kind)))
    await migration_database.close()
    with pytest.raises(CommandError, match="billing data exists"):
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    async with migration_database.read() as session:
        assert (await session.get(TariffPlan, plan_id)).published_at == instant + TICK


@pytest.mark.parametrize("new_data", ["assignment", "publication"])
async def test_downgrade_preserves_publications_and_assignments(
    migration_database, alembic_config, owners, new_data
):
    """Reject losing saved account assignments or meaningful publication dates."""
    user, _ = owners
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0020")
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Published", kind, AT)
    tariff.created_at = AT if new_data == "assignment" else AT - TICK
    async with migration_database.write() as session:
        session.add(tariff)
        await session.flush()
        if new_data == "assignment":
            session.add(PaymentAccount(user_id=user.id, created_at=AT))
    await migration_database.close()
    with pytest.raises(CommandError, match="billing data exists"):
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    async with migration_database.read() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "0020"
        assert (await session.get(TariffPlan, tariff.id)).published_at == AT
        assert await session.scalar(select(func.count()).select_from(PaymentAccountTariffPlan)) == (
            1 if new_data == "assignment" else 0
        )


@pytest.mark.parametrize("raw", ["0001-12-31 23:59:59.999999+00 BC", "10000-01-01 00:00:00+00"])
async def test_postgres_rejects_unreadable_account_cutoff_atomically(
    migration_database, owners, raw
):
    """Reject finite account timestamps outside Python's datetime range."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports wider finite dates than Python")
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(plan("First", kind, datetime.min.replace(tzinfo=UTC)))
    with pytest.raises(IntegrityError, match="Invalid creation timestamp"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                insert(PaymentAccount.__table__).values(
                    user_id=user.id,
                    created_at=cast(literal(raw), DateTime(timezone=True)),
                )
            )
    async with migration_database.read() as session:
        assert await session.scalar(select(func.count(PaymentAccount.id))) == 0


@pytest.mark.parametrize("eligible", [True, False])
async def test_downgrade_protects_an_empty_account_snapshot(
    migration_database, alembic_config, owners, eligible
):
    """Allow a lossless rollback while preserving the decision to remove a default assignment."""
    user, _ = owners
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0020")
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Published", kind, AT)
    tariff.created_at = AT
    account = PaymentAccount(user_id=user.id, created_at=AT if eligible else AT - TICK)
    async with migration_database.write() as session:
        session.add(tariff)
        await session.flush()
        session.add(account)
        await session.flush()
        if eligible:
            await session.delete(await session.get(PaymentAccountTariffPlan, (account.id, kind.id)))
    await migration_database.close()
    if eligible:
        with pytest.raises(CommandError, match="billing data exists"):
            await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    else:
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
        await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0020")
    async with migration_database.read() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "0020"
        assert await assignments(session, account.id) == {}


@pytest.mark.parametrize(
    "fault", ["CREATE TRIGGER assign_default_account_tariffs", "UPDATE alembic_version"]
)
async def test_interrupted_account_tariff_upgrade_rolls_back_and_can_retry(
    migration_database, alembic_config, fault
):
    """Roll back both database changes and the revision marker after an interrupted upgrade."""
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")

    def interrupt(_connection, _cursor, statement, *_):
        """Abort an actual migration statement after the preceding DDL ran."""
        if fault in statement:
            raise RuntimeError("account tariff upgrade interrupted")

    event.listen(Engine, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="account tariff upgrade interrupted"):
            await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0020")
    finally:
        event.remove(Engine, "before_cursor_execute", interrupt)

    async with migration_database.engine.connect() as connection:
        tables, fields = await connection.run_sync(
            lambda sync: (
                inspect(sync).get_table_names(),
                inspect(sync).get_columns("tariff_plans"),
            )
        )
        assert "payment_account_tariff_plans" not in tables
        assert "published_at" not in {field["name"] for field in fields}
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0019"
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)


@pytest.mark.parametrize("lookup", ["empty", "tuple"])
async def test_timestamp_filters_support_empty_lists_and_timestamp_tuples(
    migration_database, owners, lookup
):
    """Use empty membership filters and the composite publication key at both endpoints."""
    owner, _ = owners
    kind = TariffPlanType(name="Endpoints", field_types={})
    earliest = datetime.min.replace(tzinfo=UTC)
    latest = datetime.max.replace(tzinfo=UTC)
    first = plan("First", kind, earliest)
    last = plan("Last", kind, latest)
    async with migration_database.write() as session:
        session.add_all([first, last])
        await session.flush()
        account = PaymentAccount(user_id=owner.id, created_at=AT)
        session.add(account)
    async with migration_database.read() as session:
        if lookup == "empty":
            for model, field, all_ids in (
                (TariffPlan, TariffPlan.published_at, {first.id, last.id}),
                (PaymentAccount, PaymentAccount.created_at, {account.id}),
            ):
                assert (await session.scalars(select(model.id).where(field.in_([])))).all() == []
                assert (
                    set(await session.scalars(select(model.id).where(field.not_in([])))) == all_ids
                )
                membership = select(model.id).where(field.in_(bindparam("times", expanding=True)))
                assert (await session.scalars(membership, {"times": []})).all() == []
        else:
            key = tuple_(TariffPlan.tariff_plan_type_id, TariffPlan.published_at)
            statement = select(TariffPlan.id).where(
                key.in_([(kind.id, earliest), (kind.id, latest)])
            )
            assert set(await session.scalars(statement)) == {first.id, last.id}


async def test_account_row_switch_reloads_retained_relationship(migration_database, owners):
    """A same-UUID mapped replacement still points at the existing account assignments."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Published", kind, AT)
    account = PaymentAccount(user_id=user.id, created_at=AT)
    async with migration_database.write() as session:
        session.add(tariff)
        await session.flush()
        session.add(account)
    async with migration_database.write() as session:
        old = await session.get(PaymentAccount, account.id)
        await session.delete(old)
        replacement = PaymentAccount(
            id=account.id, user_id=user.id, created_at=AT, tariff_assignments=[]
        )
        session.add(replacement)
        await session.flush()
        reloaded = (
            await session.scalars(
                select(PaymentAccount)
                .where(PaymentAccount.id == account.id)
                .options(selectinload(PaymentAccount.tariff_assignments))
            )
        ).one()
        assert reloaded is replacement
        assert [link.tariff_plan_id for link in reloaded.tariff_assignments] == [tariff.id]


@pytest.mark.parametrize("preload", [None, "link", "link_with_account"])
async def test_retained_link_follows_account_replacement_and_rollback(
    migration_database, owners, preload
):
    """Resolve the retained link's owner after a row switch and after a savepoint rollback."""
    user, company = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    account = PaymentAccount(user_id=user.id, created_at=AT)
    other_account = PaymentAccount(company_id=company.id, created_at=AT)
    async with migration_database.write() as session:
        session.add(plan("Published", kind, AT))
        await session.flush()
        session.add_all([account, other_account])

    links = select(PaymentAccountTariffPlan).options(
        selectinload(PaymentAccountTariffPlan.payment_account)
    )
    lookup = links.where(PaymentAccountTariffPlan.payment_account_id == account.id)
    async with migration_database.write() as session:
        old = await session.get(PaymentAccount, account.id)
        untouched = (
            await session.scalars(
                links.where(PaymentAccountTariffPlan.payment_account_id == other_account.id)
            )
        ).one()
        initial_link = None
        if preload == "link":
            initial_link = await session.get(PaymentAccountTariffPlan, (account.id, kind.id))
            assert "payment_account" in inspect(initial_link).unloaded
        elif preload == "link_with_account":
            initial_link = (await session.scalars(lookup)).one()
            assert initial_link.payment_account is old
        async with session.begin_nested() as savepoint:
            await session.delete(old)
            replacement = PaymentAccount(
                id=account.id, user_id=None, company_id=company.id, created_at=AT
            )
            session.add(replacement)
            await session.flush()
            link = (await session.scalars(lookup)).one()
            if initial_link is not None:
                assert link is initial_link
            assert link.payment_account is replacement
            assert link.payment_account.company_id == company.id
            assert untouched.payment_account.company_id == company.id
            await savepoint.rollback()

        restored = (await session.scalars(lookup)).one()
        assert restored is link
        assert restored.payment_account is old
        assert restored.payment_account.user_id == user.id
        assert untouched.payment_account.company_id == company.id


async def test_publication_and_account_creation_use_routed_mapper_binds(migration_database, owners):
    """Assign a plan published in the same routed transaction without a default bind."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Published", kind, AT)
    async with (
        AsyncSession(binds={Base: migration_database.engine}) as session,
        session.begin(),
    ):
        session.add(kind)
        await session.flush()
        session.add(tariff)
        await session.flush()
        account = PaymentAccount(user_id=user.id, created_at=AT)
        session.add(account)
        await session.flush()
        assert await assignments(session, account.id) == {kind.id: tariff.id}


async def test_account_flush_preserves_explicit_assignment_collection(migration_database, owners):
    """Keep a loaded collection in memory when INSERT adds no database-generated links."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Future", kind, AT + TICK)
    async with migration_database.write() as session:
        session.add(tariff)
        await session.flush()
        link = PaymentAccountTariffPlan(tariff_plan=tariff)
        account = PaymentAccount(user_id=user.id, created_at=AT, tariff_assignments=[link])
        session.add(account)
        await session.flush()
        assert account.tariff_assignments == [link]


@pytest.mark.parametrize("installed_schema", ["public", "Tariff catalogue"])
async def test_postgres_trigger_resolves_its_installed_schema(
    migration_database, owners, installed_schema
):
    """Resolve the catalogue and assignments independently of the caller's search path."""
    dialect = migration_database.engine.dialect
    if dialect.name != "postgresql":
        pytest.skip("PostgreSQL sessions have a configurable schema search path")
    schema = dialect.identifier_preparer.quote_schema(installed_schema)
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    tariff = plan("Published", kind, AT)
    async with migration_database.write() as session:
        connection = await session.connection()
        if installed_schema != "public":
            await connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
            await connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
            await connection.run_sync(Base.metadata.create_all)
            user = User(
                email="isolated-billing@example.com",
                first_name="Billing",
                last_name="Owner",
                password_hash="unused",
            )
            session.add(user)
        session.add(tariff)
        await session.flush()

        for caller_ddl in (
            "SET LOCAL search_path TO pg_catalog",
            "CREATE TEMP TABLE tariff_plan_types (id uuid)",
        ):
            await connection.exec_driver_sql(caller_ddl)
            account_id = uuid4()
            await connection.execute(
                text(
                    f"INSERT INTO {schema}.payment_accounts (id, user_id, created_at) "
                    "VALUES (:id, :user_id, :created_at)"
                ),
                {"id": account_id, "user_id": user.id, "created_at": AT},
            )
            assigned = await connection.scalar(
                text(
                    f"SELECT tariff_plan_id FROM {schema}.payment_account_tariff_plans "
                    "WHERE payment_account_id = :id"
                ),
                {"id": account_id},
            )
            assert str(assigned) == tariff.id


async def test_account_default_tracking_batches_account_ids(memory_database):
    """Track all generated links without growing one bind list to the size of a bulk flush."""
    user = User(
        email="bulk-tariffs@example.com",
        first_name="Bulk",
        last_name="Tariffs",
        password_hash="unused",
    )
    kind = TariffPlanType(name="Subscription", field_types={})
    async with memory_database.write() as session:
        session.add_all([user, plan("Published", kind, AT)])
    observed = []

    def count_params(_connection, _cursor, statement, parameters, *_):
        """Record the parameter count for default-assignment tracking queries."""
        if statement.startswith("SELECT payment_account_tariff_plans.payment_account_id"):
            observed.append(len(parameters))

    engine = memory_database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", count_params)
    try:
        async with memory_database.write() as session:
            session.add_all(PaymentAccount(user_id=user.id, created_at=AT) for _ in range(1100))
            await session.flush()
            assert (
                await session.scalar(select(func.count()).select_from(PaymentAccountTariffPlan))
                == 1100
            )
    finally:
        event.remove(engine, "before_cursor_execute", count_params)
    assert sum(observed) == 1100
    assert len(observed) > 1
    assert max(observed) < 999
