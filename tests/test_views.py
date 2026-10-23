import importlib.util
import os
from datetime import date

import pytest
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from housing_watch import filters as F
from housing_watch import queries as Q
from housing_watch import views as V
from housing_watch.orm import SavedViewRow
from seed import seed


def state(tree=None, page="listings", **kw):
    return V.State(page, F.from_tree(tree, V.FIELDS[page]), **kw)


def test_nothing_is_seeded(pg):
    assert V.list_views(pg) == [] and V.default(pg, "listings") is None


def test_crud(pg):
    queens = {"borough": {"in": ["Queens"]}}
    a = V.create(pg, state(queens, sort=(Q.Sort("rent", "desc"),), columns=("verdict", "rent")), "Queens")
    assert a.state.filter == queens and a.state.sort == (Q.Sort("rent", "desc"),) and a.state.columns == (
        "verdict", "rent")  # fmt: skip
    b = V.create(pg, state({"type": {"in": ["new"]}}, page="events"), "New events")
    c = V.create(pg, state(q="loft"), "Lofts")
    assert c.state.sort == Q.DEFAULT_SORT and c.state.columns == V.DEFAULT_COLUMNS["listings"]  # defaults fill in
    assert [v.name for v in V.list_views(pg, "listings")] == ["Queens", "Lofts"]
    V.change(pg, c.id, move=-1)
    assert [v.name for v in V.list_views(pg, "listings")] == ["Lofts", "Queens"]
    cheap = queens | {"units": {"rent": {"le": 2500.0}}}
    V.change(pg, a.id, name="  Queens   only ", state=state(cheap))
    got = V.get(pg, a.id)
    assert got.name == "Queens only" and got.state.filter == cheap
    d = V.duplicate(pg, a.id)
    assert d.name == "Queens only (copy)" and d.state.to_json() == got.state.to_json() and d.id != a.id
    assert V.delete(pg, d.id) and not V.delete(pg, d.id) and V.get(pg, d.id) is None
    assert V.get(pg, b.id).page == "events" and V.get(pg, b.id).state.sort == ()
    with pytest.raises(V.ViewError):
        V.create(pg, state(), "   ")
    with pytest.raises(V.ViewError):
        V.change(pg, a.id, state=state(page="events"))


def test_state_refuses_what_its_page_lacks():
    with pytest.raises(V.ViewError, match="can't be sorted by ami"):
        state(sort=(Q.Sort("ami"),))  # a Units sort
    with pytest.raises(V.ViewError, match="no column income"):
        state(columns=("income",))


def test_stored_json_is_typed():
    st = state({"last_changed": {"last": {"amount": 7, "unit": "days"}}, "units": {"size": {"in": ["1br"]}}},
               sort=(Q.Sort("changed", "desc"), Q.Sort("title")))  # fmt: skip
    assert st.to_json() == {
        "version": 2, "q": "", "columns": list(V.DEFAULT_COLUMNS["listings"]),
        "filter": {"last_changed": {"last": {"amount": 7, "unit": "days"}}, "units": {"size": {"in": ["1br"]}}},
        "sort": [{"field": "changed", "direction": "desc"}, {"field": "title", "direction": "asc"}],
    }  # fmt: skip
    back, warnings = V.state_from_json("listings", st.to_json())
    assert back.to_json() == st.to_json() and warnings == []


def test_one_default_per_page(pg):
    a = V.create(pg, state(), "A", is_default=True)
    b = V.create(pg, state(), "B")
    e = V.create(pg, state(page="events"), "E", is_default=True)
    assert V.default(pg, "listings").id == a.id
    V.change(pg, b.id, is_default=True)
    assert V.default(pg, "listings").id == b.id and not V.get(pg, a.id).is_default
    assert V.default(pg, "events").id == e.id  # other pages keep theirs
    V.change(pg, b.id, is_default=False)
    assert V.default(pg, "listings") is None


def test_stale_parts_degrade_with_a_warning(pg):
    v = V.create(pg, state({"borough": {"in": ["Queens"]}}), "Old")
    stale = {"version": 2, "filter": {"borough": {"in": ["Queens"]}, "pets_ok": {"is": True},
                                      "units": {"rent": {"near": 1}}},
             "sort": [{"field": "rent", "direction": "desc"}, {"field": "floor", "direction": "asc"}],
             "columns": ["rent", "floor"]}  # fmt: skip
    with Session(pg) as s, s.begin():
        s.execute(update(SavedViewRow).where(SavedViewRow.id == v.id).values(query=stale))
    got = V.get(pg, v.id)
    assert got.state.filter == {"borough": {"in": ["Queens"]}} and got.state.sort == (Q.Sort("rent", "desc"),)
    assert got.state.columns == ("rent",)
    w = " | ".join(got.warnings)  # jsonb keeps its own key order
    assert len(got.warnings) == 4 and "pets_ok" in w and "near" in w and w.count("floor") == 2


