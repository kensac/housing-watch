from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from housing_watch import filters as F
from housing_watch import queries as Q
from housing_watch import tasks
from housing_watch.models import Band
from housing_watch.orm import RunRow
from seed import seed, seed_jobs


@pytest.fixture
def db(pg):
    seed(pg)
    return pg


def keys(page):
    return [x.key for x in page.items]


def search(db, tree: dict | None = None, **kw):
    return keys(Q.search_listings(db, Q.ListingQuery(F.from_tree(tree, F.LISTING_FIELDS), **kw)))


def by(field: str, direction: str = "asc") -> tuple[Q.Sort, ...]:
    return (Q.Sort(field, direction),)


def test_search_filters(db):
    res = Q.search_listings(db, Q.ListingQuery())
    assert set(keys(res)) == {"a:1", "a:2", "b:1", "b:2"}  # gone is hidden unless a filter asks about removal
    assert keys(res)[0] == "a:1" and res.verdict_counts == {"eligible": 1, "possible": 1, "excluded": 2}
    assert search(db, {"verdict": {"in": ["possible"]}}) == ["b:1"]
    assert search(db, {"gone_since": {"empty": False}}) == ["b:3"]
    assert search(db, {"status": {"in": ["waitlist"]}}) == ["b:1"]
    assert set(search(db, {"source": {"in": ["a"]}})) == {"a:1", "a:2"}
    assert set(search(db, {"borough": {"in": ["Bronx", "Brooklyn"]}})) == {"a:2", "b:1"}
    assert set(search(db, {"borough": {"not_in": ["Bronx", "Brooklyn"]}})) == {"a:1", "b:2"}
    assert search(db, q="main st") == ["a:1"] and search(db, q="mail") == ["a:1"]  # notes are searched too
    assert search(db, {"notes": {"contains": "MAIL"}}) == ["a:1"] and "a:1" not in search(
        db, {"notes": {"not_contains": "mail"}}
    )
    assert set(search(db, {"address": {"empty": True}})) == {"a:2", "b:1", "b:2"} and search(
        db, {"address": {"empty": False}}
    ) == ["a:1"]
    assert set(search(db, {"units": {"size": {"in": ["studio"]}, "rent": {"le": 2500.0}}})) == {"a:1", "a:2", "b:2"}
    assert search(db, {"units": {"rent": {"lt": 2000.0}}}) == []
    assert search(
        db, {"units": {"rent": {"between": {"min": 2000.0, "max": 2500.0}}}, "verdict": {"in": ["eligible"]}}
    ) == ["a:1"]
    assert "b:1" not in search(db, {"last_changed": {"last": {"amount": 24, "unit": "hours"}}})  # changed 3 days ago
    assert search(db, {"first_seen": {"before": "2000-01-01"}}) == []
    assert search(db, {"senior_only": {"is": True}}) == []
    assert search(db, {"eligible_units": {"ge": 1.0}, "verdict": {"in": ["eligible"]}}) == ["a:1"]
    assert search(db, {"unit_count": {"eq": 0.0}}) == ["b:1"]
    assert search(db, {"reason": {"contains": "bronx"}}) == ["a:2"]
    assert search(db, sort=by("title"))[0] == "a:1" and search(db, sort=by("title", "desc"))[0] == "b:2"
    first = Q.search_listings(db, Q.ListingQuery(limit=3, sort=by("title")))
    assert (first.total, first.next_offset, len(first.items)) == (4, 3, 3)
    rest = Q.search_listings(db, Q.ListingQuery(limit=3, offset=3, sort=by("title")))
    assert (keys(rest), rest.next_offset) == (["b:2"], None)
    tree = {"verdict": {"in": ["eligible"]}, "source": {"in": ["a"]}}
    tabs = Q.search_listings(db, Q.ListingQuery(F.from_tree(tree, F.LISTING_FIELDS)))
    assert tabs.verdict_counts == {"eligible": 1, "possible": 0, "excluded": 1}  # tabs ignore the verdict filter


def test_same_unit_semantics(db):
    from housing_watch.models import Unit
    from seed import L, Session, add_listing

    with Session(db) as s, s.begin():  # a studio at $3,000 and a 1br at $2,000: no single cheap studio
        add_listing(s, L("c", "1", "Queens", units=[Unit("studio", 3000.0), Unit("1br", 2000.0)]))
    assert "c:1" not in search(db, {"units": {"size": {"in": ["studio"]}, "rent": {"le": 2500.0}}})
    assert "c:1" in search(db, {"units": {"size": {"in": ["studio"]}, "rent": {"ge": 2500.0}}})
    assert "c:1" in search(db, {"units": {"size": {"in": ["1br"]}, "rent": {"le": 2500.0}}})
    # bands: both conditions on one band (a:1 has 1p max $154,440 and 2p max $176,410)
    assert "a:1" not in search(db, {"units": {"bands": {"household": {"eq": 1.0}, "income_max": {"gt": 160000.0}}}})
    assert "a:1" in search(db, {"units": {"bands": {"household": {"eq": 2.0}, "income_max": {"gt": 160000.0}}}})


