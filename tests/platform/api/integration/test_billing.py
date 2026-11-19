"""Provider-free billing on the schema installed by Alembic."""

import asyncio
import operator
import sqlite3
from contextlib import nullcontext
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import anyio
import pytest
from alembic import command as alembic
from alembic.util import CommandError
from sqlalchemy import (
    BigInteger,
    Column,
    MetaData,
    Table,
    case,
    create_engine,
    event,
    false,
    func,
    insert,
    inspect,
    literal,
    null,
    select,
    text,
    update,
)
from sqlalchemy.dialects import sqlite
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.engine import Engine
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InvalidRequestError,
    NoResultFound,
    SAWarning,
    StatementError,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable, DropTable

from throng_api.cli import migration_config
from throng_api.config import Settings
from throng_api.database import Database
from throng_api.models import (
    Base,
    Company,
    Coordination,
    PaymentAccount,
    PaymentAccountTariffPlan,
    PaymentAmount,
    PaymentLedgerEntry,
    TariffPlan,
    TariffPlanType,
    TariffValues,
    User,
    UserTariffPlan,
)

pytestmark = pytest.mark.anyio
BILLING_TABLES = {
    "tariff_field_documentation",
    "tariff_plan_types",
    "tariff_plans",
    "user_tariff_plans",
    "payment_accounts",
    "payment_account_tariff_plans",
    "payment_ledger_entries",
}


@pytest.fixture
async def billing_owners(migration_database):
    """Create two independent users and a company through the existing models."""
    alice, bob = (
        User(
            email=f"{name}@example.com",
            password_hash="unused",
            first_name=name,
            last_name="Test",
        )
        for name in ("Alice", "Bob")
    )
    async with migration_database.write() as session:
        session.add_all([alice, bob])
        await session.flush()
        company = Company(name="Example company", created_by_user_id=alice.id)
        session.add(company)
    return alice, bob, company


@pytest.fixture
async def tariff_plans(migration_database):
    """Create three types and two distinct plans of the subscription type."""
    subscription = TariffPlanType(name="Subscription", field_types={"projects": "int"})
    cloud = TariffPlanType(name="Cloud", field_types={"rate": "decimal", "priority": "bool"})
    starter = TariffPlan(name="Starter", plan_type=subscription, field_values={"projects": 1})
    pro = TariffPlan(name="Pro", plan_type=subscription, field_values={"projects": 10})
    compute = TariffPlan(
        name="Compute",
        plan_type=cloud,
        field_values={
            "rate": Decimal("0.012345678901234567890123456789"),
            "priority": True,
        },
    )
    storage = TariffPlan(
        name="Storage",
        plan_type=TariffPlanType(name="Storage", field_types={}),
        field_values={},
    )
    async with migration_database.write() as session:
        session.add_all([starter, pro, compute, storage])
    return starter, pro, compute, storage


@pytest.fixture
async def payment_accounts(migration_database, billing_owners):
    """Persist personal and organizational payment accounts with a default balance."""
    alice, _, company = billing_owners
    personal = PaymentAccount(user_id=alice.id)
    corporate = PaymentAccount(company_id=company.id)
    async with migration_database.write() as session:
        session.add_all([personal, corporate])
    return personal, corporate


@pytest.mark.parametrize("model", [TariffPlanType, TariffPlan])
@pytest.mark.parametrize("name", ["x" * 201, "x" * 200 + " ", "name\0suffix", 42])
async def test_tariff_names_validate_consistently(migration_database, model, name):
    """Refuse invalid names consistently, including names PostgreSQL formerly truncated."""
    if model is TariffPlanType:
        row = TariffPlanType(name=name, field_types={})
    else:
        plan_type = TariffPlanType(name="Base type", field_types={})
        row = TariffPlan(name=name, plan_type=plan_type, field_values={})
    with pytest.raises(
        StatementError,
        match="name_length|name_storage|must be text|invalid byte sequence",
    ):
        async with migration_database.write() as session:
            session.add(row)


async def test_tariff_round_trip_and_mutation(migration_database, tariff_plans):
    """Keep UUIDs, field types and exact Decimals through a reload and in-place edits."""
    starter, _, compute, _ = tariff_plans
    assert UUID(compute.id).version == UUID(compute.tariff_plan_type_id).version == 4
    async with migration_database.write() as session:
        plan = await session.get(TariffPlan, compute.id)
        assert plan.name == "Compute"
        assert plan.plan_type.name == "Cloud"
        assert plan.plan_type.field_types == {"rate": "decimal", "priority": "bool"}
        assert plan.field_values == compute.field_values
        assert type(plan.field_values["rate"]) is Decimal
        plan.field_values["priority"] = False
        plan.plan_type.name = "Cloud compute"
    async with migration_database.read() as session:
        plan = await session.get(TariffPlan, compute.id)
        assert plan.field_values == {**compute.field_values, "priority": False}
        assert plan.plan_type.name == "Cloud compute"
        assert type((await session.get(TariffPlan, starter.id)).field_values["projects"]) is int
        assert (
            await session.scalar(
                select(TariffPlan.field_values["rate"]).where(TariffPlan.id == compute.id)
            )
            == compute.field_values["rate"]
        )
        assert await session.scalar(select(literal(None, type_=TariffValues()))) is None


async def test_tariff_field_dictionaries_persist_in_place_merges(migration_database, tariff_plans):
    """Persist both dictionaries after merging matching fields into a stored plan and type."""
    compute = tariff_plans[2]
    async with migration_database.write() as session:
        plan = await session.get(TariffPlan, compute.id)
        plan.plan_type.field_types |= {"capacity": "int"}
        plan.field_values |= {"capacity": 2}

    async with migration_database.read() as session:
        plan = await session.get(TariffPlan, compute.id)
        assert plan.plan_type.field_types == {**compute.plan_type.field_types, "capacity": "int"}
        assert plan.field_values == {**compute.field_values, "capacity": 2}


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (None, "requires a field schema"),
        ({" ": "int"}, "nonempty strings"),
        ({1: "int"}, "nonempty strings"),
        ({"limit": "float"}, "Unsupported tariff field type"),
        ({"limit": []}, "Unsupported tariff field type"),
    ],
)
async def test_tariff_field_schema_rejects_invalid_fields(migration_database, schema, message):
    """Require a schema with string names and exclusively the supported field types."""
    with pytest.raises(ValueError, match=message):
        async with migration_database.write() as session:
            session.add(TariffPlanType(name="Invalid", field_types=schema))


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (None, "exactly the declared fields"),
        ({"limit": 1, "price": Decimal("0")}, "exactly the declared fields"),
        (
            {"limit": 1, "price": Decimal("0"), "enabled": False, "extra": 0},
            "exactly the declared fields",
        ),
        (
            {"limit": True, "price": Decimal("0"), "enabled": True},
            "'limit' must be int",
        ),
        (
            {"limit": 1, "price": 0.1, "enabled": True},
            "'price' must be int, decimal or bool",
        ),
        (
            {"limit": 1, "price": "0.1", "enabled": True},
            "'price' must be int, decimal or bool",
        ),
        ({1: 1, "price": Decimal("0"), "enabled": True}, "field names must be strings"),
        (
            {"limit": 1, "price": Decimal("NaN"), "enabled": True},
            "'price' must be finite",
        ),
        ({"limit": 1, "price": Decimal("0"), "enabled": 1}, "'enabled' must be bool"),
    ],
)
async def test_tariff_values_reject_incomplete_or_wrong_types(migration_database, values, message):
    """Validate the full schema on new plans, distinguishing bool, int and Decimal."""
    plan_type = TariffPlanType(
        name="Test", field_types={"limit": "int", "price": "decimal", "enabled": "bool"}
    )
    with pytest.raises(ValueError, match=message):
        async with migration_database.write() as session:
            session.add(TariffPlan(name="Invalid", field_values=values, plan_type=plan_type))


@pytest.mark.parametrize("target", ["schema", "values"])
@pytest.mark.parametrize("invalid_name", ["\ud83d\ude00", "\ud800", "name\0suffix"])
async def test_tariff_field_names_require_valid_unicode(migration_database, target, invalid_name):
    """Refuse keys that collapse during JSON encoding or break PostgreSQL JSON access."""
    plan_type = TariffPlanType(name="Type", field_types={"😀": "int"})
    if target == "schema":
        plan_type.field_types[invalid_name] = "int"
        row = plan_type
    else:
        row = TariffPlan(name="Plan", plan_type=plan_type, field_values={"😀": 1, invalid_name: 2})
    with pytest.raises(ValueError, match="Tariff field names must be valid Unicode without NUL"):
        async with migration_database.write() as session:
            session.add(row)


@pytest.mark.parametrize("type_id", [None, "00000000-0000-0000-0000-000000000001"])
async def test_tariff_plan_requires_a_type(migration_database, type_id):
    """Reject missing and unknown types when writing a plan by foreign key."""
    with pytest.raises(IntegrityError, match="NOT NULL|not-null|FOREIGN KEY|foreign key"):
        async with migration_database.write() as session:
            session.add(TariffPlan(name="Invalid", tariff_plan_type_id=type_id, field_values={}))


async def test_edited_tariff_keeps_all_plans_valid(migration_database, tariff_plans):
    """Check mutations of existing plans and all plans affected by an edited field schema."""
    starter, pro, _, storage = tariff_plans
    type_id = starter.tariff_plan_type_id
    with pytest.raises(ValueError, match="'projects' must be int"):
        async with migration_database.write() as session:
            plan = await session.get(TariffPlan, starter.id)
            plan.field_values["projects"] = False
    with pytest.raises(ValueError, match="exactly the declared fields"):
        async with migration_database.write() as session:
            plan_type = await session.get(TariffPlanType, type_id)
            plan_type.field_types["priority"] = "bool"
    async with migration_database.write() as session:
        plan = await session.get(TariffPlan, starter.id)
        obsolete_plan = await session.get(TariffPlan, pro.id)
        assert plan.field_values == {"projects": 1}
        assert plan.plan_type.field_types == {"projects": "int"}
        plan.plan_type.field_types["priority"] = "bool"
        plan.field_values["priority"] = True
        await session.delete(obsolete_plan)
    async with migration_database.write() as session:
        new_plan = TariffPlan(
            name="Team",
            tariff_plan_type_id=type_id,
            field_values={"projects": 20, "priority": True},
        )
        session.add(new_plan)
    with pytest.raises(ValueError, match="exactly the declared fields"):
        async with migration_database.write() as session:
            plan = await session.get(TariffPlan, new_plan.id)
            plan.tariff_plan_type_id = storage.tariff_plan_type_id


@pytest.mark.parametrize(("kind", "value"), [("bool", True), ("decimal", Decimal("1.000"))])
async def test_replacing_tariff_values_persists_a_change_of_type(migration_database, kind, value):
    """Save the new JSON type even when Python considers the old and new values equal."""
    plan = TariffPlan(
        name="Starter",
        plan_type=TariffPlanType(name="Subscription", field_types={"limit": "int"}),
        field_values={"limit": 1},
    )
    async with migration_database.write() as session:
        session.add(plan)
    async with migration_database.write() as session:
        current = await session.get(TariffPlan, plan.id)
        current.plan_type.field_types = {"limit": kind}
        current.field_values = {"limit": value}
    async with migration_database.read() as session:
        saved = await session.get(TariffPlan, plan.id)
        assert saved.plan_type.field_types == {"limit": kind}
        assert saved.field_values["limit"] == value
        assert type(saved.field_values["limit"]) is type(value)


@pytest.mark.parametrize("use_relationship", [True, False])
async def test_new_tariff_cannot_use_a_stale_schema(migration_database, use_relationship):
    """Use the stored schema when a detached type provides an out-of-date snapshot."""
    cached = TariffPlanType(name="Subscription", field_types={"limit": "int"})
    async with migration_database.write() as session:
        session.add(cached)
    async with migration_database.write() as session:
        current = await session.get(TariffPlanType, cached.id)
        current.field_types["limit"] = "bool"
    with pytest.raises(ValueError, match="'limit' must be bool"):
        async with migration_database.write() as session:
            session.add(cached)
            target = (
                {"plan_type": cached} if use_relationship else {"tariff_plan_type_id": cached.id}
            )
            session.add(TariffPlan(name="Invalid", field_values={"limit": 10}, **target))
    async with migration_database.read() as session:
        assert not (await session.scalars(select(TariffPlan.id))).all()


async def test_schema_edit_checks_stored_values_of_detached_plans(migration_database):
    """A cached plan cannot validate a schema edit against a value that is no longer stored."""
    cached = TariffPlan(
        name="Starter",
        plan_type=TariffPlanType(name="Subscription", field_types={"limit": "int"}),
        field_values={"limit": 5},
    )
    async with migration_database.write() as session:
        session.add(cached)
    plan_id = cached.id
    async with migration_database.write() as session:
        current = await session.get(TariffPlan, plan_id)
        current.plan_type.field_types["limit"] = "bool"
        current.field_values["limit"] = True
    with pytest.raises(ValueError, match="'limit' must be int"):
        async with migration_database.write() as session:
            session.add(cached)
            cached.plan_type.field_types["limit"] = "int"
    async with migration_database.read() as session:
        saved = await session.get(TariffPlan, plan_id)
        assert saved.plan_type.field_types == {"limit": "bool"}
        assert saved.field_values["limit"] is True