def test_counts(pg):
    seed(pg)
    V.create(pg, state({"verdict": {"in": ["eligible"]}}), "Eligible")
    V.create(pg, state({"type": {"in": ["new"]}}, page="events"), "New")
    assert {v.name: v.count for v in V.with_counts(pg)} == {"Eligible": 1, "New": 3}


# --- alembic 0010: version 1 (DSL strings) to version 2 -----------------------------------------------------

_spec = importlib.util.spec_from_file_location(
    "m0010", os.path.join(os.path.dirname(__file__), "..", "alembic", "versions", "0010_typed_saved_views.py")
)
M0010 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M0010)


def v1(*filters, sort="", columns=(), q=""):
    fs = [{"field": f, "op": o, "values": list(vals)} for f, o, *vals in filters]
    return {"filters": fs, "q": q, "sort": sort, "columns": list(columns)}


@pytest.mark.parametrize(
    "page, old, tree",
    [
        ("listings", v1(("borough", "in", "Brooklyn", "Queens")), {"borough": {"in": ["Brooklyn", "Queens"]}}),
        ("listings", v1(("borough", "nin", "Bronx")), {"borough": {"not_in": ["Bronx"]}}),
        ("listings", v1(("notes", "ncontains", "fee")), {"notes": {"not_contains": "fee"}}),
        ("listings", v1(("address", "empty"), ("notes", "notempty")),
         {"address": {"empty": True}, "notes": {"empty": False}}),
        ("listings", v1(("units.rent", "le", "3000")), {"units": {"rent": {"le": 3000.0}}}),
        ("listings", v1(("units.rent", "between", "2500", "1000")),
         {"units": {"rent": {"between": {"min": 1000.0, "max": 2500.0}}}}),
        ("listings", v1(("bands.household", "eq", "1")), {"units": {"bands": {"household": {"eq": 1.0}}}}),
        ("listings", v1(("senior_only", "is", "no")), {"senior_only": {"is": False}}),
        ("listings", v1(("first_seen", "last", "7d")), {"first_seen": {"last": {"amount": 7, "unit": "days"}}}),
        ("listings", v1(("last_changed", "after", "2026-10-01")), {"last_changed": {"after": "2026-10-01"}}),
        ("listings", v1(("last_changed", "between", "2026-10-07", "2026-10-01")),
         {"last_changed": {"between": {"from": "2026-10-01", "to": "2026-10-07"}}}),
        ("listings", v1(("last_changed", "after", "2026-10-01T09:30")),
         {"last_changed": {"since": "2026-10-01T09:30:00-04:00"}}),
        ("listings", v1(("last_changed", "between", "2026-10-01T09:30", "2026-10-02")),
         {"last_changed": {"since": "2026-10-01T09:30:00-04:00", "until": "2026-10-03T00:00:00-04:00"}}),
        # two filters on one field and operator: the one condition they mean together
        ("listings", v1(("borough", "in", "Bronx", "Queens"), ("borough", "in", "Queens", "Brooklyn")),
         {"borough": {"in": ["Queens"]}}),
        ("listings", v1(("units.rent", "le", "3000"), ("units.rent", "le", "2500"), ("units.rent", "ge", "900")),
         {"units": {"rent": {"le": 2500.0, "ge": 900.0}}}),
        ("listings", v1(("borough", "nin", "Bronx"), ("borough", "nin", "Queens")),
         {"borough": {"not_in": ["Bronx", "Queens"]}}),
        # the events page: v1 prefixed only the listing's own columns
        ("events", v1(("type", "in", "new"), ("listing.borough", "in", "Queens"), ("verdict", "in", "eligible"),
                      ("units.size", "in", "studio"), ("bands.household", "eq", "1")),
         {"type": {"in": ["new"]}, "listing": {"borough": {"in": ["Queens"]}, "verdict": {"in": ["eligible"]},
                                               "units": {"size": {"in": ["studio"]},
                                                         "bands": {"household": {"eq": 1.0}}}}}),
        # what v1 itself could not read is carried over, so loading warns as before
        ("listings", v1(("pets_ok", "is", "yes"), ("units.rent", "near", "1"), ("units.rent", "le", "cheap")),
         {"pets_ok": {"is": True}, "units": {"rent": {"near": ["1"], "le": ["cheap"]}}}),
    ],
)  # fmt: skip
def test_migration_keeps_every_filter(page, old, tree):
    new = M0010.upgrade_query(old, page)
    assert new["version"] == 2 and new["filter"] == tree
    assert M0010.upgrade_query(new, page) == new  # idempotent
    conds, warnings = F.from_saved(new["filter"], V.FIELDS[page])
    assert len(conds) + len(warnings) >= 1


