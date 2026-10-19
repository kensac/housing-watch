from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from housing_watch.orm import BandRow, ListingRow, UnitRow


def test_migrations_match_models(pg):
    assert {"listings", "units", "bands", "events", "runs", "snapshots", "source_state", "adapter_cache"} <= set(
        inspect(pg).get_table_names()
    )


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
