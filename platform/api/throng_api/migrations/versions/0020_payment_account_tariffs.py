"""Add tariff publication times and payment account assignments.

Revision ID: 0020
Revises: 0019
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

from throng_api.account_tariff_schema import (
    CREATE_ACCOUNT_TARIFF_TRIGGER,
    DROP_ACCOUNT_TARIFF_TRIGGER,
    PUBLICATION_STORAGE_CHECKS,
    PUBLICATION_UNIQUE,
    TRIGGER_NAME,
    PostgreSQLExactTimestamp,
    insert_initial_tariffs,
    postgres_account_tariff_body,
    stored_utc,
)
from throng_api.first_tariff_schema import (
    POSTGRES_ACCOUNT_DEFAULT_TRIGGER,
    postgres_catalog_ddl,
    postgres_catalog_function,
)
from throng_api.migrations.billing import billing_migration_schema

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

UUID = sa.Uuid(as_uuid=False)
TIMESTAMP = sa.DateTime(timezone=True)
TIMESTAMP_VALUE = TIMESTAMP.with_variant(PostgreSQLExactTimestamp(), "postgresql")
LINKS = "payment_account_tariff_plans"


def billing_tables(schema):
    plans = sa.table(
        "tariff_plans",
        sa.column("id", UUID),
        sa.column("tariff_plan_type_id", UUID),
        sa.column("created_at", TIMESTAMP),
        sa.column("published_at", TIMESTAMP),
        schema=schema,
    )
    accounts = sa.table(
        "payment_accounts",
        sa.column("id", UUID),
        sa.column("created_at", TIMESTAMP),
        schema=schema,
    )
    return plans, accounts


def lock_schema(connection, schema):
    if schema:
        connection.exec_driver_sql(
            f"LOCK TABLE {schema}payment_accounts, {schema}tariff_plans IN ACCESS EXCLUSIVE MODE NOWAIT"
        )


def backfill_publications(connection, plans):
    by_type = defaultdict(list)
    for plan_id, type_id, created_at in connection.execute(
        sa.select(plans.c.id, plans.c.tariff_plan_type_id, plans.c.created_at)
    ):
        by_type[type_id].append((stored_utc(created_at), plan_id))

    latest = datetime.max.replace(tzinfo=UTC)
    tick = timedelta(microseconds=1)
    updates = []
    for rows in by_type.values():
        previous = None
        for index, (created_at, plan_id) in enumerate(sorted(rows)):
            # Existing plans can tie. Break ties by id, preserving chronological order
            # and leaving enough microseconds for the rest even at datetime.max.
            timestamp = min(created_at, latest - (len(rows) - index - 1) * tick)
            if previous is not None and timestamp <= previous:
                timestamp = previous + tick
            updates.append({"plan_id": plan_id, "timestamp": timestamp})
            previous = timestamp
    if updates:
        connection.execute(
            plans.update()
            .where(plans.c.id == sa.bindparam("plan_id", type_=UUID))
            .values(published_at=sa.bindparam("timestamp", type_=TIMESTAMP_VALUE)),
            updates,
        )


def backfill_account_tariffs(connection, accounts, prefix):
    # Decode old timestamps just as the ORM did. This also handles legacy SQLite
    # ISO spellings and asyncpg's infinity representation for Python's endpoints.
    cutoffs = sa.Table(
        "_account_tariff_cutoffs",
        sa.MetaData(),
        sa.Column("id", UUID, primary_key=True),
        sa.Column("created_at", TIMESTAMP_VALUE, nullable=False),
        prefixes=["TEMPORARY"],
        schema="pg_temp" if connection.dialect.name == "postgresql" else None,
    )
    cutoffs.create(connection)
    with connection.execute(sa.select(accounts.c.id, accounts.c.created_at)) as rows:
        for batch in rows.partitions(1000):
            connection.execute(
                cutoffs.insert(),
                [
                    {"id": account_id, "created_at": stored_utc(created_at)}
                    for account_id, created_at in batch
                ],
            )
    connection.exec_driver_sql(
        insert_initial_tariffs("account.created_at", accounts=cutoffs.fullname, schema=prefix)
    )
    cutoffs.drop(connection)


def would_recreate_assignments(connection, plans, accounts):
    earliest = stored_utc(connection.scalar(sa.select(sa.func.min(plans.c.published_at))))
    if earliest is None:
        return False
    with connection.execute(sa.select(accounts.c.created_at)) as rows:
        return any(stored_utc(created_at) >= earliest for created_at in rows.scalars())


def upgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context()) as (schema, prefix):
        lock_schema(connection, prefix)
        plans, accounts = billing_tables(schema)
        key_prefix = f"{schema}." if schema is not None else ""
        dialect = connection.dialect.name
        op.add_column(
            "tariff_plans", sa.Column("published_at", TIMESTAMP, nullable=True), schema=schema
        )
        backfill_publications(connection, plans)
        with op.batch_alter_table("tariff_plans", schema=schema) as batch:
            batch.alter_column("published_at", existing_type=TIMESTAMP, nullable=False)
            batch.create_unique_constraint(
                PUBLICATION_UNIQUE, ["tariff_plan_type_id", "published_at"]
            )
            name, check = PUBLICATION_STORAGE_CHECKS[dialect]
            batch.create_check_constraint(batch.f(f"ck_tariff_plans_{name}"), check)
        op.create_table(
            LINKS,
            sa.Column(
                "payment_account_id",
                UUID,
                sa.ForeignKey(f"{key_prefix}payment_accounts.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("tariff_plan_type_id", UUID, primary_key=True),
            sa.Column("tariff_plan_id", UUID, nullable=False),
            sa.ForeignKeyConstraint(
                ("tariff_plan_id", "tariff_plan_type_id"),
                (f"{key_prefix}tariff_plans.id", f"{key_prefix}tariff_plans.tariff_plan_type_id"),
            ),
            schema=schema,
        )
        op.create_index(
            "ix_payment_account_tariff_plans_tariff_plan_id",
            LINKS,
            ["tariff_plan_id"],
            schema=schema,
        )
        backfill_account_tariffs(connection, accounts, prefix)
        if prefix:
            connection.exec_driver_sql(
                postgres_catalog_ddl(
                    postgres_catalog_function(
                        f"{TRIGGER_NAME}()",
                        "trigger",
                        postgres_account_tariff_body(),
                        replace=False,
                    ),
                    POSTGRES_ACCOUNT_DEFAULT_TRIGGER,
                    schema=schema,
                )
            )
        else:
            for statement in CREATE_ACCOUNT_TARIFF_TRIGGER["sqlite"]:
                connection.exec_driver_sql(statement)


def downgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context()) as (schema, prefix):
        lock_schema(connection, prefix)
        plans, accounts = billing_tables(schema)
        dialect = connection.dialect.name
        if prefix:
            connection.exec_driver_sql(
                f"LOCK TABLE {prefix}{LINKS} IN ACCESS EXCLUSIVE MODE NOWAIT"
            )
        with connection.execute(sa.select(plans.c.created_at, plans.c.published_at)) as rows:
            changed = any(
                stored_utc(created_at) != stored_utc(published_at)
                for created_at, published_at in rows
            )
        # An empty snapshot is meaningful too if a later upgrade would repopulate it.
        if (
            changed
            or connection.scalar(sa.select(1).select_from(sa.table(LINKS, schema=schema)).limit(1))
            or would_recreate_assignments(connection, plans, accounts)
        ):
            raise CommandError(
                "Cannot downgrade while billing data exists in assignments or publication dates"
            )
        if prefix:
            connection.exec_driver_sql(f"DROP TRIGGER {TRIGGER_NAME} ON {prefix}payment_accounts")
            connection.exec_driver_sql(f"DROP FUNCTION {prefix}{TRIGGER_NAME}()")
        else:
            for statement in DROP_ACCOUNT_TARIFF_TRIGGER["sqlite"]:
                connection.exec_driver_sql(statement)
        op.drop_table(LINKS, schema=schema)
        with op.batch_alter_table("tariff_plans", schema=schema) as batch:
            batch.drop_constraint(PUBLICATION_UNIQUE, type_="unique")
            name, _ = PUBLICATION_STORAGE_CHECKS[dialect]
            batch.drop_constraint(batch.f(f"ck_tariff_plans_{name}"), type_="check")
            batch.drop_column("published_at")
