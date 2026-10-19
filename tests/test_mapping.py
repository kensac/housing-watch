from datetime import UTC, datetime

from sqlalchemy.orm import Session

from housing_watch.mapping import apply_listing, listing_from_row
from housing_watch.models import Band, Listing, Unit
from housing_watch.orm import ListingRow


def test_roundtrip(pg):
    lst = Listing(
        source="t", ext_id="a", title="T", url="u", borough="Queens", notes="n",
        units=[Unit("studio", 2400.0, 130, "4B", [Band(1, 82557, 154440)]), Unit("1br")],
    )  # fmt: skip
    now = datetime.now(UTC)
    with Session(pg) as s:
        s.add(apply_listing(lst, ListingRow(fingerprint="x", first_seen=now, last_seen=now, last_changed=now)))
        s.commit()
        assert listing_from_row(s.get(ListingRow, "t:a")) == lst