async def test_tariff_edit_checks_the_stored_type_after_reassignment(
    migration_database, tariff_plans
):
    """Changing values on a detached plan cannot rely on the type it used to reference."""
    cached, _, _, _ = tariff_plans
    plan_id = cached.id
    async with migration_database.write() as session:
        current = await session.get(TariffPlan, plan_id)
        current.plan_type = TariffPlanType(name="Flag", field_types={"projects": "bool"})
        current.field_values["projects"] = True
    with pytest.raises(ValueError, match="'projects' must be bool"):
        async with migration_database.write() as session:
            session.add(cached)
            cached.field_values["projects"] = 8
    async with migration_database.read() as session:
        assert (await session.get(TariffPlan, plan_id)).field_values["projects"] is True


@pytest.mark.parametrize("field_type", ["int", "bool"])
async def test_replacing_a_type_with_the_same_id_checks_existing_plans(
    migration_database, tariff_plans, field_type
):
    """Check the new schema when SQLAlchemy combines a delete and insert into an update."""
    starter, _, _, _ = tariff_plans
    type_id = starter.tariff_plan_type_id
    outcome = (
        pytest.raises(ValueError, match="'projects' must be bool")
        if field_type == "bool"
        else nullcontext()
    )
    with outcome:
        async with migration_database.write() as session:
            await session.delete(await session.get(TariffPlanType, type_id))
            session.add(
                TariffPlanType(id=type_id, name="Replacement", field_types={"projects": field_type})
            )
    async with migration_database.read() as session:
        restored = await session.get(TariffPlanType, type_id)
        assert restored.field_types == {"projects": "int"}
        assert restored.name == ("Replacement" if field_type == "int" else "Subscription")


async def test_ancestor_rollback_restores_the_object_replaced_at_the_same_key(
    migration_database, tariff_plans
):
    """Retain an original instance after an open child replaced it in one flush."""
    type_id = tariff_plans[0].tariff_plan_type_id
    async with migration_database.write() as session:
        original = await session.get(TariffPlanType, type_id)
        outer = await session.begin_nested()
        await session.begin_nested()
        await session.delete(original)
        replacement = TariffPlanType(
            id=type_id, name="Replacement", field_types={"projects": "int"}
        )
        session.add(replacement)
        await session.flush()
        assert await session.get(TariffPlanType, type_id) is replacement

        await outer.rollback()

        assert await session.get(TariffPlanType, type_id) is original
        assert original.name == "Subscription"
        original.name = "Retained"

    async with migration_database.read() as session:
        assert (await session.get(TariffPlanType, type_id)).name == "Retained"


async def test_tariff_ids_are_canonical_before_assignment(migration_database, billing_owners):
    """One UUID cannot name two types or bypass the user's primary key by changing case."""
    alice, _, _ = billing_owners
    type_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    plan_type = TariffPlanType(id=type_id.upper(), name="Subscription", field_types={})
    starter = TariffPlan(name="Starter", plan_type=plan_type, field_values={})
    pro = TariffPlan(name="Pro", plan_type=plan_type, field_values={})
    async with migration_database.write() as session:
        session.add_all([starter, pro])
        await session.flush()
        assert starter.tariff_plan_type_id == type_id
        assert await session.get(TariffPlanType, type_id.upper()) is plan_type
        session.add(UserTariffPlan(user_id=UUID(alice.id), tariff_plan=starter))
    with pytest.raises(IntegrityError, match="pk_tariff_plan_types|UNIQUE constraint"):
        async with migration_database.write() as session:
            session.add(TariffPlanType(id=type_id, name="Duplicate", field_types={}))
    with pytest.raises(IntegrityError, match="pk_user_tariff_plans|UNIQUE constraint"):
        async with migration_database.write() as session:
            session.add(
                UserTariffPlan(
                    user_id=alice.id,
                    tariff_plan_type_id=type_id.upper(),
                    tariff_plan_id=pro.id.upper(),
                )
            )
    async with migration_database.read() as session:
        assert (await session.scalars(select(TariffPlanType.id))).one() == type_id
        assert (await session.scalars(select(UserTariffPlan.tariff_plan_id))).one() == starter.id


async def test_tariff_with_uppercase_foreign_key_loads_its_type_in_same_session(
    migration_database,
):
    """Keep the in-memory foreign key consistent with the UUID used for relationship loading."""
    plan_type = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(plan_type)
    async with migration_database.write() as session:
        plan = TariffPlan(name="Base", tariff_plan_type_id=plan_type.id.upper(), field_values={})
        session.add(plan)
        await session.flush()
        loaded = await session.scalar(select(TariffPlan).where(TariffPlan.id == plan.id))
        assert loaded is plan
        assert loaded.plan_type.id == loaded.tariff_plan_type_id == plan_type.id


@pytest.mark.parametrize("invalid_id", ["A" * 32, "z" * 32, b"a" * 32, "a" * 32 + "\0b"])
async def test_sqlite_billing_uuid_guard_applies_to_raw_writes(migration_database, invalid_id):
    """Store only canonical UUID keys, regardless of the SQLite client's bind processor."""
    if migration_database.engine.dialect.name != "sqlite":
        pytest.skip("PostgreSQL has a native UUID type")
    async with migration_database.engine.begin() as connection:
        with pytest.raises(IntegrityError, match="canonical_uuid"):
            await connection.execute(
                text(
                    "INSERT INTO tariff_plan_types (id, name, created_at, field_types) "
                    "VALUES (:id, 'Raw', CURRENT_TIMESTAMP, '{}')"
                ),
                {"id": invalid_id},
            )


@pytest.mark.parametrize("encoding", ["UTF-16le", "UTF-16be"])
async def test_billing_migration_accepts_uuids_on_utf16_sqlite(tmp_path, encoding):
    """Support a pre-existing SQLite file independently of its text encoding."""
    file = tmp_path / "database.sqlite"
    connection = sqlite3.connect(file)
    try:
        connection.execute(f"PRAGMA encoding='{encoding}'")
        connection.execute("CREATE TABLE encoding_probe (id INTEGER)")
        connection.execute("DROP TABLE encoding_probe")
    finally:
        connection.close()
    settings = Settings(database_url=f"sqlite+aiosqlite:///{file}", _env_file=None)
    config = migration_config(settings)
    await anyio.to_thread.run_sync(alembic.upgrade, config, "0019")
    await anyio.to_thread.run_sync(alembic.upgrade, config, "0020")
    database = Database(settings.database_url)
    try:
        async with database.write() as session:
            plan_type = TariffPlanType(name="Cloud", field_types={})
            session.add(plan_type)
        async with database.read() as session:
            assert (await session.scalars(select(TariffPlanType.id))).one() == plan_type.id
    finally:
        await database.close()


async def test_tariff_update_rejects_signaling_nan(migration_database, tariff_plans):
    """A replacement dictionary can be compared without evaluating a signaling NaN."""
    _, _, compute, _ = tariff_plans
    with pytest.raises(ValueError, match="'rate' must be finite"):
        async with migration_database.write() as session:
            current = await session.get(TariffPlan, compute.id)
            current.field_values = {**current.field_values, "rate": Decimal("sNaN")}


async def test_repeatable_read_cannot_commit_a_schema_for_an_unseen_new_plan(
    migration_database,
):
    """A plan insert changes the type's row version so an older schema writer must retry."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("Use PostgreSQL's repeatable-read isolation")
    plan_type = TariffPlanType(name="Subscription", field_types={"limit": "int"})
    async with migration_database.write() as session:
        session.add(plan_type)
    with pytest.raises(DBAPIError, match="could not serialize access"):
        async with migration_database.sessions.begin() as editor:
            await editor.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            current = await editor.get(TariffPlanType, plan_type.id)
            assert not (await editor.scalars(select(TariffPlan.id))).all()
            async with migration_database.sessions.begin() as writer:
                writer.add(
                    TariffPlan(
                        name="Base",
                        tariff_plan_type_id=plan_type.id,
                        field_values={"limit": 1},
                    )
                )
            current.field_types["limit"] = "bool"
    async with migration_database.read() as session:
        saved = (await session.scalars(select(TariffPlan))).one()
        assert saved.plan_type.field_types == {"limit": "int"}
        assert saved.field_values == {"limit": 1}


async def test_session_dml_cannot_bypass_tariff_validation(migration_database, tariff_plans):
    """Use unit-of-work writes for the models whose JSON requires cross-row validation."""
    starter, _, _, _ = tariff_plans
    async with migration_database.write() as session:
        for statement in (
            insert(TariffPlanType).values(name="Invalid", field_types={"projects": "float"}),
            insert(TariffPlan).values(
                name="Invalid",
                tariff_plan_type_id=starter.tariff_plan_type_id,
                field_values={},
            ),
            update(TariffPlanType).values(field_types={"projects": "bool"}),
            update(TariffPlan).values(field_values={"projects": False}),
        ):
            with pytest.raises(
                InvalidRequestError,
                match="Write tariff types and plans through mapped instances",
            ):
                await session.execute(statement)
        plan = await session.get(TariffPlan, starter.id)
        plan.field_values["projects"] = 2
    async with migration_database.read() as session:
        saved = await session.get(TariffPlan, starter.id)
        assert saved.plan_type.field_types == {"projects": "int"}
        assert saved.field_values == {"projects": 2}


async def test_user_can_hold_different_plan_types_and_replace_a_plan(
    migration_database, billing_owners, tariff_plans
):
    """Allow any number of distinct types per user and independent assignments across users."""
    alice, bob, _ = billing_owners
    starter, pro, compute, storage = tariff_plans
    async with migration_database.write() as session:
        session.add_all(
            [
                UserTariffPlan(user_id=alice.id, tariff_plan=starter),
                UserTariffPlan(user_id=alice.id, tariff_plan=compute),
                UserTariffPlan(user_id=alice.id, tariff_plan=storage),
                UserTariffPlan(user_id=bob.id, tariff_plan=starter),
            ]
        )
    async with migration_database.write() as session:
        assignment = await session.get(UserTariffPlan, (alice.id, starter.tariff_plan_type_id))
        assignment.tariff_plan_id = pro.id
    async with migration_database.read() as session:
        assignments = list(await session.scalars(select(UserTariffPlan)))
        assert {item.tariff_plan.name for item in assignments if item.user_id == alice.id} == {
            "Pro",
            "Compute",
            "Storage",
        }
        assert {item.tariff_plan_id for item in assignments if item.user_id == bob.id} == {
            starter.id
        }


async def test_database_rejects_duplicate_or_forged_user_plan_types(
    migration_database, billing_owners, tariff_plans
):
    """Enforce user/type uniqueness and plan/type consistency on direct inserts and updates."""
    alice, bob, _ = billing_owners
    starter, pro, compute, _ = tariff_plans
    async with migration_database.write() as session:
        session.add_all(
            [
                UserTariffPlan(user_id=alice.id, tariff_plan=starter),
                UserTariffPlan(user_id=alice.id, tariff_plan=compute),
            ]
        )
    for plan in (starter, pro):
        with pytest.raises(IntegrityError, match="pk_user_tariff_plans|UNIQUE constraint"):
            async with migration_database.engine.begin() as connection:
                await connection.execute(
                    insert(UserTariffPlan).values(
                        user_id=alice.id,
                        tariff_plan_type_id=starter.tariff_plan_type_id,
                        tariff_plan_id=plan.id,
                    )
                )
    with pytest.raises(IntegrityError, match="fk_user_tariff_plans_tariff_plan_id|FOREIGN KEY"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                insert(UserTariffPlan).values(
                    user_id=bob.id,
                    tariff_plan_type_id=compute.tariff_plan_type_id,
                    tariff_plan_id=pro.id,
                )
            )
    with pytest.raises(IntegrityError, match="pk_user_tariff_plans|UNIQUE constraint"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                update(UserTariffPlan)
                .where(
                    UserTariffPlan.user_id == alice.id,
                    UserTariffPlan.tariff_plan_type_id == compute.tariff_plan_type_id,
                )
                .values(tariff_plan_type_id=pro.tariff_plan_type_id, tariff_plan_id=pro.id)
            )
    async with migration_database.read() as session:
        assert set(await session.scalars(select(UserTariffPlan.tariff_plan_id))) == {
            starter.id,
            compute.id,
        }


async def test_concurrent_assignments_of_the_same_type_commit_once(
    migration_database, billing_owners, tariff_plans
):
    """Two independent transactions cannot both assign a subscription to one user."""
    alice, _, _ = billing_owners
    starter, pro, _, _ = tariff_plans

    async def assign(plan):
        """Insert an assignment without acquiring the application's coordination lock."""
        async with migration_database.sessions.begin() as session:
            session.add(
                UserTariffPlan(
                    user_id=alice.id,
                    tariff_plan_type_id=plan.tariff_plan_type_id,
                    tariff_plan_id=plan.id,
                )
            )

    results = await asyncio.gather(assign(starter), assign(pro), return_exceptions=True)
    assert results.count(None) == 1
    assert sum(isinstance(result, IntegrityError) for result in results) == 1
    async with migration_database.read() as session:
        assert (await session.scalars(select(UserTariffPlan.tariff_plan_id))).one() in {
            starter.id,
            pro.id,
        }


