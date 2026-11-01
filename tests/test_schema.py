from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from housing_watch.orm import BandRow, Base, ListingRow, UnitRow


def test_migrations_match_models(pg):
    """The migrated schema is the ORM's: a model change without its migration fails here."""
    ctx = {"include_name": lambda name, type_, _: not (type_ == "table" and name.startswith("procrastinate"))}
    with pg.connect() as c:
        assert compare_metadata(MigrationContext.configure(c, opts=ctx), Base.metadata) == []


def test_listing_units_bands_roundtrip(pg):
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    with Session(pg) as s:
        lst = ListingRow(
            key="t:a",
            source="t",
            ext_id="a",
            status="open",
            kind="re-rental",
            fingerprint="x",
            first_seen=now,
            last_seen=now,
            last_changed=now,
        )
        lst.units = [UnitRow(pos=0, size="studio", rent=2400, ami=130, bands=[BandRow(household=1, income_max=154440)])]
        s.add(lst)
        s.commit()
        got = s.scalars(select(ListingRow)).one()
        assert got.units[0].bands[0].income_max == 154440
