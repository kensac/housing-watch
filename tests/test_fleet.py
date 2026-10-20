"""The Procrastinate fleet end to end: real workers on the throwaway database, fake adapters."""

import asyncio
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import procrastinate
import pytest
from procrastinate import PsycopgConnector
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from housing_watch import db, fetch, metrics, runner, tasks
from housing_watch.adapters import Adapter
from housing_watch.models import Listing, Result, Unit
from housing_watch.orm import ChunkResultRow, EventRow, ListingRow, RunRow, SourceStateRow

# Procrastinate's schema is applied by alembic 0002 as of this migration file; bump with a new revision.
PROCRASTINATE_SCHEMA_AT = "03.04.00_50_post_add_retry_failed_job_procedure.sql"


def L(ext_id: str, source: str) -> Listing:
    return Listing(source=source, ext_id=ext_id, title=ext_id, url="u", units=[Unit("studio", 2000.0)])


class Plain(Adapter):
    name, title, url = "t_plain", "Plain", "u"

    def fetch(self):
        self.cache["n"] = self.cache.get("n", 0) + 1
        return Result(listings=[L("one", self.name)])


class Broken(Adapter):
    name, title, url = "t_broken", "Broken", "u"

    def fetch(self):
        raise RuntimeError("site changed")


class Rendered(Adapter):
    name, title, url = "t_rendered", "Rendered", "u"

    def fetch(self):
        return Result(listings=[L(fetch.rendered("https://example.invalid/"), self.name)])


class Chunky(Adapter):
    name, title, url = "t_chunky", "Chunky", "u"
    parallel = 2
    keys = ["a", "b", "c", "d"]
    fail_key: str | None = None
    lock = threading.Lock()
    inflight = peak = fail_calls = 0

    def chunks(self):
        self.cache["planned"] = True
        return list(self.keys)

    def fetch_chunk(self, key):
        cls = type(self)
        with cls.lock:
            cls.inflight += 1
            cls.peak = max(cls.peak, cls.inflight)
        try:
            time.sleep(0.2)
            if key == cls.fail_key:
                cls.fail_calls += 1
                raise RuntimeError(f"chunk {key} broke")
            assert self.cache["planned"]  # every chunk starts from the cache as planned
            self.cache[f"seen_{key}"] = key
            return Result(listings=[L(key, self.name)])
        finally:
            with cls.lock:
                cls.inflight -= 1


FAKES = {c.name: c for c in (Plain, Broken, Rendered, Chunky)}


@pytest.fixture
def fleet(pg, pg_url, monkeypatch):
    with pg.begin() as c:
        c.execute(
            text(
                "TRUNCATE procrastinate_jobs, procrastinate_events, procrastinate_periodic_defers,"
                " procrastinate_workers RESTART IDENTITY CASCADE"
            )
        )
    tasks.set_engine(pg)
    monkeypatch.setattr(tasks.CHUNK_RETRY, "wait", 0)
    monkeypatch.setattr(tasks.RUN_RETRY, "wait", 0)
    monkeypatch.setattr(tasks, "load_all", lambda: dict(FAKES))
    monkeypatch.setattr(fetch, "BYPARR_URL", "")
    monkeypatch.setattr(tasks.app.periodic_registry, "periodic_tasks", {})  # tests call tick themselves
    Chunky.peak, Chunky.fail_key, Chunky.fail_calls = 0, None, 0
    with tasks.app.replace_connector(PsycopgConnector(conninfo=tasks.libpq_url(pg_url))) as app:
        app.open()
        yield app
        app.close()
    tasks.set_engine(None)


def pending(pg, queues) -> int:
    with pg.connect() as c:
        return c.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE status IN ('todo', 'doing') AND queue_name = ANY(:q)"),
            {"q": list(queues)},
        ).scalar()


def drain(app, pg, queues=("sources",), name="w1", concurrency=4):
    """Run a worker until the given queues are empty (a wait=False worker stops at the first empty fetch)."""

    async def once():
        async with app.open_async():
            await app.run_worker_async(
                queues=list(queues), wait=False, concurrency=concurrency, name=name, install_signal_handlers=False
            )

    for _ in range(20):
        asyncio.run(once())
        if not pending(pg, queues):
            break
    app.open()  # back to sync use for the test body
    assert not pending(pg, queues)