@pytest.mark.parametrize("new_recipient", [False, True])
async def test_assignment_transfer_can_share_a_flush_with_removing_the_previous_owner(
    migration_database, billing_owners, tariff_plans, new_recipient
):
    """Flush the assignment's new owner before deleting its previous owner."""
    alice, bob, _ = billing_owners
    starter = tariff_plans[0]
    plan_type_id = starter.tariff_plan_type_id
    async with migration_database.write() as session:
        session.add(UserTariffPlan(user_id=bob.id, tariff_plan=starter))

    async with migration_database.write() as session:
        old_owner = await session.get(User, bob.id)
        assignment = await session.get(UserTariffPlan, (bob.id, plan_type_id))
        recipient = alice
        if new_recipient:
            recipient = User(
                id=str(uuid4()),
                email="recipient@example.com",
                password_hash="unused",
                first_name="New",
                last_name="Recipient",
            )
            session.add(recipient)
        assignment.user_id = recipient.id
        await session.delete(old_owner)
        await session.flush()
        assert await session.get(UserTariffPlan, (recipient.id, plan_type_id)) is assignment

    async with migration_database.read() as session:
        moved = await session.get(UserTariffPlan, (recipient.id, plan_type_id))
        assert moved.tariff_plan_id == starter.id
        assert await session.get(User, bob.id) is None


async def test_deleting_user_removes_a_loaded_assignment_from_the_session(
    migration_database, billing_owners, tariff_plans
):
    """Keep an ORM lookup consistent with a database cascade in the same session."""
    _, bob, _ = billing_owners
    starter = tariff_plans[0]
    key = (bob.id, starter.tariff_plan_type_id)
    async with migration_database.write() as session:
        session.add(UserTariffPlan(user_id=bob.id, tariff_plan=starter))

    async with migration_database.write() as session:
        user = await session.get(User, bob.id)
        loaded = await session.get(UserTariffPlan, key)
        assert loaded.tariff_plan_id == starter.id
        await session.delete(user)
        await session.flush()
        assert await session.get(UserTariffPlan, key) is None
        assert await session.get(TariffPlan, starter.id) is not None


@pytest.mark.filterwarnings("error:Identity map already had an identity:sqlalchemy.exc.SAWarning")
@pytest.mark.parametrize("rollback", [False, True])
async def test_cascaded_assignment_does_not_conflict_with_a_reused_key(
    migration_database, billing_owners, tariff_plans, rollback
):
    """Remove cascaded identities so a transfer can target a recreated user."""
    alice, bob, _ = billing_owners
    starter, pro, *_ = tariff_plans
    source_key = (alice.id, starter.tariff_plan_type_id)
    target_key = (bob.id, pro.tariff_plan_type_id)
    async with migration_database.write() as session:
        session.add_all(
            [
                UserTariffPlan(user_id=alice.id, tariff_plan=starter),
                UserTariffPlan(user_id=bob.id, tariff_plan=pro),
            ]
        )

    async with migration_database.write() as session:
        moved = await session.get(UserTariffPlan, source_key)
        removed = await session.get(UserTariffPlan, target_key)
        owner = await session.get(User, bob.id)
        savepoint = await session.begin_nested()
        await session.delete(owner)
        await session.flush()
        session.add(
            User(
                id=bob.id,
                email="recreated@example.com",
                password_hash="unused",
                first_name="Recreated",
                last_name="Owner",
            )
        )
        moved.user_id = bob.id
        await session.flush()
        assert inspect(removed).deleted
        assert await session.get(UserTariffPlan, target_key) is moved
        assert moved.tariff_plan_id == starter.id

        if rollback:
            await savepoint.rollback()
            assert await session.get(UserTariffPlan, source_key) is moved
            assert await session.get(UserTariffPlan, target_key) is removed
            assert removed.tariff_plan_id == pro.id
        else:
            await savepoint.commit()

    async with migration_database.read() as session:
        assignment = await session.get(UserTariffPlan, target_key)
        assert assignment.tariff_plan_id == (pro.id if rollback else starter.id)


async def test_assignment_cascade_normalizes_native_uuid_in_the_owner_identity(
    migration_database, tariff_plans
):
    """A PostgreSQL owner created with uppercase UUID text still loses its assignment."""
    if migration_database.engine.dialect.name == "sqlite":
        pytest.skip("SQLite's existing owner table stores the UUID text as supplied")
    starter = tariff_plans[0]
    owner = User(
        id=str(uuid4()).upper(),
        email="uuid@example.com",
        password_hash="unused",
        first_name="UUID",
        last_name="Owner",
    )
    async with migration_database.write() as session:
        assignment = UserTariffPlan(user=owner, tariff_plan=starter)
        session.add(assignment)
        await session.flush()
        key = (assignment.user_id, assignment.tariff_plan_type_id)
        assert key[0] == owner.id.lower()
        await session.delete(owner)
        await session.flush()
        assert await session.get(UserTariffPlan, key) is None


@pytest.fixture
async def sqlite_case_distinct_owners(tmp_path, request):
    """Create existing SQLite owners with physically distinct UUID spellings."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/case-owners.db", _env_file=None
    )
    config = migration_config(settings)
    await anyio.to_thread.run_sync(alembic.upgrade, config, "0019")
    database = Database(settings.database_url)
    key = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    spelling = getattr(request, "param", "uppercase")
    alternate = {"uppercase": key.upper(), "braced": "{" + key + "}", "dashed": key}[spelling]
    lower, upper = (
        User(
            id=value,
            email=f"case-{label}@example.com",
            password_hash="unused",
            first_name=label,
            last_name="Owner",
        )
        for label, value in (("lower", key), ("upper", alternate))
    )
    company = Company(id=alternate, name="Upper", created_by_user_id=key)
    plan = TariffPlan(
        name="Example", plan_type=TariffPlanType(name="Example", field_types={}), field_values={}
    )
    try:
        async with database.write() as session:
            session.add(lower)
            await session.flush()
            session.add(Company(id=key, name="Lower", created_by_user_id=key))
            if spelling == "dashed":
                # The old schema accepts physical dashed keys through direct SQL.
                await session.execute(
                    insert(User.__table__).values(
                        id=literal(alternate),
                        email=upper.email,
                        password_hash=upper.password_hash,
                        first_name=upper.first_name,
                        last_name=upper.last_name,
                    )
                )
                await session.execute(
                    insert(Company.__table__).values(
                        id=literal(alternate), name=company.name, created_by_user_id=key
                    )
                )
            else:
                session.add_all([upper, company])
        await anyio.to_thread.run_sync(alembic.upgrade, config, "head")
        async with database.write() as session:
            session.add(plan)
        if spelling == "dashed":
            async with database.read() as session:
                upper = await session.scalar(select(User).where(User.email == upper.email))
                company = await session.scalar(select(Company).where(Company.name == company.name))
        yield database, lower, upper, company, plan
    finally:
        await database.close()


@pytest.mark.parametrize("relationship", ["assignment", "user_account", "company_account"])
@pytest.mark.parametrize(
    "sqlite_case_distinct_owners", ["uppercase", "braced", "dashed"], indirect=True
)
@pytest.mark.parametrize(
    "owner_input", ["owner-object", "reloaded-owner", "owner-id", "merge", "typed-insert"]
)
async def test_sqlite_billing_does_not_link_a_different_owner(
    sqlite_case_distinct_owners, relationship, owner_input
):
    """Prevent a relationship from aliasing a physically distinct SQLite owner."""
    database, _, upper_user, upper_company, plan = sqlite_case_distinct_owners
    with pytest.raises((ValueError, StatementError), match="stored SQLite UUID"):
        async with database.write() as session:
            name = "company" if relationship == "company_account" else "user"
            owner = upper_company if name == "company" else upper_user
            if owner_input == "reloaded-owner":
                query = (
                    select(Company).where(Company.name == upper_company.name)
                    if name == "company"
                    else select(User).where(User.email == upper_user.email)
                )
                owner = await session.scalar(query)
            if owner_input in ("owner-object", "reloaded-owner"):
                values = {name: owner}
            else:
                values = {f"{name}_id": owner.id}
            model = UserTariffPlan if relationship == "assignment" else PaymentAccount
            if model is UserTariffPlan:
                values.update(tariff_plan_id=plan.id, tariff_plan_type_id=plan.tariff_plan_type_id)
            if owner_input == "typed-insert":
                await session.execute(insert(model).values(values))
            elif owner_input == "merge":
                await session.merge(model(**values))
            else:
                session.add(model(**values))


@pytest.mark.parametrize("refresh_owner", [False, True])
@pytest.mark.parametrize(
    "sqlite_case_distinct_owners", ["uppercase", "braced", "dashed"], indirect=True
)
async def test_sqlite_owner_cascade_preserves_assignment_at_a_distinct_key(
    sqlite_case_distinct_owners, refresh_owner
):
    """Deleting a distinct legacy owner leaves the canonical owner's assignment loaded."""
    database, lower, upper, _, plan = sqlite_case_distinct_owners
    key = (lower.id, plan.tariff_plan_type_id)
    async with database.write() as session:
        session.add(UserTariffPlan(user_id=lower.id, tariff_plan=plan))

    async with database.write() as session:
        assignment = await session.get(UserTariffPlan, key)
        if refresh_owner:
            session.add(upper)
            await session.refresh(upper)
        await session.delete(upper)
        await session.flush()
        assert await session.get(UserTariffPlan, key) is assignment
        assert assignment.tariff_plan_id == plan.id


@pytest.mark.parametrize("owner_field", ["user_id", "company_id"])
@pytest.mark.parametrize(
    "sqlite_case_distinct_owners", ["uppercase", "braced", "dashed"], indirect=True
)
async def test_sqlite_payment_account_rejects_a_noncanonical_stored_owner(
    sqlite_case_distinct_owners, owner_field
):
    """Require a canonical payment account owner even for SQL that copies a physical key."""
    database, _, upper_user, upper_company, _ = sqlite_case_distinct_owners
    source = (
        select(User.id).where(User.email == upper_user.email)
        if owner_field == "user_id"
        else select(Company.id).where(Company.name == upper_company.name)
    )
    with pytest.raises(IntegrityError, match=f"{owner_field}_canonical_uuid"):
        async with database.write() as session:
            await session.execute(insert(PaymentAccount).from_select([owner_field], source))


@pytest.mark.parametrize("sqlite_case_distinct_owners", ["dashed"], indirect=True)
async def test_sqlite_owner_lookup_keeps_distinct_physical_identities(sqlite_case_distinct_owners):
    """Load the intended account owner even if a legacy spelling is already mapped."""
    database, lower, legacy_user, legacy_company, _ = sqlite_case_distinct_owners
    async with database.write() as session:
        user_account = PaymentAccount(user_id=lower.id)
        company_account = PaymentAccount(company_id=lower.id)
        session.add_all([user_account, company_account])

    async with database.read() as session:
        legacy = await session.scalar(select(User).where(User.email == legacy_user.email))
        company = await session.scalar(select(Company).where(Company.name == legacy_company.name))
        await session.refresh(legacy)
        await session.refresh(company)
        assert legacy.email == legacy_user.email
        assert company.name == legacy_company.name
        account = await session.get(
            PaymentAccount, user_account.id, options=[joinedload(PaymentAccount.user)]
        )
        corporate = await session.get(
            PaymentAccount, company_account.id, options=[joinedload(PaymentAccount.company)]
        )
        assert account.user.email == lower.email
        assert corporate.company.name == "Lower"


async def test_replacing_user_at_the_same_uuid_leaves_assignment_loaded(
    migration_database, billing_owners, tariff_plans
):
    """A replacement object at the same primary key updates the owner instead of deleting it."""
    _, bob, _ = billing_owners
    starter = tariff_plans[0]
    key = (bob.id, starter.tariff_plan_type_id)
    async with migration_database.write() as session:
        session.add(UserTariffPlan(user_id=bob.id, tariff_plan=starter))

    async with migration_database.write() as session:
        owner = await session.get(User, bob.id)
        assignment = await session.get(UserTariffPlan, key)
        await session.delete(owner)
        person = User(
            id=bob.id,
            email="replacement@example.com",
            password_hash="unused",
            first_name="Replacement",
            last_name="Owner",
        )
        session.add(person)
        await session.flush()
        assert assignment.tariff_plan_id == starter.id
        assert await session.get(UserTariffPlan, key) is assignment


