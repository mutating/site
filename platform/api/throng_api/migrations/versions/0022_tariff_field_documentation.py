"""Store localized documentation for tariff fields.

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

from throng_api.billing_schema import FIELD_DOCUMENTATION_TABLE, field_documentation_checks
from throng_api.migrations.billing import billing_migration_schema
from throng_api.tariff_documentation_schema import (
    DOCUMENTATION_DROP_OID,
    MANAGED_DOCUMENTATION,
    create_documentation_guards,
)

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context()) as (name, prefix):
        uuid = sa.Uuid(as_uuid=False)
        type_key = f"{name}.tariff_plan_types.id" if name else "tariff_plan_types.id"
        op.create_table(
            FIELD_DOCUMENTATION_TABLE,
            sa.Column("id", uuid, primary_key=True),
            sa.Column(
                "tariff_plan_type_id",
                uuid,
                sa.ForeignKey(type_key, ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("field_name", sa.Text(), nullable=False),
            sa.Column("language_code", sa.Text(), nullable=False),
            sa.Column("comment", sa.Text(), nullable=False),
            *field_documentation_checks(),
            schema=name,
        )
        op.create_index(
            "ix_tariff_field_documentation_tariff_plan_type_id",
            FIELD_DOCUMENTATION_TABLE,
            ["tariff_plan_type_id"],
            schema=name,
        )
        if prefix:
            connection.exec_driver_sql(
                create_documentation_guards(prefix + FIELD_DOCUMENTATION_TABLE)
            )


def downgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context()) as (name, prefix):
        info = {MANAGED_DOCUMENTATION: True}
        if prefix:
            relation = prefix + FIELD_DOCUMENTATION_TABLE
            info[DOCUMENTATION_DROP_OID] = connection.scalar(
                sa.text("SELECT pg_catalog.to_regclass(:relation)::oid"), {"relation": relation}
            )
            connection.exec_driver_sql(f"LOCK TABLE {relation} IN ACCESS EXCLUSIVE MODE NOWAIT")
        if connection.scalar(
            sa.select(1).select_from(sa.table(FIELD_DOCUMENTATION_TABLE, schema=name)).limit(1)
        ):
            raise CommandError("Cannot downgrade while tariff field documentation exists")
        op.drop_table(FIELD_DOCUMENTATION_TABLE, schema=name, info=info)
