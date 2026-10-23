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


def search(db, *fs, **kw):
    return keys(Q.search_listings(db, Q.ListingQuery(F.parse(list(fs), F.LISTING_FIELDS), **kw)))


def test_search_filters(db):
    res = Q.search_listings(db, Q.ListingQuery())
    assert set(keys(res)) == {"a:1", "a:2", "b:1", "b:2"}  # gone is hidden unless a filter asks about removal
    assert keys(res)[0] == "a:1" and res.verdict_counts == {"eligible": 1, "possible": 1, "excluded": 2}
    assert search(db, "verdict.in.possible") == ["b:1"]
    assert search(db, "gone_since.notempty") == ["b:3"]
    assert search(db, "status.in.waitlist") == ["b:1"]
    assert set(search(db, "source.in.a")) == {"a:1", "a:2"}
    assert set(search(db, "borough.in.Bronx,Brooklyn")) == {"a:2", "b:1"}
    assert set(search(db, "borough.nin.Bronx,Brooklyn")) == {"a:1", "b:2"}
    assert search(db, q="main st") == ["a:1"] and search(db, q="mail") == ["a:1"]  # notes are searched too
    assert search(db, "notes.contains.MAIL") == ["a:1"] and "a:1" not in search(db, "notes.ncontains.mail")
    assert set(search(db, "address.empty")) == {"a:2", "b:1", "b:2"} and search(db, "address.notempty") == ["a:1"]
    assert set(search(db, "units.size.in.studio", "units.rent.le.2500")) == {"a:1", "a:2", "b:2"}
    assert search(db, "units.rent.lt.2000") == []
    assert search(db, "units.rent.between.2000,2500", "verdict.in.eligible") == ["a:1"]
    assert "b:1" not in search(db, "last_changed.last.24h")  # changed 3 days ago
    assert search(db, "first_seen.before.2000-01-01") == []
    assert search(db, "senior_only.is.yes") == []
    assert search(db, "eligible_units.ge.1", "verdict.in.eligible") == ["a:1"]
    assert search(db, "unit_count.eq.0") == ["b:1"]
    assert search(db, "reason.contains.bronx") == ["a:2"]
    assert search(db, sort="title")[0] == "a:1" and search(db, sort="-title")[0] == "b:2"
    first = Q.search_listings(db, Q.ListingQuery(limit=3, sort="title"))
    assert (first.total, first.next_offset, len(first.items)) == (4, 3, 3)
    rest = Q.search_listings(db, Q.ListingQuery(limit=3, offset=3, sort="title"))
    assert (keys(rest), rest.next_offset) == (["b:2"], None)
    tabs = Q.search_listings(db, Q.ListingQuery(F.parse(["verdict.in.eligible", "source.in.a"], F.LISTING_FIELDS)))
    assert tabs.verdict_counts == {"eligible": 1, "possible": 0, "excluded": 1}  # tabs ignore the verdict filter


def test_same_unit_semantics(db):
    from housing_watch.models import Unit
    from seed import L, Session, add_listing

    with Session(db) as s, s.begin():  # a studio at $3,000 and a 1br at $2,000: no single cheap studio
        add_listing(s, L("c", "1", "Queens", units=[Unit("studio", 3000.0), Unit("1br", 2000.0)]))
    assert "c:1" not in search(db, "units.size.in.studio", "units.rent.le.2500")
    assert "c:1" in search(db, "units.size.in.studio", "units.rent.ge.2500")
    assert "c:1" in search(db, "units.size.in.1br", "units.rent.le.2500")
    # bands: both conditions on one band (a:1 has 1p max $154,440 and 2p max $176,410)
    assert "a:1" not in search(db, "bands.household.eq.1", "bands.income_max.gt.160000")
    assert "a:1" in search(db, "bands.household.eq.2", "bands.income_max.gt.160000")


