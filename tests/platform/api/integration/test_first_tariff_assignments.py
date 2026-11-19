"""Database-driven activation of the first tariff in a catalogue type."""

import asyncio
import gc
from decimal import Decimal
from uuid import uuid4

import anyio
import pytest
from alembic import command as alembic
from sqlalchemy import event, insert, inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import load_only, raiseload, selectinload

from tests.platform.api.integration.test_account_tariffs import AT, TICK, assignments, plan
from tests.platform.api.integration.test_account_tariffs import owners as owners
from throng_api.accounts import delete_empty_payment_accounts
from throng_api.first_tariff_schema import ASSIGN_FIRST_TARIFF
from throng_api.models import (
    Base,
    Coordination,
    PaymentAccount,
    PaymentAccountTariffPlan,
    PaymentLedgerEntry,
    TariffPlan,
    TariffPlanType,
    User,
    UserTariffPlan,
)

pytestmark = pytest.mark.anyio


def read_account(account_id):
    """Read an account together with the catalogue choices actually assigned to it."""
    return (
        select(PaymentAccount)
        .where(PaymentAccount.id == account_id)
        .options(selectinload(PaymentAccount.tariff_assignments))
    )


def tariff_row(type_id, name, when):
    """Build a complete row for Core bulk inserts without the ORM catalogue writer."""
    return {
        "id": str(uuid4()),
        "tariff_plan_type_id": type_id,
        "name": name,
        "published_at": when,
        "field_values": {},
    }


def inserts_account_assignments(statement):
    """Match the migration's data write with or without a qualified table name."""
    first_line = statement.lstrip().partition("\n")[0]
    return first_line.startswith("INSERT INTO ") and "payment_account_tariff_plans (" in first_line


@pytest.fixture
async def existing_accounts(migration_database, owners):
    """Create accounts of both owner kinds before any catalogue activation."""
    user, company = owners
    personal = PaymentAccount(user_id=user.id, created_at=AT)
    organization = PaymentAccount(company_id=company.id, created_at=AT - TICK)
    async with migration_database.write() as session:
        session.add_all([personal, organization])
    return personal, organization


async def test_billing_triggers_follow_metadata_create_and_drop(migration_database):
    """Install the guards after the referenced tables, including after rebuilding metadata."""
    async with migration_database.engine.begin() as connection:
        # No changes are due on the already migrated schema.
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(Base.metadata.create_all)
    async with migration_database.sessions.begin() as session:
        user = User(
            email="metadata-tariffs@example.com",
            first_name="Tariff",
            last_name="Owner",
            password_hash="unused",
        )
        session.add(user)
        await session.flush()
        account = PaymentAccount(user_id=user.id, created_at=AT)
        session.add(account)
        await session.flush()
        first = plan("First", TariffPlanType(name="Subscription", field_types={}), AT)
        session.add(first)
        await session.flush()
        assert await assignments(session, account.id) == {first.tariff_plan_type_id: first.id}


async def test_first_tariff_initializes_all_existing_accounts(
    migration_database, owners, existing_accounts
):
    """Assign by insertion to both owners, and keep the publication cutoff on new accounts."""
    user, _ = owners
    personal, organization = existing_accounts
    old_type = TariffPlanType(name="Cloud", field_types={})
    old_plan = plan("Current cloud", old_type, AT - TICK)
    new_type = TariffPlanType(name="Subscription", field_types={})
    first = plan("First to be inserted", new_type, AT + TICK)
    later = plan("Later", new_type, AT + 2 * TICK)

    async with migration_database.write() as session:
        session.add_all([old_plan, new_type])
        await session.flush()
        account = (await session.scalars(read_account(personal.id))).one()
        assert account.tariff_assignments[0].tariff_plan_id == old_plan.id
        assert await assignments(session, personal.id) == {old_type.id: old_plan.id}
        assert await assignments(session, organization.id) == {old_type.id: old_plan.id}

        session.add(first)
        await session.flush()
        assert (await session.scalars(read_account(personal.id))).one() is account
        assert {link.tariff_plan_id for link in account.tariff_assignments} == {
            first.id,
            old_plan.id,
        }
        assert await assignments(session, organization.id) == {
            new_type.id: first.id,
            old_type.id: old_plan.id,
        }

        already_loaded = account.tariff_assignments
        session.add(later)
        await session.flush()
        assert account.tariff_assignments is already_loaded
        assert await assignments(session, personal.id) == {
            new_type.id: first.id,
            old_type.id: old_plan.id,
        }

        before_publication = PaymentAccount(user_id=user.id, created_at=AT)
        after_publication = PaymentAccount(user_id=user.id, created_at=AT + 2 * TICK)
        session.add_all([before_publication, after_publication])
        await session.flush()
        assert await assignments(session, before_publication.id) == {old_type.id: old_plan.id}
        assert await assignments(session, after_publication.id) == {
            new_type.id: later.id,
            old_type.id: old_plan.id,
        }


async def test_subsequent_insert_or_upsert_does_not_refill_a_missing_slot(
    migration_database, existing_accounts
):
    """Keep a deliberately removed assignment until a first insert into an empty type."""
    personal, organization = existing_accounts
    catalogue = TariffPlan.__table__
    kind = TariffPlanType(name="Cloud", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
        await session.flush()

    row = tariff_row(kind.id, "First", AT + TICK)
    dialect = migration_database.engine.dialect.name
    dialect_insert = postgres_insert if dialect == "postgresql" else sqlite_insert
    async with migration_database.engine.begin() as connection:
        await connection.execute(insert(catalogue).values(row))
        await connection.execute(
            PaymentAccountTariffPlan.__table__.delete().where(
                PaymentAccountTariffPlan.payment_account_id == personal.id
            )
        )
        await connection.execute(
            dialect_insert(catalogue).values(row).on_conflict_do_nothing(index_elements=["id"])
        )
        await connection.execute(
            dialect_insert(catalogue)
            .values(row)
            .on_conflict_do_update(index_elements=["id"], set_={"name": "Renamed"})
        )
        await connection.execute(
            insert(catalogue).values(tariff_row(kind.id, "Backdated revision", AT))
        )

    async with migration_database.read() as session:
        assert await assignments(session, personal.id) == {}
        assert await assignments(session, organization.id) == {kind.id: row["id"]}


async def test_bulk_insert_picks_the_first_physical_row_per_type(
    migration_database, existing_accounts
):
    """Seed each distinct type exactly once even when variants are in a single SQL statement."""
    subscription = TariffPlanType(name="Subscription", field_types={})
    compute = TariffPlanType(name="Compute", field_types={})
    empty = TariffPlanType(name="Empty", field_types={})
    async with migration_database.write() as session:
        session.add_all([subscription, compute, empty])
    first_subscription = tariff_row(subscription.id, "First subscription", AT + 2 * TICK)
    first_compute = tariff_row(compute.id, "First compute", AT + TICK)
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            insert(TariffPlan.__table__).values(
                [
                    first_subscription,
                    tariff_row(subscription.id, "Backdated subscription", AT),
                    first_compute,
                    tariff_row(compute.id, "Backdated compute", AT),
                ]
            )
        )
    async with migration_database.read() as session:
        for account in existing_accounts:
            assert await assignments(session, account.id) == {
                subscription.id: first_subscription["id"],
                compute.id: first_compute["id"],
            }


async def test_a_type_with_no_remaining_plans_can_be_initialized_again(
    migration_database, existing_accounts
):
    """Treat an insert into an emptied type as its first new plan."""
    kind = TariffPlanType(name="Subscription", field_types={})
    original = plan("Original", kind, AT)
    async with migration_database.write() as session:
        session.add(original)
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            PaymentAccountTariffPlan.__table__.delete().where(
                PaymentAccountTariffPlan.tariff_plan_type_id == kind.id
            )
        )
        await connection.execute(TariffPlan.__table__.delete().where(TariffPlan.id == original.id))
    async with migration_database.write() as session:
        replacement = plan("A new start", await session.get(TariffPlanType, kind.id), AT)
        session.add(replacement)
        await session.flush()
        for account in existing_accounts:
            assert await assignments(session, account.id) == {kind.id: replacement.id}


