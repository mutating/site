"""Store normalized member names for directory search.

Revision ID: 0020
Revises: 0019
"""

import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("name_search", sa.Text(), nullable=False, server_default=""))
    users = sa.table(
        "users",
        sa.column("id"),
        sa.column("first_name"),
        sa.column("last_name"),
        sa.column("name_search"),
    )
    connection = op.get_bind()
    for user in connection.execute(sa.select(users)).mappings():
        name = " ".join(f"{user['first_name']} {user['last_name']}".split())
        connection.execute(
            users.update()
            .where(users.c.id == user["id"])
            .values(name_search=unicodedata.normalize("NFC", name).casefold())
        )


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("name_search")