def jobs(pg):
    with pg.connect() as c:
        return c.execute(
            text("SELECT task_name, queue_name, status::text, attempts FROM procrastinate_jobs ORDER BY id")
        ).all()


def run_of(pg, source) -> RunRow:
    with Session(pg) as s:
        return s.scalars(select(RunRow).where(RunRow.source == source).order_by(RunRow.id.desc())).first()


def test_single_chunk_runs_inline(fleet, pg):
    tasks.source_run.defer(source="t_plain")
    drain(fleet, pg)
    run = run_of(pg, "t_plain")
    assert run.ok and run.listings == 1 and run.events == 1 and run.workers == ["w1"] and run.chunks is None
    with Session(pg) as s:
        assert db.load_cache(s, "t_plain") == {"n": 1}
        assert s.scalars(select(EventRow.detail)).one() == {"baseline": True}  # first successful run
        assert s.get(SourceStateRow, "t_plain").next_due > db.utcnow() + timedelta(hours=3)  # the default 4h cadence


def test_adapter_error_is_a_failed_run_not_a_retry(fleet, pg):
    tasks.source_run.defer(source="t_broken")
    drain(fleet, pg)
    run = run_of(pg, "t_broken")
    assert run.ok is False and "site changed" in run.error
    assert [j[:3] for j in jobs(pg)] == [("source_run", "sources", "succeeded")]
    assert jobs(pg)[0][3] == 1  # ran once


def test_needs_byparr_moves_the_job_to_the_byparr_queue(fleet, pg, monkeypatch):
    tasks.source_run.defer(source="t_rendered")
    drain(fleet, pg)  # a worker without byparr
    assert jobs(pg) == [("source_run", "byparr", "todo", 1)]
    assert run_of(pg, "t_rendered") is None  # the hand-off is not a failed run
    with Session(pg) as s:
        assert s.get(SourceStateRow, "t_rendered").queue == "byparr"  # tick queues it there from now on
    monkeypatch.setattr(fetch, "rendered", lambda url, **kw: "rendered-page")
    drain(fleet, pg, queues=("sources", "byparr"), name="hetzner")
    run = run_of(pg, "t_rendered")
    assert run.ok and run.workers == ["hetzner"]
    with Session(pg) as s:
        assert s.get(ListingRow, "t_rendered:rendered-page") is not None


def test_chunks_fan_out_merge_and_respect_parallel(fleet, pg):
    tasks.source_run.defer(source="t_chunky")
    drain(fleet, pg, concurrency=4)
    run = run_of(pg, "t_chunky")
    assert run.ok and run.chunks == 4 and run.listings == 4 and run.events == 4, run.error
    assert 1 <= Chunky.peak <= Chunky.parallel  # Procrastinate locks cap the chunks in flight
    with Session(pg) as s:
        assert db.load_cache(s, "t_chunky") == {"planned": True, **{f"seen_{k}": k for k in "abcd"}}
        assert s.scalars(select(ChunkResultRow)).all() == []  # cleared once merged
        assert sorted(s.scalars(select(ListingRow.ext_id))) == ["a", "b", "c", "d"]
    names = [j[0] for j in jobs(pg)]
    assert names.count("source_chunk") == 4 and names.count("source_merge") == 1


def test_chunks_from_several_workers_are_recorded(fleet, pg):
    tasks.source_run.defer(source="t_chunky")

    async def two():
        async with fleet.open_async():
            await asyncio.gather(
                *(
                    fleet.run_worker_async(
                        queues=["sources"], wait=False, concurrency=1, name=n, install_signal_handlers=False
                    )
                    for n in ("oci", "gcp")
                )
            )

    for _ in range(20):
        asyncio.run(two())
        if not pending(pg, ["sources"]):
            break
    fleet.open()
    run = run_of(pg, "t_chunky")
    assert run.ok and set(run.workers) <= {"oci", "gcp"} and run.workers