async def test_replacement_without_activation_keeps_a_loaded_collection(migration_database, owners):
    """Leave an existing collection usable when the only old plan is deleted after the insert."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    scheduled = plan("Scheduled", kind, AT + TICK)
    account = PaymentAccount(user_id=user.id, created_at=AT)
    async with migration_database.write() as session:
        session.add(scheduled)
        await session.flush()
        session.add(account)
    async with migration_database.write() as session:
        loaded = (await session.scalars(read_account(account.id))).one()
        assert loaded.tariff_assignments == []
        existing = await session.get(TariffPlan, scheduled.id)
        session.add(plan("Replacement", existing.plan_type, AT))
        await session.delete(existing)
        await session.flush()
        assert await assignments(session, account.id) == {}
        assert loaded.tariff_assignments == []


@pytest.mark.parametrize("has_existing_account", [False, True])
async def test_first_tariff_and_new_account_can_share_an_orm_flush(
    migration_database, owners, has_existing_account
):
    """Track generated links whether the target account is new or already in the session."""
    user, company = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    first = plan("First", kind, AT)
    new = PaymentAccount(company_id=company.id, created_at=AT, tariff_assignments=[])
    async with migration_database.write() as session:
        if has_existing_account:
            existing = PaymentAccount(user_id=user.id, created_at=AT)
            session.add(existing)
            await session.flush()
            assert await session.scalar(read_account(existing.id)) is existing
            assert existing.tariff_assignments == []
        session.add_all([new, first])
        await session.flush()
        assert await session.scalar(read_account(new.id)) is new
        assert [link.tariff_plan_id for link in new.tariff_assignments] == [first.id]
        if has_existing_account:
            assert await session.scalar(read_account(existing.id)) is existing
            assert [link.tariff_plan_id for link in existing.tariff_assignments] == [first.id]


async def test_explicit_reassignment_to_a_new_variant_is_not_a_fanout(
    migration_database, existing_accounts
):
    """Reuse the loaded collection when only its existing mapped link is changed."""
    kind = TariffPlanType(name="Subscription", field_types={})
    account_id = existing_accounts[0].id
    async with migration_database.write() as session:
        first = plan("First", kind, AT)
        session.add(first)
        await session.flush()
        account = (await session.scalars(read_account(account_id))).one()
        loaded = account.tariff_assignments
        later = plan("Later", kind, AT + TICK)
        loaded[0].tariff_plan = later
        await session.flush()
        assert account.tariff_assignments is loaded
        assert loaded[0].tariff_plan_id == later.id
        assert await assignments(session, account_id) == {kind.id: later.id}


@pytest.mark.parametrize("release_inner", [False, True])
async def test_first_tariff_savepoint_rollback_clears_loaded_links(
    migration_database, existing_accounts, release_inner
):
    """Invalidate both warmed collections and partially loaded links at the affected scope."""
    account_id = existing_accounts[0].id
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        account = (await session.scalars(read_account(account_id))).one()
        assert account.tariff_assignments == []
        async with session.begin_nested() as outer:
            async with session.begin_nested() as inner:
                session.add(plan("First", kind, AT))
                await session.flush()
                loaded = (
                    await session.scalars(
                        select(PaymentAccountTariffPlan)
                        .where(PaymentAccountTariffPlan.payment_account_id == account_id)
                        .options(
                            load_only(PaymentAccountTariffPlan.payment_account_id),
                            raiseload(PaymentAccountTariffPlan.tariff_plan),
                        )
                    )
                ).one()
                assert "tariff_plan_id" in inspect(loaded).unloaded
                assert (await session.scalars(read_account(account_id))).one() is account
                assert account.tariff_assignments == [loaded]
                key = (account_id, kind.id)
                if not release_inner:
                    await inner.rollback()
            if release_inner:
                assert await session.get(PaymentAccountTariffPlan, key) is loaded
                await outer.rollback()
            else:
                assert await session.get(PaymentAccountTariffPlan, key) is None
        assert inspect(loaded).detached
        assert await session.get(PaymentAccountTariffPlan, key) is None
        assert (await session.scalars(read_account(account_id))).one() is account
        assert account.tariff_assignments == []


async def test_rolling_back_a_reactivation_then_its_parent_restores_the_original_collection(
    migration_database, owners
):
    """An empty collection loaded after the inner rollback must refresh after the outer one."""
    user, _ = owners
    account = PaymentAccount(user_id=user.id, created_at=AT)
    first = plan("Original", TariffPlanType(name="Subscription", field_types={}), AT)
    async with migration_database.write() as session:
        session.add(account)
        await session.flush()
        session.add(first)
    async with migration_database.write() as session:
        loaded = (await session.scalars(read_account(account.id))).one()
        original = loaded.tariff_assignments[0]
        original_plan = original.tariff_plan
        kind = original_plan.plan_type
        async with session.begin_nested() as outer:
            await session.delete(original)
            await session.flush()
            await session.delete(original_plan)
            await session.flush()
            async with session.begin_nested() as inner:
                session.add(plan("Replacement", kind, AT))
                await session.flush()
                assert await session.scalar(read_account(account.id)) is loaded
                assert len(loaded.tariff_assignments) == 1
                assert loaded.tariff_assignments[0] is not original
                await inner.rollback()
            assert await session.scalar(read_account(account.id)) is loaded
            assert loaded.tariff_assignments == []
            await outer.rollback()
        assert (
            await session.get(PaymentAccountTariffPlan, (account.id, first.tariff_plan_type_id))
            is original
        )
        assert await session.scalar(read_account(account.id)) is loaded
        assert loaded.tariff_assignments == [original]


async def test_rolling_back_the_transaction_that_seeded_a_type(
    migration_database, existing_accounts
):
    """Remove generated links and keep existing accounts usable after a top-level rollback."""
    account_id = existing_accounts[0].id
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.sessions() as session:
        async with session.begin() as transaction:
            account = (await session.scalars(read_account(account_id))).one()
            assert account.tariff_assignments == []
            session.add(plan("First", kind, AT))
            await session.flush()
            key = (account_id, kind.id)
            assert (await session.scalars(read_account(account_id))).one() is account
            issued = account.tariff_assignments[0]
            await transaction.rollback()

        assert inspect(issued).detached
        assert await session.get(PaymentAccountTariffPlan, key) is None
        assert (await session.scalars(read_account(account_id))).one() is account
        assert account.tariff_assignments == []


@pytest.mark.parametrize("retain_explicit_link", [False, True])
@pytest.mark.parametrize("release_inner", [False, True])
@pytest.mark.parametrize("refresh_collection", [False, True])
async def test_retrying_a_rolled_back_account_does_not_republish_an_implicit_tariff(
    migration_database, owners, retain_explicit_link, release_inner, refresh_collection
):
    """A transient account keeps explicit inputs and discards links it read back from the DB."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    future = plan("Scheduled cloud", TariffPlanType(name="Cloud", field_types={}), AT + TICK)
    async with migration_database.write() as session:
        session.add_all([kind, future])
    account = PaymentAccount(
        user_id=user.id,
        created_at=AT,
        tariff_assignments=[PaymentAccountTariffPlan(tariff_plan=future)]
        if retain_explicit_link
        else [],
    )
    abandoned = plan("Rolled-back tariff", kind, AT)
    async with migration_database.write() as session:
        async with session.begin_nested() as outer:
            async with session.begin_nested() as inner:
                session.add(account)
                await session.flush()
                session.add(abandoned)
                await session.flush()
                abandoned_id = abandoned.id
                assert "tariff_assignments" in inspect(account).unloaded
                if refresh_collection:
                    gc.collect()
                    await session.refresh(account, ["tariff_assignments"])
                    assert any(link.tariff_plan is abandoned for link in account.tariff_assignments)
                if not release_inner:
                    await inner.rollback()
            if release_inner:
                await outer.rollback()

        assert inspect(account).transient
        assert inspect(abandoned).transient
        assert await session.get(TariffPlan, abandoned_id) is None
        session.add(account)
        await session.flush()
        assert await session.get(TariffPlan, abandoned_id) is None
        expected = {future.tariff_plan_type_id: future.id} if retain_explicit_link else {}
        assert await assignments(session, account.id) == expected


