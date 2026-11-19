"""Store private organization avatars and backgrounds.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

from throng_api.migrations.billing import billing_migration_schema, payment_owner_schema

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context(), lock_timeout="1ms") as (
        schema,
        _,
    ):
        owner_schema = payment_owner_schema(connection, schema, "company_id")
        with op.batch_alter_table("companies", schema=owner_schema) as batch:
            batch.add_column(sa.Column("avatar_data", sa.LargeBinary(), nullable=True))
            batch.add_column(sa.Column("background_data", sa.LargeBinary(), nullable=True))
            batch.add_column(sa.Column("avatar_version", sa.String(64), nullable=True))
            batch.add_column(sa.Column("background_version", sa.String(64), nullable=True))


def downgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context(), lock_timeout="1ms") as (
        schema,
        _,
    ):
        owner_schema = payment_owner_schema(connection, schema, "company_id")
        with op.batch_alter_table("companies", schema=owner_schema) as batch:
            batch.drop_column("background_version")
            batch.drop_column("avatar_version")
            batch.drop_column("background_data")
            batch.drop_column("avatar_data")
