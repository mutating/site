"""Index organization pages and scope inventory charts to actual inventory changes.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

from throng_api.migrations.billing import billing_migration_schema

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

PAGE_INDEXES = (
    ("memberships", ("company_id", "id")),
    ("computer_ownerships", ("company_id", "computer_id")),
    ("company_invitations", ("company_id", sa.text("created_at DESC"), "id")),
)
COMPANY_OWNERSHIP_CHANGED = sa.column("before_company_id").is_distinct_from(
    sa.column("after_company_id")
)
INVENTORY_CHANGED = sa.or_(
    sa.column("before_total") != sa.column("after_total"),
    sa.column("before_user_id").is_distinct_from(sa.column("after_user_id")),
    COMPANY_OWNERSHIP_CHANGED,
)


def upgrade():
    with billing_migration_schema(op.get_bind(), op.get_context(), lock_timeout="1ms") as (
        schema,
        _,
    ):
        for table, columns in PAGE_INDEXES:
            op.create_index(f"ix_{table}_company_order", table, list(columns), schema=schema)
            op.drop_index(f"ix_{table}_company_id", table_name=table, schema=schema)
        for side, changed in (("before", INVENTORY_CHANGED), ("after", COMPANY_OWNERSHIP_CHANGED)):
            owner = f"{side}_company_id"
            predicate = sa.and_(sa.column(owner).is_not(None), changed)
            op.create_index(
                f"ix_inventory_history_{side}_company_time",
                "computer_resource_changes",
                [owner, "created_at"],
                schema=schema,
                sqlite_where=predicate,
                postgresql_where=predicate,
            )


def downgrade():
    with billing_migration_schema(op.get_bind(), op.get_context(), lock_timeout="1ms") as (
        schema,
        _,
    ):
        for side in ("after", "before"):
            op.drop_index(
                f"ix_inventory_history_{side}_company_time",
                table_name="computer_resource_changes",
                schema=schema,
            )
        for table, _ in reversed(PAGE_INDEXES):
            op.create_index(f"ix_{table}_company_id", table, ["company_id"], schema=schema)
            op.drop_index(f"ix_{table}_company_order", table_name=table, schema=schema)