async def test_retry_does_not_restore_an_explicit_link_the_caller_removed(
    migration_database, owners
):
    """Keep removal of a draft link when the account is retried after a later activation."""
    user, _ = owners
    future = plan("Scheduled", TariffPlanType(name="Cloud", field_types={}), AT + TICK)
    async with migration_database.write() as session:
        session.add(future)
    explicit = PaymentAccountTariffPlan(tariff_plan=future)
    account = PaymentAccount(user_id=user.id, created_at=AT, tariff_assignments=[explicit])
    async with migration_database.write() as session:
        async with session.begin_nested() as outer:
            session.add(account)
            await session.flush()
            async with session.begin_nested():
                await session.delete(explicit)
                await session.flush()
                session.add(plan("First", TariffPlanType(name="Subscription", field_types={}), AT))
                await session.flush()
                assert "tariff_assignments" in inspect(account).unloaded
            await outer.rollback()
        session.add(account)
        await session.flush()
        assert await assignments(session, account.id) == {}


@pytest.mark.parametrize("edit", ["refresh", "rollback_edit", "release_edit", "release_fk_edit"])
async def test_account_retry_keeps_expired_inputs_to_an_explicit_assignment(
    migration_database, owners, edit
):
    """Restore the caller's last surviving draft even after its mapped attributes expire."""
    user, _ = owners
    cloud = TariffPlanType(name="Cloud", field_types={})
    original = plan("Scheduled", cloud, AT + TICK)
    replacement = plan("Also scheduled", cloud, AT + 2 * TICK)
    async with migration_database.write() as session:
        session.add_all([original, replacement])
    explicit = PaymentAccountTariffPlan(tariff_plan=original)
    account = PaymentAccount(user_id=user.id, created_at=AT, tariff_assignments=[explicit])

    async with migration_database.write() as session:
        async with session.begin_nested() as outer:
            session.add(account)
            await session.flush()
            if edit == "refresh":
                await session.refresh(explicit)
                assert "payment_account" in inspect(explicit).unloaded
            else:
                async with session.begin_nested() as inner:
                    if edit == "release_fk_edit":
                        explicit.tariff_plan_id = replacement.id
                    else:
                        explicit.tariff_plan = replacement
                    await session.flush()
                    if edit == "release_fk_edit":
                        assert explicit.tariff_plan is original
                    if edit == "rollback_edit":
                        await inner.rollback()
                if edit == "rollback_edit":
                    assert "tariff_plan_id" in inspect(explicit).expired_attributes

            session.add(plan("First", TariffPlanType(name="Subscription", field_types={}), AT))
            await session.flush()
            assert "tariff_assignments" in inspect(account).unloaded
            await outer.rollback()

        session.add(account)
        await session.flush()
        expected = replacement if edit in {"release_edit", "release_fk_edit"} else original
        assert await assignments(session, account.id) == {cloud.id: expected.id}


@pytest.mark.parametrize("release_inner", [False, True])
async def test_account_retry_keeps_one_explicit_link_after_a_row_switch(
    migration_database, owners, release_inner
):
    """Keep the surviving draft link when SQLAlchemy replaces an object at the same key."""
    user, _ = owners
    cloud = TariffPlanType(name="Cloud", field_types={})
    original = plan("Scheduled", cloud, AT + TICK)
    replacement = plan("Also scheduled", cloud, AT + 2 * TICK)
    async with migration_database.write() as session:
        session.add_all([original, replacement])
    explicit = PaymentAccountTariffPlan(tariff_plan=original)
    account = PaymentAccount(user_id=user.id, created_at=AT, tariff_assignments=[explicit])

    async with migration_database.write() as session:
        async with session.begin_nested() as outer:
            session.add(account)
            await session.flush()
            async with session.begin_nested() as inner:
                await session.delete(explicit)
                switched = PaymentAccountTariffPlan(tariff_plan=replacement)
                account.tariff_assignments = [switched]
                await session.flush()
                assert (
                    await session.get(PaymentAccountTariffPlan, (account.id, cloud.id)) is switched
                )
                if not release_inner:
                    await inner.rollback()
            if not release_inner:
                # SQLAlchemy expires the account's columns when its collection change rolls back.
                await session.refresh(account)

            session.add(plan("First", TariffPlanType(name="Subscription", field_types={}), AT))
            await session.flush()
            assert "tariff_assignments" in inspect(account).unloaded
            await outer.rollback()

        session.add(account)
        await session.flush()
        expected = replacement if release_inner else original
        assert await assignments(session, account.id) == {cloud.id: expected.id}


@pytest.mark.parametrize(
    ("activation", "load_link"),
    [
        (None, True),
        ("outer", True),
        ("release_inner", True),
        ("rollback_inner", True),
        ("outer", False),
    ],
)
async def test_account_retry_keeps_a_loaded_assignment_created_by_foreign_keys(
    migration_database, owners, activation, load_link
):
    """Internal expiration keeps the draft's loaded links even with no loaded backreference."""
    user, _ = owners
    cloud = TariffPlanType(name="Cloud", field_types={})
    selected = plan("Scheduled", cloud, AT + TICK)
    async with migration_database.write() as session:
        session.add(selected)
    account = PaymentAccount(user_id=user.id, created_at=AT)
    first = plan("First", TariffPlanType(name="Subscription", field_types={}), AT)

    async with migration_database.write() as session:
        async with session.begin_nested() as outer:
            session.add(account)
            await session.flush()
            link = PaymentAccountTariffPlan(
                payment_account_id=account.id,
                tariff_plan_type_id=cloud.id,
                tariff_plan_id=selected.id,
            )
            session.add(link)
            await session.flush()
            if load_link:
                await session.refresh(account, ["tariff_assignments"])
                assert account.tariff_assignments == [link]
            assert "payment_account" in inspect(link).unloaded

            if activation == "outer":
                session.add(first)
                await session.flush()
            elif activation is not None:
                async with session.begin_nested() as inner:
                    session.add(first)
                    await session.flush()
                    if activation == "rollback_inner":
                        await inner.rollback()
            if activation is not None:
                assert "tariff_assignments" in inspect(account).unloaded
            await outer.rollback()

        session.add(account)
        await session.flush()
        expected = {cloud.id: selected.id} if load_link else {}
        assert await assignments(session, account.id) == expected


@pytest.mark.parametrize("release_inner", [False, True])
@pytest.mark.parametrize("change", ["retarget", "delete", "replace"])
async def test_account_retry_keeps_the_loaded_assignment_from_the_surviving_savepoint(
    migration_database, owners, release_inner, change
):
    """Keep the earlier loaded relationship if a savepoint's edit or deletion is rolled back."""
    user, _ = owners
    cloud = TariffPlanType(name="Cloud", field_types={})
    selected = plan("Scheduled", cloud, AT + TICK)
    replacement = plan("Also scheduled", cloud, AT + 2 * TICK)
    async with migration_database.write() as session:
        session.add_all([selected, replacement])
    account = PaymentAccount(user_id=user.id, created_at=AT)

    async with migration_database.write() as session:
        async with session.begin_nested() as outer:
            session.add(account)
            await session.flush()
            link = PaymentAccountTariffPlan(
                payment_account_id=account.id,
                tariff_plan_type_id=cloud.id,
                tariff_plan_id=selected.id,
            )
            session.add(link)
            await session.flush()
            await session.refresh(account, ["tariff_assignments"])
            assert account.tariff_assignments == [link]
            assert "payment_account" in inspect(link).unloaded

            async with session.begin_nested() as inner:
                if change == "retarget":
                    link.tariff_plan_id = replacement.id
                else:
                    await session.delete(link)
                    if change == "replace":
                        switched = PaymentAccountTariffPlan(
                            payment_account_id=account.id,
                            tariff_plan_type_id=cloud.id,
                            tariff_plan_id=replacement.id,
                        )
                        session.add(switched)
                await session.flush()
                if change == "replace" and release_inner:
                    await session.refresh(account, ["tariff_assignments"])
                    assert account.tariff_assignments == [switched]
                session.add(plan("First", TariffPlanType(name="Subscription", field_types={}), AT))
                await session.flush()
                assert "tariff_assignments" in inspect(account).unloaded
                if not release_inner:
                    await inner.rollback()
            if not release_inner:
                await session.refresh(link, ["tariff_plan_id"])
                assert link.tariff_plan_id == selected.id
            await outer.rollback()

        session.add(account)
        await session.flush()
        if not release_inner:
            expected = {cloud.id: selected.id}
        elif change == "delete":
            expected = {}
        else:
            expected = {cloud.id: replacement.id}
        assert await assignments(session, account.id) == expected


