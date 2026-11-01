"""listings.updated: when any stored field of a listing last changed, the key of the read-side cache

last_seen moves on every scrape and last_changed only with an event, so neither tells the cache whether a
listing's stored fields moved. Existing rows start at the migration's time, which invalidates the cache once.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-01 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listings", sa.Column("updated", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
    )


def downgrade() -> None:
    op.drop_column("listings", "updated")