async def test_assignment_rollback_restores_its_composite_key_after_releasing_a_child(
    migration_database, billing_owners, tariff_plans
):
    """Keep the assigned tariff attached after two moves are rolled back together."""
    alice, bob, _ = billing_owners
    starter, pro, *_ = tariff_plans
    plan_type_id = starter.tariff_plan_type_id
    third = User(
        email="third@example.com", password_hash="unused", first_name="Third", last_name="Owner"
    )
    async with migration_database.write() as session:
        session.add_all([third, UserTariffPlan(user_id=alice.id, tariff_plan=starter)])

    async with migration_database.write() as session:
        other = await session.get(User, bob.id)
        last = await session.get(User, third.id)
        assignment = await session.get(UserTariffPlan, (alice.id, plan_type_id))
        outer = await session.begin_nested()
        assignment.user = other
        await session.flush()
        child = await session.begin_nested()
        assignment.user = last
        await session.flush()
        await child.commit()

        await outer.rollback()

        assert await session.get(UserTariffPlan, (alice.id, plan_type_id)) is assignment
        assert await session.get(UserTariffPlan, (bob.id, plan_type_id)) is None
        assert await session.get(UserTariffPlan, (third.id, plan_type_id)) is None
        assignment.tariff_plan = await session.get(TariffPlan, pro.id)

    async with migration_database.read() as session:
        assert (
            await session.get(UserTariffPlan, (alice.id, plan_type_id))
        ).tariff_plan_id == pro.id


@pytest.mark.parametrize("from_user_cascade", [False, True])
async def test_ancestor_rollback_restores_deleted_assignment_state(
    migration_database, billing_owners, tariff_plans, from_user_cascade
):
    """Retain the assignment that an open inner savepoint deleted or cascaded."""
    _, bob, _ = billing_owners
    starter, pro, *_ = tariff_plans
    key = (bob.id, starter.tariff_plan_type_id)
    async with migration_database.write() as session:
        session.add(UserTariffPlan(user_id=bob.id, tariff_plan=starter))

    async with migration_database.write() as session:
        owner = await session.get(User, bob.id)
        assignment = await session.get(UserTariffPlan, key)
        outer = await session.begin_nested()
        await session.begin_nested()
        await session.delete(owner if from_user_cascade else assignment)
        await session.flush()
        assert await session.get(UserTariffPlan, key) is None

        await outer.rollback()

        assert await session.get(UserTariffPlan, key) is assignment
        assignment.tariff_plan = await session.get(TariffPlan, pro.id)

    async with migration_database.read() as session:
        assert (await session.get(UserTariffPlan, key)).tariff_plan_id == pro.id


async def test_payment_account_ownership_and_negative_balances_are_database_constraints(
    migration_database, billing_owners, payment_accounts
):
    """Enforce one valid owner and a nonnegative balance including in raw SQL writes."""
    alice, _, company = billing_owners
    personal, corporate = payment_accounts
    assert UUID(personal.id).version == UUID(corporate.id).version == 4
    async with migration_database.read() as session:
        user_account = await session.get(PaymentAccount, personal.id)
        company_account = await session.get(PaymentAccount, corporate.id)
        assert (user_account.user_id, user_account.company_id) == (alice.id, None)
        assert (company_account.user_id, company_account.company_id) == (
            None,
            company.id,
        )
        assert type(user_account.balance) is Decimal
        assert user_account.balance == company_account.balance == Decimal("0")
    for owners in ({}, {"user_id": alice.id, "company_id": company.id}):
        with pytest.raises(IntegrityError, match="exactly_one_owner"):
            async with migration_database.engine.begin() as connection:
                await connection.execute(insert(PaymentAccount).values(**owners))
    with pytest.raises(IntegrityError, match="FOREIGN KEY|fk_payment_accounts_user_id"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(insert(PaymentAccount).values(user_id=str(uuid4())))
    with pytest.raises(IntegrityError, match="nonnegative_balance"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                insert(PaymentAccount).values(user_id=alice.id, balance=text("-1"))
            )
    with pytest.raises(IntegrityError, match="nonnegative_balance"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                update(PaymentAccount)
                .where(PaymentAccount.id == corporate.id)
                .values(balance=text("-1"))
            )


@pytest.mark.parametrize("owner_column", ["user_id", "company_id"])
async def test_payment_account_owner_uuid_is_canonical_in_typed_queries(
    migration_database, billing_owners, payment_accounts, owner_column
):
    """Type both inserts and filters for the same UUID representations as mapped assignments."""
    alice, _, company = billing_owners
    personal, corporate = payment_accounts
    owner_id, existing = (
        (alice.id, personal) if owner_column == "user_id" else (company.id, corporate)
    )
    spelling = (
        owner_id.replace("-", "")
        if migration_database.engine.dialect.name == "sqlite"
        else owner_id.upper()
    )
    owner_field = getattr(PaymentAccount, owner_column)
    async with migration_database.write() as session:
        assert (
            await session.scalar(select(PaymentAccount.id).where(owner_field == spelling))
            == existing.id
        )
        account_id = await session.scalar(
            insert(PaymentAccount).values({owner_field: spelling}).returning(PaymentAccount.id)
        )
        assert getattr(await session.get(PaymentAccount, account_id), owner_column) == owner_id


@pytest.mark.parametrize("owner_kind", ["user", "company"])
@pytest.mark.parametrize("via_relationship", [False, True])
async def test_payment_account_can_share_a_flush_with_new_owner(
    migration_database, billing_owners, owner_kind, via_relationship
):
    """Flush a new owner before its account, including when the child is added first."""
    alice, _, _ = billing_owners
    if owner_kind == "user":
        owner = User(
            id=str(uuid4()),
            email="new@example.com",
            password_hash="unused",
            first_name="New",
            last_name="Owner",
        )
    else:
        owner = Company(id=str(uuid4()), name="New company", created_by_user_id=alice.id)
    owner_id_field = f"{owner_kind}_id"
    ownership = {owner_kind: owner} if via_relationship else {owner_id_field: owner.id}
    account = PaymentAccount(**ownership)
    async with migration_database.write() as session:
        session.add_all([account, owner])
    async with migration_database.read() as session:
        restored = await session.get(PaymentAccount, account.id)
        assert getattr(restored, owner_id_field) == owner.id


@pytest.mark.parametrize("owner_kind", ["user", "company"])
@pytest.mark.parametrize("via_relationship", [False, True])
async def test_ledger_can_share_a_flush_with_a_new_account_and_owner(
    migration_database, billing_owners, owner_kind, via_relationship
):
    """Flush an imported opening entry after its account and owner in one transaction."""
    alice, _, _ = billing_owners
    if owner_kind == "user":
        owner = User(
            email="opening@example.com", password_hash="unused", first_name="New", last_name="Owner"
        )
    else:
        owner = Company(name="New company", created_by_user_id=alice.id)
    account = PaymentAccount(id=str(uuid4()), balance=Decimal(2), **{owner_kind: owner})
    account_reference = (
        {"payment_account": account} if via_relationship else {"payment_account_id": account.id}
    )
    entry = PaymentLedgerEntry(operation_id="opening", amount=Decimal(2), **account_reference)
    async with migration_database.write() as session:
        if via_relationship:
            session.add(entry)
        else:
            session.add_all([entry, account, owner])

    async with migration_database.read() as session:
        restored = await session.get(PaymentLedgerEntry, entry.id)
        assert restored.payment_account_id == account.id
        assert restored.amount == (await session.get(PaymentAccount, account.id)).balance


@pytest.mark.parametrize(
    "amount", [0.1, Decimal("Infinity"), Decimal("1000000000000"), Decimal("0.0000001")]
)
async def test_unrepresentable_payment_amounts_fail_before_writing(migration_database, amount):
    """Refuse amounts that would be approximated, non-finite or outside the range."""
    with pytest.raises(StatementError, match="Payment amount"):
        async with migration_database.read() as session:
            await session.scalar(select(literal(amount, type_=PaymentAmount())))


@pytest.mark.parametrize(
    ("dialect", "stored"),
    [(sqlite.dialect(), 1234567), (asyncpg.dialect(), Decimal("1.234567"))],
)
def test_payment_amount_dialect_storage(dialect, stored):
    """Bind exact integer millionths in SQLite and native Decimal values in asyncpg."""
    column_type = PaymentAmount()
    bound = column_type.process_bind_param(Decimal("1.234567"), dialect)
    assert type(bound) is type(stored) and bound == stored
    assert column_type.process_result_value(stored, dialect) == Decimal("1.234567")
    assert column_type.process_bind_param(None, dialect) is None
    assert column_type.process_result_value(None, dialect) is None
    assert column_type.compare_values(Decimal("1.2345670"), Decimal("1.234567"))
    assert column_type.compare_values(None, None)


@pytest.mark.parametrize("field", ["balance", "amount"])
@pytest.mark.parametrize("invalid", [1, 1.0, True, Decimal("sNaN")])
async def test_replacing_a_payment_amount_checks_the_new_type(
    migration_database, payment_accounts, field, invalid
):
    """Reject non-Decimal values even if equal to the stored amount, and reject signaling NaN."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        entry = await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id="credit",
            amount=Decimal(1),
        )
    model, row_id = (
        (PaymentAccount, personal.id) if field == "balance" else (PaymentLedgerEntry, entry.id)
    )
    with pytest.raises(StatementError, match="finite Decimals"):
        async with migration_database.write() as session:
            row = await session.get(model, row_id)
            setattr(row, field, invalid)
            await session.flush()
    async with migration_database.read() as session:
        saved = await session.get(model, row_id)
        value = getattr(saved, field)
        assert type(value) is Decimal and value == Decimal(1)


async def test_database_rejects_non_numeric_money_storage(migration_database, payment_accounts):
    """Reject invalid storage values even when writes bypass the Decimal bind processor."""
    personal, _ = payment_accounts
    with pytest.raises(IntegrityError, match="balance_(storage|finite)"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                update(PaymentAccount)
                .where(PaymentAccount.id == personal.id)
                .values(balance=text("'NaN'"))
            )
    with pytest.raises(IntegrityError, match="amount_(storage|finite)"):
        async with migration_database.engine.begin() as connection:
            await connection.execute(
                insert(PaymentLedgerEntry).values(
                    payment_account_id=personal.id,
                    operation_id="invalid",
                    amount=text("'NaN'"),
                )
            )


@pytest.mark.parametrize(
    "operation_id",
    ["", " ", "\t\n", "x" * 129, "x" * 128 + " ", b"bytes", "x\0" + "x" * 129, 42],
)
async def test_invalid_ledger_operation_ids_cannot_change_the_balance(
    migration_database, payment_accounts, operation_id
):
    """Enforce nonempty bounded operation identifiers and roll back the attempted posting."""
    personal, _ = payment_accounts
    with pytest.raises(
        StatementError,
        match="valid_operation_id|operation_id_storage|must be text|invalid byte sequence",
    ):
        async with migration_database.write() as session:
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id=operation_id,
                amount=Decimal("1"),
            )
    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)
        assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()


@pytest.mark.parametrize("comment", [b"bytes", 42, "before\0after"])
async def test_invalid_payment_comments_do_not_post_money(
    migration_database, payment_accounts, comment
):
    """Keep comments as text and roll back any related balance changes on invalid input."""
    personal, _ = payment_accounts
    with pytest.raises(StatementError, match="comment_storage|must be text|invalid byte sequence"):
        async with migration_database.write() as session:
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="credit",
                amount=Decimal("1"),
                comment=comment,
            )
    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)
        assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()


@pytest.mark.parametrize("op", [operator.mul, operator.truediv, operator.floordiv, operator.mod])
def test_payment_amounts_reject_sql_operators_that_do_not_preserve_scale(op):
    """Prevent ordinary ORM expressions from multiplying or dividing the scaled SQLite value."""
    with pytest.raises(TypeError, match="Use Decimal arithmetic"):
        select(op(PaymentAccount.balance, Decimal(2)))
    with pytest.raises(TypeError, match="Use Decimal arithmetic"):
        select(op(Decimal(2), PaymentAccount.balance))
    with pytest.raises(TypeError, match="Use Decimal arithmetic"):
        select(op(Decimal(2), PaymentAccount.__table__.c.balance))


async def test_payment_amount_addition_and_subtraction_are_exact(
    migration_database, payment_accounts
):
    """Keep additive ORM expressions in currency units in either operand order."""
    personal, _ = payment_accounts
    balance = PaymentAccount.__table__.c.balance
    async with migration_database.read() as session:
        assert await session.scalar(
            select(Decimal("1.123456") + balance - Decimal("0.123455"))
            .where(PaymentAccount.id == personal.id)
            .order_by(balance.desc())
        ) == Decimal("1.000001")


async def test_payment_amounts_require_scaled_sql_operands(migration_database, payment_accounts):
    """Prevent two SQL operands with different units from producing a plausible wrong result."""
    account, _ = payment_accounts
    with pytest.raises(TypeError, match="PaymentAmount SQL operands"):
        select(PaymentAccount.balance + literal(Decimal(2)))
    with pytest.raises(TypeError, match="PaymentAmount SQL operands"):
        select(PaymentAccount.balance < literal(Decimal(2)))
    with pytest.raises(TypeError, match="PaymentAmount SQL operands"):
        select(PaymentAccount.balance == literal(Decimal(2)))
    threshold = Table("thresholds", MetaData(), Column("value", BigInteger))
    with pytest.raises(TypeError, match="PaymentAmount SQL operands"):
        select(PaymentAccount.balance == threshold.c.value)
    with pytest.raises(TypeError, match="PaymentAmount SQL operands"):
        select(PaymentAccount.balance.is_distinct_from(literal(Decimal(2))))
    with pytest.raises(TypeError, match="PaymentAmount SQL operands"):
        select(PaymentAccount.balance.is_not_distinct_from(literal(Decimal(2))))
    async with migration_database.write() as session:
        assert await session.scalar(
            select(PaymentAccount.balance + literal(Decimal(2), type_=PaymentAmount())).where(
                PaymentAccount.id == account.id
            )
        ) == Decimal(2)
        assert (
            await session.scalar(
                select(PaymentAccount.balance.is_distinct_from(null())).where(
                    PaymentAccount.id == account.id
                )
            )
            is True
        )
        updated = await session.scalar(
            update(PaymentAccount)
            .where(PaymentAccount.id == account.id)
            .values(balance=PaymentAccount.balance + Decimal(0))
            .returning(PaymentAccount)
            .execution_options(synchronize_session="fetch")
        )
        assert updated.balance == Decimal(0)


@pytest.mark.parametrize("attribute", ["balance", "amount"])
@pytest.mark.parametrize(
    "expression",
    [
        literal(Decimal(1)),
        literal(Decimal(1), type_=PaymentAmount()),
        case((false(), literal(Decimal(2), type_=PaymentAmount())), else_=Decimal(1)),
    ],
    ids=["numeric-literal", "payment-literal", "case"],
)
async def test_mapped_payment_fields_require_decimal_values(
    migration_database, payment_accounts, attribute, expression
):
    """Use Decimal assignments so each amount goes through its own bind processor."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        if attribute == "balance":
            row = await session.get(PaymentAccount, personal.id)
        else:
            row = PaymentLedgerEntry(payment_account_id=personal.id, operation_id="opening")
            session.add(row)

        with pytest.raises(TypeError, match="require a Python Decimal"):
            setattr(row, attribute, expression)
        setattr(row, attribute, Decimal(2))
        await session.flush()
        await session.refresh(row)
        assert getattr(row, attribute) == Decimal(2)


async def test_payment_amount_in_checks_literal_lists_and_subqueries(
    migration_database, payment_accounts
):
    """Check the actual SQL operands in membership tests after SQLAlchemy coerces them."""
    tiny, whole = payment_accounts
    async with migration_database.write() as session:
        for account, amount in ((tiny, Decimal("0.000001")), (whole, Decimal("1"))):
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=account.id,
                operation_id="credit",
                amount=amount,
            )
    raw = literal(Decimal(1))
    scaled = literal(Decimal(1), type_=PaymentAmount())
    async with migration_database.read() as session:
        for predicate, expected in (
            (PaymentAccount.balance.in_, whole.id),
            (PaymentAccount.balance.not_in, tiny.id),
        ):
            for operand in ([raw], select(raw)):
                with pytest.raises(TypeError, match="PaymentAmount SQL operands"):
                    select(PaymentAccount.id).where(predicate(operand))
            for operand in ([Decimal(1)], [scaled], select(scaled)):
                matches = await session.scalars(select(PaymentAccount.id).where(predicate(operand)))
                assert matches.one() == expected


