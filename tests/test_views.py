import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from housing_watch import filters as F
from housing_watch import views as V
from housing_watch.orm import SavedViewRow
from seed import seed


def state(*fs, page="listings", **kw):
    return V.State(page, F.parse(list(fs), V.FIELDS[page]), **kw)


def test_nothing_is_seeded(pg):
    assert V.list_views(pg) == [] and V.default(pg, "listings") is None


def test_crud(pg):
    a = V.create(pg, state("borough.in.Queens", sort="-rent", columns=("verdict", "rent")), "Queens")
    assert a.state.key() == (("borough.in.Queens",), "", "-rent", ("verdict", "rent"))
    assert a.url() == f"/listings?f=borough.in.Queens&sort=-rent&cols=verdict%2Crent&view={a.id}"
    b = V.create(pg, state("type.in.new", page="events"), "New events")
    c = V.create(pg, state(q="loft"), "Lofts")
    assert [v.name for v in V.list_views(pg, "listings")] == ["Queens", "Lofts"]
    V.change(pg, c.id, move=-1)
    assert [v.name for v in V.list_views(pg, "listings")] == ["Lofts", "Queens"]
    V.change(pg, a.id, name="  Queens   only ", state=state("borough.in.Queens", "units.rent.le.2500"))
    got = V.get(pg, a.id)
    assert got.name == "Queens only" and [c.url() for c in got.state.filters][-1] == "units.rent.le.2500"
    d = V.duplicate(pg, a.id)
    assert d.name == "Queens only (copy)" and d.state.key() == got.state.key() and d.id != a.id
    assert V.delete(pg, d.id) and not V.delete(pg, d.id) and V.get(pg, d.id) is None
    assert V.get(pg, b.id).page == "events"
    with pytest.raises(V.ViewError):
        V.create(pg, state(), "   ")
    with pytest.raises(V.ViewError):
        V.change(pg, a.id, state=state(page="events"))


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


def test_stale_fields_degrade_with_a_warning(pg):
    v = V.create(pg, state("borough.in.Queens"), "Old")
    with Session(pg) as s, s.begin():
        s.execute(
            update(SavedViewRow)
            .where(SavedViewRow.id == v.id)
            .values(
                query={
                    "filters": [
                        {"field": "borough", "op": "in", "values": ["Queens"]},
                        {"field": "pets_ok", "op": "is", "values": ["yes"]},
                        {"field": "units.rent", "op": "near", "values": ["1"]},
                    ],
                    "sort": "-rent",
                    "columns": ["rent", "floor"],
                }
            )  # fmt: skip
        )
    got = V.get(pg, v.id)
    assert [c.url() for c in got.state.filters] == ["borough.in.Queens"] and got.state.sort == "-rent"
    assert len(got.warnings) == 3 and "pets_ok" in got.warnings[0] and "floor" in got.warnings[2]


def test_counts(pg):
    seed(pg)
    V.create(pg, state("verdict.in.eligible"), "Eligible")
    V.create(pg, state("type.in.new", page="events"), "New")
    assert {v.name: v.count for v in V.with_counts(pg)} == {"Eligible": 1, "New": 3}
