from housing_watch import db, parse
from housing_watch.criteria import ELIGIBLE, EXCLUDED, POSSIBLE, listing_verdict, unit_verdict
from housing_watch.models import Band, Listing, Snapshot, Unit


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


def test_parse_helpers():
    assert parse.size("1 Bedroom") == "1br" and parse.size("Studio") == "studio" and parse.size("2-Bedroom") == "2br"
    assert parse.money_range("$80,511.- $154,440") == (80511, 154440)
    assert parse.household_range("1-3 people") == [1, 2, 3]
    assert parse.ami_max(130, 1) == 154440 and parse.ami_max(165, 1) == 196020 and parse.ami_max(125, 1) == 148500
    b = parse.bands_from_row("1-3 people", "$95,452 – $198,510", 130)
    assert b[0] == Band(1, 95452, 154440) and b[-1] == Band(3, 95452, 198510)
    assert parse.borough("Long Island City, NY 11101") == "Queens"
    assert parse.borough("Brooklyn") == "Brooklyn" and parse.borough("2401 Third Ave, Bronx, NY 10451") == "Bronx"


def test_change_detection():
    c = db.connect(":memory:")
    good = Unit("studio", 2400, 130, unit_no="2A", bands=[Band(1, 82557, 154440)])
    assert db.record_listings(c, "t", [L([good])], now=1) == 1
    assert db.record_listings(c, "t", [L([good])], now=2) == 0  # unchanged
    good.rent = 2500
    assert db.record_listings(c, "t", [L([good])], now=3) == 1  # changed
    assert db.record_listings(c, "t", [], now=4) == 1  # gone
    assert db.record_listings(c, "t", [L([good])], now=5) == 1  # reopened
    types = [r["type"] for r in c.execute("SELECT type FROM events ORDER BY id")]
    assert types == ["new", "changed", "gone", "reopened"]


def test_snapshot_changes():
    c = db.connect(":memory:")
    s = Snapshot("p", "url", "url", "No vacancies", [])
    assert db.record_snapshots(c, "p", [s], now=1) == 1
    assert db.record_snapshots(c, "p", [s], now=2) == 0
    s2 = Snapshot("p", "url", "url", "No vacancies\n123 Main St Unit 4B Studio $2,400", ["https://x/ad.pdf"])
    assert db.record_snapshots(c, "p", [s2], now=3) == 1
    ev = c.execute("SELECT * FROM events WHERE type='page_changed'").fetchone()
    assert "123 Main St" in ev["detail"] and "ad.pdf" in ev["detail"]


def test_digest_builds():
    from housing_watch import digest

    c = db.connect(":memory:")
    db.record_listings(c, "t", [L([Unit("studio", 2400, 130, bands=[Band(1, 82557, 154440)])], borough="Queens")])
    subject, plain, _, ids = digest.build(c)
    assert "1 eligible" in subject and "ELIGIBLE" in plain and ids


def test_fingerprint_with_missing_rent():
    c = db.connect(":memory:")
    lst = L([Unit("studio", 2000.0), Unit("studio", None)], borough="Queens")
    assert db.record_listings(c, "t", [lst]) == 1
