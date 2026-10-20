"""tracker_rows, tracker_links: the application tracker sheet and manual matches to listings

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-20 02:10:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracker_rows",
        sa.Column("row_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("property", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.Column("cells", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("synced", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("row_key"),
    )
    op.create_table(
        "tracker_links",
        sa.Column("listing_key", sa.Text(), nullable=False),
        sa.Column("row_key", sa.Text(), nullable=True),
        sa.Column("created", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("listing_key"),
    )


def downgrade() -> None:
    op.drop_table("tracker_links")
    op.drop_table("tracker_rows")
