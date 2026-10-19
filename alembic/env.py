"""Alembic env: schema from orm.Base, URL from HW_DATABASE_URL. Procrastinate's tables are excluded."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from housing_watch.orm import Base

config = context.config


def include_name(name, type_, parent_names):
    return not (type_ == "table" and name.startswith("procrastinate"))


def run_migrations_online() -> None:
    conn = config.attributes.get("connection")  # tests pass an open connection
    if conn is None:
        engine = create_engine(os.environ["HW_DATABASE_URL"], poolclass=pool.NullPool)
        with engine.connect() as conn:
            _run(conn)
    else:
        _run(conn)


def _run(conn) -> None:
    context.configure(connection=conn, target_metadata=Base.metadata, include_name=include_name)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