def test_value_counts(db):
    fs = F.LISTING_FIELDS
    assert Q.is_enum(db, fs["borough"]) and not Q.is_enum(db, fs["units.rent"]) and Q.is_enum(db, fs["verdict"])
    counts = dict(Q.value_counts(db, "listings", fs["borough"], F.parse(["verdict.in.eligible"], fs)))
    assert counts == {"Queens": 1}  # under the other filters
    counts = dict(Q.value_counts(db, "listings", fs["borough"], F.parse(["borough.in.Queens"], fs)))
    assert counts["Bronx"] == 1 and counts["Queens"] == 1  # a field's own filter doesn't narrow its counts
    assert dict(Q.value_counts(db, "listings", fs["verdict"], [])) == {"eligible": 1, "possible": 1, "excluded": 2}
    assert dict(Q.value_counts(db, "listings", fs["units.size"], []))["studio"] == 3
    ev = dict(Q.value_counts(db, "events", F.EVENT_FIELDS["type"], []))
    assert ev["new"] == 3 and ev["source_failed"] == 1
    assert dict(Q.value_counts(db, "events", F.EVENT_FIELDS["listing.borough"], []))["Queens"] == 2  # a:1, b:3


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
    assert o.matrix["excluded"]["closed"] == 1 and o.total == 4
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
    ev = lambda *fs: [e.key for e in Q.events(db, F.parse(list(fs), F.EVENT_FIELDS)).items]  # noqa: E731
    assert ev("type.in.new", "first_run.is.no") == ["a:2", "a:1"]
    assert ev("listing.borough.in.Queens") == ["b:3", "a:1"] and ev("verdict.in.eligible") == ["b:3", "a:1"]
    assert ev("units.size.in.studio", "type.in.closed") == ["b:2"] and ev("at.last.1m") == []
    first = Q.events(db, limit=2)
    assert len(first.items) == 2 and first.next_before
    assert Q.events(db, before=first.next_before, limit=10).items[0].id < first.next_before
    assert {e.source for e in Q.events(db, F.parse(["source.in.a"], F.EVENT_FIELDS)).items} == {"a"}


def test_fleet_without_procrastinate(db, monkeypatch):
    monkeypatch.setattr(Q, "JOBS_TABLE", "procrastinate_missing")
    f = Q.fleet(db)
    assert not f.available and {h.name for h in f.hosts} == {"oci", "hetzner"}


def test_fleet(db):
    seed_jobs(db)
    f = Q.fleet(db)
    assert f.available and f.queues["sources"] == {"succeeded": 1, "failed": 1, "doing": 1, "todo": 1}
    assert f.workers == []  # nothing has registered in fleet_workers yet
    t = datetime.now(UTC)
    tasks.beat(db, "oci", 4, ["sources"], t)
    tasks.beat(db, "oci", 2, ["sources", "byparr"], t)  # a restart updates the row
    with Session(db) as s, s.begin():
        s.add(RunRow(source="c", started=t, workers=["oci"]))  # in flight
    (w,) = Q.fleet(db).workers
    assert w.name == "oci" and w.queues == ["sources", "byparr"] and w.concurrency == 2
    assert w.doing == ["c"] and w.age < 60
    assert f.failures[0].error == "TimeoutError: boom" and f.failures[0].args == {"source": "b"}


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
    units = Q.search_units(db, q(sort="rent")).items
    mine = [(u.unit_no, u.verdict) for u in units if u.key == "u:1"]
    assert mine == [("2A", "excluded"), ("1A", "eligible")] and units[0].rent == 1800.0
    # a unit condition holds on the row's own unit; band conditions on that unit's bands
    got = Q.search_units(db, q([F.parse_one("units.size.in.studio", F.LISTING_FIELDS),
                                F.parse_one("bands.income_min.le.72000", F.LISTING_FIELDS)])).items  # fmt: skip
    assert {(u.key, u.unit_no) for u in got} >= {("u:1", "1A")} and all(u.size == "studio" for u in got)
    bronx = [u for u in Q.search_units(db, q()).items if u.borough == "Bronx"]
    assert bronx and all(u.verdict == "excluded" and u.reason == "in Bronx" for u in bronx)  # listing rules
    v = dict(Q.value_counts(db, "units", F.LISTING_FIELDS["units.size"], []))
    assert v["2br"] == 1 and v["studio"] >= 2
