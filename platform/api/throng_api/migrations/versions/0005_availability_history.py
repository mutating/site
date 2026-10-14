"""Record online capacity intervals from this migration onward.

Revision ID: 0005
Revises: 0004
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    baseline = op.create_table(
        "availability_baseline",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=False),
    )
    intervals = op.create_table(
        "capacity_intervals",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("computer_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False)),
        sa.Column("company_id", sa.Uuid(as_uuid=False)),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False),
        sa.Column("cpu_millis", sa.BigInteger(), nullable=False),
        sa.Column("memory_bytes", sa.BigInteger(), nullable=False),
        sa.Column("cpu_occupied_millis", sa.BigInteger(), nullable=False),
        sa.Column("memory_occupied_bytes", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("ends_at >= starts_at", name="ordered_interval"),
        sa.CheckConstraint("cpu_millis >= 0 AND memory_bytes >= 0", name="positive_capacity"),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND company_id IS NULL) OR (user_id IS NULL AND company_id IS NOT NULL)",
            name="exactly_one_owner",
        ),
    )
    for name, columns in [
        ("computer_id", ["computer_id"]),
        ("user_time", ["user_id", "ends_at"]),
        ("company_time", ["company_id", "ends_at"]),
    ]:
        op.create_index("ix_capacity_intervals_" + name, "capacity_intervals", columns)
    bind = op.get_bind()
    now = datetime.now(UTC)
    bind.execute(baseline.insert().values(id=1, available_from=now))
    # Reflect with UUID/date types so both SQLite and PostgreSQL backfills bind correctly.
    metadata = sa.MetaData()
    computers = sa.Table("computers", metadata, autoload_with=bind)
    owners = sa.Table("computer_ownerships", metadata, autoload_with=bind)
    resources = sa.Table("computer_resources", metadata, autoload_with=bind)
    cpu_id = "00000000000000000000000000000001"
    ram_id = "00000000000000000000000000000002"
    for row in bind.execute(
        sa.select(computers, owners.c.user_id, owners.c.company_id)
        .join(owners, computers.c.id == owners.c.computer_id)
        .where(
            computers.c.online.is_(True),
            computers.c.enabled.is_(True),
            computers.c.session_id.is_not(None),
            computers.c.lease_expires_at > now,
        )
    ).mappings():
        amounts = bind.execute(
            sa.select(resources.c.resource_type_id, resources.c.total, resources.c.occupied).where(
                resources.c.computer_id == row["id"], resources.c.deleted_at.is_(None)
            )
        ).all()
        normalized = {
            str(key).replace("-", ""): (total, occupied) for key, total, occupied in amounts
        }
        bind.execute(
            intervals.insert().values(
                id=str(uuid4()),
                created_at=now,
                computer_id=str(row["id"]),
                user_id=str(row["user_id"]) if row["user_id"] else None,
                company_id=str(row["company_id"]) if row["company_id"] else None,
                starts_at=now,
                ends_at=row["lease_expires_at"],
                closed=False,
                cpu_occupied_millis=normalized.get(cpu_id, (0, 0))[1],
                memory_occupied_bytes=normalized.get(ram_id, (0, 0))[1],
                cpu_millis=normalized.get(cpu_id, (0, 0))[0],
                memory_bytes=normalized.get(ram_id, (0, 0))[0],
            )
        )


def downgrade():
    op.drop_table("capacity_intervals")
    op.drop_table("availability_baseline")
