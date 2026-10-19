"""Procrastinate's schema, so Alembic owns all DDL.

The SQL comes from the installed Procrastinate (SchemaManager.get_schema()), which is its schema at that
version. A Procrastinate upgrade that ships files in procrastinate/sql/migrations/ needs a new revision
that runs them; tests/test_fleet.py fails until one exists (PROCRASTINATE_SCHEMA_AT).

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The raw DBAPI connection runs the script as-is: no bind-parameter or % parsing.
    op.get_bind().connection.dbapi_connection.execute(SchemaManager.get_schema())


def downgrade() -> None:
    op.execute(
        """
        DO $$ DECLARE f regprocedure; BEGIN
          FOR f IN SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = current_schema() AND p.proname LIKE 'procrastinate%' LOOP
            EXECUTE 'DROP FUNCTION ' || f || ' CASCADE';
          END LOOP;
        END $$
        """
    )
    op.execute(
        "DROP TABLE procrastinate_events, procrastinate_periodic_defers, procrastinate_jobs, procrastinate_workers"
    )
    op.execute("DROP TYPE procrastinate_job_to_defer_v1, procrastinate_job_event_type, procrastinate_job_status")
