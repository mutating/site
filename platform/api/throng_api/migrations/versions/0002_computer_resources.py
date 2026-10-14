"""Resource inventory, immutable history and system administrators.

Revision ID: 0002
Revises: 0001
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

CPU = "00000000-0000-0000-0000-000000000001"
MEMORY = "00000000-0000-0000-0000-000000000002"


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("is_system_admin", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    types = op.create_table(
        "resource_types",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    resources = op.create_table(
        "computer_resources",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "computer_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("computers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "resource_type_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("resource_types.id"),
            nullable=False,
        ),
        sa.Column("total", sa.BigInteger(), nullable=False),
        sa.Column("occupied", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("computer_id", "resource_type_id"),
        sa.CheckConstraint("total >= 0 AND total <= 1152921504606846976", name="valid_total"),
        sa.CheckConstraint("occupied >= 0 AND occupied <= total", name="valid_occupation"),
        sa.CheckConstraint("version >= 1", name="positive_version"),
        sa.CheckConstraint("deleted_at IS NULL OR occupied = 0", name="deleted_unoccupied"),
    )
    op.create_index("ix_computer_resources_computer_id", "computer_resources", ["computer_id"])
    op.create_index(
        "ix_computer_resources_resource_type_id", "computer_resources", ["resource_type_id"]
    )
    history = op.create_table(
        "computer_resource_changes",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("computer_resource_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("computer_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column(
            "resource_type_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("resource_types.id"),
            nullable=False,
        ),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("before_total", sa.BigInteger(), nullable=False),
        sa.Column("before_occupied", sa.BigInteger(), nullable=False),
        sa.Column("after_total", sa.BigInteger(), nullable=False),
        sa.Column("after_occupied", sa.BigInteger(), nullable=False),
        sa.Column("before_user_id", sa.Uuid(as_uuid=False)),
        sa.Column("before_company_id", sa.Uuid(as_uuid=False)),
        sa.Column("after_user_id", sa.Uuid(as_uuid=False)),
        sa.Column("after_company_id", sa.Uuid(as_uuid=False)),
        sa.Column("actor_id", sa.Uuid(as_uuid=False)),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.UniqueConstraint("computer_resource_id", "version"),
        sa.CheckConstraint(
            "before_user_id IS NULL OR before_company_id IS NULL", name="before_single_owner"
        ),
        sa.CheckConstraint(
            "after_user_id IS NULL OR after_company_id IS NULL", name="after_single_owner"
        ),
        sa.CheckConstraint(
            "before_total >= 0 AND before_occupied >= 0 AND before_occupied <= before_total",
            name="valid_before",
        ),
        sa.CheckConstraint(
            "after_total >= 0 AND after_occupied >= 0 AND after_occupied <= after_total",
            name="valid_after",
        ),
    )
    op.create_index("ix_resource_history_revision", "computer_resource_changes", ["revision"])
    op.create_index("ix_resource_history_created_at", "computer_resource_changes", ["created_at"])
    for side in ("before", "after"):
        for owner in ("user", "company"):
            op.create_index(
                f"ix_resource_history_{side}_{owner}",
                "computer_resource_changes",
                [f"{side}_{owner}_id", "created_at"],
            )

    bind = op.get_bind()
    now = datetime.now(UTC)
    bind.execute(
        types.insert(),
        [
            {
                "id": CPU,
                "created_at": now,
                "name": "CPU",
                "description": "CPU capacity; 1000 millicores equal one core",
                "code": "cpu",
                "unit": "millicore",
                "builtin": True,
            },
            {
                "id": MEMORY,
                "created_at": now,
                "name": "Memory",
                "description": "Memory capacity in bytes",
                "code": "memory",
                "unit": "byte",
                "builtin": True,
            },
        ],
    )
    bind.execute(sa.text("UPDATE coordination SET revision = revision + 1 WHERE id = 1"))
    sequence = bind.scalar(sa.text("SELECT revision FROM coordination WHERE id = 1"))
    computers = sa.table(
        "computers",
        sa.column("id", sa.Uuid(as_uuid=False)),
        *[
            sa.column(name, sa.BigInteger())
            for name in ("cpu_millis", "memory_bytes", "reserved_cpu", "reserved_memory")
        ],
    )
    owners = sa.table(
        "computer_ownerships",
        *[
            sa.column(name, sa.Uuid(as_uuid=False))
            for name in ("computer_id", "user_id", "company_id")
        ],
    )
    for row in bind.execute(
        sa.select(computers, owners.c.user_id, owners.c.company_id).outerjoin(
            owners, computers.c.id == owners.c.computer_id
        )
    ).mappings():
        for type_id, total, occupied in [
            (CPU, row["cpu_millis"], row["reserved_cpu"]),
            (MEMORY, row["memory_bytes"], row["reserved_memory"]),
        ]:
            resource_id = str(uuid4())
            bind.execute(
                resources.insert().values(
                    id=resource_id,
                    created_at=now,
                    computer_id=row["id"],
                    resource_type_id=type_id,
                    total=total,
                    occupied=occupied,
                    version=1,
                )
            )
            bind.execute(
                history.insert().values(
                    id=str(uuid4()),
                    created_at=now,
                    computer_resource_id=resource_id,
                    computer_id=row["id"],
                    resource_type_id=type_id,
                    version=1,
                    revision=sequence,
                    before_total=0,
                    before_occupied=0,
                    after_total=total,
                    after_occupied=occupied,
                    after_user_id=row["user_id"],
                    after_company_id=row["company_id"],
                    reason="migration",
                )
            )
    with op.batch_alter_table("computers") as batch:
        for constraint in (
            "positive_capacity",
            "nonnegative_reservation",
            "reservation_within_capacity",
        ):
            batch.drop_constraint(op.f(f"ck_computers_{constraint}"), type_="check")
        for column in ("cpu_millis", "memory_bytes", "reserved_cpu", "reserved_memory"):
            batch.drop_column(column)
        batch.create_check_constraint("positive_capacity", "disk_bytes > 0")
        batch.create_check_constraint("nonnegative_reservation", "reserved_disk >= 0")
        batch.create_check_constraint("reservation_within_capacity", "reserved_disk <= disk_bytes")


def downgrade():
    bind = op.get_bind()
    computers = sa.table("computers", sa.column("id", sa.Uuid(as_uuid=False)))
    resources = sa.table(
        "computer_resources",
        sa.column("computer_id", sa.Uuid(as_uuid=False)),
        sa.column("resource_type_id", sa.Uuid(as_uuid=False)),
        sa.column("total", sa.BigInteger()),
        sa.column("occupied", sa.BigInteger()),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
    )
    capacities = {}
    for computer_id in bind.scalars(sa.select(computers.c.id)):
        rows = {
            row.resource_type_id: row
            for row in bind.execute(
                sa.select(resources).where(
                    resources.c.computer_id == computer_id, resources.c.deleted_at.is_(None)
                )
            )
        }
        if any(key not in rows or rows[key].total <= 0 for key in (CPU, MEMORY)):
            raise RuntimeError("Restore positive CPU and memory resources before downgrading")
        if rows[CPU].total > 1_000_000:
            raise RuntimeError("CPU capacity exceeds the legacy limit")
        capacities[computer_id] = rows
    with op.batch_alter_table("computers") as batch:
        for name in ("cpu_millis", "memory_bytes", "reserved_cpu", "reserved_memory"):
            batch.add_column(
                sa.Column(
                    name,
                    sa.Integer() if name in ("cpu_millis", "reserved_cpu") else sa.BigInteger(),
                    nullable=False,
                    server_default="1" if name in ("cpu_millis", "memory_bytes") else "0",
                )
            )
        for constraint in (
            "positive_capacity",
            "nonnegative_reservation",
            "reservation_within_capacity",
        ):
            batch.drop_constraint(op.f(f"ck_computers_{constraint}"), type_="check")
        batch.create_check_constraint(
            "positive_capacity", "cpu_millis > 0 AND memory_bytes > 0 AND disk_bytes > 0"
        )
        batch.create_check_constraint(
            "nonnegative_reservation",
            "reserved_cpu >= 0 AND reserved_memory >= 0 AND reserved_disk >= 0",
        )
        batch.create_check_constraint(
            "reservation_within_capacity",
            "reserved_cpu <= cpu_millis AND reserved_memory <= memory_bytes AND reserved_disk <= disk_bytes",
        )
    restored = sa.table(
        "computers",
        sa.column("id", sa.Uuid(as_uuid=False)),
        *[
            sa.column(name, sa.BigInteger())
            for name in ("cpu_millis", "memory_bytes", "reserved_cpu", "reserved_memory")
        ],
    )
    for key, rows in capacities.items():
        bind.execute(
            restored.update()
            .where(restored.c.id == key)
            .values(
                cpu_millis=rows[CPU].total,
                reserved_cpu=rows[CPU].occupied,
                memory_bytes=rows[MEMORY].total,
                reserved_memory=rows[MEMORY].occupied,
            )
        )
    with op.batch_alter_table("computers") as batch:
        for name in ("cpu_millis", "memory_bytes", "reserved_cpu", "reserved_memory"):
            batch.alter_column(name, server_default=None)
    op.drop_table("computer_resource_changes")
    op.drop_table("computer_resources")
    op.drop_table("resource_types")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_system_admin")