@pytest.mark.parametrize(
    ("authorship", "activation"),
    [
        ("foreign_keys", None),
        ("foreign_keys", "before_release"),
        ("foreign_keys", "after_release"),
        ("relationship", None),
        ("relationship", "before_release"),
        ("relationship", "after_release"),
        ("relationship", "rollback_inner"),
    ],
)
async def test_retrying_a_replacement_account_keeps_its_loaded_assignment(
    migration_database, owners, authorship, activation
):
    """Preserve a loaded draft after the ORM switches account instances at the same key."""
    user, _ = owners
    cloud = TariffPlanType(name="Cloud", field_types={})
    selected = plan("Scheduled", cloud, AT + TICK)
    async with migration_database.write() as session:
        session.add(selected)
    original = PaymentAccount(user_id=user.id, created_at=AT)
    first = plan("First", TariffPlanType(name="Subscription", field_types={}), AT)

    async with migration_database.write() as session:
        async with session.begin_nested() as outer:
            session.add(original)
            await session.flush()
            if authorship == "foreign_keys":
                link = PaymentAccountTariffPlan(
                    payment_account_id=original.id,
                    tariff_plan_type_id=cloud.id,
                    tariff_plan_id=selected.id,
                )
            else:
                link = PaymentAccountTariffPlan(payment_account=original, tariff_plan=selected)
            session.add(link)
            await session.flush()

            async with session.begin_nested() as inner:
                replacement = PaymentAccount(id=original.id, user_id=user.id, created_at=AT)
                await session.delete(original)
                session.add(replacement)
                await session.flush()
                await session.refresh(replacement, ["tariff_assignments"])
                assert replacement.tariff_assignments == [link]
                if activation in {"before_release", "rollback_inner"}:
                    session.add(first)
                    await session.flush()
                if activation == "rollback_inner":
                    await inner.rollback()

            if activation == "after_release":
                session.add(first)
                await session.flush()
            if activation == "rollback_inner":
                await session.refresh(original)
                retry, discarded = original, replacement
            else:
                retry, discarded = replacement, original
            if activation is not None:
                assert "tariff_assignments" in inspect(retry).unloaded
            await outer.rollback()

        session.add(retry)
        await session.flush()
        assert discarded not in session
        assert await assignments(session, retry.id) == {cloud.id: selected.id}


async def test_readding_an_expunged_account_does_not_republish_a_rolled_back_tariff(
    migration_database, existing_accounts
):
    """An existing account detached after the eager read must discard a rolled-back link too."""
    account_id = existing_accounts[0].id
    new_type = TariffPlanType(name="Subscription", field_types={})
    abandoned = plan("Rolled-back tariff", new_type, AT)
    async with migration_database.write() as session:
        async with session.begin_nested() as savepoint:
            session.add(abandoned)
            await session.flush()
            account = (await session.scalars(read_account(account_id))).one()
            assert account.tariff_assignments[0].tariff_plan is abandoned
            abandoned_id = abandoned.id
            session.expunge(account)
            await savepoint.rollback()
        assert await session.get(TariffPlan, abandoned_id) is None
        session.add(account)
        await session.flush()
        assert await session.get(TariffPlan, abandoned_id) is None
        assert await assignments(session, account_id) == {}


async def test_invalid_first_tariff_cannot_leave_assignments(migration_database, existing_accounts):
    """Undo the trigger's fan-out when validation rejects the plan after SQL INSERT."""
    kind = TariffPlanType(name="Subscription", field_types={"limit": "int"})
    async with migration_database.write() as session:
        session.add(kind)
        await session.flush()
        with pytest.raises(ValueError, match="must be int"):
            async with session.begin_nested():
                session.add(
                    TariffPlan(plan_type=kind, name="Invalid", field_values={"limit": False})
                )
                await session.flush()
        for account in existing_accounts:
            assert await assignments(session, account.id) == {}

        accepted = TariffPlan(plan_type=kind, name="First valid", field_values={"limit": 1})
        session.add(accepted)
        await session.flush()
        for account in existing_accounts:
            assert await assignments(session, account.id) == {kind.id: accepted.id}


@pytest.fixture
async def previous_revision(migration_database, alembic_config, existing_accounts):
    """Set up mixed existing assignments and gaps with the original account trigger only."""
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0020")
    personal, organization = existing_accounts
    subscription = TariffPlanType(name="Subscription", field_types={})
    compute = TariffPlanType(name="Compute", field_types={})
    empty = TariffPlanType(name="Empty", field_types={})
    later = plan("Published later", subscription, AT + TICK)
    earlier = plan("Published first", subscription, AT)
    first_compute = plan("Scheduled compute", compute, AT + 2 * TICK)
    async with migration_database.write() as session:
        session.add_all([later, first_compute, empty])
        await session.flush()
        session.add(earlier)
        await session.flush()
        session.add_all(
            [
                PaymentAccountTariffPlan(payment_account_id=personal.id, tariff_plan=later),
                PaymentAccountTariffPlan(
                    payment_account_id=organization.id, tariff_plan=first_compute
                ),
            ]
        )
    return (
        {personal.id: {subscription.id: later.id}, organization.id: {compute.id: first_compute.id}},
        {
            personal.id: {subscription.id: later.id, compute.id: first_compute.id},
            organization.id: {subscription.id: earlier.id, compute.id: first_compute.id},
        },
    )


async def assert_assignments(database, expected):
    """Check the exact account/type map against the committed database contents."""
    async with database.read() as session:
        assert {
            account_id: await assignments(session, account_id) for account_id in expected
        } == expected


async def test_upgrade_fills_gaps_and_can_replay_over_saved_assignments(
    migration_database, alembic_config, previous_revision
):
    """Keep saved choices while filling missing types and replaying an already installed revision."""
    before, after = previous_revision
    await assert_assignments(migration_database, before)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
    await assert_assignments(migration_database, after)

    # A lost revision marker and a partially filled table must also be safe to replay.
    missing_account = next(iter(before))
    (missing_type,) = after[missing_account].keys() - before[missing_account].keys()
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            PaymentAccountTariffPlan.__table__.delete().where(
                PaymentAccountTariffPlan.payment_account_id == missing_account,
                PaymentAccountTariffPlan.tariff_plan_type_id == missing_type,
            )
        )
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.stamp, alembic_config, "0020")
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    await assert_assignments(migration_database, after)

    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0020")
    await assert_assignments(migration_database, after)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
    await assert_assignments(migration_database, after)


