from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from housing_watch import db
from housing_watch.models import Band, Listing, Result, Snapshot, Unit
from housing_watch.orm import EventRow, RunRow, SourceStateRow


def L(ext_id="a", rent=2400.0, **kw):
    return Listing(source="t", ext_id=ext_id, title="T", url="u", units=[Unit("studio", rent, 130, "2A")], **kw)


def events(s, **where):
    q = select(EventRow).order_by(EventRow.id)
    for k, v in where.items():
        q = q.where(getattr(EventRow, k) == v)
    return s.scalars(q).all()


def finish(s, source, ok, error=None, every_hours=None):
    db.finish_run(s, db.start_run(s, source), source, ok, error=error, every_hours=every_hours)


def test_changed_carries_before_and_after(pg):
    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L()])
        db.record_listings(s, "t", [L(rent=2500.0)])
        ev = events(s, type="changed")[0]
        assert ev.detail["before"]["units"][0][2] == 2400.0 and ev.detail["after"]["units"][0][2] == 2500.0


def test_closed_and_reopened(pg):
    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L()])
        db.record_listings(s, "t", [L(status="closed")])
        db.record_listings(s, "t", [L()])
        assert [e.type for e in events(s)] == ["new", "closed", "reopened"]


def test_gone_listing_back_as_closed_is_closed_not_reopened(pg):
    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L()])
        db.record_listings(s, "t", [])
        db.record_listings(s, "t", [L(status="closed")])
        db.record_listings(s, "t", [])
        db.record_listings(s, "t", [L()])
        assert [e.type for e in events(s)] == ["new", "gone", "closed", "gone", "reopened"]


def test_unit_rows_untouched_when_nothing_moved(pg):
    from housing_watch.orm import UnitRow

    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L()])
        before = s.scalars(select(UnitRow.id)).all()
        db.record_listings(s, "t", [L(notes="new notes")])  # not part of the fingerprint
        assert s.scalars(select(UnitRow.id)).all() == before
        assert db.load_listing(s, "t:a").notes == "new notes"


def test_chart_year_is_stamped_and_stored_without_an_event(pg):
    from housing_watch.orm import UnitRow

    def caps():  # 130% AMI caps for 1-2 people on HUD's 2025 chart
        bands = [Band(1, None, 147_420), Band(2, None, 168_480)]
        return Listing(source="t", ext_id="a", title="T", url="u", units=[Unit("studio", 2400.0, 130, "2A", bands)])

    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [caps()])
        assert s.scalar(select(UnitRow.chart_year)) == 2025  # stamped from the printed caps
        s.execute(update(UnitRow).values(chart_year=None))  # a row stored before its chart year was known
        assert db.record_listings(s, "t", [caps()]) == 0
        assert s.scalar(select(UnitRow.chart_year)) == 2025  # stored again, with no event


def test_duplicate_listing_in_one_run_does_not_flap(pg):
    with Session(pg) as s, s.begin():
        assert db.record_listings(s, "t", [L(), L()]) == 1
        assert db.record_listings(s, "t", [L(), L()]) == 0


def test_baseline_flag(pg):
    with Session(pg) as s, s.begin():
        assert db.is_baseline(s, "t")
        db.record_listings(s, "t", [L()], baseline=True)
        db.record_snapshots(s, "t", [Snapshot("t", "p", "p", "text")], baseline=True)
        finish(s, "t", True)
        assert not db.is_baseline(s, "t")
        db.record_listings(s, "t", [L(), L("b")])
        assert [(e.type, (e.detail or {}).get("baseline")) for e in events(s)] == [
            ("new", True), ("watching", True), ("new", None),
        ]  # fmt: skip


def test_one_unreported_failure_recovers_silently(pg):
    with Session(pg) as s, s.begin():
        finish(s, "t", False, error="timed out")
        finish(s, "t", True)
        assert events(s, type="source_failed") == [] and events(s, type="source_recovered") == []
        assert s.get(SourceStateRow, "t").failing_since is None


def test_failures_report_on_2nd_and_10th_then_recover(pg):
    with Session(pg) as s, s.begin():
        for _ in range(10):
            finish(s, "t", False, error="boom")
        st = s.get(SourceStateRow, "t")
        assert st.consecutive_failures == 10 and st.failing_since is not None
        assert [e.detail["fails"] for e in events(s, type="source_failed")] == [2, 10]
        finish(s, "t", True)
        assert len(events(s, type="source_recovered")) == 1
        assert st.consecutive_failures == 0 and st.failing_since is None and st.last_ok is not None


def test_a_run_finishing_after_a_newer_one_leaves_the_source_state_alone(pg):
    with Session(pg) as s, s.begin():
        lost = db.start_run(s, "t")  # its worker died mid-run; the retried job recorded a new run
        finish(s, "t", True, every_hours=1)
        st = s.get(SourceStateRow, "t")
        due = st.next_due
        db.finish_run(s, lost, "t", False, error="abandoned", every_hours=1)  # tick, hours later
        assert s.get(RunRow, lost).ok is False
        assert (st.consecutive_failures, st.failing_since, st.next_due) == (0, None, due)
        finish(s, "t", True, every_hours=1)
        assert events(s, type="source_recovered") == []