async def test_post_payment_records_exact_balances_and_ledger_history(
    migration_database, payment_accounts
):
    """Post a credit and a debit with distinct UUIDs and a shared logical operation ID."""
    personal, corporate = payment_accounts
    operation_id = str(uuid4())
    async with migration_database.write() as session:
        account = await session.get(PaymentAccount, personal.id)
        credit = await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id.upper(),
            operation_id=operation_id,
            amount=Decimal("999999999999.999999"),
            comment="Credit",
        )
        assert credit.payment_account_id == personal.id
        assert account.balance == Decimal("999999999999.999999")
        debit = await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id=operation_id,
            amount=Decimal("-0.000001"),
        )
        assert account.balance == Decimal("999999999999.999998")
    async with migration_database.read() as session:
        entries = {
            entry.id: entry
            for entry in await session.scalars(
                select(PaymentLedgerEntry).where(PaymentLedgerEntry.operation_id == operation_id)
            )
        }
        assert UUID(credit.id).version == UUID(debit.id).version == 4 and credit.id != debit.id
        assert set(entries) == {credit.id, debit.id}
        assert {item.payment_account_id for item in entries.values()} == {personal.id}
        assert entries[credit.id].amount == Decimal("999999999999.999999")
        assert entries[credit.id].comment == "Credit"
        assert entries[debit.id].amount == Decimal("-0.000001")
        assert entries[debit.id].comment is None
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(
            "999999999999.999998"
        )
        assert (await session.get(PaymentAccount, corporate.id)).balance == Decimal("0")


async def test_rejected_payment_insert_rolls_back_the_balance(migration_database, payment_accounts):
    """A repeated UUID cannot change the balance twice, and every entry needs an account."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        original = await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id="credit",
            amount=Decimal("3.80"),
            entry_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
    with pytest.raises(IntegrityError, match="pk_payment_ledger_entries|UNIQUE constraint"):
        async with migration_database.write() as session:
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="credit",
                amount=Decimal("3.80"),
                entry_id=original.id.upper(),
            )
    with pytest.raises(NoResultFound, match="Payment account not found"):
        async with migration_database.write() as session:
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=str(uuid4()),
                operation_id="missing",
                amount=Decimal("1"),
            )
    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal("3.80")
        assert (await session.scalars(select(PaymentLedgerEntry.id))).one() == original.id


async def test_post_rejects_a_sql_expression_as_the_amount(migration_database, payment_accounts):
    """Apply the same immutable Decimal to the balance and the logged amount."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(1)
        )
        current_balance = (
            select(PaymentAccount.balance).where(PaymentAccount.id == personal.id).scalar_subquery()
        )
        with pytest.raises(TypeError, match="Posting amount must be a Decimal"):
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="repeat balance",
                amount=current_balance,
            )
        assert await session.scalar(select(current_balance)) == Decimal(1)
        assert (await session.scalars(select(PaymentLedgerEntry.amount))).all() == [Decimal(1)]


@pytest.mark.parametrize("relationship_name", ["user", "company"])
@pytest.mark.parametrize("rejected", [False, True])
async def test_post_preserves_loaded_payment_account_owner(
    migration_database, payment_accounts, relationship_name, rejected
):
    """Keep the loaded owner through successful and rejected postings."""
    personal, corporate = payment_accounts
    account_id = personal.id if relationship_name == "user" else corporate.id
    async with migration_database.write() as session:
        account = await session.get(
            PaymentAccount,
            account_id,
            options=[selectinload(getattr(PaymentAccount, relationship_name))],
        )
        owner = getattr(account, relationship_name)
        assert owner.id == getattr(account, f"{relationship_name}_id")
        outcome = (
            pytest.raises(IntegrityError, match="valid_operation_id") if rejected else nullcontext()
        )
        with outcome:
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=account_id,
                operation_id="" if rejected else "credit",
                amount=Decimal(1),
            )
        assert getattr(account, relationship_name) is owner
        assert account.balance == Decimal(0 if rejected else 1)


@pytest.mark.parametrize("after_rollback", [False, True])
async def test_post_preserves_expiration_until_the_account_is_reloaded(
    migration_database, payment_accounts, after_rollback
):
    """Refresh all account fields on get after posting to an expired instance."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        account = await session.get(PaymentAccount, personal.id)
        if after_rollback:
            savepoint = await session.begin_nested()
            await PaymentLedgerEntry.post(
                session, payment_account_id=personal.id, operation_id="reverted", amount=Decimal(1)
            )
            await savepoint.rollback()
        else:
            session.expire(account)
        assert inspect(account).expired

        await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(2)
        )

        reloaded = await session.get(PaymentAccount, personal.id)
        assert reloaded is account
        assert reloaded.user_id == personal.user_id
        assert reloaded.balance == Decimal(2)


async def test_post_rejects_an_account_created_after_its_initial_lookup(
    migration_database, billing_owners, monkeypatch
):
    """Reject the post even if a concurrent transaction creates the missing account."""
    alice, _, _ = billing_owners
    account_id = str(uuid4())
    async with migration_database.sessions.begin() as session:
        if migration_database.engine.dialect.name == "sqlite":
            original_connection = session.connection

            async def postgres_lookup_policy(*args, **kwargs):
                """Exercise the PostgreSQL lookup policy with real SQL in SQLite CI."""
                await original_connection(*args, **kwargs)
                return SimpleNamespace(dialect=asyncpg.dialect())

            monkeypatch.setattr(session, "connection", postgres_lookup_policy)
        original_scalar = session.scalar

        async def create_account_after_lookup(statement, *args, **kwargs):
            """Commit the account after the posting attempt has observed its absence."""
            assert statement.is_select
            result = await original_scalar(statement, *args, **kwargs)
            assert result is None
            async with migration_database.write() as other:
                other.add(PaymentAccount(id=account_id, user_id=alice.id))
            return result

        monkeypatch.setattr(session, "scalar", create_account_after_lookup)
        with pytest.raises(NoResultFound, match="Payment account not found"):
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=account_id,
                operation_id="credit",
                amount=Decimal("1"),
            )
    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, account_id)).balance == Decimal("0")
        assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()


async def test_post_flushes_invalid_pending_data_before_changing_balance(
    migration_database, payment_accounts
):
    """Recover from a pending validation error with autoflush disabled and commit safely."""
    personal, _ = payment_accounts
    async with migration_database.sessions(autoflush=False) as session, session.begin():
        session.add(
            TariffPlan(
                name="Invalid",
                plan_type=TariffPlanType(name="Test", field_types={}),
                field_values=None,
            )
        )
        with pytest.raises(ValueError, match="exactly the declared fields"):
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="credit",
                amount=Decimal("1"),
            )
        session.expunge_all()
    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal("0")
        assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()


@pytest.mark.parametrize("outer_write", [False, True])
async def test_outer_rollback_includes_released_sqlite_savepoint(memory_database, outer_write):
    """Keep posted money inside its outer SQLite transaction after releasing a savepoint."""
    async with memory_database.write() as session:
        owner = User(
            email="billing@example.com",
            password_hash="unused",
            first_name="Test",
            last_name="Owner",
        )
        session.add(owner)
        await session.flush()
        account = PaymentAccount(user_id=owner.id)
        session.add(account)
    async with memory_database.sessions() as session:
        outer = await session.begin()
        if outer_write:
            await session.execute(update(Coordination).values(revision=Coordination.revision + 1))
        async with session.begin_nested():
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=account.id,
                operation_id="credit",
                amount=Decimal(1),
            )
        await outer.rollback()
    async with memory_database.read() as session:
        assert (await session.get(PaymentAccount, account.id)).balance == Decimal(0)
        assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()


@pytest.mark.parametrize("caller_savepoint", [False, True])
@pytest.mark.parametrize("manual_begin", [False, True])
async def test_post_in_standard_sqlite_session_stays_inside_outer_transaction(
    caller_savepoint, manual_begin
):
    """A standalone SQLite engine can use either its driver or SQLAlchemy to manage BEGIN."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    if manual_begin:

        @event.listens_for(engine.sync_engine, "connect")
        def disable_implicit_begin(connection, _):
            connection.isolation_level = None

        @event.listens_for(engine.sync_engine, "begin")
        def begin_transaction(connection):
            connection.exec_driver_sql("BEGIN")

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions.begin() as session:
            owner = User(
                email="sqlite@example.com",
                password_hash="unused",
                first_name="A",
                last_name="B",
            )
            session.add(owner)
            await session.flush()
            account = PaymentAccount(user_id=owner.id)
            session.add(account)
        async with sessions() as session:
            outer = await session.begin()
            async with session.begin_nested() if caller_savepoint else nullcontext():
                assert await session.scalar(select(PaymentAccount.id)) == account.id
                await PaymentLedgerEntry.post(
                    session,
                    payment_account_id=account.id,
                    operation_id="credit",
                    amount=Decimal(1),
                )
            await outer.rollback()
        async with sessions() as session:
            assert (await session.get(PaymentAccount, account.id)).balance == Decimal(0)
            assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()
    finally:
        await engine.dispose()


