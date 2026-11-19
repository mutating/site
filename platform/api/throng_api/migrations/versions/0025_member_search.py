"""Store normalized member names for directory search.

Revision ID: 0025
Revises: 0024
"""

import unicodedata

import sqlalchemy as sa
from alembic import op

from throng_api.migrations.billing import billing_migration_schema, payment_owner_schema

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context(), lock_timeout="1ms") as (schema, _):
        owner_schema = payment_owner_schema(connection, schema, "user_id")
        op.add_column(
            "users",
            sa.Column("name_search", sa.Text(), nullable=False, server_default=""),
            schema=owner_schema,
        )
        users = sa.table(
            "users",
            sa.column("id"),
            sa.column("first_name"),
            sa.column("last_name"),
            sa.column("name_search"),
            schema=owner_schema,
        )
        for user in connection.execute(sa.select(users)).mappings():
            name = " ".join(f"{user['first_name']} {user['last_name']}".split())
            connection.execute(
                users.update()
                .where(users.c.id == user["id"])
                .values(name_search=unicodedata.normalize("NFC", name).casefold())
            )


def downgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context(), lock_timeout="1ms") as (
        schema,
        _,
    ):
        owner_schema = payment_owner_schema(connection, schema, "user_id")
        with op.batch_alter_table("users", schema=owner_schema) as batch:
            batch.drop_column("name_search")