def test_run_bookkeeping(pg):
    with Session(pg) as s, s.begin():
        run_id = db.start_run(s, "t", job_id=7, worker="oci")
        db.finish_run(s, run_id, "t", True, listings=3, events=2, every_hours=24, workers=["oci", "gcp", "oci"])
        run = s.get(RunRow, run_id)
        assert (run.ok, run.listings, run.events, run.job_id, run.workers) == (True, 3, 2, 7, ["gcp", "oci"])
        st = s.get(SourceStateRow, "t")
        assert timedelta(hours=23) < st.next_due - st.last_attempt <= timedelta(hours=24)


def test_claim_due(pg):
    now = db.utcnow()
    with Session(pg) as s, s.begin():
        assert db.claim_due(s, {"a": 1, "b": 24}, now) == [("a", None), ("b", None)]  # never run: due now
        assert db.claim_due(s, {"a": 1, "b": 24}, now) == []  # pushed a cycle ahead when claimed
        db.set_queue(s, "a", "byparr")
        assert db.claim_due(s, {"a": 1, "b": 24}, now + timedelta(hours=2)) == [("a", "byparr")]


def test_cache_roundtrip(pg):
    with Session(pg) as s, s.begin():
        assert db.load_cache(s, "t") == {}
        db.save_cache(s, "t", {"x": {"u": 1}})
        db.save_cache(s, "t", {"x": {"u": 2}, "y": [1]})
        assert db.load_cache(s, "t") == {"x": {"u": 2}, "y": [1]}


def test_save_chunk_completes_once_all_are_in(pg):
    with Session(pg) as s, s.begin():
        run_id = db.start_run(s, "t")
        db.plan_run(s, run_id, ["a", "b"], {"c": 1})
        assert db.open_plan(s, run_id) == (["a", "b"], {"c": 1})
        assert not db.save_chunk(s, run_id, "b", ok=True, result={}, worker="w1")
        assert not db.save_chunk(s, run_id, "b", ok=True, result={}, worker="w1")  # a retry overwrites
        assert db.save_chunk(s, run_id, "a", ok=False, error="x", worker="w2")
        assert [c.key for c in db.chunk_results(s, run_id)] == ["a", "b"]  # in plan order
        assert db.mergeable_runs(s, timedelta(minutes=-1)) == [(run_id, "t")]


def test_result_json_roundtrip():
    res = Result(
        listings=[L(borough="Queens")],
        snapshots=[Snapshot("t", "p", "p", "text", ["l"])],
    )
    res.listings[0].units[0].bands = [Band(1, 2, 3)]
    assert Result.from_json(res.to_json()) == res


def test_digest_collapses_baseline(pg):
    from sqlalchemy.orm import Session

    from housing_watch import digest
    from housing_watch.models import Band, Listing, Unit

    good = Listing(
        "t", "a", "Good", "u", borough="Queens", units=[Unit("studio", 2400, 130, bands=[Band(1, 82557, 154440)])]
    )
    meh = Listing("t", "b", "Meh", "u", borough="Queens")
    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [good, meh], baseline=True)
    out = digest.send(pg, dry_run=True)
    assert "Good" in out and "Meh" not in out and "First run of 1 source(s) (t): 2 listings" in out


def test_updated_moves_with_stored_fields_not_with_scrapes(pg):
    from dataclasses import replace

    from housing_watch.orm import ListingRow

    def year(y):
        return replace(L(), units=[Unit("studio", 2400.0, 130, "2A", chart_year=y)])

    t = db.utcnow()
    runs = [  # (what the source lists, whether a stored field moved)
        ([year(2024)], True),  # new
        ([year(2024)], False),  # the same again: only last_seen moves
        ([year(2025)], True),  # chart_year alone: no event, but verdicts read it
        ([replace(year(2025), title="T2")], True),  # title alone: no event, but the UI shows it
        ([], True),  # gone
        ([replace(year(2025), title="T2")], True),  # reopened
    ]
    with Session(pg) as s, s.begin():
        for i, (listed, moved) in enumerate(runs):
            now = t + timedelta(minutes=i)
            db.record_listings(s, "t", listed, now=now)
            s.flush()
            row = s.scalar(select(ListingRow))
            assert (row.updated == now) == moved, i


def test_unchanged_scrape_keeps_the_judged_cache(pg):
    from dataclasses import replace

    from housing_watch import queries

    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L()])
    first = queries.judged(pg)
    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [L()])
    assert queries.judged(pg) is first
    with Session(pg) as s, s.begin():
        db.record_listings(s, "t", [replace(L(), title="T2")])
    assert [x.title for x in queries.judged(pg)] == ["T2"]
    with Session(pg) as s, s.begin():  # another source's run stamps the newest `updated`
        db.record_listings(s, "u", [replace(L(), source="u")])
    queries.judged(pg)
    with Session(pg) as s, s.begin():  # a run that started earlier commits later, with an older timestamp
        db.record_listings(s, "t", [replace(L(), title="T3")], now=db.utcnow() - timedelta(minutes=5))
    assert [x.title for x in queries.judged(pg) if x.source == "t"] == ["T3"]