async def test_session_close_rolls_back_post_with_an_open_savepoint(
    migration_database, payment_accounts
):
    """Close an uncommitted session with an active caller savepoint and a successful post."""
    personal, _ = payment_accounts
    async with migration_database.sessions() as session:
        await session.begin_nested()
        entry = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(1)
        )
        entry_id = entry.id
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(1)

    async with migration_database.read() as session:
        assert await session.get(PaymentLedgerEntry, entry_id) is None
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)


@pytest.fixture(params=["sqlalchemy", "driver", "skip_rollback"])
async def autocommit_sqlite_engine(request, tmp_path):
    """Distinguish SQLAlchemy and Python driver autocommit modes on separate connections."""
    if request.param == "driver":
        if not hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"):
            pytest.skip("sqlite3 driver autocommit needs Python 3.12")
        options = {"connect_args": {"autocommit": True}}
    else:
        options = {"isolation_level": "AUTOCOMMIT"}
        if request.param == "skip_rollback":
            options["skip_autocommit_rollback"] = True
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'autocommit.sqlite'}",
        poolclass=NullPool,
        **options,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        await engine.dispose()


async def test_sqlite_savepoint_does_not_change_explicit_engine_autocommit(
    autocommit_sqlite_engine,
):
    """Keep ordinary Engine savepoints in the transaction mode selected by the caller."""
    async with autocommit_sqlite_engine.begin() as connection:
        async with connection.begin_nested():
            await connection.execute(insert(Coordination).values(id=1, revision=3))
        async with autocommit_sqlite_engine.connect() as reader:
            assert await reader.scalar(select(Coordination.revision)) == 3


@pytest.mark.parametrize("isolation_level", ["IMMEDIATE", "EXCLUSIVE"])
async def test_sqlite_savepoint_keeps_the_configured_write_reservation(tmp_path, isolation_level):
    """Open the physical parent with the configured lock before the first savepoint."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'savepoint.sqlite'}",
        connect_args={"isolation_level": isolation_level, "timeout": 0},
    )
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.run_sync(Coordination.__table__.create)
            await connection.execute(insert(Coordination).values(id=1, revision=0))
        async with engine.connect() as first:
            async with first.begin_nested():
                assert await first.scalar(select(Coordination.revision)) == 0
                with pytest.raises(DBAPIError, match="database is locked"):
                    async with engine.begin() as other:
                        await other.execute(update(Coordination).values(revision=2))
                await first.execute(update(Coordination).values(revision=1))
            await first.rollback()
        async with engine.connect() as reader:
            assert await reader.scalar(select(Coordination.revision)) == 0
    finally:
        await engine.dispose()


def test_sqlite_savepoints_on_a_synchronous_connection():
    """Keep an ordinary synchronous sqlite3 savepoint within the outer transaction."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE example (value INT)"))
        with engine.connect() as conn:
            with conn.begin_nested():
                conn.execute(text("INSERT INTO example VALUES (1)"))
            conn.rollback()
            assert conn.scalar(text("SELECT COUNT(*) FROM example")) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("caller_savepoint", [False, True])
async def test_post_requires_transactional_sqlite_driver(
    autocommit_sqlite_engine, caller_savepoint
):
    """Refuse an autocommit connection before any money or ledger records change."""
    sessions = async_sessionmaker(autocommit_sqlite_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        owner = User(
            email="autocommit@example.com",
            password_hash="unused",
            first_name="A",
            last_name="B",
        )
        session.add(owner)
        await session.flush()
        account = PaymentAccount(user_id=owner.id)
        session.add(account)
    async with sessions.begin() as session:
        if autocommit_sqlite_engine.dialect.skip_autocommit_rollback:
            await session.execute(text("BEGIN"))
        async with session.begin_nested() if caller_savepoint else nullcontext():
            assert await session.scalar(select(PaymentAccount.id)) == account.id
            with pytest.raises(InvalidRequestError, match="transactional SQLite"):
                await PaymentLedgerEntry.post(
                    session,
                    payment_account_id=account.id,
                    operation_id="credit",
                    amount=Decimal(1),
                )
    async with sessions() as session:
        assert (await session.get(PaymentAccount, account.id)).balance == Decimal(0)
        assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()


async def test_failed_post_restores_balance_without_cached_account(
    migration_database, payment_accounts
):
    """A failed insert leaves the balance unchanged when the account was absent from the cache."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        with pytest.raises(IntegrityError, match="valid_operation_id") as failure:
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="",
                amount=Decimal(12),
            )
        assert failure.value is not None
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)


@pytest.mark.parametrize("intermediate_savepoint", [False, True])
@pytest.mark.parametrize("loaded_before_rollback", [False, True])
async def test_callers_savepoint_rollback_expires_balances_loaded_after_post(
    migration_database, payment_accounts, intermediate_savepoint, loaded_before_rollback
):
    """Invalidate an account loaded only after a post when the caller rolls back its savepoint."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        outer = await session.begin_nested()
        if intermediate_savepoint:
            async with session.begin_nested():
                await PaymentLedgerEntry.post(
                    session,
                    payment_account_id=personal.id,
                    operation_id="credit",
                    amount=Decimal(5),
                )
        else:
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="credit",
                amount=Decimal(5),
            )
        account = None
        if loaded_before_rollback:
            account = await session.get(PaymentAccount, personal.id)
            assert account.balance == Decimal(5)
        await outer.rollback()
        restored = await session.get(PaymentAccount, personal.id)
        if loaded_before_rollback:
            assert restored is account
        assert restored.balance == Decimal(0)
        assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()


async def test_ancestor_savepoint_rollback_discards_cached_posts_in_open_child(
    migration_database, payment_accounts
):
    """Closing a child during an ancestor rollback discards its cached balance and entry."""
    personal, corporate = payment_accounts
    async with migration_database.write() as session:
        prior = await PaymentLedgerEntry.post(
            session,
            payment_account_id=corporate.id,
            operation_id="prior",
            amount=Decimal(3),
        )
        saved = await session.get(PaymentAccount, corporate.id)
        outer = await session.begin_nested()
        inner = await session.begin_nested()
        entry = await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id="credit",
            amount=Decimal(5),
        )
        entry_id = entry.id
        account = await session.get(PaymentAccount, personal.id)
        assert account.balance == Decimal(5)

        await outer.rollback()

        assert not inner.is_active
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)
        assert await session.get(PaymentLedgerEntry, entry_id) is None
        assert saved.balance == Decimal(3)
        assert await session.get(PaymentLedgerEntry, prior.id) is prior


@pytest.mark.parametrize(
    ("prior_exists", "cached"), [(True, "kept"), (True, "expunged"), (False, "reloaded")]
)
async def test_savepoint_rollback_restores_the_prior_ledger_identity(
    migration_database, payment_accounts, prior_exists, cached
):
    """Discard the reverted post while retaining a restored row with the same UUID."""
    personal, _ = payment_accounts
    entry_id = str(uuid4())
    if prior_exists:
        async with migration_database.write() as session:
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="original",
                amount=Decimal(1),
                entry_id=entry_id,
            )

    async with migration_database.write() as session:
        original = await session.get(PaymentLedgerEntry, entry_id)
        outer = await session.begin_nested()
        if original is not None:
            await session.delete(original)
            await session.flush()
        replacement = await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id="replacement",
            amount=Decimal(2),
            entry_id=entry_id,
        )
        if cached != "kept":
            session.expunge(replacement)
        if cached == "reloaded":
            replacement = await session.get(PaymentLedgerEntry, entry_id)
            assert replacement.operation_id == "replacement"

        await outer.rollback()

        assert await session.get(PaymentLedgerEntry, entry_id) is original
        if original is not None:
            original.comment = "retained"
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(prior_exists)

    if prior_exists:
        async with migration_database.read() as session:
            restored = await session.get(PaymentLedgerEntry, entry_id)
            assert restored.operation_id == "original" and restored.comment == "retained"


@pytest.mark.parametrize("make_replacement", [False, True])
async def test_savepoint_rollback_discards_a_reloaded_and_deleted_post(
    migration_database, payment_accounts, make_replacement
):
    """A ledger object loaded after posting cannot be resurrected by a nested rollback."""
    personal, _ = payment_accounts
    entry_id = str(uuid4())
    async with migration_database.write() as session:
        outer = await session.begin_nested()
        await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id="credit",
            amount=Decimal(1),
            entry_id=entry_id,
        )
        loaded = await session.get(PaymentLedgerEntry, entry_id)
        await session.delete(loaded)
        await session.flush()
        if make_replacement:
            replacement = await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="replacement",
                amount=Decimal(2),
                entry_id=entry_id,
            )
            assert await session.get(PaymentLedgerEntry, entry_id) is replacement

        await outer.rollback()

        assert await session.get(PaymentLedgerEntry, entry_id) is None
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)


@pytest.mark.parametrize("open_child", [False, True])
async def test_savepoint_rollback_discards_a_post_reloaded_under_a_changed_uuid(
    migration_database, payment_accounts, open_child
):
    """Track the ledger row across a flush that changes its UUID before rollback."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        outer = await session.begin_nested()
        if open_child:
            await session.begin_nested()
        entry = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(1)
        )
        old_id, new_id = entry.id, str(uuid4())
        entry.id = new_id
        await session.flush()
        session.expunge(entry)
        reloaded = await session.get(PaymentLedgerEntry, new_id)
        assert reloaded is not None and reloaded is not entry

        await outer.rollback()

        assert await session.get(PaymentLedgerEntry, old_id) is None
        assert await session.get(PaymentLedgerEntry, new_id) is None
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)


async def test_nested_ledger_loads_keep_only_committed_entries_after_rollback(
    migration_database, payment_accounts
):
    """Track reloaded posts through ancestor scopes and retain unrelated committed rows."""
    personal, corporate = payment_accounts
    async with migration_database.write() as session:
        durable = await PaymentLedgerEntry.post(
            session, payment_account_id=corporate.id, operation_id="committed", amount=Decimal(1)
        )

    async with migration_database.write() as session:
        outer = await session.begin_nested()
        pending = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="pending", amount=Decimal(2)
        )
        pending_id = pending.id
        session.expunge(pending)
        async with session.begin_nested():
            committed = await session.get(PaymentLedgerEntry, durable.id)
            reloaded = await session.get(PaymentLedgerEntry, pending_id)
            assert committed.operation_id == "committed"
            assert reloaded.operation_id == "pending"

        await outer.rollback()

        assert await session.get(PaymentLedgerEntry, durable.id) is committed
        assert await session.get(PaymentLedgerEntry, pending_id) is None


async def test_savepoint_rollback_drops_the_detached_ledger_state_for_a_reverted_key(
    migration_database, payment_accounts
):
    """Never return a detached ledger object that rollback restored at its old UUID."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        outer = await session.begin_nested()
        posted = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(1)
        )
        old_id, new_id = posted.id, str(uuid4())
        session.expunge(posted)
        reloaded = await session.get(PaymentLedgerEntry, old_id)
        reloaded.id = new_id
        await session.flush()
        session.expunge(reloaded)

        await outer.rollback()

        assert await session.get(PaymentLedgerEntry, old_id) is None
        assert await session.get(PaymentLedgerEntry, new_id) is None
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)


@pytest.mark.parametrize("released_child", [False, True])
@pytest.mark.parametrize("reloaded", [False, True])
async def test_nested_ledger_key_change_is_tracked_in_its_own_savepoint(
    migration_database, payment_accounts, released_child, reloaded
):
    """Roll back a rekey independently of the savepoint that created the ledger row."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        outer = await session.begin_nested()
        entry = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(1)
        )
        old_id, new_id = entry.id, str(uuid4())
        child = await session.begin_nested()
        entry.id = new_id
        await session.flush()
        if reloaded:
            session.expunge(entry)
            changed = await session.get(PaymentLedgerEntry, new_id)
            assert changed is not None and changed is not entry

        if released_child:
            await child.commit()
            await outer.rollback()
            assert await session.get(PaymentLedgerEntry, old_id) is None
            assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)
        else:
            await child.rollback()
            restored = await session.get(PaymentLedgerEntry, old_id)
            assert restored.operation_id == "credit"
            if not reloaded:
                assert restored is entry
            assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(1)
            await outer.commit()
        assert await session.get(PaymentLedgerEntry, new_id) is None


@pytest.mark.parametrize("reloaded", [False, True])
async def test_ancestor_rollback_clears_a_posted_account_at_its_changed_uuid(
    migration_database, payment_accounts, reloaded
):
    """Discard stale account identities when rollback skips an open child's ORM snapshot."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        outer = await session.begin_nested()
        await session.begin_nested()
        entry = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(5)
        )
        entry_id = entry.id
        account = await session.get(PaymentAccount, personal.id)
        assert account.balance == Decimal(5)
        await session.delete(entry)
        await session.flush()
        new_id = str(uuid4())
        account.id = new_id
        await session.flush()
        if reloaded:
            session.expunge(account)
            account = await session.get(PaymentAccount, new_id)
            assert account.balance == Decimal(5)

        await outer.rollback()

        assert await session.get(PaymentAccount, new_id) is None
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)
        assert await session.get(PaymentLedgerEntry, entry_id) is None


