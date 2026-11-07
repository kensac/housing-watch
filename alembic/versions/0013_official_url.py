"""listings.official_url: an aggregator's link to the real posting, so its listing can be read as a copy of it.

Nullable and outside the fingerprint, so adding it is no event; workers on the previous image never write it.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-07 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("official_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("listings", "official_url")
