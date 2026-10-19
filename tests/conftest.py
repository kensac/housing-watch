import os

# An example profile for the tests: one person, studio/1BR, Bronx excluded. Set before criteria is imported.
os.environ.setdefault("HW_INCOME", "150000")
os.environ.setdefault("HW_SIZES", "studio,1br")
os.environ.setdefault("HW_EXCLUDED_BOROUGHS", "Bronx")

import uuid  # noqa: E402

import pytest  # noqa: E402

TEST_DB = os.environ.get("HW_TEST_DATABASE_URL")  # e.g. postgresql+psycopg://postgres:pg@127.0.0.1:5432/postgres


@pytest.fixture(scope="session")
def pg_url():
    """A throwaway database, migrated to head; dropped after the session. Skips without HW_TEST_DATABASE_URL."""
    if not TEST_DB:
        pytest.skip("HW_TEST_DATABASE_URL not set")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    name = f"hw_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(TEST_DB, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(TEST_DB).set(database=name).render_as_string(hide_password=False)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    eng = create_engine(url)
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    eng.dispose()
    yield url
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def pg(pg_url):
    """An engine on the migrated test database, with every app table emptied first."""
    from sqlalchemy import create_engine, text

    from housing_watch.orm import Base

    eng = create_engine(pg_url)
    with eng.begin() as c:
        c.execute(
            text("TRUNCATE " + ", ".join(t.name for t in Base.metadata.sorted_tables) + " RESTART IDENTITY CASCADE")
        )
    yield eng
    eng.dispose()


@pytest.fixture
def store(pg):
    """A Session in one transaction on the emptied test database, for change-detection tests."""
    from sqlalchemy.orm import Session

    with Session(pg) as s, s.begin():
        yield s