@pytest.mark.parametrize("interrupt_after", ["trigger", "backfill", "version"])
async def test_upgrade_interruption_rolls_back_and_retry_preserves_saved_links(
    migration_database, alembic_config, previous_revision, interrupt_after
):
    """Roll back installed DDL, newly inserted data and the revision marker together."""
    before, after = previous_revision
    await migration_database.close()

    def interrupt(_connection, _cursor, statement, *_):
        """Abort immediately after a specified statement has successfully run."""
        sql = statement.lstrip()
        matches = {
            "trigger": sql.startswith(("CREATE TRIGGER", "DO $billing_ddl$"))
            and ASSIGN_FIRST_TARIFF in sql,
            "backfill": inserts_account_assignments(sql),
            "version": sql.startswith("UPDATE alembic_version"),
        }
        if matches[interrupt_after]:
            raise RuntimeError("interrupted first-tariff backfill")

    event.listen(Engine, "after_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="interrupted first-tariff backfill"):
            await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
    finally:
        event.remove(Engine, "after_cursor_execute", interrupt)

    await assert_assignments(migration_database, before)
    async with migration_database.write() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "0020"
        async with session.begin_nested() as probe:
            new_type = TariffPlanType(name="Probe after interruption", field_types={})
            session.add(plan("Probe", new_type, AT))
            await session.flush()
            for account_id, previous_links in before.items():
                assert await assignments(session, account_id) == previous_links
            await probe.rollback()
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
    await assert_assignments(migration_database, after)


async def overlapping_writes(database, first, second, *, roll_back_first=False):
    """Run competing statements while the first holds its transaction open."""
    async with database.engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(first)
        started = asyncio.Event()

        async def write_second():
            """Use a separate database connection and its own transaction."""
            async with database.engine.begin() as competitor:
                started.set()
                await competitor.execute(second)

        follower = asyncio.create_task(write_second())
        try:
            await asyncio.wait_for(started.wait(), 5)
            done, _ = await asyncio.wait({follower}, timeout=0.05)
            assert not done
        finally:
            if roll_back_first:
                await transaction.rollback()
            else:
                await transaction.commit()
            await asyncio.wait_for(follower, 5)


async def wait_for_postgres_blocker(database, holder, waiting=None):
    """Wait for PostgreSQL to report the actual lock dependency between transactions."""
    blockers = text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_locks
            WHERE NOT granted AND pid IS NOT NULL
                AND (CAST(:waiting AS integer) IS NULL OR pid = :waiting)
                AND :holder = ANY(pg_blocking_pids(pid))
        )
    """)
    async with asyncio.timeout(10), database.engine.connect() as observer:
        while not await observer.scalar(blockers, {"holder": holder, "waiting": waiting}):  # noqa: ASYNC110
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("account_first", [False, True])
async def test_overlapping_account_and_first_tariff_have_no_gap(
    migration_database, owners, account_first
):
    """Avoid the two inserts each missing the other in separate transactions."""
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    account_id = str(uuid4())
    first_tariff = tariff_row(kind.id, "First", AT)
    account = insert(PaymentAccount.__table__).values(id=account_id, user_id=user.id, created_at=AT)
    tariff = insert(TariffPlan.__table__).values(first_tariff)
    first, second = (account, tariff) if account_first else (tariff, account)
    await overlapping_writes(migration_database, first, second)
    await assert_assignments(migration_database, {account_id: {kind.id: first_tariff["id"]}})


@pytest.mark.parametrize("roll_back_first", [False, True])
async def test_overlapping_first_plans_have_one_committed_winner(
    migration_database, existing_accounts, roll_back_first
):
    """Choose the first committed insert into the type even if an earlier writer rolls back."""
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    first = tariff_row(kind.id, "First attempted", AT + TICK)
    second = tariff_row(kind.id, "Second attempted", AT)
    await overlapping_writes(
        migration_database,
        insert(TariffPlan.__table__).values(first),
        insert(TariffPlan.__table__).values(second),
        roll_back_first=roll_back_first,
    )
    expected = second if roll_back_first else first
    await assert_assignments(
        migration_database, {account.id: {kind.id: expected["id"]} for account in existing_accounts}
    )


@pytest.mark.parametrize("stale_insert", ["account", "tariff"])
async def test_postgres_repeatable_read_retries_conflicting_initialization(
    migration_database, owners, stale_insert
):
    """Abort writes whose older snapshot cannot see the other side of an activation."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports explicit repeatable-read snapshots")
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    account_id = str(uuid4())
    first_tariff = tariff_row(kind.id, "First", AT)
    account = insert(PaymentAccount.__table__).values(id=account_id, user_id=user.id, created_at=AT)
    tariff = insert(TariffPlan.__table__).values(first_tariff)
    winner, stale = (tariff, account) if stale_insert == "account" else (account, tariff)

    async with migration_database.engine.connect() as older:
        await older.execution_options(isolation_level="REPEATABLE READ")
        with pytest.raises(DBAPIError) as rejected:
            async with older.begin():
                await older.execute(select(PaymentAccount.id))
                async with migration_database.engine.begin() as other:
                    await other.execute(winner)
                await older.execute(stale)
        assert rejected.value.orig.sqlstate == "40001"
    async with migration_database.engine.begin() as retry:
        await retry.execute(stale)
    await assert_assignments(migration_database, {account_id: {kind.id: first_tariff["id"]}})


async def test_postgres_activation_skips_a_concurrently_deleted_account(
    migration_database, existing_accounts
):
    """Seed surviving accounts even when a visible account is deleted during the trigger."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("SQLite serializes both writers before the trigger can read")
    removed, surviving = existing_accounts
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    first = tariff_row(kind.id, "First", AT)
    await overlapping_writes(
        migration_database,
        PaymentAccount.__table__.delete().where(PaymentAccount.id == removed.id),
        insert(TariffPlan.__table__).values(first),
    )
    await assert_assignments(
        migration_database, {removed.id: {}, surviving.id: {kind.id: first["id"]}}
    )
    async with migration_database.read() as session:
        assert await session.get(PaymentAccount, removed.id) is None


@pytest.mark.parametrize("mutation", ["delete", "rekey"])
async def test_postgres_account_replacement_overlaps_first_tariff(
    migration_database, existing_accounts, mutation
):
    """Use one lock order for changing an account key and seeding its replacement."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL locks the affected account rows independently")
    old, untouched = existing_accounts
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    first = tariff_row(kind.id, "First", AT)
    moved_id, replacement_id = str(uuid4()), str(uuid4())
    reached = asyncio.get_running_loop().create_future()

    async def activate():
        """Insert the first plan in a separate transaction and report its backend PID."""
        async with migration_database.engine.begin() as publisher:
            reached.set_result(await publisher.scalar(text("SELECT pg_backend_pid()")))
            await publisher.execute(insert(TariffPlan.__table__).values(first))

    task = None
    try:
        async with migration_database.engine.begin() as changing:
            holder = await changing.scalar(text("SELECT pg_backend_pid()"))
            accounts = PaymentAccount.__table__
            if mutation == "delete":
                await changing.execute(accounts.delete().where(accounts.c.id == old.id))
            else:
                await changing.execute(
                    accounts.update().where(accounts.c.id == old.id).values(id=moved_id)
                )
            task = asyncio.create_task(activate())
            waiting = await asyncio.wait_for(reached, 10)
            await wait_for_postgres_blocker(migration_database, holder, waiting)
            await asyncio.wait_for(
                changing.execute(
                    insert(accounts).values(id=replacement_id, user_id=old.user_id, created_at=AT)
                ),
                10,
            )
    finally:
        if task is not None:
            await asyncio.wait_for(task, 10)

    expected = {
        old.id: {},
        untouched.id: {kind.id: first["id"]},
        replacement_id: {kind.id: first["id"]},
    }
    if mutation == "rekey":
        expected[moved_id] = {kind.id: first["id"]}
    await assert_assignments(migration_database, expected)


