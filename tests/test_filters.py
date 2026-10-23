from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Boolean, Column, MetaData, Table
from sqlalchemy.dialects import postgresql

from housing_watch import filters as F
from housing_watch.orm import ListingRow

FS = F.LISTING_FIELDS


def one(tree: dict, fields=FS) -> F.Cond:
    (c,) = F.from_tree(tree, fields)
    return c


def test_fields_come_from_the_model():
    assert FS["borough"].type == "enum" and FS["borough"].choices[-1] == "Staten Island"
    assert FS["units.rent"].type == "number" and FS["units.rent"].money and FS["units.size"].type == "enum"
    assert FS["first_seen"].type == "time" and FS["senior_only"].type == "bool" and FS["units.chart_year"]
    assert FS["notes"].type == "text" and FS["units.set_aside"].type == "text"  # open text stays text
    assert FS["verdict"].scope == "computed" and FS["verdict"].choices == ("eligible", "possible", "excluded")
    band = FS["units.bands.household"]
    assert band.group == "Income band" and band.path == ("units", "bands", "household")
    assert not {"key", "fingerprint", "units.id", "units.bands.unit_id"} & set(FS)  # internal columns stay hidden
    assert F.EVENT_FIELDS["listing.borough"].scope == "listing" and F.EVENT_FIELDS["first_run"].type == "bool"
    assert F.EVENT_FIELDS["type"].type == "enum" and F.EVENT_FIELDS["listing.units.bands.household"]


def test_a_new_column_is_filterable():
    md = MetaData()
    listings = Table("listings", md, *[c._copy() for c in ListingRow.__table__.columns], Column("pets_ok", Boolean))
    fs = F.listing_fields(listings)
    assert fs["pets_ok"].type == "bool" and fs["pets_ok"].label == "Pets ok"
    assert one({"pets_ok": {"is": True}}, fs).value is True


TREES = [
    {"borough": {"in": ["Brooklyn", "Queens"]}},
    {"borough": {"not_in": ["Bronx"]}},
    {"units": {"rent": {"le": 3000.0}}},
    {"units": {"rent": {"between": {"min": 1000.0, "max": 2500.5}}}},
    {"last_changed": {"after": "2026-10-01"}},
    {"last_changed": {"between": {"from": "2026-10-01", "to": "2026-10-07"}}},
    {"last_changed": {"since": "2026-10-01T09:30:00-04:00"}},
    {"first_seen": {"last": {"amount": 7, "unit": "days"}}},
    {"senior_only": {"is": False}},
    {"notes": {"contains": "no fee, ever"}},
    {"address": {"empty": True}},
    {"verdict": {"in": ["eligible", "possible"]}},
    {"units": {"size": {"empty": False}, "bands": {"household": {"eq": 1.0}}}},
]


@pytest.mark.parametrize("tree", TREES)
def test_tree_round_trip(tree):
    assert F.to_tree(F.from_tree(tree, FS)) == tree


def test_several_operators_on_one_field_all_hold():
    conds = F.from_tree({"units": {"rent": {"ge": 1000, "le": 2500}}, "verdict": {"in": ["eligible"]}}, FS)
    assert [(c.field.name, c.op, c.value) for c in conds] == [
        ("units.rent", "ge", 1000.0), ("units.rent", "le", 2500.0), ("verdict", "in", ("eligible",))]  # fmt: skip


def test_values_are_typed():
    assert one({"units": {"rent": {"le": 3000}}}).value == 3000.0
    assert one({"last_changed": {"after": "2026-10-01"}}).value == date(2026, 10, 1)
    assert one({"last_changed": {"since": "2026-10-01T09:30"}}).value.tzinfo is not None  # naive is New York
    assert one({"units": {"rent": {"between": {"min": 9, "max": 1}}}}).value == F.Range(1, 9)  # either order
    assert one({"first_seen": {"last": {"amount": 2, "unit": "hours"}}}).value == F.Duration(2, "hours")
    assert F.from_tree({"borough": None, "units": {"rent": {"le": None}}}, FS) == []  # null means absent


@pytest.mark.parametrize(
    "tree, msg",
    [
        ({"nope": {"in": ["x"]}}, "Unknown filter field “nope”"),
        ({"borough": {"le": 3}}, "can't be filtered with “le”"),
        ({"units": {"rent": {"le": "cheap"}}}, "is not a number"),
        ({"units": {"rent": {"le": True}}}, "is not a number"),
        ({"units": {"rent": {"between": {"min": 1}}}}, "give min and max"),
        ({"last_changed": {"after": "yesterday"}}, "is not a date"),
        ({"first_seen": {"last": {"amount": 0, "unit": "days"}}}, "a whole number"),
        ({"first_seen": {"last": {"amount": 1, "unit": "weeks"}}}, "a whole number"),
        ({"senior_only": {"is": "maybe"}}, "use true or false"),
        ({"verdict": {"in": ["great"]}}, "is not one of"),
        ({"borough": {"in": ["Brooklyn", "Jersey City"]}}, "is not one of"),
        ({"borough": {"in": []}}, "expected one or more"),
        ({"notes": {"contains": "  "}}, "expected some text"),
        ({"units": "cheap"}, "Expected an object of fields"),
    ],
)
def test_friendly_errors(tree, msg):
    with pytest.raises(F.FilterError, match=msg):
        F.from_tree(tree, FS)


