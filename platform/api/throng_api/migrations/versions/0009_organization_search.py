"""Unicode organization name search and stable natural ordering.

Revision ID: 0009
Revises: 0008
"""

import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "companies", sa.Column("name_search", sa.Text(), nullable=False, server_default="")
    )
    op.add_column("companies", sa.Column("name_key", sa.Text(), nullable=False, server_default=""))
    companies = sa.table(
        "companies",
        sa.column("id"),
        sa.column("name"),
        sa.column("name_search"),
        sa.column("name_key"),
    )
    connection = op.get_bind()
    # Keep the backfill definition frozen with this migration.
    for row in connection.execute(sa.select(companies.c.id, companies.c.name)).mappings():
        normalized = unicodedata.normalize("NFC", row["name"]).casefold()

        def number(match):
            digits = match[0].lstrip("0") or "0"
            return f"\x01{len(digits):03}{digits}"

        connection.execute(
            companies.update()
            .where(companies.c.id == row["id"])
            .values(name_search=normalized, name_key=re.sub(r"[0-9]+", number, normalized))
        )


def downgrade():
    with op.batch_alter_table("companies") as batch:
        batch.drop_column("name_key")
        batch.drop_column("name_search")
