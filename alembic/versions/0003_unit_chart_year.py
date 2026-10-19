"""units.chart_year: the HUD chart year an ad's printed income caps are on

Revision ID: 0003
Revises: 0001
Create Date: 2026-10-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("units", sa.Column("chart_year", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("units", "chart_year")
