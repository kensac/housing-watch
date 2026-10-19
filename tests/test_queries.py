import pytest

from housing_watch import queries as Q
from seed import procrastinate_engine, seed


@pytest.fixture
def db(pg):
    seed(pg)
    return pg


def keys(page):
    return [x.key for x in page.items]


def test_search_filters(db):
    res = Q.search_listings(db, Q.ListingFilters())
    assert set(keys(res)) == {"a:1", "a:2", "b:1", "b:2"}  # gone is hidden unless asked for
    assert keys(res)[0] == "a:1" and res.verdict_counts == {"eligible": 1, "possible": 1, "excluded": 2}
    assert keys(Q.search_listings(db, Q.ListingFilters(verdict="possible"))) == ["b:1"]
    assert keys(Q.search_listings(db, Q.ListingFilters(status="gone"))) == ["b:3"]
    assert keys(Q.search_listings(db, Q.ListingFilters(status="waitlist"))) == ["b:1"]
    assert set(keys(Q.search_listings(db, Q.ListingFilters(source="a")))) == {"a:1", "a:2"}
    assert keys(Q.search_listings(db, Q.ListingFilters(borough="Bronx"))) == ["a:2"]
    assert keys(Q.search_listings(db, Q.ListingFilters(q="main st"))) == ["a:1"]
    assert keys(Q.search_listings(db, Q.ListingFilters(q="mail"))) == ["a:1"]  # notes are searched too
    assert set(keys(Q.search_listings(db, Q.ListingFilters(size="studio", max_rent=2500)))) == {"a:1", "a:2", "b:2"}
    assert keys(Q.search_listings(db, Q.ListingFilters(max_rent=2000))) == []
    assert "b:1" not in keys(Q.search_listings(db, Q.ListingFilters(since="24h")))  # changed 3 days ago
    assert keys(Q.search_listings(db, Q.ListingFilters(sort="title")))[0] == "a:1"
    assert keys(Q.search_listings(db, Q.ListingFilters(sort="-title")))[0] == "b:2"
    paged = Q.search_listings(db, Q.ListingFilters(per_page=3, page=2, sort="title"))
    assert (paged.total, paged.pages, keys(paged)) == (4, 2, ["b:2"])


def test_parse_since():
    assert Q.parse_since("") is None and Q.parse_since("junk") is None
    assert (Q.now() - Q.parse_since("2h")).total_seconds() == pytest.approx(7200, abs=5)
    assert Q.parse_since("2026-10-01").year == 2026


def test_listing_detail(db):
    d = Q.listing_detail(db, "a:1")
    assert d.summary.verdict == "eligible" and d.notes == "Apply by mail" and d.household == 1
    assert d.units[0].verdict == "eligible" and d.units[0].bands[0] == (1, 82557, 154440)
    assert [e.type for e in d.events] == ["new"]
    bronx = Q.listing_detail(db, "a:2")
    assert bronx.summary.reason == "in Bronx" and bronx.units[0].verdict == "eligible"  # unit judged on its own
    assert Q.listing_detail(db, "nope:1") is None


def test_verdicts_follow_the_profile(db):
    from dataclasses import replace

    from housing_watch.criteria import PROFILE

    rich = replace(PROFILE, income=500_000)
    assert Q.search_listings(db, Q.ListingFilters(), rich).verdict_counts["eligible"] == 0


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
    assert page.type_counts["new"] == 3 and page.next_before is None
    assert next(e for e in page.items if e.key == "a:1").verdict == "eligible"
    assert [e.key for e in Q.events(db, type="new", baseline=False).items] == ["a:2", "a:1"]
    first = Q.events(db, limit=2)
    assert len(first.items) == 2 and first.next_before
    assert Q.events(db, before=first.next_before, limit=10).items[0].id < first.next_before
    assert {e.source for e in Q.events(db, source="a").items} == {"a"}


def test_fleet_without_procrastinate(db):
    f = Q.fleet(db)
    assert not f.available and {h["name"] for h in f.hosts} == {"oci", "hetzner"}


def test_fleet(db, pg_url):
    eng = procrastinate_engine(pg_url)
    try:
        f = Q.fleet(eng)
    finally:
        eng.dispose()
    assert f.available and f.queues["sources"] == {"succeeded": 1, "failed": 1, "doing": 1, "todo": 1}
    (w,) = f.workers  # host: from the newest run among its jobs
    assert w["host"] == "hetzner" and w["doing"] == ["source_run a"] and w["age"] < 60
    assert f.failures[0]["error"] == "TimeoutError: boom"