async def test_postgres_catalogue_edit_can_overlap_publishing_a_first_tariff(
    migration_database, existing_accounts
):
    """Acquire the coordination row before the type row in both mapped transactions."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can lock the tariff type and coordination rows independently")
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    first_id, second_id = str(uuid4()), str(uuid4())
    reached = asyncio.get_running_loop().create_future()

    async def publish():
        """Flush a plan whose values match the new schema in the other transaction."""
        async with migration_database.sessions.begin() as session:
            reached.set_result(await session.scalar(text("SELECT pg_backend_pid()")))
            session.add(
                TariffPlan(
                    id=second_id,
                    tariff_plan_type_id=kind.id,
                    name="Concurrent plan",
                    field_values={"limit": 2},
                    published_at=AT + TICK,
                )
            )

    task = None
    try:
        async with migration_database.sessions.begin() as editing:
            holder = await editing.scalar(text("SELECT pg_backend_pid()"))
            schema = await editing.get(TariffPlanType, kind.id)
            schema.field_types = {"limit": "int"}
            await editing.flush()
            task = asyncio.create_task(publish())
            waiting = await asyncio.wait_for(reached, 10)
            await wait_for_postgres_blocker(migration_database, holder, waiting)
            editing.add(
                TariffPlan(
                    id=first_id,
                    plan_type=schema,
                    name="Plan from schema editor",
                    field_values={"limit": 1},
                    published_at=AT,
                )
            )
            await asyncio.wait_for(editing.flush(), 10)
    finally:
        if task is not None:
            await asyncio.wait_for(task, 10)
    await assert_assignments(
        migration_database, {account.id: {kind.id: first_id} for account in existing_accounts}
    )
    async with migration_database.read() as session:
        assert (await session.get(TariffPlan, second_id)).field_values == {"limit": 2}


@pytest.mark.parametrize("operation", ["ledger", "assignment"])
async def test_postgres_mapped_billing_writes_can_overlap_empty_account_cleanup(
    migration_database, existing_accounts, operation
):
    """Order account row and foreign-key locks before a transaction creates another billing row."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can lock individual account rows")
    personal, organization = existing_accounts
    new_type = TariffPlanType(name="New type", field_types={})
    assigned = plan("Existing", TariffPlanType(name="Old type", field_types={}), AT)
    async with migration_database.write() as session:
        session.add(new_type)
        if operation == "assignment":
            session.add(assigned)
            await session.flush()
            await session.execute(
                PaymentAccountTariffPlan.__table__.delete().where(
                    PaymentAccountTariffPlan.payment_account_id == personal.id
                )
            )

    async def cleanup():
        """Use the same write context and predicate as account-owner deletion."""
        async with migration_database.write() as session:
            await delete_empty_payment_accounts(session, PaymentAccount.user_id == personal.user_id)

    created_id = str(uuid4())
    task = None
    try:
        async with migration_database.sessions.begin() as session:
            holder = await session.scalar(text("SELECT pg_backend_pid()"))
            if operation == "ledger":
                await PaymentLedgerEntry.post(
                    session,
                    payment_account_id=personal.id,
                    operation_id="credit",
                    amount=Decimal("1"),
                )
            else:
                session.add(
                    PaymentAccountTariffPlan(
                        payment_account_id=personal.id,
                        tariff_plan_type_id=assigned.tariff_plan_type_id,
                        tariff_plan_id=assigned.id,
                    )
                )
                await session.flush()
            task = asyncio.create_task(cleanup())
            await wait_for_postgres_blocker(migration_database, holder)
            if operation == "ledger":
                session.add(PaymentAccount(id=created_id, company_id=organization.company_id))
            else:
                session.add(
                    TariffPlan(
                        id=created_id,
                        tariff_plan_type_id=new_type.id,
                        name="First",
                        field_values={},
                    )
                )
            await asyncio.wait_for(session.flush(), 10)
    finally:
        if task is not None:
            await asyncio.wait_for(task, 10)

    async with migration_database.read() as session:
        original = await session.get(PaymentAccount, personal.id)
        if operation == "ledger":
            assert original.balance == Decimal("1")
            assert await session.get(PaymentAccount, created_id) is not None
        else:
            assert original is None
            assert (await assignments(session, organization.id))[new_type.id] == created_id


async def test_postgres_cascading_user_tariff_delete_can_overlap_an_account_writer(
    migration_database,
):
    """Let the owner foreign key reject a new account even when its writer holds coordination."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("SQLite serializes all writers")
    user = User(
        email="deleting-tariff-owner@example.com",
        first_name="Tariff",
        last_name="Owner",
        password_hash="unused",
    )
    tariff = plan("Subscription", TariffPlanType(name="Subscription", field_types={}), AT)
    async with migration_database.write() as session:
        session.add(user)
        await session.flush()
        session.add(UserTariffPlan(user_id=user.id, tariff_plan=tariff))

    async with migration_database.write() as creating:
        async with asyncio.timeout(5), migration_database.sessions.begin() as deleting:
            owner = await deleting.get(User, user.id)
            await deleting.delete(owner)
            await deleting.flush()
        with pytest.raises(IntegrityError) as rejected:
            async with creating.begin_nested():
                creating.add(PaymentAccount(user_id=user.id, created_at=AT))
                await creating.flush()
        assert rejected.value.orig.sqlstate == "23503"
    async with migration_database.read() as session:
        assert await session.get(User, user.id) is None
        assert await session.get(UserTariffPlan, (user.id, tariff.tariff_plan_type_id)) is None


async def test_backfill_prevents_concurrent_account_and_plan_writes(
    migration_database, alembic_config, existing_accounts, previous_revision
):
    """Keep the migration's snapshot stable until both the data and triggers commit."""
    _, after = previous_revision
    kind = TariffPlanType(name="During migration", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    first = tariff_row(kind.id, "First", AT)
    account_id = str(uuid4())
    account = insert(PaymentAccount.__table__).values(
        id=account_id, user_id=existing_accounts[0].user_id, created_at=AT - TICK
    )
    tariff = insert(TariffPlan.__table__).values(first)

    async with migration_database.engine.connect() as competitor:
        sqlite = competitor.dialect.name == "sqlite"
        await competitor.exec_driver_sql(
            "PRAGMA busy_timeout=100" if sqlite else "SET lock_timeout = '100ms'"
        )
        await competitor.commit()
        errors = []

        async def attempt_writes():
            """Attempt both writes while the migrator holds its database locks."""
            for statement in (account, tariff):
                try:
                    async with competitor.begin():
                        await competitor.execute(statement)
                except DBAPIError as error:
                    errors.append(str(error.orig))

        def during_backfill(_connection, _cursor, statement, *_):
            """Pause the actual SQL backfill after installation has acquired its locks."""
            if inserts_account_assignments(statement):
                anyio.from_thread.run(attempt_writes)

        event.listen(Engine, "before_cursor_execute", during_backfill)
        try:
            await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
        finally:
            event.remove(Engine, "before_cursor_execute", during_backfill)

        assert len(errors) == 2
        assert all(("database is locked" if sqlite else "lock timeout") in msg for msg in errors)
        await assert_assignments(migration_database, after)
        async with competitor.begin():
            await competitor.execute(account)
            await competitor.execute(tariff)
    await assert_assignments(
        migration_database,
        {
            **{key: {**links, kind.id: first["id"]} for key, links in after.items()},
            account_id: {kind.id: first["id"]},
        },
    )


async def test_backfill_reads_existing_billing_schema_at_scale(
    migration_database, alembic_config, owners
):
    """Fill the whole account/type set without a per-account SQL parameter list."""
    user, company = owners
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0020")
    subscription = TariffPlanType(name="Subscription", field_types={})
    compute = TariffPlanType(name="Compute", field_types={})
    async with migration_database.write() as session:
        session.add_all(
            [plan("First subscription", subscription, AT), plan("First cloud", compute, AT)]
        )
    account_ids = [str(uuid4()) for _ in range(515)]
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            insert(PaymentAccount.__table__),
            [
                {
                    "id": account_id,
                    "created_at": AT - TICK,
                    "user_id": user.id if index % 2 == 0 else None,
                    "company_id": company.id if index % 2 else None,
                }
                for index, account_id in enumerate(account_ids)
            ],
        )
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
    async with migration_database.read() as session:
        keys = set(
            await session.execute(
                select(
                    PaymentAccountTariffPlan.payment_account_id,
                    PaymentAccountTariffPlan.tariff_plan_type_id,
                )
            )
        )
        assert keys == {
            (account_id, type_id)
            for account_id in account_ids
            for type_id in (subscription.id, compute.id)
        }


