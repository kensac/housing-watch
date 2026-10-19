"""fleet_workers: each worker's name (host), queues and heartbeat

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19 20:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fleet_workers",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("queues", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("started", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("fleet_workers")
