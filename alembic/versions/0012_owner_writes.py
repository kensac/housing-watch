"""Owner writes: paused sources, listing overrides, the profile row.

All additive, so workers still on the previous image keep running against it: they never read the new
column or tables (a paused source just keeps running on them until they move forward).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-06 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_state", sa.Column("paused", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_table(
        "listing_overrides",
        sa.Column("listing_key", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("patch", postgresql.JSONB(), nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(op.f("ix_listing_overrides_source"), "listing_overrides", ["source"])
    op.create_table(
        "profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household", sa.Integer(), nullable=False),
        sa.Column("income", sa.Integer(), nullable=False),
        sa.Column("sizes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("excluded_boroughs", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("updated", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("profile")
    op.drop_index(op.f("ix_listing_overrides_source"), table_name="listing_overrides")
    op.drop_table("listing_overrides")
    op.drop_column("source_state", "paused")
