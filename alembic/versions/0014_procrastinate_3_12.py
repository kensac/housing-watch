"""Procrastinate 3.11/3.12 schema migrations: lock_mode, procrastinate_defer_jobs_v2, a lock index.

Only the pre-migrations run here. The post-migrations drop procrastinate_defer_jobs_v1 and
procrastinate_defer_periodic_job_v2, which workers on the previous image (Procrastinate 3.10) still call,
so they wait for a later revision once every host runs 3.12. 03.12.00_03 builds its index CONCURRENTLY,
which cannot run inside the migration's transaction, so it is built plainly: procrastinate_jobs is kept
small by fleet.tasks.cleanup.

A database created since 3.12 already has this schema from 0002 (SchemaManager.get_schema() is the
installed version's), so it is skipped when procrastinate_jobs.lock_mode exists. downgrade() leaves the
schema as it is: 3.10 runs against it, since the v1 functions are kept.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence
from pathlib import Path

import procrastinate
import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRE = (
    "03.11.00_01_pre_fetch_job_skip_null_lock.sql",
    "03.12.00_01_pre_add_lock_mode.sql",
    "03.12.00_02_pre_retry_job_fail_on_queueing_lock_conflict.sql",
    "03.12.00_03_pre_add_todo_doing_lock_index.sql",
)


def upgrade() -> None:
    bind = op.get_bind()
    if "lock_mode" in {c["name"] for c in sa.inspect(bind).get_columns("procrastinate_jobs")}:
        return
    mig = Path(procrastinate.__file__).parent / "sql" / "migrations"
    for name in PRE:
        sql = (mig / name).read_text().replace("CREATE INDEX CONCURRENTLY", "CREATE INDEX")
        # The raw DBAPI connection runs the script as-is: no bind-parameter or % parsing.
        bind.connection.dbapi_connection.execute(sql)


def downgrade() -> None:
    pass
