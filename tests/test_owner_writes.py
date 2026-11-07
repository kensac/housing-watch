"""The owner's writes beyond views and tracker links: source controls, listing overrides, the profile."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from housing_watch import control, db, overrides, profiles, queries
from housing_watch.eligibility import PROFILE
from housing_watch.models import Band, Listing, NotFound, Refused, Unit
from housing_watch.orm import EventRow, ListingOverrideRow, SourceStateRow
from seed import Session, gql, seed

SRC = "mgny"  # any real adapter: control refuses unknown sources


def L(rent=2400.0, borough="Queens", **kw):
    return Listing(source=SRC, ext_id="a", title="T", url="https://x.example/a", borough=borough,
                   units=[Unit("studio", rent, 130, "2A", bands=[Band(1, 100000, 160000)])], **kw)  # fmt: skip


def record(pg, *lsts):
    with Session(pg) as s, s.begin():
        return db.record_listings(s, SRC, list(lsts))


def events(pg, typ):
    with Session(pg) as s:
        return s.scalars(select(EventRow).where(EventRow.type == typ).order_by(EventRow.id)).all()


# --- sources --------------------------------------------------------------------------------------------


def test_pause_skips_the_tick_and_run_now_makes_it_due(pg):
    with Session(pg) as s, s.begin():
        assert [x for x, _ in db.claim_due(s, {SRC: 1})] == [SRC]  # first claim: due, pushed an hour ahead
    with Session(pg) as s, s.begin():
        assert db.claim_due(s, {SRC: 1}) == []
    control.run_now(pg, SRC)
    with Session(pg) as s, s.begin():
        assert [x for x, _ in db.claim_due(s, {SRC: 1})] == [SRC]
    control.set_paused(pg, SRC, True)
    control_state = control.state(pg, SRC)
    assert control_state[0] is True
    with pytest.raises(Refused, match="paused"):
        control.run_now(pg, SRC)
    with Session(pg) as s, s.begin():
        s.get(SourceStateRow, SRC).next_due = db.utcnow() - timedelta(hours=1)
        assert db.claim_due(s, {SRC: 1}) == []  # due, but paused
    control.set_paused(pg, SRC, False)
    with Session(pg) as s, s.begin():
        assert [x for x, _ in db.claim_due(s, {SRC: 1})] == [SRC]
    with pytest.raises(NotFound):
        control.run_now(pg, "no_such_source")


def test_run_now_ends_a_refusal_backoff(pg):
    with Session(pg) as s, s.begin():
        db.finish_run(s, db.start_run(s, SRC), SRC, False, error="Blocked: GET x: 403", every_hours=1)
        db.finish_run(s, db.start_run(s, SRC), SRC, False, error="Blocked: GET x: 403", every_hours=1)
        assert s.get(SourceStateRow, SRC).next_due > db.utcnow() + timedelta(minutes=90)
    control.run_now(pg, SRC)
    assert control.state(pg, SRC)[1] <= db.utcnow()


# --- overrides ------------------------------------------------------------------------------------------


def test_override_is_stored_kept_across_scrapes_and_cleared(pg):
    record(pg, L())
    key = f"{SRC}:a"
    control.set_override(pg, key, {"borough": "Brooklyn", "notes": "checked by phone"}, None, "agent said so")
    with Session(pg) as s:
        lst = db.load_listing(s, key)
        assert (lst.borough, lst.notes, lst.units[0].rent) == ("Brooklyn", "checked by phone", 2400.0)
        ov = s.get(ListingOverrideRow, key)
        assert ov.raw["borough"] == "Queens" and ov.reason == "agent said so"
    (ch,) = events(pg, "changed")
    assert ch.detail["override"] is True

    record(pg, L(rent=2500.0))  # the source moved its rent: kept as the source's, the override still holds
    with Session(pg) as s:
        lst = db.load_listing(s, key)
        assert (lst.borough, lst.units[0].rent) == ("Brooklyn", 2500.0)
        assert s.get(ListingOverrideRow, key).raw["units"][0]["rent"] == 2500.0

    units = [{"size": "1br", "rent": 1800, "ami": 80, "bands": [{"household": 1, "income_min": 50000,
                                                                 "income_max": 155000}]}]  # fmt: skip
    control.set_override(pg, key, {}, units, None)  # replaces the whole override: borough back to the source's
    with Session(pg) as s:
        lst = db.load_listing(s, key)
        assert (lst.borough, lst.notes, [(u.size, u.rent) for u in lst.units]) == ("Queens", None, [("1br", 1800.0)])
    d = queries.listing_detail(pg, key)
    assert d.override.units is True and d.override.fields == []

    control.clear_override(pg, key)
    with Session(pg) as s:
        lst = db.load_listing(s, key)
        assert [(u.size, u.rent) for u in lst.units] == [("studio", 2500.0)]
        assert s.get(ListingOverrideRow, key) is None
    assert events(pg, "changed")[-1].detail["override"] == "cleared"
    assert queries.listing_detail(pg, key).override is None
    with pytest.raises(NotFound):
        control.clear_override(pg, key)


def test_override_of_a_gone_listing_keeps_it_gone(pg):
    record(pg, L())
    record(pg)  # gone
    control.set_override(pg, f"{SRC}:a", {"status": "waitlist"}, None, None)
    with Session(pg) as s:
        assert db._listing_row(s, f"{SRC}:a").gone_since is not None


def test_override_refusals(pg):
    record(pg, L())
    key = f"{SRC}:a"
    for fields, units, msg in [
        ({"rent": 1}, None, "cannot override rent"),
        ({"borough": "Jersey"}, None, "not one of"),
        ({"deadline": "next week"}, None, "ISO date"),
        ({"senior_only": "yes"}, None, "true or false"),
        ({"title": " "}, None, "non-empty"),
        ({}, None, "at least one"),
        (None, [{"size": "penthouse"}], "not one of"),
        (None, [{"size": "1br", "rent": -1}], "non-negative"),
        (None, [{"size": "1br", "floor": 3}], "unknown field"),
        (None, [{"size": "1br", "bands": [{"household": 1, "income_min": 9, "income_max": 1}]}], "above"),
    ]:
        with pytest.raises(Refused, match=msg):
            control.set_override(pg, key, fields, units, None)
    with pytest.raises(NotFound):
        control.set_override(pg, "mgny:none", {"notes": "x"}, None, None)


def test_apply_keeps_identity():
    lst = overrides.apply(L(), overrides.validate({"title": "New", "deadline": "2026-12-01"}, None))
    assert (lst.key(), lst.title, lst.deadline) == (f"{SRC}:a", "New", "2026-12-01")


# --- profile --------------------------------------------------------------------------------------------


def test_profile_set_partly_and_reset(pg):
    assert profiles.current(pg) == PROFILE and not profiles.is_set(pg)
    p = profiles.update(pg, income=99000, sizes=["studio"])
    assert (p.household, p.income, p.sizes) == (PROFILE.household, 99000, frozenset({"studio"}))
    assert profiles.current(pg) == p and profiles.is_set(pg)
    p = profiles.update(pg, household=2)
    assert (p.household, p.income) == (2, 99000)
    bad = [
        ({"income": 0}, "income"),
        ({"household": 0}, "household"),
        ({"sizes": []}, "at least one"),
        ({"sizes": ["mansion"]}, "not one of"),
        ({"excluded_boroughs": ["Hoboken"]}, "not one of"),
    ]
    for kw, msg in bad:
        with pytest.raises(Refused, match=msg):
            profiles.update(pg, **kw)
    assert profiles.reset(pg) == PROFILE and profiles.current(pg) == PROFILE


def test_profile_change_rejudges_through_graphql(pg):
    from fastapi.testclient import TestClient

    from housing_watch.web.app import create_app

    seed(pg)
    c = TestClient(create_app(pg))
    same = {"origin": "http://testserver"}

    def verdict():
        return gql(c, '{ listing(key: "a:1") { summary { verdict } } }')["data"]["listing"]["summary"]["verdict"]

    assert verdict() == "eligible"
    r = c.post("/graphql", headers=same, json={"query": "mutation { setProfile(input: { income: 900000 }) "
                                                        "{ profile { income } custom } }"})  # fmt: skip
    assert r.json()["data"]["setProfile"] == {"profile": {"income": 900000}, "custom": True}
    assert verdict() == "excluded"  # over income now
    assert gql(c, "{ profile { custom } }")["data"]["profile"]["custom"] is True
    r = c.post("/graphql", json={"query": "mutation { resetProfile { custom } }"})  # no Origin: refused
    assert r.json()["errors"][0]["extensions"]["code"] == "FORBIDDEN"
    c.post("/graphql", headers=same, json={"query": "mutation { resetProfile { custom } }"})
    assert verdict() == "eligible"