def test_value_counts(db):
    fs = F.LISTING_FIELDS
    assert Q.is_enum(db, fs["borough"]) and not Q.is_enum(db, fs["units.rent"]) and Q.is_enum(db, fs["verdict"])
    counts = dict(Q.value_counts(db, "listings", fs["borough"], F.from_tree({"verdict": {"in": ["eligible"]}}, fs)))
    assert {k: n for k, n in counts.items() if n} == {"Queens": 1}  # under the other filters
    assert list(counts)[:5] == list(fs["borough"].choices)  # an enum lists every value, in order
    counts = dict(Q.value_counts(db, "listings", fs["borough"], F.from_tree({"borough": {"in": ["Queens"]}}, fs)))
    assert counts["Bronx"] == 1 and counts["Queens"] == 1  # a field's own filter doesn't narrow its counts
    assert dict(Q.value_counts(db, "listings", fs["verdict"], [])) == {"eligible": 1, "possible": 1, "excluded": 2}
    assert dict(Q.value_counts(db, "listings", fs["units.size"], []))["studio"] == 3
    ev = dict(Q.value_counts(db, "events", F.EVENT_FIELDS["type"], []))
    assert ev["new"] == 3 and ev["source_failed"] == 1
    assert dict(Q.value_counts(db, "events", F.EVENT_FIELDS["listing.borough"], []))["Queens"] == 2  # a:1, b:3
    assert dict(Q.value_counts(db, "events", F.EVENT_FIELDS["listing.verdict"], []))["eligible"] == 2


def test_listing_detail(db):
    d = Q.listing_detail(db, "a:1")
    assert d.summary.verdict == "eligible" and d.notes == "Apply by mail" and d.household == 1
    assert d.units[0].verdict == "eligible" and d.units[0].bands[0] == Band(1, 82557, 154440)
    assert [e.type for e in d.events] == ["new"]
    bronx = Q.listing_detail(db, "a:2")
    assert bronx.summary.reason == "in Bronx" and bronx.units[0].verdict == "eligible"  # unit judged on its own
    assert Q.listing_detail(db, "nope:1") is None


def test_verdicts_follow_the_profile(db):
    from dataclasses import replace

    from housing_watch.criteria import PROFILE

    rich = replace(PROFILE, income=500_000)
    assert Q.search_listings(db, Q.ListingQuery(), rich).verdict_counts["eligible"] == 0


def test_overview(db):
    o = Q.overview(db)
    assert o.eligible_open == 1 and [x.key for x in o.eligible_now] == ["a:1"]
    assert Q.MatrixCell("excluded", "closed", 1) in o.matrix and len(o.matrix) == 15 and o.total == 4
    assert (o.new_24h, o.baseline_24h, o.changed_24h) == (2, 0, 2)
    assert o.failing == ["b"] and o.pending == 6 and o.last_digest is None
    assert o.recent[0].type == "source_failed"


def test_sources(db):
    rows = {s.source: s for s in Q.sources(db)}
    assert rows["b"].health == "failing" and rows["b"].consecutive_failures == 3
    assert rows["b"].last_error == "TimeoutError: boom" and rows["b"].workers == ["hetzner"]
    assert rows["a"].health == "ok" and rows["a"].listings == 2 and rows["a"].eligible == 1
    assert rows["a"].last_duration == pytest.approx(12)
    assert next(iter(Q.sources(db))).source == "b"  # failing first


def test_events(db):
    page = Q.events(db)
    assert [e.type for e in page.items][:2] == ["source_failed", "gone"]
    assert page.total == 6 and page.next_before is None
    assert next(e for e in page.items if e.key == "a:1").verdict == "eligible"
    ev = lambda tree: [e.key for e in Q.events(db, F.from_tree(tree, F.EVENT_FIELDS)).items]  # noqa: E731
    assert ev({"type": {"in": ["new"]}, "first_run": {"is": False}}) == ["a:2", "a:1"]
    assert ev({"listing": {"borough": {"in": ["Queens"]}}}) == ["b:3", "a:1"]
    assert ev({"listing": {"verdict": {"in": ["eligible"]}}}) == ["b:3", "a:1"]
    assert ev({"listing": {"units": {"size": {"in": ["studio"]}}}, "type": {"in": ["closed"]}}) == ["b:2"]
    assert ev({"at": {"last": {"amount": 1, "unit": "minutes"}}}) == []
    first = Q.events(db, limit=2)
    assert len(first.items) == 2 and first.next_before
    assert Q.events(db, before=first.next_before, limit=10).items[0].id < first.next_before
    assert {e.source for e in Q.events(db, F.from_tree({"source": {"in": ["a"]}}, F.EVENT_FIELDS)).items} == {"a"}


