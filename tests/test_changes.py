from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from housing_watch import changes, db, digest
from housing_watch.models import Band, Listing, Unit
from housing_watch.orm import EventRow


def T(n: int) -> datetime:
    return datetime.fromtimestamp(1_800_000_000 + n, UTC)


def B(status="open", units=None, **kw) -> dict:
    return {"status": status, "deadline": None, "units": units or [["1br", "8L", 2900.0, 130, None]], **kw}


def test_describe():
    assert changes.describe(B(), B("waitlist")) == ["Status: open → waitlist"]
    cheaper = B(units=[["1br", "8L", 2750.0, 130, None]])
    assert changes.describe(B(), cheaper) == ["Rent: 1BR #8L 130% AMI $2,900 → $2,750"]
    assert changes.describe(B(), B(units=[["2br", "8L", 2900.0, 130, None]])) == [
        "Relabeled: 1BR #8L 130% AMI $2,900 → 2BR #8L 130% AMI $2,900"
    ]
    assert changes.describe(B(), B(units=[["2br", "9A", 3400.0, 130, None]])) == [
        "Unit removed: 1BR #8L 130% AMI $2,900",
        "Unit added: 2BR #9A 130% AMI $3,400",
    ]
    assert changes.describe(B(), B(units=[["1br", "8L", 2900.0, 130, False]])) == [
        "1BR #8L 130% AMI: not stated → taken"
    ]
    bands = [["studio", None, 2400.0, 130, None, None, [[1, 80000, 150000]]]]
    wider = [["studio", None, 2400.0, 130, None, None, [[1, 80000, 154440]]]]
    assert changes.describe(B(units=bands), B(units=wider)) == [
        "Income limits, Studio 130% AMI: 1p $80,000-$150,000 → 1p $80,000-$154,440"
    ]
    assert changes.describe(B(deadline="2026-11-01"), B()) == ["Deadline: 2026-11-01 → none"]


def test_noise():
    assert changes.is_noise(B(), B("unknown"))  # the label disappeared
    assert changes.is_noise(B("unknown"), B(), prior="open")  # and came back
    assert not changes.is_noise(B("unknown"), B())  # first status ever shown: news
    assert not changes.is_noise(B("unknown"), B(), prior="closed")  # closed, then open: news
    assert not changes.is_noise(B(), B("waitlist"))
    assert not changes.is_noise(B(), B("unknown", units=[["1br", "8L", 2750.0, 130, None]]))  # rent moved too


def test_record_marks_flaps_and_digest_skips_them(pg):
    good = [Unit("1br", 2900, 130, unit_no="8L", bands=[Band(1, 90000, 154440)])]

    def L(status="open", units=good):
        return Listing("t", "a", "T", "u", borough="Queens", status=status, units=units)

    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L()], now=T(1))
    with Session(pg) as s, s.begin():
        s.execute(EventRow.__table__.update().values(notified=T(1)))
    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L("unknown")], now=T(2))
        db.record_listings(s, "t", [L("open")], now=T(3))
    with Session(pg) as s:
        ds = [e.detail for e in s.scalars(select(EventRow).where(EventRow.type == "changed").order_by(EventRow.id))]
    assert [d.get("noise") for d in ds] == [True, True]
    assert not digest.urgent(pg)
    subject, *_ = digest.build(pg)
    assert subject == ""  # marked read, nothing mailed

    cheaper = [Unit("1br", 2750, 130, unit_no="8L", bands=[Band(1, 90000, 154440)])]
    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L(units=cheaper)], now=T(4))
    assert digest.urgent(pg)
    subject, plain, *_ = digest.build(pg)
    assert "1 eligible" in subject and "changed: Rent: 1BR #8L 130% AMI $2,900 → $2,750" in plain