@pytest.mark.parametrize(
    "order", ["plan_first", "scheduled_plan_first", "account_first", "account_between_plans"]
)
async def test_postgres_writable_cte_uses_the_physical_insert_order(
    migration_database, owners, existing_accounts, order
):
    """Apply both rules when a single command inserts a first plan and an account."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports writable CTEs")
    user, _ = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    first_id, second_id, account_id = (str(uuid4()) for _ in range(3))
    parameters = {
        "kind": kind.id,
        "owner": user.id,
        "account": account_id,
        "first": first_id,
        "second": second_id,
        "start": AT,
        "publication": AT + TICK
        if order in {"scheduled_plan_first", "account_between_plans"}
        else AT - TICK,
        "backdated": AT - TICK,
    }
    async with migration_database.engine.begin() as connection:
        if order == "account_first":
            await connection.execute(
                text("""
                    WITH new_account AS (
                        INSERT INTO payment_accounts (id, user_id, created_at)
                        VALUES (:account, :owner, :start) RETURNING id
                    ), first_plan AS (
                        INSERT INTO tariff_plans
                          (id, tariff_plan_type_id, name, field_values, created_at, published_at)
                        SELECT :first, :kind, 'First', '{}', :start, :publication
                        FROM new_account RETURNING id
                    )
                    INSERT INTO tariff_plans
                      (id, tariff_plan_type_id, name, field_values, created_at, published_at)
                    SELECT :second, :kind, 'Newer', '{}', :start, :start FROM first_plan
                """),
                parameters,
            )
        elif order == "account_between_plans":
            await connection.execute(
                text("""
                    WITH first_plan AS (
                        INSERT INTO tariff_plans
                          (id, tariff_plan_type_id, name, field_values, created_at, published_at)
                        VALUES (:first, :kind, 'Scheduled', '{}', :start, :publication)
                        RETURNING id
                    ), new_account AS (
                        INSERT INTO payment_accounts (id, user_id, created_at)
                        SELECT :account, :owner, :start FROM first_plan RETURNING id
                    )
                    INSERT INTO tariff_plans
                      (id, tariff_plan_type_id, name, field_values, created_at, published_at)
                    SELECT :second, :kind, 'Backdated', '{}', :start, :backdated
                    FROM new_account
                """),
                parameters,
            )
        else:
            await connection.execute(
                text("""
                    WITH first_plan AS (
                        INSERT INTO tariff_plans
                          (id, tariff_plan_type_id, name, field_values, created_at, published_at)
                        VALUES (:first, :kind, 'First', '{}', :start, :publication) RETURNING id
                    )
                    INSERT INTO payment_accounts (id, user_id, created_at)
                    SELECT :account, :owner, :start FROM first_plan
                """),
                parameters,
            )

    await assert_assignments(
        migration_database,
        {
            **{account.id: {kind.id: first_id} for account in existing_accounts},
            account_id: {}
            if order in {"scheduled_plan_first", "account_between_plans"}
            else {kind.id: first_id},
        },
    )


async def test_postgres_copy_uses_the_first_row_even_with_a_sequential_scan(
    migration_database, existing_accounts
):
    """Seed an empty type once when COPY imports multiple variants before a later SQL read."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL COPY has an independent batching path")
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add_all([kind, plan("Other", TariffPlanType(name="Cloud", field_types={}), AT)])
    first_id, second_id = str(uuid4()), str(uuid4())
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql("ANALYZE tariff_plans")
        await connection.exec_driver_sql("SET LOCAL enable_indexscan = off")
        await connection.exec_driver_sql("SET LOCAL enable_bitmapscan = off")
        driver = (await connection.get_raw_connection()).driver_connection
        await driver.copy_records_to_table(
            "tariff_plans",
            columns=[
                "id",
                "tariff_plan_type_id",
                "name",
                "field_values",
                "created_at",
                "published_at",
            ],
            records=[
                (first_id, kind.id, "First copied", "{}", AT, AT + TICK),
                (second_id, kind.id, "Backdated", "{}", AT, AT),
            ],
        )
    async with migration_database.read() as session:
        for account in existing_accounts:
            assert (await assignments(session, account.id))[kind.id] == first_id


@pytest.mark.parametrize("stale_insert", ["account", "tariff"])
async def test_postgres_upgrade_invalidates_preexisting_snapshots(
    migration_database, alembic_config, owners, stale_insert
):
    """Reject an older snapshot even when the other actor wrote before the triggers existed."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can hold an older repeatable-read snapshot across an upgrade")
    user, _ = owners
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0020")
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(kind)
    account_id = str(uuid4())
    first_tariff = tariff_row(kind.id, "First", AT)
    account = insert(PaymentAccount.__table__).values(id=account_id, user_id=user.id, created_at=AT)
    tariff = insert(TariffPlan.__table__).values(first_tariff)
    winner, stale = (tariff, account) if stale_insert == "account" else (account, tariff)
    async with migration_database.engine.connect() as older:
        await older.execution_options(isolation_level="REPEATABLE READ")
        with pytest.raises(DBAPIError) as rejected:
            async with older.begin():
                await older.execute(select(User.id))
                async with migration_database.engine.begin() as other:
                    await other.execute(winner)
                await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
                await older.execute(stale)
        assert rejected.value.orig.sqlstate == "40001"
    async with migration_database.engine.begin() as retry:
        await retry.execute(stale)
    await assert_assignments(migration_database, {account_id: {kind.id: first_tariff["id"]}})


@pytest.mark.parametrize("shadow_tables", ["none", "partial", "catalogue"])
async def test_postgres_upgrade_uses_the_schema_containing_the_billing_tables(
    migration_database, alembic_config, existing_accounts, shadow_tables
):
    """Install and call the triggers even with another schema first on the search path."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can resolve tables from several schemas")
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0020")
    kind = TariffPlanType(name="Subscription", field_types={})
    original = plan("Before upgrade", TariffPlanType(name="Cloud", field_types={}), AT + TICK)
    async with migration_database.write() as session:
        session.add_all([kind, original])
        await session.execute(text("CREATE SCHEMA workspace"))
        if shadow_tables == "partial":
            await session.execute(
                text("CREATE TABLE workspace.tariff_plan_types (id uuid PRIMARY KEY)")
            )
            await session.execute(
                text("CREATE TABLE workspace.coordination (id int PRIMARY KEY, revision bigint)")
            )
        elif shadow_tables == "catalogue":
            for table in (
                "coordination",
                "tariff_plan_types",
                "tariff_plans",
                "payment_accounts",
                "payment_account_tariff_plans",
                "payment_ledger_entries",
                "user_tariff_plans",
            ):
                await session.execute(
                    text(f"CREATE TABLE workspace.{table} (LIKE public.{table} INCLUDING ALL)")
                )

    def use_workspace_first(connection, _cursor, statement, *_):
        """Prepend a different schema when Alembic reads the installed revision."""
        if statement.startswith("SELECT alembic_version.version_num"):
            connection.exec_driver_sql("SET LOCAL search_path TO workspace, public")

    event.listen(Engine, "before_cursor_execute", use_workspace_first)
    try:
        await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
    finally:
        event.remove(Engine, "before_cursor_execute", use_workspace_first)
    await assert_assignments(
        migration_database,
        {account.id: {original.tariff_plan_type_id: original.id} for account in existing_accounts},
    )

    tariff_id = str(uuid4())
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
        await connection.execute(
            text("""
                INSERT INTO public.tariff_plans
                  (id, tariff_plan_type_id, name, field_values, created_at, published_at)
                VALUES (:id, :kind, 'First', '{}', :start, :start)
            """),
            {"id": tariff_id, "kind": kind.id, "start": AT},
        )
    await assert_assignments(
        migration_database,
        {
            account.id: {original.tariff_plan_type_id: original.id, kind.id: tariff_id}
            for account in existing_accounts
        },
    )


@pytest.mark.parametrize("remove", ["delete", "move"])
async def test_postgres_an_older_snapshot_cannot_skip_reactivating_an_emptied_type(
    migration_database, existing_accounts, remove
):
    """Changing the catalogue must invalidate snapshots that still see its removed plan."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can keep a repeatable-read snapshot across another write")
    kind = TariffPlanType(name="Subscription", field_types={})
    destination = TariffPlanType(name="Archive", field_types={})
    original = plan("Before removal", kind, AT)
    async with migration_database.write() as session:
        session.add_all([original, destination])
    replacement = tariff_row(kind.id, "Replacement", AT + TICK)
    insert_replacement = insert(TariffPlan.__table__).values(replacement)
    async with migration_database.engine.connect() as older:
        await older.execution_options(isolation_level="REPEATABLE READ")
        with pytest.raises(DBAPIError) as rejected:
            async with older.begin():
                await older.execute(select(User.id))
                async with migration_database.engine.begin() as other:
                    await other.execute(
                        PaymentAccountTariffPlan.__table__.delete().where(
                            PaymentAccountTariffPlan.tariff_plan_id == original.id
                        )
                    )
                    where = TariffPlan.id == original.id
                    catalogue = TariffPlan.__table__
                    await other.execute(
                        catalogue.delete().where(where)
                        if remove == "delete"
                        else update(catalogue)
                        .where(where)
                        .values(tariff_plan_type_id=destination.id)
                    )
                await older.execute(insert_replacement)
        assert rejected.value.orig.sqlstate == "40001"
    async with migration_database.engine.begin() as retry:
        await retry.execute(insert_replacement)
    await assert_assignments(
        migration_database,
        {account.id: {kind.id: replacement["id"]} for account in existing_accounts},
    )


async def test_postgres_catalogue_truncate_can_finish_before_a_waiting_writer(
    migration_database, existing_accounts
):
    """Avoid an inverse lock order and see the cleared catalogue even on an older snapshot."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL has its own locking rules for TRUNCATE")
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(plan("Original", kind, AT))
    replacement = tariff_row(kind.id, "Replacement", AT + TICK)
    async with migration_database.engine.connect() as writing:
        await writing.execution_options(isolation_level="REPEATABLE READ")
        async with writing.begin():
            await writing.execute(
                update(Coordination)
                .where(Coordination.id == 1)
                .values(revision=Coordination.revision + 1)
            )
            async with asyncio.timeout(5), migration_database.engine.begin() as truncating:
                await truncating.exec_driver_sql("TRUNCATE tariff_plans CASCADE")
            await writing.execute(insert(TariffPlan.__table__).values(replacement))
    await assert_assignments(
        migration_database,
        {account.id: {kind.id: replacement["id"]} for account in existing_accounts},
    )