def test_migration_means_the_same_as_v1():
    old = v1(("units.rent", "le", "3000"), ("units.size", "in", "studio", "1br"), ("verdict", "in", "eligible"),
             ("last_changed", "last", "24h"), sort="-rent", columns=("verdict", "rent"), q="loft")  # fmt: skip
    st, warnings = V.state_from_json("listings", M0010.upgrade_query(old))
    assert warnings == [] and st.q == "loft" and st.columns == ("verdict", "rent")
    assert st.sort == (Q.Sort("rent", "desc"),)
    labels = sorted(c.label() for c in st.filters)
    assert labels == ["Last changed in the last 24 hours", "Rent ≤ $3,000", "Unit size is studio, 1br",
                      "Verdict is eligible"]  # fmt: skip
    assert M0010.downgrade_query(M0010.upgrade_query(old)) == old  # and back


@pytest.mark.parametrize(
    "sort, page, want",
    [("", "listings", []), ("rent", "listings", [{"field": "rent", "direction": "asc"}]),
     ("-rent", "units", [{"field": "rent", "direction": "desc"}]),
     ("changed", "listings", [{"field": "changed", "direction": "desc"}]),  # v1: newest first
     ("-changed", "units", [{"field": "changed", "direction": "asc"}]), ("", "events", [])],
)  # fmt: skip
def test_migration_sorts(sort, page, want):
    assert M0010.upgrade_query(v1(sort=sort), page)["sort"] == want


@pytest.mark.parametrize(
    "old",
    [v1(("notes", "contains", "a"), ("notes", "contains", "b")),
     v1(("units.rent", "eq", "1"), ("units.rent", "eq", "2")),
     v1(("borough", "in", "Bronx"), ("borough", "in", "Queens"))],
)  # fmt: skip
def test_migration_refuses_what_it_cannot_keep(old):
    with pytest.raises(M0010.Unrepresentable):
        M0010.upgrade_query(old)


def test_migration_runs_on_the_table(pg_url):
    """Down to 0009, store version-1 rows, up to head: every row is version 2 and loads without warnings."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    eng = create_engine(pg_url)

    def migrate(fn, rev):
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            fn(cfg, rev)

    with eng.begin() as c:
        c.execute(text("TRUNCATE saved_views RESTART IDENTITY"))
    migrate(command.downgrade, "0009")
    rows = [("listings", "Cheap", v1(("units.rent", "le", "2500"), ("bands.household", "eq", "1"), sort="-rent")),
            ("units", "Studios", v1(("units.size", "in", "studio"), columns=("verdict", "rent"))),
            ("events", "New eligible", v1(("type", "in", "new"), ("verdict", "in", "eligible")))]  # fmt: skip
    import json

    with eng.begin() as c:
        for page, name, q in rows:
            c.execute(text("INSERT INTO saved_views(page, name, query, position, is_default) VALUES "
                           "(:p, :n, CAST(:q AS jsonb), 0, false)"),
                      {"p": page, "n": name, "q": json.dumps(q)})  # fmt: skip
    try:
        migrate(command.upgrade, "head")
        got = {v.name: v for v in V.list_views(eng)}
        assert all(v.warnings == [] for v in got.values()) and len(got) == 3
        assert got["Cheap"].state.filter == {"units": {"rent": {"le": 2500.0}, "bands": {"household": {"eq": 1.0}}}}
        assert got["Cheap"].state.sort == (Q.Sort("rent", "desc"),)
        assert got["New eligible"].state.filter == {
            "type": {"in": ["new"]},
            "listing": {"verdict": {"in": ["eligible"]}},
        }
        migrate(command.downgrade, "0009")
        with eng.connect() as c:
            back = {n: q for n, q in c.execute(text("SELECT name, query FROM saved_views"))}
        assert back == {name: q for _, name, q in rows}  # down again: version 1 exactly
    finally:
        migrate(command.upgrade, "head")
        with eng.begin() as c:
            c.execute(text("TRUNCATE saved_views RESTART IDENTITY"))
        eng.dispose()


def test_view_dates_survive(pg):
    v = V.create(pg, state({"last_changed": {"between": {"from": "2026-10-01", "to": "2026-10-07"}}}), "Week")
    (c,) = V.get(pg, v.id).state.filters
    assert c.value == F.Days(date(2026, 10, 1), date(2026, 10, 7))