async def test_savepoint_restores_account_renamed_before_and_after_posting(
    migration_database, payment_accounts
):
    """Retain the original account object when the first key change precedes the post."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        account = await session.get(PaymentAccount, personal.id)
        savepoint = await session.begin_nested()
        intermediate_id, final_id = str(uuid4()), str(uuid4())
        account.id = intermediate_id
        await session.flush()
        entry = await PaymentLedgerEntry.post(
            session, payment_account_id=intermediate_id, operation_id="credit", amount=Decimal(1)
        )
        entry_id = entry.id
        await session.delete(entry)
        await session.flush()
        account.id = final_id
        await session.flush()

        await savepoint.rollback()

        assert await session.get(PaymentAccount, personal.id) is account
        assert account.balance == Decimal(0)
        assert await session.get(PaymentAccount, intermediate_id) is None
        assert await session.get(PaymentAccount, final_id) is None
        assert await session.get(PaymentLedgerEntry, entry_id) is None
        account.balance = Decimal(3)

    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(3)


@pytest.mark.parametrize("released_child", [False, True])
async def test_reloaded_ledger_keeps_its_origin_across_nested_key_rollbacks(
    migration_database, payment_accounts, released_child
):
    """Find the restored UUID after rolling back a rekey, then roll back the original post."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        outer = await session.begin_nested()
        entry = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(1)
        )
        original_id = entry.id
        middle = await session.begin_nested()
        if released_child:
            entry.id = str(uuid4())
            await session.flush()
        inner = await session.begin_nested()
        entry.id = str(uuid4())
        await session.flush()
        if released_child:
            await inner.commit()

        await middle.rollback()
        restored = await session.get(PaymentLedgerEntry, original_id)
        assert restored.operation_id == "credit"
        session.expunge(restored)
        reloaded = await session.get(PaymentLedgerEntry, original_id)
        assert reloaded is not None and reloaded is not restored

        await outer.rollback()

        assert await session.get(PaymentLedgerEntry, original_id) is None
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)


@pytest.mark.parametrize("release_child", [False, True])
async def test_reloaded_post_is_tracked_in_its_surviving_savepoint(
    migration_database, payment_accounts, release_child
):
    """Keep a reloaded row until the savepoint containing its creation rolls back."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        outer = await session.begin_nested()
        entry = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(1)
        )
        entry_id = entry.id
        middle = await session.begin_nested()
        entry.id = str(uuid4())
        await session.flush()
        child = await session.begin_nested()
        entry.id = entry_id
        await session.flush()
        session.expunge(entry)
        reloaded = await session.get(PaymentLedgerEntry, entry_id)
        if release_child:
            await child.commit()

        await middle.rollback()
        assert await session.get(PaymentLedgerEntry, entry_id) is reloaded
        assert reloaded.amount == Decimal(1)

        await outer.rollback()
        assert await session.get(PaymentLedgerEntry, entry_id) is None
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)


async def test_parent_rollback_restores_account_key_before_released_child(
    migration_database, payment_accounts
):
    """Preserve the first mapped identity across two savepoints when the outer one rolls back."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        account = await session.get(PaymentAccount, personal.id)
        outer = await session.begin_nested()
        intermediate_id, final_id = str(uuid4()), str(uuid4())
        account.id = intermediate_id
        await session.flush()
        child = await session.begin_nested()
        account.id = final_id
        await session.flush()
        await PaymentLedgerEntry.post(
            session, payment_account_id=final_id, operation_id="credit", amount=Decimal(1)
        )
        await child.commit()

        await outer.rollback()

        assert await session.get(PaymentAccount, personal.id) is account
        assert account.balance == Decimal(0)
        assert await session.get(PaymentAccount, intermediate_id) is None
        assert await session.get(PaymentAccount, final_id) is None
        assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()


@pytest.mark.parametrize("exchange", [False, True])
async def test_ancestor_rollback_retains_accounts_after_key_changes(
    migration_database, payment_accounts, exchange
):
    """Restore held accounts after returning to an earlier key or exchanging two keys."""
    personal, corporate = payment_accounts
    async with migration_database.write() as session:
        left = await session.get(PaymentAccount, personal.id)
        right = await session.get(PaymentAccount, corporate.id)
        outer = await session.begin_nested()
        await session.begin_nested()
        temporary_id = str(uuid4())
        left.id = temporary_id
        await session.flush()
        entry = await PaymentLedgerEntry.post(
            session, payment_account_id=temporary_id, operation_id="credit", amount=Decimal(1)
        )
        await session.delete(entry)
        await session.flush()
        if exchange:
            right.id = personal.id
            await session.flush()
        left.id = corporate.id if exchange else personal.id
        await session.flush()

        await outer.rollback()

        assert await session.get(PaymentAccount, personal.id) is left
        assert await session.get(PaymentAccount, corporate.id) is right
        assert left.balance == right.balance == Decimal(0)
        assert not (await session.scalars(select(PaymentLedgerEntry.id))).all()


@pytest.mark.parametrize("reloaded", [False, True])
async def test_rollback_refreshes_ordinary_ledger_and_account_updates(
    migration_database, payment_accounts, reloaded
):
    """Reload reverted non-key updates, including objects first read after a flush."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        entry = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(1)
        )
        account = await session.get(PaymentAccount, personal.id)
        entry_id = entry.id
        outer = await session.begin_nested()
        if not reloaded:
            await session.begin_nested()
        account.balance = Decimal(5)
        entry.comment = "reverted"
        await session.flush()
        if reloaded:
            session.expunge(account)
            session.expunge(entry)
            account = await session.get(PaymentAccount, personal.id)
            entry = await session.get(PaymentLedgerEntry, entry_id)
            assert account.balance == Decimal(5) and entry.comment == "reverted"

        await outer.rollback()

        assert await session.get(PaymentAccount, personal.id) is account
        assert await session.get(PaymentLedgerEntry, entry_id) is entry
        assert account.balance == Decimal(1) and entry.comment is None


@pytest.mark.parametrize("model", [PaymentAccount, PaymentLedgerEntry])
@pytest.mark.parametrize("reload_during_change", [False, True])
async def test_rollback_retains_reloaded_objects_that_returned_to_an_earlier_key(
    migration_database, payment_accounts, model, reload_during_change
):
    """Keep a reloaded object in the session when its primary key survives rollback."""
    personal, corporate = payment_accounts
    if model is PaymentLedgerEntry:
        async with migration_database.write() as session:
            committed = await PaymentLedgerEntry.post(
                session, payment_account_id=corporate.id, operation_id="credit", amount=Decimal(1)
            )
        row_id, attribute, changed = committed.id, "comment", "retained"
    else:
        row_id, attribute, changed = personal.id, "balance", Decimal(3)

    async with migration_database.write() as session:
        row = await session.get(model, row_id)
        savepoint = await session.begin_nested()
        temporary_id = str(uuid4())
        row.id = temporary_id
        await session.flush()
        if reload_during_change:
            session.expunge(row)
            row = await session.get(model, temporary_id)
        row.id = row_id
        await session.flush()
        session.expunge(row)
        reloaded = await session.get(model, row_id)

        await savepoint.rollback()

        assert reloaded in session
        assert await session.get(model, row_id) is reloaded
        setattr(reloaded, attribute, changed)

    async with migration_database.read() as session:
        assert getattr(await session.get(model, row_id), attribute) == changed


async def test_ancestor_rollback_restores_deleted_ledger_after_rekey(
    migration_database, payment_accounts
):
    """Restore the original key and instance when an open child changed and deleted a row."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        committed = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="credit", amount=Decimal(1)
        )
    entry_id = committed.id
    async with migration_database.write() as session:
        entry = await session.get(PaymentLedgerEntry, entry_id)
        outer = await session.begin_nested()
        await session.begin_nested()
        new_id = str(uuid4())
        entry.id = new_id
        await session.flush()
        await session.delete(entry)
        await session.flush()

        await outer.rollback()

        assert await session.get(PaymentLedgerEntry, entry_id) is entry
        assert await session.get(PaymentLedgerEntry, new_id) is None
        entry.comment = "restored"

    async with migration_database.read() as session:
        assert (await session.get(PaymentLedgerEntry, entry_id)).comment == "restored"


@pytest.mark.parametrize("same_flush", [False, True])
async def test_rollback_keeps_reloaded_existing_ledger_after_temporary_uuid_reuse(
    migration_database, payment_accounts, same_flush
):
    """Recognize the surviving ledger row even after another row temporarily used its UUID."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        saved = await PaymentLedgerEntry.post(
            session, payment_account_id=personal.id, operation_id="saved", amount=Decimal(1)
        )
        saved_id = saved.id
        scope = await session.begin_nested()
        saved.id = str(uuid4())
        if same_flush:
            other = PaymentLedgerEntry(
                id=saved_id,
                payment_account_id=personal.id,
                operation_id="temporary",
                amount=Decimal(2),
            )
            session.add(other)
            with pytest.warns(SAWarning, match="conflicts with persistent instance"):
                await session.flush()
        else:
            await session.flush()
            other = await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="temporary",
                amount=Decimal(2),
                entry_id=saved_id,
            )
        await session.delete(other)
        await session.flush()
        saved.id = saved_id
        await session.flush()
        session.expunge(saved)
        reloaded = await session.get(PaymentLedgerEntry, saved_id)
        assert reloaded.operation_id == "saved"

        await scope.rollback()

        assert await session.get(PaymentLedgerEntry, saved_id) is reloaded
        reloaded.comment = "retained"

    async with migration_database.read() as session:
        assert (await session.get(PaymentLedgerEntry, saved_id)).comment == "retained"


async def test_rollback_does_not_rebind_a_reloaded_account_to_another_owner(
    migration_database, payment_accounts
):
    """A cached row loaded at a reused key cannot apply a write to the original account."""
    personal, corporate = payment_accounts
    async with migration_database.write() as session:
        left = await session.get(PaymentAccount, personal.id)
        right = await session.get(PaymentAccount, corporate.id)
        scope = await session.begin_nested()
        left.balance = Decimal(1)
        await session.flush()
        session.expunge(left)
        moved_left = await session.get(PaymentAccount, personal.id)
        moved_left.id = str(uuid4())
        await session.flush()
        session.expunge(moved_left)
        right.id = personal.id
        await session.flush()
        session.expunge(right)
        reused = await session.get(PaymentAccount, personal.id)
        assert reused.company_id == corporate.company_id

        await scope.rollback()

        assert (await session.get(PaymentAccount, personal.id)).user_id == personal.user_id
        assert (await session.get(PaymentAccount, corporate.id)).company_id == corporate.company_id
        assert reused not in session
        reused.balance = Decimal(7)

    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)
        assert (await session.get(PaymentAccount, corporate.id)).balance == Decimal(0)


@pytest.mark.parametrize("release_child", [False, True])
async def test_ancestor_rollback_retains_alias_reloaded_at_the_original_uuid(
    migration_database, payment_accounts, release_child
):
    """Compose the original key when returning to it across two savepoints."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        account = await session.get(PaymentAccount, personal.id)
        outer = await session.begin_nested()
        account.id = str(uuid4())
        await session.flush()
        inner = await session.begin_nested()
        account.id = personal.id
        await session.flush()
        session.expunge(account)
        reloaded = await session.get(PaymentAccount, personal.id)
        if release_child:
            await inner.commit()

        await outer.rollback()

        assert await session.get(PaymentAccount, personal.id) is reloaded
        reloaded.balance = Decimal(7)

    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(7)


async def test_ancestor_rollback_rejects_alias_of_replacement_account(
    migration_database, payment_accounts
):
    """A reread of an uncommitted replacement must not take over the original account."""
    personal, corporate = payment_accounts
    async with migration_database.write() as session:
        original = await session.get(PaymentAccount, personal.id)
        outer = await session.begin_nested()
        await session.begin_nested()
        await session.delete(original)
        replacement = PaymentAccount(id=personal.id, user_id=None, company_id=corporate.company_id)
        session.add(replacement)
        await session.flush()
        session.expunge(replacement)
        reloaded = await session.get(PaymentAccount, personal.id)
        assert reloaded.company_id == corporate.company_id

        await outer.rollback()

        assert await session.get(PaymentAccount, personal.id) is original
        assert reloaded not in session
        reloaded.balance = Decimal(17)

    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal(0)


async def test_failed_post_keeps_other_accounts_loaded(migration_database, payment_accounts):
    """Rolling back one posting preserves the in-memory state of unrelated payment accounts."""
    personal, corporate = payment_accounts
    async with migration_database.write() as session:
        saved = await session.get(PaymentAccount, personal.id)
        await PaymentLedgerEntry.post(
            session,
            payment_account_id=saved.id,
            operation_id="credit",
            amount=Decimal(1),
        )
        with pytest.raises(NoResultFound, match="Payment account not found"):
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=str(uuid4()),
                operation_id="missing",
                amount=Decimal(2),
            )
        assert saved.id == personal.id and saved.balance == Decimal(1)
        with pytest.raises(IntegrityError, match="valid_operation_id"):
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=corporate.id,
                operation_id="",
                amount=Decimal(3),
            )
        assert saved.id == personal.id and saved.balance == Decimal(1)