def test_every_problem_is_reported():
    with pytest.raises(F.FilterError) as e:
        F.from_tree({"nope": {"in": ["x"]}, "borough": {"le": 3}}, FS)
    assert "nope" in str(e.value) and "le" in str(e.value)


def test_labels():
    lbl = lambda t: one(t).label()  # noqa: E731
    assert lbl({"borough": {"in": ["Brooklyn", "Queens"]}}) == "Borough is Brooklyn, Queens"
    assert lbl({"units": {"rent": {"le": 3000}}}) == "Rent ≤ $3,000"
    assert lbl({"units": {"rent": {"between": {"min": 1000, "max": 2500}}}}) == "Rent between $1,000 and $2,500"
    assert lbl({"last_changed": {"after": "2026-10-01"}}) == "Last changed after Oct 1, 2026"
    assert lbl({"last_changed": {"since": "2026-10-01T09:30"}}) == "Last changed since Oct 1, 2026, 9:30 AM"
    assert lbl({"first_seen": {"last": {"amount": 7, "unit": "days"}}}) == "First seen in the last 7 days"
    assert lbl({"first_seen": {"last": {"amount": 1, "unit": "hours"}}}) == "First seen in the last 1 hour"
    assert lbl({"senior_only": {"is": False}}) == "Senior only: no"
    assert (
        lbl({"notes": {"empty": True}}) == "Notes is empty" and lbl({"notes": {"empty": False}}) == "Notes is not empty"
    )


def sql(tree: dict) -> str:
    c = one(tree)
    clause = F.sql(c) if c.field.scope == "listing" else F.listing_where([c])[0]
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_sql_per_operator():
    assert sql({"borough": {"in": ["Brooklyn", "Queens"]}}) == "listings.borough IN ('Brooklyn', 'Queens')"
    assert sql({"borough": {"not_in": ["Bronx"]}}) == "(listings.borough NOT IN ('Bronx')) OR listings.borough IS NULL"
    assert "ILIKE" in sql({"notes": {"contains": "50%"}}) and "50\\" in sql({"notes": {"contains": "50%"}})
    assert sql({"notes": {"not_contains": "x"}}) == "listings.notes NOT ILIKE '%%x%%' OR listings.notes IS NULL"
    assert sql({"address": {"empty": True}}) == "listings.address IS NULL OR listings.address = ''"
    assert sql({"senior_only": {"is": True}}) == "listings.senior_only IS true"
    assert "listings.first_seen >= '2026-10-01" in sql({"first_seen": {"after": "2026-09-30"}})
    day = {"from": "2026-10-01", "to": "2026-10-01"}
    assert "listings.first_seen < '2026-10-02" in sql({"first_seen": {"between": day}})
    assert "listings.first_seen >= '2026-10-01 09:30" in sql({"first_seen": {"since": "2026-10-01T09:30"}})
    assert "listings.first_seen < '2026-10-01 09:30" in sql({"first_seen": {"until": "2026-10-01T09:30"}})
    assert sql({"gone_since": {"empty": False}}) == "listings.gone_since IS NOT NULL"
    u = sql({"units": {"rent": {"between": {"min": 1000, "max": 2000}}}})
    assert u.startswith("EXISTS (SELECT 1") and "units.rent BETWEEN 1000.0 AND 2000.0" in u
    assert "units.rent != 5.0 OR units.rent IS NULL" in sql({"units": {"rent": {"ne": 5}}})
    for op, sym in [("eq", "="), ("lt", "<"), ("le", "<="), ("gt", ">"), ("ge", ">=")]:
        assert f"units.ami {sym} 130" in sql({"units": {"ami": {op: 130}}})


def test_unit_and_band_conditions_share_one_exists():
    tree = {"units": {"size": {"in": ["studio"]}, "rent": {"le": 2500}, "bands": {"household": {"eq": 1}}}}
    (clause,) = F.listing_where(F.from_tree(tree, FS))
    s = str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert s.count("EXISTS") == 2 and "units.size IN ('studio') AND units.rent <= 2500.0" in s
    assert "bands.unit_id = units.id AND bands.household = 1.0" in s


def test_match_mirrors_sql():
    m = lambda t, v: F.match(one(t), v)  # noqa: E731
    assert m({"verdict": {"in": ["eligible"]}}, "eligible") and not m({"verdict": {"not_in": ["eligible"]}}, "eligible")
    assert m({"eligible_units": {"between": {"min": 1, "max": 3}}}, 2) and not m({"eligible_units": {"gt": 2}}, 2)
    assert m({"reason": {"contains": "BRONX"}}, "in Bronx") and m({"reason": {"empty": True}}, "")
    assert m({"unit_count": {"ne": 1}}, None) and not m({"unit_count": {"eq": 1}}, None)
    assert m({"application": {"empty": False}}, "Applied") and not m({"application": {"empty": False}}, None)


def test_saved_trees_degrade():
    conds, warnings = F.from_saved({"borough": {"in": ["Queens"]}, "pets_ok": {"is": True}}, FS)
    assert F.to_tree(conds) == {"borough": {"in": ["Queens"]}}
    assert warnings and "pets_ok" in warnings[0] and "dropped" in warnings[0]


def test_last_is_relative_to_now():
    lo, hi = F._bounds(one({"first_seen": {"last": {"amount": 2, "unit": "hours"}}}))
    assert hi is None and abs((datetime.now(UTC) - lo).total_seconds() - 7200) < 5
