"""saved_views: named filter/sort/column sets for the web UI

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19 03:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("page", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "saved_views_one_default", "saved_views", ["page"], unique=True, postgresql_where=sa.text("is_default")
    )
    op.create_index("saved_views_page", "saved_views", ["page", "position"], unique=False)


def downgrade() -> None:
    op.drop_index("saved_views_page", table_name="saved_views")
    op.drop_index("saved_views_one_default", table_name="saved_views", postgresql_where=sa.text("is_default"))
    op.drop_table("saved_views")
