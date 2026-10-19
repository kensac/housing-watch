from datetime import UTC, datetime

import pytest
from sqlalchemy import Boolean, Column, MetaData, Table
from sqlalchemy.dialects import postgresql

from housing_watch import filters as F
from housing_watch.orm import ListingRow

FS = F.LISTING_FIELDS


def test_fields_come_from_the_model():
    assert FS["borough"].type == "text" and FS["units.rent"].type == "number" and FS["units.rent"].money
    assert FS["first_seen"].type == "time" and FS["senior_only"].type == "bool" and FS["units.chart_year"]
    assert FS["verdict"].scope == "computed" and FS["bands.household"].group == "Income band"
    assert not {"key", "fingerprint", "units.id", "bands.unit_id"} & set(FS)  # internal columns stay hidden
    assert F.EVENT_FIELDS["listing.borough"].scope == "listing" and F.EVENT_FIELDS["first_run"].type == "bool"


def test_a_new_column_is_filterable():
    md = MetaData()
    listings = Table("listings", md, *[c._copy() for c in ListingRow.__table__.columns], Column("pets_ok", Boolean))
    fs = F.listing_fields(listings)
    assert fs["pets_ok"].type == "bool" and fs["pets_ok"].label == "Pets ok"
    assert F.parse(["pets_ok.is.yes"], fs)[0].values == (True,)


@pytest.mark.parametrize(
    "raw",
    [
        "borough.in.Brooklyn,Queens",
        "borough.nin.Bronx",
        "units.rent.le.3000",
        "units.rent.between.1000,2500.5",
        "last_changed.after.2026-10-01",
        "last_changed.between.2026-10-01,2026-10-07",
        "first_seen.last.7d",
        "senior_only.is.no",
        "notes.contains.no fee\\, ever",
        "address.empty",
        "verdict.in.eligible,possible",
        "units.size.notempty",
    ],
)
def test_url_round_trip(raw):
    c = F.parse_one(raw, FS)
    assert c.url() == raw and F.parse_one(c.url(), FS).raw == c.raw
    assert F.from_json([c.to_json()], FS)[0][0].url() == raw


def test_values_are_typed_and_canonical():
    c = F.parse_one("units.rent.le.3000.0", FS)
    assert c.values == (3000.0,) and c.url() == "units.rent.le.3000"
    assert F.parse_one("notes.contains.a\\,b", FS).values == ("a,b",)
    assert F.parse_one("last_changed.after.2026-10-01", FS).values[0].tzinfo is not None


@pytest.mark.parametrize(
    "raw, msg",
    [
        ("nope.in.x", "Unknown filter field"),
        ("borough.le.3", "can't be filtered with “le”"),
        ("units.rent.le.cheap", "is not a number"),
        ("units.rent.between.1", "expected 2"),
        ("last_changed.after.yesterday", "is not a date"),
        ("first_seen.last.week", "should look like"),
        ("senior_only.is.maybe", "use yes or no"),
        ("verdict.in.great", "is not one of"),
        ("borough.in", "expected one or more"),
    ],
)
def test_friendly_errors(raw, msg):
    with pytest.raises(F.FilterError, match=msg):
        F.parse_one(raw, FS)


def test_labels():
    lbl = lambda s: F.parse_one(s, FS).label()  # noqa: E731
    assert lbl("borough.in.Brooklyn,Queens") == "Borough is Brooklyn, Queens"
    assert lbl("units.rent.le.3000") == "Rent ≤ $3,000"
    assert lbl("units.rent.between.1000,2500") == "Rent between $1,000 and $2,500"
    assert lbl("last_changed.after.2026-10-01") == "Last changed after Oct 1, 2026"
    assert lbl("first_seen.last.7d") == "First seen in the last 7 days"
    assert lbl("senior_only.is.no") == "Senior only: no" and lbl("notes.empty") == "Notes is empty"


def sql(raw: str) -> str:
    c = F.parse_one(raw, FS)
    clause = F.sql(c) if c.field.scope == "listing" else F.listing_where([c])[0]
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_sql_per_operator():
    assert sql("borough.in.Brooklyn,Queens") == "listings.borough IN ('Brooklyn', 'Queens')"
    assert sql("borough.nin.Bronx") == "(listings.borough NOT IN ('Bronx')) OR listings.borough IS NULL"
    assert "ILIKE" in sql("notes.contains.50%") and "50\\" in sql("notes.contains.50%")  # the % is escaped
    assert sql("notes.ncontains.x") == "listings.notes NOT ILIKE '%%x%%' OR listings.notes IS NULL"
    assert sql("address.empty") == "listings.address IS NULL OR listings.address = ''"
    assert sql("senior_only.is.yes") == "listings.senior_only IS true"
    assert "listings.first_seen >= '2026-10-01" in sql("first_seen.after.2026-09-30")
    assert "listings.first_seen < '2026-10-02" in sql("first_seen.between.2026-10-01,2026-10-01")
    assert sql("gone_since.notempty") == "listings.gone_since IS NOT NULL"
    u = sql("units.rent.between.1000,2000")
    assert u.startswith("EXISTS (SELECT 1") and "units.rent BETWEEN 1000.0 AND 2000.0" in u
    assert "units.rent != 5.0 OR units.rent IS NULL" in sql("units.rent.ne.5")
    for op, sym in [("eq", "="), ("lt", "<"), ("le", "<="), ("gt", ">"), ("ge", ">=")]:
        assert f"units.ami {sym} 130" in sql(f"units.ami.{op}.130")


def test_unit_and_band_conditions_share_one_exists():
    w = F.listing_where(F.parse(["units.size.in.studio", "units.rent.le.2500", "bands.household.eq.1"], FS))
    (clause,) = w
    s = str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert s.count("EXISTS") == 2 and "units.size IN ('studio') AND units.rent <= 2500.0" in s
    assert "bands.unit_id = units.id AND bands.household = 1.0" in s


def test_match_mirrors_sql():
    c = lambda s: F.parse_one(s, FS)  # noqa: E731
    assert F.match(c("verdict.in.eligible"), "eligible") and not F.match(c("verdict.nin.eligible"), "eligible")
    assert F.match(c("eligible_units.between.1,3"), 2) and not F.match(c("eligible_units.gt.2"), 2)
    assert F.match(c("reason.contains.BRONX"), "in Bronx") and F.match(c("reason.empty"), "")
    assert F.match(c("unit_count.ne.1"), None) and not F.match(c("unit_count.eq.1"), None)


def test_saved_json_degrades():
    conds, warnings = F.from_json(
        [{"field": "borough", "op": "in", "values": ["Queens"]}, {"field": "pets_ok", "op": "is", "values": ["yes"]}],
        FS,
    )
    assert [c.url() for c in conds] == ["borough.in.Queens"]
    assert warnings and "pets_ok" in warnings[0] and "dropped" in warnings[0]


def test_last_is_relative_to_now():
    lo, hi = F._bounds(F.parse_one("first_seen.last.2h", FS))
    assert hi is None and abs((datetime.now(UTC) - lo).total_seconds() - 7200) < 5
