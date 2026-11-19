"""Add tariff definitions, user assignments, payment accounts and ledger entries.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

from throng_api.first_tariff_schema import postgres_relation_schema
from throng_api.migrations.billing import billing_migration_schema

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None
OPERATION_ID_WHITESPACE = " \t\n\r\v\f"
BILLING_TABLES = (
    "payment_ledger_entries",
    "payment_accounts",
    "user_tariff_plans",
    "tariff_plans",
    "tariff_plan_types",
)


def uuid_check(column="id", *, nullable=False):
    canonical = (
        f"typeof({column}) = 'text' AND length({column}) = 32 "
        f"AND instr({column}, char(0)) = 0 AND {column} NOT GLOB '*[^0-9a-f]*'"
    )
    constraint = f"{column} IS NULL OR ({canonical})" if nullable else canonical
    return sa.CheckConstraint(constraint, name=f"{column}_canonical_uuid").ddl_if(dialect="sqlite")


def text_check(column):
    return sa.CheckConstraint(
        f"{column} IS NULL OR (typeof({column}) = 'text' AND instr({column}, char(0)) = 0)",
        name=f"{column}_storage",
    ).ddl_if(dialect="sqlite")


def amount_checks(column):
    return (
        sa.CheckConstraint(
            f"typeof({column}) = 'integer' AND "
            f"{column} BETWEEN -999999999999999999 AND 999999999999999999",
            name=f"{column}_storage",
        ).ddl_if(dialect="sqlite"),
        sa.CheckConstraint(f"{column} <> 'NaN'", name=f"{column}_finite").ddl_if(
            dialect="postgresql"
        ),
    )


def legacy_owner_key(connection, owner):
    if connection.dialect.name == "sqlite":
        return f"{owner}.id"
    schema = connection.execute(
        sa.text(postgres_relation_schema("pg_catalog.to_regclass(:owner)")), {"owner": owner}
    ).scalar_one()
    return f"{schema}.{owner}.id"


def upgrade():
    connection = op.get_bind()
    # Capture the same legacy tables the application sees before selecting the DDL schema.
    user_key = legacy_owner_key(connection, "users")
    company_key = legacy_owner_key(connection, "companies")
    with billing_migration_schema(connection, op.get_context()) as (schema, _):
        prefix = f"{schema}." if schema is not None else ""
        uuid = sa.Uuid(as_uuid=False)
        amount = sa.Numeric(18, 6).with_variant(sa.BigInteger(), "sqlite")
        op.create_table(
            "tariff_plan_types",
            sa.Column("id", uuid, primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("field_types", sa.JSON(), nullable=False),
            uuid_check(),
            text_check("name"),
            sa.CheckConstraint("length(name) <= 200", name="name_length"),
            schema=schema,
        )
        op.create_table(
            "tariff_plans",
            sa.Column("id", uuid, primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column(
                "tariff_plan_type_id",
                uuid,
                sa.ForeignKey(f"{prefix}tariff_plan_types.id"),
                nullable=False,
            ),
            sa.Column("field_values", sa.JSON(), nullable=False),
            sa.UniqueConstraint("id", "tariff_plan_type_id"),
            uuid_check(),
            text_check("name"),
            sa.CheckConstraint("length(name) <= 200", name="name_length"),
            schema=schema,
        )
        op.create_index(
            "ix_tariff_plans_tariff_plan_type_id",
            "tariff_plans",
            ["tariff_plan_type_id"],
            schema=schema,
        )
        op.create_table(
            "user_tariff_plans",
            sa.Column(
                "user_id", uuid, sa.ForeignKey(user_key, ondelete="CASCADE"), primary_key=True
            ),
            sa.Column("tariff_plan_type_id", uuid, primary_key=True),
            sa.Column("tariff_plan_id", uuid, nullable=False),
            uuid_check("user_id"),
            sa.ForeignKeyConstraint(
                ("tariff_plan_id", "tariff_plan_type_id"),
                (f"{prefix}tariff_plans.id", f"{prefix}tariff_plans.tariff_plan_type_id"),
            ),
            schema=schema,
        )
        op.create_index(
            "ix_user_tariff_plans_tariff_plan_id",
            "user_tariff_plans",
            ["tariff_plan_id"],
            schema=schema,
        )
        op.create_table(
            "payment_accounts",
            sa.Column("id", uuid, primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("user_id", uuid, sa.ForeignKey(user_key)),
            sa.Column("company_id", uuid, sa.ForeignKey(company_key)),
            sa.Column("balance", amount, nullable=False, server_default="0"),
            uuid_check(),
            uuid_check("user_id", nullable=True),
            uuid_check("company_id", nullable=True),
            sa.CheckConstraint(
                "(user_id IS NOT NULL AND company_id IS NULL) OR "
                "(user_id IS NULL AND company_id IS NOT NULL)",
                name="exactly_one_owner",
            ),
            sa.CheckConstraint("balance >= 0", name="nonnegative_balance"),
            *amount_checks("balance"),
            schema=schema,
        )
        op.create_index(
            "ix_payment_accounts_user_id", "payment_accounts", ["user_id"], schema=schema
        )
        op.create_index(
            "ix_payment_accounts_company_id", "payment_accounts", ["company_id"], schema=schema
        )
        op.create_table(
            "payment_ledger_entries",
            sa.Column("id", uuid, primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "payment_account_id",
                uuid,
                sa.ForeignKey(f"{prefix}payment_accounts.id"),
                nullable=False,
            ),
            sa.Column("operation_id", sa.Text(), nullable=False),
            sa.Column("amount", amount, nullable=False),
            sa.Column("comment", sa.Text()),
            uuid_check(),
            sa.CheckConstraint(
                "length(operation_id) BETWEEN 1 AND 128 "
                f"AND trim(operation_id, '{OPERATION_ID_WHITESPACE}') <> ''",
                name="valid_operation_id",
            ),
            text_check("operation_id"),
            text_check("comment"),
            *amount_checks("amount"),
            schema=schema,
        )
        op.create_index(
            "ix_payment_ledger_entries_payment_account_id",
            "payment_ledger_entries",
            ["payment_account_id"],
            schema=schema,
        )
        op.create_index(
            "ix_payment_ledger_entries_operation_id",
            "payment_ledger_entries",
            ["operation_id"],
            schema=schema,
        )


def downgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context(), lock_timeout="1ms") as (
        schema,
        prefix,
    ):
        # Block writers from creating billing data between the guard and the drops.
        if prefix:
            tables = ", ".join(prefix + name for name in BILLING_TABLES)
            connection.exec_driver_sql(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE NOWAIT")
        for name in BILLING_TABLES:
            if connection.scalar(sa.select(1).select_from(sa.table(name, schema=schema)).limit(1)):
                raise CommandError("Cannot downgrade while billing data exists")
        for name in BILLING_TABLES:
            op.drop_table(name, schema=schema)
