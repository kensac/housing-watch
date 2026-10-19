"""fleet: chunk results, run plans, a source's learned queue

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("chunks", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("source_state", sa.Column("queue", sa.Text(), nullable=True))
    op.create_table(
        "chunk_results",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cache", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("worker", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("chunk_results")
    op.drop_column("source_state", "queue")
    op.drop_column("runs", "plan")
    op.drop_column("runs", "chunks")
