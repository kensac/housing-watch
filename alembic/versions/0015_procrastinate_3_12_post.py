"""Procrastinate 3.12 post-migrations: drop the v1 defer functions and the (id, lock) index.

0014 ran the pre-migrations and held these back, since workers on the 3.10 image still called
procrastinate_defer_jobs_v1 and procrastinate_defer_periodic_job_v2. Every host now runs a 3.12 image,
which calls neither. 03.12.00_51 drops its index CONCURRENTLY, which cannot run inside the migration's
transaction, so it is dropped plainly: procrastinate_jobs is kept small by fleet.tasks.cleanup.

Every statement is DROP ... IF EXISTS, so a database whose 0002 built the 3.12 schema directly, which never
had these objects, runs it as a no-op.

downgrade() leaves the schema as it is: it removes only objects the 3.12 code does not use, so the
schema at 0014 and at 0015 serve the same image. Running a pre-3.12 image again would need them restored
by hand from Procrastinate's 03.02.00_01 and 03.12.00_01 files.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence
from pathlib import Path

import procrastinate
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POST = (
    "03.12.00_50_post_add_lock_mode.sql",
    "03.12.00_51_post_drop_id_lock_index.sql",
)


def upgrade() -> None:
    bind = op.get_bind()
    mig = Path(procrastinate.__file__).parent / "sql" / "migrations"
    for name in POST:
        sql = (mig / name).read_text().replace("DROP INDEX CONCURRENTLY", "DROP INDEX")
        # The raw DBAPI connection runs the script as-is: no bind-parameter or % parsing.
        bind.connection.dbapi_connection.execute(sql)


def downgrade() -> None:
    pass
