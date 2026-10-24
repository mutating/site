"""Editable repository bindings with immutable history and workflow-wide policies.

Revision ID: 0015
Revises: 0014
"""

import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

FIELDS = (
    "repository",
    "repository_owner_id",
    "workflow_path",
    "workflow_paths",
    "ref",
    "job_key",
    "company_id",
    "user_id",
    "computer_ids",
    "cpu_millis",
    "memory_bytes",
    "disk_bytes",
    "preparation_seconds",
    "timeout_seconds",
    "max_parallel",
    "enabled",
    "repository_binding",
)


def upgrade():
    with op.batch_alter_table("ci_routes") as batch:
        batch.add_column(sa.Column("workflow_paths", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("repository_binding", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("authorized_by", sa.Uuid(as_uuid=False), nullable=True))
        batch.create_foreign_key(
            "fk_ci_routes_authorized_by_users", "users", ["authorized_by"], ["id"]
        )
    op.add_column(
        "ci_reservations",
        sa.Column("route_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("ci_reservations", sa.Column("workflow_path", sa.String(250), nullable=True))
    history = op.create_table(
        "ci_route_revisions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "route_id", sa.Uuid(as_uuid=False), sa.ForeignKey("ci_routes.id"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.UniqueConstraint("route_id", "revision"),
    )
    op.create_index("ix_ci_route_revisions_route_id", "ci_route_revisions", ["route_id"])
    connection = op.get_bind()
    routes = sa.table(
        "ci_routes",
        sa.column("id", sa.Uuid(as_uuid=False)),
        sa.column("created_by", sa.Uuid(as_uuid=False)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        *[
            sa.column(
                name,
                sa.JSON()
                if name in {"computer_ids", "workflow_paths"}
                else sa.Uuid(as_uuid=False)
                if name in {"user_id", "company_id"}
                else sa.String(),
            )
            for name in FIELDS
        ],
    )
    # Bind UUID/JSON types explicitly; SQLite's raw storage representation is different.
    for row in connection.execute(sa.select(routes)).mappings():
        configuration = {name: row[name] for name in FIELDS}
        for name in ("computer_ids", "workflow_paths"):
            if isinstance(configuration[name], str):
                configuration[name] = json.loads(configuration[name])
        configuration["enabled"] = bool(configuration["enabled"])
        configuration["repository_binding"] = False
        connection.execute(
            history.insert().values(
                id=str(uuid4()),
                route_id=row["id"],
                revision=1,
                actor_id=row["created_by"],
                action="imported",
                configuration=configuration,
                created_at=row["created_at"],
            )
        )
    connection.execute(sa.text("UPDATE ci_routes SET authorized_by = created_by"))


def downgrade():
    op.drop_table("ci_route_revisions")
    op.drop_column("ci_reservations", "workflow_path")
    op.drop_column("ci_reservations", "route_revision")
    with op.batch_alter_table("ci_routes") as batch:
        batch.drop_constraint("fk_ci_routes_authorized_by_users", type_="foreignkey")
        for name in ("authorized_by", "revision", "repository_binding", "workflow_paths"):
            batch.drop_column(name)
