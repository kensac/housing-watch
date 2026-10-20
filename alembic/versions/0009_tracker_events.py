"""tracker_events: tracker row status changes, recorded by each sync

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-20 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracker_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("row_key", sa.Text(), nullable=False),
        sa.Column("status_from", sa.Text(), nullable=True),
        sa.Column("status_to", sa.Text(), nullable=True),
        sa.Column("baseline", sa.Boolean(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tracker_events_row_key"), "tracker_events", ["row_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tracker_events_row_key"), table_name="tracker_events")
    op.drop_table("tracker_events")
