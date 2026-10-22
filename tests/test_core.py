from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from housing_watch import db, parse
from housing_watch.criteria import ELIGIBLE, EXCLUDED, POSSIBLE, listing_verdict, unit_verdict
from housing_watch.models import Band, Listing, Snapshot, Unit
from housing_watch.orm import EventRow


def T(n: int) -> datetime:
    return datetime.fromtimestamp(1_800_000_000 + n, UTC)


def L(units, **kw):
    return Listing(source="t", ext_id=kw.pop("ext_id", "a"), title="T", url="u", units=units, **kw)


def test_unit_verdicts():
    assert unit_verdict(Unit("studio", 2400, 130, bands=[Band(1, 82557, 154440)]))[0] == ELIGIBLE
    assert unit_verdict(Unit("1br", 2200, 125, bands=[Band(1, 82115, 147420)]))[0] == EXCLUDED  # over income
    assert unit_verdict(Unit("1br", 3982, 130, bands=[Band(1, 146126, 154440)]))[0] == ELIGIBLE
    assert unit_verdict(Unit("2br", 4300, 130, bands=[Band(2, 1, 2)]))[0] == EXCLUDED
    assert unit_verdict(Unit("1br", 3000, 130, bands=[Band(1, 160000, 196020)]))[0] == EXCLUDED  # under min
    assert unit_verdict(Unit("studio", 1500, 60))[0] == EXCLUDED  # AMI alone rules it out
    assert unit_verdict(Unit("studio", 2600, 130))[0] == POSSIBLE  # no table: keep
    assert unit_verdict(Unit("unknown"))[0] == POSSIBLE
    assert unit_verdict(Unit("1br", 3478, 165, set_aside="mobility", bands=[Band(1, 1, 196020)]))[0] == EXCLUDED


def test_listing_verdicts():
    good = Unit("studio", 2400, 130, bands=[Band(1, 82557, 154440)])
    assert listing_verdict(L([good], borough="Queens")).verdict == ELIGIBLE
    assert listing_verdict(L([good])).verdict == POSSIBLE  # borough unknown: could be the Bronx
    assert listing_verdict(L([good], borough="Bronx")).verdict == EXCLUDED
    assert listing_verdict(L([good], status="closed")).verdict == EXCLUDED
    assert listing_verdict(L([good], senior_only=True)).verdict == EXCLUDED
    assert listing_verdict(L([])).verdict == POSSIBLE
    # not applicable yet (upcoming) or now (no status): excluded unless plainly eligible
    unsized = Unit("unknown", 2400, 130, bands=[Band(1, 80000, 154440)])
    assert listing_verdict(L([unsized], borough="Queens", status="upcoming")).verdict == EXCLUDED
    assert listing_verdict(L([], borough="Queens", status="upcoming")).verdict == EXCLUDED
    assert listing_verdict(L([good], borough="Queens", status="upcoming")).verdict == ELIGIBLE
    assert listing_verdict(L([good], borough="Queens", status="unknown")).verdict == EXCLUDED
    assert listing_verdict(L([unsized], borough="Queens", status="waitlist")).verdict == POSSIBLE


def test_parse_helpers():
    assert parse.size("1 Bedroom") == "1br" and parse.size("Studio") == "studio" and parse.size("2-Bedroom") == "2br"
    assert parse.money_range("$80,511.- $154,440") == (80511, 154440)
    assert parse.household_range("1-3 people") == [1, 2, 3]
    b = parse.bands_from_row("1-3 people", "$95,452 – $198,510", 130)
    assert b[0] == Band(1, 95452, 154440) and b[-1] == Band(3, 95452, 198510)
    assert parse.borough("Long Island City, NY 11101") == "Queens"
    assert parse.borough("Brooklyn") == "Brooklyn" and parse.borough("2401 Third Ave, Bronx, NY 10451") == "Bronx"


def test_change_detection(pg):
    good = Unit("studio", 2400, 130, unit_no="2A", bands=[Band(1, 82557, 154440)])
    with Session(pg) as s, s.begin():
        assert db.record_listings(s, "t", [L([good])], now=T(1)) == 1
        assert db.record_listings(s, "t", [L([good])], now=T(2)) == 0  # unchanged
        good.rent = 2500
        assert db.record_listings(s, "t", [L([good])], now=T(3)) == 1  # changed
        assert db.record_listings(s, "t", [], now=T(4)) == 1  # gone
        assert db.record_listings(s, "t", [L([good])], now=T(5)) == 1  # reopened
        types = s.scalars(select(EventRow.type).order_by(EventRow.id)).all()
    assert types == ["new", "changed", "gone", "reopened"]


def test_snapshot_changes(pg):
    sn = Snapshot("p", "url", "url", "No vacancies", [])
    with Session(pg) as s, s.begin():
        assert db.record_snapshots(s, "p", [sn], now=T(1)) == 1
        assert db.record_snapshots(s, "p", [sn], now=T(2)) == 0
        s2 = Snapshot("p", "url", "url", "No vacancies\n123 Main St Unit 4B Studio $2,400", ["https://x/ad.pdf"])
        assert db.record_snapshots(s, "p", [s2], now=T(3)) == 1
        ev = s.scalars(select(EventRow).where(EventRow.type == "page_changed")).one()
        assert ev.detail["added"] == ["123 Main St Unit 4B Studio $2,400"] and ev.detail["new_links"] == [
            "https://x/ad.pdf"
        ]


def test_digest_builds(pg):
    from housing_watch import digest

    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L([Unit("studio", 2400, 130, bands=[Band(1, 82557, 154440)])], borough="Queens")])
    subject, plain, _, ids = digest.build(pg)
    assert "1 eligible" in subject and "ELIGIBLE" in plain and ids
    assert digest.urgent(pg)
    assert "ELIGIBLE" in digest.send(pg, dry_run=True)
    assert digest.urgent(pg)  # a dry run marks nothing


def test_fingerprint_with_missing_rent(pg):
    lst = L([Unit("studio", 2000.0), Unit("studio", None)], borough="Queens")
    with Session(pg) as s, s.begin():
        assert db.record_listings(s, "t", [lst]) == 1


def test_every_source_runs_at_least_hourly():
    from housing_watch.adapters import load_all

    slow = {name: cls.every_hours for name, cls in load_all().items() if cls.every_hours > 1}
    assert not slow, slow


def test_money_repairs():
    assert parse.money("$ 1,5 47.67") == 1547.67  # a thousands group split by a space
    assert parse.money("$2.468") == 2468 and parse.money("$2.46") == 2.46  # dot as thousands separator
    assert parse.money("$1,500 2BR") == 1500 and parse.money("$12,345.50") == 12345.5
