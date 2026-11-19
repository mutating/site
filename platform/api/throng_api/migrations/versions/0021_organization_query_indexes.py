"""Index organization pages and scope inventory charts to actual inventory changes.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
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
    for table, columns in PAGE_INDEXES:
        op.create_index(f"ix_{table}_company_order", table, list(columns))
        op.drop_index(f"ix_{table}_company_id", table_name=table)
    for side, changed in (("before", INVENTORY_CHANGED), ("after", COMPANY_OWNERSHIP_CHANGED)):
        owner = f"{side}_company_id"
        predicate = sa.and_(sa.column(owner).is_not(None), changed)
        op.create_index(
            f"ix_inventory_history_{side}_company_time",
            "computer_resource_changes",
            [owner, "created_at"],
            sqlite_where=predicate,
            postgresql_where=predicate,
        )


def downgrade():
    for side in ("after", "before"):
        op.drop_index(
            f"ix_inventory_history_{side}_company_time",
            table_name="computer_resource_changes",
        )
    for table, _ in reversed(PAGE_INDEXES):
        op.create_index(f"ix_{table}_company_id", table, ["company_id"])
        op.drop_index(f"ix_{table}_company_order", table_name=table)