def test_a_chunk_that_keeps_failing_fails_the_run(fleet, pg):
    Chunky.fail_key = "c"
    tasks.source_run.defer(source="t_chunky")
    drain(fleet, pg)
    run = run_of(pg, "t_chunky")
    assert run.ok is False and "chunk 'c'" in run.error and "broke" in run.error
    with Session(pg) as s:
        assert s.scalars(select(ListingRow)).all() == []  # a partial source is never recorded
    assert [j[2] for j in jobs(pg) if j[0] == "source_chunk"].count("failed") == 1
    assert Chunky.fail_calls == tasks.CHUNK_RETRY.retries + 1  # Procrastinate retried it first


def test_tick_enqueues_due_sources_once(fleet, pg):
    ts = int(datetime(2026, 1, 5, 10, 7).timestamp())
    assert tasks.tick(ts).startswith("4 due")
    assert tasks.tick(ts + 60).startswith("0 due")  # claimed sources are a cycle ahead
    runs = [j for j in jobs(pg) if j[0] == "source_run"]
    assert len(runs) == 4 and all(j[1:3] == ("sources", "todo") for j in runs)


def test_tick_queues_the_digest_at_its_slots(fleet, pg, monkeypatch):
    monkeypatch.setattr(tasks, "DIGEST_HOURS", [8])
    tasks.tick(int(datetime(2026, 1, 5, 8, 1).timestamp()))
    assert not [j for j in jobs(pg) if j[0] == "digest"]
    tasks.tick(int(datetime(2026, 1, 5, 8, 0).timestamp()))
    tasks.tick(int(datetime(2026, 1, 5, 8, 0).timestamp()))  # deduped while one is waiting
    assert [j[:3] for j in jobs(pg) if j[0] == "digest"] == [("digest", "digest", "todo")]


def test_runs_with_events_queue_an_urgent_digest_check(fleet, pg):
    tasks.source_run.defer(source="t_plain")
    drain(fleet, pg)
    assert [j[:3] for j in jobs(pg) if j[0] == "digest"] == [("digest", "digest", "todo")]
    tasks.source_run.defer(source="t_plain")
    drain(fleet, pg)  # no new events: nothing more queued
    assert len([j for j in jobs(pg) if j[0] == "digest"]) == 1


def test_tick_abandons_lost_runs(fleet, pg):
    with Session(pg) as s, s.begin():
        run_id = db.start_run(s, "t_plain")
        s.get(RunRow, run_id).started = db.utcnow() - timedelta(hours=7)
    tasks.tick(int(time.time()))
    run = run_of(pg, "t_plain")
    assert run.id == run_id and run.ok is False and run.error.startswith("abandoned")


def test_retry_stalled_requeues_jobs_of_dead_workers(fleet, pg):
    job_id = tasks.source_run.defer(source="t_plain")
    with pg.begin() as c:  # a worker that claimed the job and died: no heartbeat for minutes
        wid = c.execute(
            text("INSERT INTO procrastinate_workers(last_heartbeat) VALUES (now() - interval '5 minutes') RETURNING id")
        ).scalar()
        c.execute(
            text("UPDATE procrastinate_jobs SET status='doing', worker_id=:w WHERE id=:j"), {"w": wid, "j": job_id}
        )

    class Ctx:
        app = fleet

    assert asyncio.run(_open_and(fleet, tasks.retry_stalled.func(Ctx, 0))) == "1 stalled jobs retried"
    drain(fleet, pg)
    assert run_of(pg, "t_plain").ok


async def _open_and(app, coro):
    async with app.open_async():
        return await coro