async def test_ledger_sum_retains_digits_across_accounts(migration_database, payment_accounts):
    """Read an exact sum that exceeds the range of a single line but fits a SQLite integer."""
    async with migration_database.write() as session:
        for account in payment_accounts:
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=account.id,
                operation_id="credit",
                amount=Decimal("999999999999.999999"),
            )
    async with migration_database.read() as session:
        assert await session.scalar(select(func.sum(PaymentLedgerEntry.amount))) == Decimal(
            "1999999999999.999998"
        )


@pytest.mark.parametrize(("kind", "status"), [("unused", 204), ("funded", 409), ("history", 409)])
@pytest.mark.parametrize("owner", ["user", "company"])
async def test_owner_deletion_only_cleans_unused_payment_accounts(
    api, user, migration_database, tariff_plans, owner, kind, status
):
    """Allow owner deletion to clear assignments and empty accounts while protecting money."""
    starter, *_ = tariff_plans
    if owner == "user":
        owner_id = user["id"]
        path = f"/v1/users/{owner_id}"
        account = PaymentAccount(user_id=owner_id)
    else:
        response = await api.post("/v1/companies", json={"name": "Billing test"})
        assert response.status_code == 201, response.text
        owner_id = response.json()["id"]
        path = f"/v1/companies/{owner_id}"
        account = PaymentAccount(company_id=owner_id)
    async with migration_database.write() as session:
        if owner == "user":
            session.add(
                UserTariffPlan(
                    user_id=owner_id,
                    tariff_plan_id=starter.id,
                    tariff_plan_type_id=starter.tariff_plan_type_id,
                )
            )
        if kind == "funded":
            account.balance = Decimal("1")
        session.add(account)
        if kind == "history":
            await session.flush()
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=account.id,
                operation_id="no_charge",
                amount=Decimal("0"),
            )
    response = await api.delete(path)
    assert response.status_code == status, response.text
    async with migration_database.read() as session:
        saved = await session.get(PaymentAccount, account.id)
        saved_owner = await session.get(User if owner == "user" else Company, owner_id)
        if owner == "user":
            assignment = await session.get(UserTariffPlan, (owner_id, starter.tariff_plan_type_id))
            assert (assignment is None) == (kind == "unused")
            assert await session.get(TariffPlan, starter.id) is not None
        if kind == "unused":
            assert saved is saved_owner is None
        else:
            assert saved_owner is not None
            assert saved.balance == Decimal("1" if kind == "funded" else "0")
            assert await session.scalar(select(func.count(PaymentLedgerEntry.id))) == (
                kind == "history"
            )
        assignments = await session.scalars(
            select(PaymentAccountTariffPlan.tariff_plan_type_id).where(
                PaymentAccountTariffPlan.payment_account_id == account.id
            )
        )
        assert len(assignments.all()) == (0 if kind == "unused" else 3)


@pytest.mark.parametrize("failure", ["overdraft", "duplicate"])
async def test_a_caught_posting_failure_preserves_other_postings_in_the_transaction(
    migration_database, payment_accounts, failure
):
    """An overdraft cannot abort unrelated postings when the caller catches the error."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        account = await session.get(PaymentAccount, personal.id)
        first = await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id="first",
            amount=Decimal("1"),
        )
        assert account.balance == Decimal("1")
        if failure == "overdraft":
            rejected = {"amount": Decimal("-2")}
            constraint = "nonnegative_balance"
            conflict_warning = nullcontext()
        else:
            rejected = {"amount": Decimal("1"), "entry_id": first.id}
            constraint = "pk_payment_ledger_entries|UNIQUE constraint"
            conflict_warning = pytest.warns(SAWarning, match="conflicts with persistent instance")
        with conflict_warning, pytest.raises(IntegrityError, match=constraint):
            await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id="declined",
                **rejected,
            )
        assert await session.get(PaymentAccount, personal.id) is account
        assert account.balance == Decimal("1")
        await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id="second",
            amount=Decimal("0.50"),
        )
    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal("1.50")
        assert set(await session.scalars(select(PaymentLedgerEntry.operation_id))) == {
            "first",
            "second",
        }


async def test_concurrent_debits_cannot_overdraw(migration_database, payment_accounts):
    """Commit only the funded debit and its ledger record during concurrent withdrawals."""
    personal, _ = payment_accounts
    async with migration_database.write() as session:
        await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id="fund",
            amount=Decimal("1"),
        )

    async def debit(operation_id):
        """Withdraw from the same account in an independent transaction."""
        async with migration_database.sessions.begin() as session:
            return await PaymentLedgerEntry.post(
                session,
                payment_account_id=personal.id,
                operation_id=operation_id,
                amount=Decimal("-0.75"),
            )

    results = await asyncio.gather(debit("debit1"), debit("debit2"), return_exceptions=True)
    assert sum(isinstance(result, PaymentLedgerEntry) for result in results) == 1
    assert (
        sum(
            isinstance(result, IntegrityError) and "nonnegative_balance" in str(result)
            for result in results
        )
        == 1
    )
    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal("0.25")
        assert set(await session.scalars(select(PaymentLedgerEntry.amount))) == {
            Decimal("1"),
            Decimal("-0.75"),
        }


async def test_billing_migration_round_trip_preserves_existing_owners(
    migration_database, alembic_config, migration_head, billing_owners
):
    """Upgrade from 0019, check ORM/schema parity, and downgrade the empty billing tables."""
    alice, bob, company = billing_owners
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with migration_database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        assert BILLING_TABLES <= tables
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == migration_head
        )
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    async with migration_database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        assert not BILLING_TABLES & tables
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0019"
    async with migration_database.read() as session:
        assert set(await session.scalars(select(User.id))) == {alice.id, bob.id}
        assert (await session.scalars(select(Company.id))).one() == company.id


async def test_billing_downgrade_keeps_existing_data(
    migration_database, alembic_config, payment_accounts
):
    """Refuse to roll back a populated billing schema and retain the ledger and balance."""
    personal, _ = payment_accounts
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0020")
    async with migration_database.write() as session:
        entry = await PaymentLedgerEntry.post(
            session,
            payment_account_id=personal.id,
            operation_id="credit",
            amount=Decimal("1"),
        )
    await migration_database.close()
    with pytest.raises(CommandError, match="billing data exists"):
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    async with migration_database.read() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "0020"
        assert (await session.get(PaymentLedgerEntry, entry.id)).amount == Decimal("1")
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal("1")


@pytest.mark.parametrize("failure", ["table", "version"])
async def test_interrupted_billing_upgrade_rolls_back_ddl(
    migration_database, alembic_config, failure
):
    """Leave the old schema intact when DDL or the version update fails and permit retry."""
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")

    def interrupt_upgrade(connection, cursor, statement, parameters, context, executemany):
        """Interrupt a specific SQL statement during the real migration."""
        ddl = getattr(context.compiled, "statement", None)
        interrupted = {
            "table": isinstance(ddl, CreateTable) and ddl.element.name == "tariff_plans",
            "version": statement.lstrip().startswith("UPDATE alembic_version"),
        }
        if interrupted[failure]:
            raise RuntimeError("interrupted billing upgrade")

    event.listen(Engine, "before_cursor_execute", interrupt_upgrade)
    try:
        with pytest.raises(RuntimeError, match="interrupted billing upgrade"):
            await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0020")
    finally:
        event.remove(Engine, "before_cursor_execute", interrupt_upgrade)
    async with migration_database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        assert not BILLING_TABLES & tables
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0019"
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)


async def test_billing_downgrade_locks_out_writes_after_its_empty_check(
    migration_database, alembic_config
):
    """A concurrent writer cannot commit into billing tables once rollback checks their contents."""
    async with migration_database.engine.connect() as competitor:
        if competitor.dialect.name == "sqlite":
            await competitor.exec_driver_sql("PRAGMA busy_timeout=50")
        else:
            await competitor.exec_driver_sql("SET lock_timeout = '50ms'")
        await competitor.commit()
        blocked = []

        async def add_tariff_type():
            """Attempt a real write on a separate connection with a short lock timeout."""
            try:
                async with competitor.begin():
                    await competitor.execute(
                        insert(TariffPlanType).values(name="Concurrent", field_types={})
                    )
            except DBAPIError as error:
                blocked.append(str(error.orig))

        def before_drop(connection, cursor, statement, parameters, context, executemany):
            """Try the concurrent write after the downgrade's emptiness check and before its first drop."""
            ddl = getattr(context.compiled, "statement", None)
            if isinstance(ddl, DropTable) and ddl.element.name == "payment_ledger_entries":
                anyio.from_thread.run(add_tariff_type)

        event.listen(Engine, "before_cursor_execute", before_drop)
        try:
            await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
        finally:
            event.remove(Engine, "before_cursor_execute", before_drop)
        assert len(blocked) == 1 and any(
            message in blocked[0] for message in ("database is locked", "lock timeout")
        )
    async with migration_database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0019"


async def test_postgres_billing_downgrade_does_not_wait_holding_ledger_lock(
    migration_database, alembic_config, migration_head, payment_accounts
):
    """Refuse an online downgrade while another transaction holds a payment-account write lock."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("SQLite serializes writers with one database-wide lock")
    personal, _ = payment_accounts

    def bound_migration_wait(connection, cursor, statement, parameters, context, executemany):
        """Keep a broken version of this test from hanging on the blocked downgrade."""
        if statement.startswith(
            ("LOCK TABLE payment_ledger_entries", "LOCK TABLE payment_accounts")
        ):
            connection.exec_driver_sql("SET LOCAL lock_timeout = '150ms'")

    async with migration_database.sessions.begin() as writer:
        await writer.execute(
            update(PaymentAccount)
            .where(PaymentAccount.id == personal.id)
            .values(balance=Decimal("0"))
        )
        event.listen(Engine, "before_cursor_execute", bound_migration_wait)
        try:
            with pytest.raises(DBAPIError, match="could not obtain lock"):
                await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
        finally:
            event.remove(Engine, "before_cursor_execute", bound_migration_wait)
        await PaymentLedgerEntry.post(
            writer,
            payment_account_id=personal.id,
            operation_id="credit",
            amount=Decimal("1"),
        )
    async with migration_database.read() as session:
        assert (await session.get(PaymentAccount, personal.id)).balance == Decimal("1")
        assert (
            await session.scalar(text("SELECT version_num FROM alembic_version")) == migration_head
        )


@pytest.mark.parametrize("parent", [User, Company])
async def test_postgres_downgrade_does_not_wait_for_billing_fk_parents(
    migration_database, alembic_config, migration_head, parent
):
    """Preserve billing data when PostgreSQL cannot promptly remove foreign keys."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL removes foreign-key triggers from the parent tables")

    async with migration_database.sessions.begin() as reader:
        await reader.execute(select(parent.id))
        with pytest.raises(DBAPIError) as conflict:
            await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
        assert conflict.value.orig.sqlstate == "55P03"
    async with migration_database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        assert BILLING_TABLES <= tables
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == migration_head
        )


async def test_downgrade_sees_concurrent_data_even_with_repeatable_read_default(
    migration_database, alembic_config, migration_head
):
    """Check the database after locking it, even if its default snapshots are repeatable."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL has a configurable default transaction isolation level")
    async with migration_database.engine.begin() as connection:
        name = connection.dialect.identifier_preparer.quote_identifier(
            connection.engine.url.database
        )
        await connection.exec_driver_sql(
            f"ALTER DATABASE {name} SET default_transaction_isolation TO 'repeatable read'"
        )
    await migration_database.close()
    inserted = []

    async def add_tariff_type():
        """Commit a tariff immediately after the migrator first reads the applied revision."""
        async with migration_database.write() as session:
            plan_type = TariffPlanType(name="Concurrent", field_types={})
            session.add(plan_type)
        inserted.append(plan_type.id)

    def after_version_read(connection, cursor, statement, parameters, context, executemany):
        """Run the other transaction before Alembic acquires its downgrade locks."""
        if not inserted and statement.startswith("SELECT alembic_version.version_num"):
            anyio.from_thread.run(add_tariff_type)

    event.listen(Engine, "after_cursor_execute", after_version_read)
    try:
        with pytest.raises(CommandError, match="billing data exists"):
            await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    finally:
        event.remove(Engine, "after_cursor_execute", after_version_read)
    async with migration_database.read() as session:
        assert len(inserted) == 1
        assert (await session.scalars(select(TariffPlanType.id))).one() == inserted[0]
        assert (
            await session.scalar(text("SELECT version_num FROM alembic_version")) == migration_head
        )