@pytest.mark.parametrize("coordinated", [False, True])
async def test_postgres_catalogue_reload_can_overlap_account_creation(
    migration_database, owners, coordinated
):
    """Avoid waiting for coordination while holding the locks taken by a catalogue reset."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL takes separate table locks for TRUNCATE")
    user, company = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    original = plan("Original", kind, AT)
    organization = PaymentAccount(company_id=company.id, created_at=AT)
    async with migration_database.write() as session:
        session.add_all([original, organization])
    account = PaymentAccount(user_id=user.id, created_at=AT)
    replacement = tariff_row(kind.id, "Replacement", AT + TICK)
    insert_replacement = insert(TariffPlan.__table__).values(replacement)
    truncate = text("TRUNCATE tariff_plans CASCADE")

    if coordinated:

        async def create_account():
            """Start a real application write while the catalogue is locked for maintenance."""
            async with migration_database.write() as writer:
                writer.add(account)

        follower = None
        try:
            async with migration_database.write() as loader:
                holder = await loader.scalar(text("SELECT pg_backend_pid()"))
                await loader.execute(truncate)
                follower = asyncio.create_task(create_account())
                await wait_for_postgres_blocker(migration_database, holder)
                await loader.execute(insert_replacement)
        finally:
            if follower is not None:
                await asyncio.wait_for(follower, 10)
    else:
        async with migration_database.write() as writer:
            with pytest.raises(DBAPIError) as busy:
                async with migration_database.engine.begin() as loader:
                    await loader.exec_driver_sql("SET LOCAL statement_timeout = '2s'")
                    await loader.execute(truncate)
                    await loader.execute(insert_replacement)
            assert busy.value.orig.sqlstate == "55P03"
            writer.add(account)
        await assert_assignments(
            migration_database,
            {row.id: {kind.id: original.id} for row in (organization, account)},
        )
        async with migration_database.engine.begin() as loader:
            await loader.execute(truncate)
            await loader.execute(insert_replacement)

    expected = {organization.id: {kind.id: replacement["id"]}, account.id: {}}
    if not coordinated:
        # The account already exists when the retry inserts the first tariff of the cleared type.
        expected[account.id] = {kind.id: replacement["id"]}
    await assert_assignments(migration_database, expected)


@pytest.mark.parametrize("conflicting", [True, False])
async def test_postgres_catalogue_reload_checks_the_upsert_lock_modes(
    migration_database, existing_accounts, conflicting
):
    """Allow row KEY SHARE while rejecting a table SHARE that would block the upsert."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL distinguishes row and table lock modes")
    kind = TariffPlanType(name="Subscription", field_types={})
    original = plan("Original", kind, AT)
    async with migration_database.write() as session:
        session.add(original)
    replacement = tariff_row(kind.id, "Replacement", AT + TICK)

    async def reload_catalogue():
        """Publish the first tariff after clearing the catalogue within one transaction."""
        async with migration_database.engine.begin() as loader:
            await loader.exec_driver_sql("SET LOCAL statement_timeout = '2s'")
            await loader.exec_driver_sql("TRUNCATE tariff_plans CASCADE")
            await loader.execute(insert(TariffPlan.__table__).values(replacement))

    async with migration_database.engine.begin() as holder:
        if conflicting:
            await holder.exec_driver_sql("LOCK TABLE coordination IN SHARE MODE")
            with pytest.raises(DBAPIError) as busy:
                await reload_catalogue()
            assert busy.value.orig.sqlstate == "55P03"
        else:
            await holder.exec_driver_sql("SELECT id FROM coordination WHERE id = 1 FOR KEY SHARE")
            await reload_catalogue()
    expected = original.id if conflicting else replacement["id"]
    await assert_assignments(
        migration_database, {account.id: {kind.id: expected} for account in existing_accounts}
    )


async def test_postgres_assignment_reload_releases_its_lock_for_a_waiting_writer(
    migration_database, owners
):
    """Also fail quickly when restoring the assignment table directly after truncating it."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL takes separate table locks for TRUNCATE")
    user, company = owners
    kind = TariffPlanType(name="Subscription", field_types={})
    original = plan("Original", kind, AT)
    organization = PaymentAccount(company_id=company.id, created_at=AT)
    async with migration_database.write() as session:
        session.add_all([original, organization])
    account = PaymentAccount(user_id=user.id, created_at=AT)
    expected = {kind.id: original.id}

    async def reload_assignments(*accounts):
        """Restore the supplied account slots using the existing catalogue row."""
        async with migration_database.engine.begin() as loader:
            await loader.exec_driver_sql("SET LOCAL statement_timeout = '2s'")
            await loader.exec_driver_sql("TRUNCATE payment_account_tariff_plans")
            await loader.execute(
                insert(PaymentAccountTariffPlan.__table__),
                [
                    {
                        "payment_account_id": row.id,
                        "tariff_plan_type_id": kind.id,
                        "tariff_plan_id": original.id,
                    }
                    for row in accounts
                ],
            )

    async with migration_database.write() as writer:
        with pytest.raises(DBAPIError) as busy:
            await reload_assignments(organization)
        assert busy.value.orig.sqlstate == "55P03"
        writer.add(account)
    await assert_assignments(migration_database, {organization.id: expected, account.id: expected})
    await reload_assignments(organization, account)
    await assert_assignments(migration_database, {organization.id: expected, account.id: expected})


async def test_postgres_catalogue_reload_wait_policy_ends_with_the_transaction(
    migration_database, existing_accounts
):
    """Restore normal waiting for coordination when a pooled connection is reused."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL takes separate table locks for TRUNCATE")
    kind = TariffPlanType(name="Subscription", field_types={})
    async with migration_database.write() as session:
        session.add(plan("Original", kind, AT))
    replacement = tariff_row(kind.id, "Replacement", AT + TICK)

    async with migration_database.engine.connect() as connection:
        async with connection.begin():
            await connection.exec_driver_sql("TRUNCATE tariff_plans CASCADE")

        async def publish():
            """Publish in a new transaction on the exact same connection."""
            async with connection.begin():
                await connection.execute(insert(TariffPlan.__table__).values(replacement))

        follower = None
        try:
            async with migration_database.write() as writer:
                holder = await writer.scalar(text("SELECT pg_backend_pid()"))
                follower = asyncio.create_task(publish())
                await wait_for_postgres_blocker(migration_database, holder)
        finally:
            if follower is not None:
                await asyncio.wait_for(follower, 10)

    await assert_assignments(
        migration_database,
        {account.id: {kind.id: replacement["id"]} for account in existing_accounts},
    )


async def test_postgres_upgrade_fails_fast_while_the_coordination_row_is_locked(
    migration_database, alembic_config
):
    """Avoid waiting with billing tables locked when a transaction already holds the guard row."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports independent row and table locks")
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0020")

    def bound_wait(connection, _cursor, statement, *_):
        """Bound the wait in case an implementation blocks inside its data write."""
        if statement.startswith("LOCK TABLE") and "coordination" in statement:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '100ms'")

    async with migration_database.engine.begin() as writer:
        await writer.exec_driver_sql("SELECT id FROM coordination WHERE id = 1 FOR UPDATE")
        event.listen(Engine, "before_cursor_execute", bound_wait)
        try:
            with pytest.raises(DBAPIError, match="could not obtain lock"):
                await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
        finally:
            event.remove(Engine, "before_cursor_execute", bound_wait)
    async with migration_database.read() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "0020"
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0021")