def test_fleet_without_procrastinate(db, monkeypatch):
    monkeypatch.setattr(Q, "JOBS_TABLE", "procrastinate_missing")
    f = Q.fleet(db)
    assert not f.available and {h.name for h in f.hosts} == {"oci", "hetzner"}


def test_fleet(db):
    seed_jobs(db)
    f = Q.fleet(db)
    assert f.available and {(c.status, c.count) for c in f.queues if c.queue == "sources"} == {
        ("succeeded", 1), ("failed", 1), ("doing", 1), ("todo", 1)}  # fmt: skip
    assert f.workers == []  # nothing has registered in fleet_workers yet
    t = datetime.now(UTC)
    tasks.beat(db, "oci", 4, ["sources"], t)
    tasks.beat(db, "oci", 2, ["sources", "byparr"], t)  # a restart updates the row
    with Session(db) as s, s.begin():
        s.add(RunRow(source="c", started=t, workers=["oci"]))  # in flight
    (w,) = Q.fleet(db).workers
    assert w.name == "oci" and w.queues == ["sources", "byparr"] and w.concurrency == 2
    assert w.doing == ["c"] and w.age < 60
    assert f.failures[0].error == "TimeoutError: boom" and f.failures[0].args == Q.JobArgs(source="b")


def test_units_page(db):
    from housing_watch.models import Band, Unit
    from seed import L, add_listing

    two = Unit("2br", 1800.0, 130, "2A", [Band(1, 70000, 154440)])
    cheap = Unit("studio", 2100.0, 130, "1A", [Band(1, 72000, 154440)])
    with Session(db) as s, s.begin():
        add_listing(s, L("u", "1", "Queens", units=[two, cheap]))
    q = Q.ListingQuery
    # listings: the rent range covers only units that fit (the 2BR is not wanted), so the sort is honest
    (x,) = [x for x in Q.search_listings(db, q()).items if x.key == "u:1"]
    assert (x.rent_min, x.rent_max) == (2100.0, 2100.0)
    units = Q.search_units(db, q(sort=by("rent"))).items
    mine = [(u.unit_no, u.verdict) for u in units if u.key == "u:1"]
    assert mine == [("2A", "excluded"), ("1A", "eligible")] and units[0].rent == 1800.0
    # a unit condition holds on the row's own unit; band conditions on that unit's bands
    tree = {"units": {"size": {"in": ["studio"]}, "bands": {"income_min": {"le": 72000}}}}
    got = Q.search_units(db, q(F.from_tree(tree, F.LISTING_FIELDS))).items
    assert {(u.key, u.unit_no) for u in got} >= {("u:1", "1A")} and all(u.size == "studio" for u in got)
    bronx = [u for u in Q.search_units(db, q()).items if u.borough == "Bronx"]
    assert bronx and all(u.verdict == "excluded" and u.reason == "in Bronx" for u in bronx)  # listing rules
    v = dict(Q.value_counts(db, "units", F.LISTING_FIELDS["units.size"], []))
    assert v["2br"] == 1 and v["studio"] >= 2


def test_multi_sort_breaks_ties_in_order(db):
    from seed import L, add_listing

    with Session(db) as s, s.begin():  # two more without a rent: the second sort orders them
        add_listing(s, L("c", "1", "Queens", units=[]))
        add_listing(s, L("c", "2", "Queens", units=[]))
    got = Q.search_listings(db, Q.ListingQuery(sort=(Q.Sort("rent"), Q.Sort("title", "desc")))).items
    no_rent = [x.title for x in got if x.rent_min is None]
    assert len(no_rent) > 1 and no_rent == sorted(no_rent, reverse=True) and got[-1].rent_min is None
    newest = Q.search_listings(db, Q.ListingQuery(sort=by("changed", "desc"))).items
    assert [x.last_changed for x in newest] == sorted((x.last_changed for x in newest), reverse=True)


def test_deadline_is_a_date():
    assert Q.day("2026-11-01") == Q.day("2026-11-01T00:00:00Z") and Q.day("2026-11-01").year == 2026
    assert Q.day("by appointment") is None and Q.day(None) is None


def test_job_args_cover_every_task():
    import inspect

    from housing_watch import tasks

    names = {f for f in Q.JobArgs.__dataclass_fields__}
    for t in [t for t in tasks.app.tasks.values() if not t.name.startswith("builtin:")]:  # never queued here
        params = set(inspect.signature(t.func).parameters) - {"context"}
        assert params <= names, (t.name, params - names)
    assert Q.JobArgs.of({"source": "a", "retry_note": 1}) == Q.JobArgs(source="a")  # unknown kwargs are left out
