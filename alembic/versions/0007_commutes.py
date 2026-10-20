"""commutes: transit time from listing addresses to the commute destination

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-20 01:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commutes",
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("route", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("depart", sa.DateTime(timezone=True), nullable=True),
        sa.Column("computed", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("origin", "destination"),
    )


def downgrade() -> None:
    op.drop_table("commutes")