def test_reside_spreads_changed_pages_over_chunks_and_merges(fleet, pg, monkeypatch):
    from housing_watch import adapters
    from housing_watch.adapters import reside

    page = (Path(__file__).parent / "fixtures" / "reside" / "115-stanwix-st-apartment-unit-218.html").read_text()
    pages = {f"https://residenewyork.com/property/p{i}/": "2026-10-01" for i in range(12)}
    calls: list[str] = []

    def html(u, **kw):
        if u in reside.SITEMAPS:
            return "".join(f"<loc>{x}</loc><lastmod>{lm}</lastmod>" for x, lm in pages.items()) * (
                u == reside.SITEMAPS[0]
            )
        calls.append(u)
        return page

    monkeypatch.setattr(fetch, "html", html)
    monkeypatch.setattr(reside, "read_form", lambda: [])  # no browser here; the form has its own tests
    monkeypatch.setattr(reside.Reside, "min_listings", 1)
    monkeypatch.setattr(adapters, "CHUNK_LEAST", 3)
    monkeypatch.setattr(tasks, "load_all", lambda: {**FAKES, "reside": reside.Reside})
    tasks.source_run.defer(source="reside")
    drain(fleet, pg)
    run = run_of(pg, "reside")
    assert run.ok and run.chunks == 4 and run.listings == 12, run.error
    assert sorted(calls) == sorted(pages)  # every page fetched exactly once, across the chunks
    with Session(pg) as s:
        assert set(db.load_cache(s, "reside")) == set(pages) | {reside.FORM}  # chunks merged; the plan is not saved
    calls.clear()
    moved, gone = list(pages)[0], list(pages)[-1]
    pages[moved] = "2026-10-02"
    del pages[gone]
    tasks.source_run.defer(source="reside")
    drain(fleet, pg)
    run = run_of(pg, "reside")
    assert run.ok and run.chunks is None and run.listings == 11 and calls == [moved]  # one small chunk, inline
    with Session(pg) as s:
        assert s.scalars(select(EventRow.key).where(EventRow.type == "gone")).all() == ["reside:p11"]
        assert set(db.load_cache(s, "reside")) == set(pages) | {reside.FORM}


def test_inline_run_handles_chunks_and_byparr(fleet, pg):
    assert runner.run_inline(pg, "t_chunky").startswith("ok 4L")
    assert runner.run_inline(pg, "t_rendered").startswith("SKIP")
    assert run_of(pg, "t_rendered") is None


def test_worker_queues_follow_caps():
    assert tasks.queues("") == ["sources"]
    assert tasks.queues("byparr") == ["sources", "byparr"]
    assert tasks.queues("byparr, digest") == ["sources", "byparr", "digest"]


def test_libpq_url():
    assert tasks.libpq_url("postgresql+psycopg://u:p@h:5/d") == "postgresql://u:p@h:5/d"


def test_metrics_read_jobs_and_source_state(fleet, pg):
    tasks.source_run.defer(source="t_plain")
    drain(fleet, pg)
    got = {(m.name, tuple(sorted(s.labels.items()))): s.value for m in metrics.DbCollector(lambda: pg).collect()
           for s in m.samples}  # fmt: skip
    assert got[("hw_jobs", (("status", "succeeded"), ("task", "source_run")))] == 1
    assert got[("hw_source_consecutive_failures", (("source", "t_plain"),))] == 0
    assert ("hw_source_last_success_timestamp_seconds", (("source", "t_plain"),)) in got


def test_procrastinate_schema_is_current():
    """A Procrastinate upgrade with new SQL migrations needs an alembic revision that applies them."""
    mig = Path(procrastinate.__file__).parent / "sql" / "migrations"
    assert max(p.name for p in mig.glob("*.sql")) == PROCRASTINATE_SCHEMA_AT


def test_migrations_downgrade_and_upgrade_again(pg_url):
    base = os.environ["HW_TEST_DATABASE_URL"]
    name = f"hw_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(make_url(base).set(database=name).render_as_string(hide_password=False))
    try:
        from alembic import command
        from alembic.config import Config

        db.migrate(eng)
        db.migrate(eng)  # idempotent
        assert "procrastinate_jobs" in inspect(eng).get_table_names()
        cfg = Config()
        cfg.set_main_option("script_location", str(Path(db.__file__).resolve().parents[2] / "alembic"))
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0001")
        with eng.connect() as c:
            assert not c.execute(text("SELECT count(*) FROM pg_proc WHERE proname LIKE 'procrastinate%'")).scalar()
            assert not c.execute(text("SELECT to_regclass('procrastinate_jobs')")).scalar()
        db.migrate(eng)
        with eng.connect() as c:
            assert c.execute(text("SELECT to_regclass('chunk_results')")).scalar()
    finally:
        eng.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def test_periodic_tasks_registered():
    assert {t.task.name for t in tasks.app.periodic_registry.periodic_tasks.values()} == {
        "tick", "retry_stalled", "cleanup", "commute", "tracker",
    }  # fmt: skip
